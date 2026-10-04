# Authentication & Per-User Usage — Implementation Plan

> Companion to [`PROJECT_PLAN.md`](PROJECT_PLAN.md) (architecture) and
> [`SPRINT_2_DAY.md`](SPRINT_2_DAY.md) (the completed build).
>
> **Scope: lean + refresh tokens.** Two new tables, not four. The cuts and the
> reasons for them are in §12 — they are cuts of *implementation*, and each one
> names the seam that makes it cheap to add later.

---

## 1. The problem

The API is open. Anyone who can reach it can start an analysis, read every other
person's analysis, and drain the day's free-tier quota. Three problems, one cause:
nothing knows who is asking.

| Today | With accounts |
|---|---|
| Every analysis and data source is world-readable | Rows scoped by `user_id`; one account cannot read another's work |
| `llm_calls` records *which model*, never *who* | Every call, token and run attributable to an account |
| Only a per-analysis cap ([`CallBudget`](../backend/app/llm/budget.py): 12 calls / 80k tokens) | A per-account daily ceiling, checked before a run starts |

`CallBudget` is unchanged. It bounds **one question**; what is missing is a bound
on **one person per day**.

---

## 2. Decisions

| Decision | Choice | Why, and what was rejected |
|---|---|---|
| Password hashing | **`pwdlib[argon2]`** (Argon2id) | OWASP's first choice. Rejected `passlib` (unmaintained since 2020) and raw bcrypt (silent 72-byte truncation) |
| JWT library | **`PyJWT`** | What FastAPI's own docs use. Rejected `python-jose` (thin maintenance, CVE history) |
| Signing | **HS256**, `AUTH_SECRET_KEY` | One service signs and verifies. `security.py` is the seam if this ever becomes RS256 |
| Token shape | **Access 30 min + refresh 14 days, rotated** | An access token cannot be withdrawn, so it must be short. The refresh row in the database is what makes signing out real |
| Frontend storage | **Access token in memory; refresh token in an `httpOnly` cookie** | XSS can read neither. Rejected `localStorage`: one XSS is then a permanent account takeover |
| Identity | **email + password** | The email *is* the username. Stored lowercased, matched case-insensitively |
| Enforcement | **Router-level `dependencies=[...]`** | A new endpoint on a protected router is protected by default. Per-endpoint decoration fails open the day someone forgets — §9 has the test that pins this |
| Usage storage | **Queried from `llm_calls`, no rollup table** | At this scale an indexed `WHERE user_id = ? AND created_at >= today` is instant. `usage_service.today()` is the seam: its body becomes a rollup read if it ever needs to |

---

## 3. Data model

**Two new tables, three altered.** One hand-written Alembic revision.

### 3.1 `users`

```
id              uuid pk
email           varchar(254) unique indexed    -- stored lowercased
hashed_password varchar(255)
full_name       varchar(120) null
is_active       boolean default true
is_admin        boolean default false          -- no admin UI yet; one column now, no migration later
token_version   integer default 0
last_login_at   timestamptz null
created_at / updated_at                        -- TimestampMixin
```

`token_version` is the piece that is usually left out. Without it, changing a
password does not invalidate a stolen access token until it expires on its own.
It is a claim on every token and a check on every request.

### 3.2 `refresh_tokens`

```
id           uuid pk
user_id      uuid fk -> users.id on delete cascade, indexed
token_hash   varchar(64) unique indexed    -- sha256; the token itself is never stored
expires_at   timestamptz
revoked_at   timestamptz null
replaced_by  uuid null fk -> refresh_tokens.id on delete set null
user_agent   varchar(200) null
created_at   timestamptz
```

**Rotation with reuse detection.** Refreshing revokes the presented token and
issues a new one, linked back through `replaced_by`. A token that has already
been exchanged should never be seen again — so if one *is*, the copy leaked:
revoke every token in that user's chain and bump `token_version`, which also
kills their live access tokens. Cheap to build now, impossible to retrofit
credibly later, and a concrete thing to point at in an interview.

### 3.3 Altered

| Table | Change | Nullable? |
|---|---|---|
| `analyses` | `+ user_id`, index `(user_id, created_at)` | **not null** after backfill |
| `llm_calls` | `+ user_id`, index `(user_id, created_at)` | nullable — evaluation runs have no user |
| `data_sources` | `+ owner_id`, indexed | **nullable on purpose**: `NULL` = shared source, queryable by all, editable only by an admin |

`llm_calls.user_id` is denormalised (reachable via `analysis_id`). Deliberate:
"what did this person spend" must not need a join, and rows with
`analysis_id IS NULL` would be invisible otherwise.

---

## 4. Modules

```
backend/app/
  core/security.py          # hash/verify, encode/decode, hash_token — no DB, no request
  db/models/user.py         # User, RefreshToken
  schemas/auth.py           # RegisterIn, LoginIn, TokenOut, UserOut, UsageOut
  services/auth_service.py  # register, authenticate, issue, rotate, revoke
  services/usage_service.py # today(), over_quota()
  api/deps.py               # the dependency chain
  api/routes/auth.py        # /auth/*
```

`core/security.py` holds no database access — pure functions over strings and
claims. That is what makes it unit-testable with no fixtures, and it is the only
file to touch if signing changes.

---

## 5. The dependency chain

```python
# app/api/deps.py
bearer = HTTPBearer(auto_error=False)

async def get_current_user(creds, session) -> User:
    """Decode the token and load the account. 401 on anything wrong."""
    if creds is None:
        raise unauthenticated("Not signed in.")
    claims = decode_token(creds.credentials, expected_type="access")   # raises -> 401
    user = await session.get(User, uuid.UUID(claims["sub"]))
    if user is None or user.token_version != claims["tv"]:
        raise unauthenticated("This session is no longer valid. Sign in again.")
    return user

async def get_active_user(user = Depends(get_current_user)) -> User:
    if not user.is_active:
        raise HTTPException(403, "This account is disabled.")
    return user

async def enforce_quota(user = Depends(get_active_user), session = ...) -> None:
    """Only on endpoints that spend model calls."""
    if (limit := await usage_service.over_quota(session, user)) is not None:
        raise HTTPException(429, f"Daily {limit} limit reached. It resets at midnight UTC.")

ActiveUser = Annotated[User, Depends(get_active_user)]
```

Applied at the **router**, so future endpoints inherit it:

```python
router = APIRouter(prefix="/analyses", dependencies=[Depends(get_active_user)])

@router.post("", status_code=202, dependencies=[Depends(enforce_quota)])
async def start_analysis(body: AnalysisCreate, user: ActiveUser, session: SessionDep): ...
```

Declaring `user: ActiveUser` in the handler does **not** re-run the dependency —
FastAPI caches per request — so the router guard and the handler's need for the
object cost one resolution between them.

Public routers (no dependency): `/health`, `/auth/register`, `/auth/login`,
`/auth/refresh`.

---

## 6. Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/auth/register` | public | 201 + access token, sets refresh cookie. Gated by `AUTH_ALLOW_REGISTRATION` |
| POST | `/auth/login` | public | JSON body. Same response shape |
| POST | `/auth/refresh` | refresh cookie | Rotates. Reuse of a revoked token burns the chain |
| POST | `/auth/logout` | refresh cookie | Revokes that token, clears the cookie |
| POST | `/auth/change-password` | active | Bumps `token_version`, revokes all refresh tokens |
| GET | `/auth/me` | active | Profile + today's usage + remaining quota, in one call |

**Login failure is always the same message and the same work** — "Email or
password is incorrect" — whether or not the address exists. A distinct 404 is a
free account-enumeration oracle, and an early `return` is a timing one, so an
unknown email still runs a verification against a dummy hash.

---

## 7. Usage & quota

Counted by query, not a rollup:

```sql
SELECT count(*)                       AS llm_calls,
       coalesce(sum(input_tokens), 0) AS input_tokens,
       coalesce(sum(output_tokens), 0) AS output_tokens
FROM llm_calls
WHERE user_id = :user AND created_at >= date_trunc('day', now()) AND NOT cached;
```

plus a `count(*)` on `analyses` for the same window. `NOT cached` matters: a
cache hit carries the original call's token counts, so counting it would bill
someone twice for one request.

Limits come from settings (`QUOTA_ANALYSES_PER_DAY=20`,
`QUOTA_LLM_CALLS_PER_DAY=150`, `QUOTA_TOKENS_PER_DAY=1_000_000`).

`user_id` reaches the telemetry row the way `analysis_id` already does:
`run_analysis(...)` takes it and threads it to the recorder.

**Quota is checked before a run, not during.** With `MAX_CONCURRENT_RUNS = 2`
and 12 calls per analysis, worst-case overshoot past a daily ceiling is 24 calls.
Bounded, and worth the simplicity.

---

## 8. Ownership scoping — the actual security boundary

Authentication without scoping is decoration. Every read path changes:

```python
# before
await session.get(Analysis, analysis_id)
# after
select(Analysis).where(Analysis.id == analysis_id, Analysis.user_id == user.id)
```

- `list_analyses` filters on `user_id`.
- `get_analysis` for someone else's run returns **404, not 403** — a 403 confirms
  the row exists.
- Data sources: visible when `owner_id = user.id OR owner_id IS NULL`. Only the
  owner may sync, edit or delete; a `NULL`-owner source is admin-only to change.
- Starting an analysis against an invisible data source is a 404.

---

## 9. Testing

| Test | What it pins |
|---|---|
| `unit/test_security.py` | Hash ≠ password; verify round-trips; expired rejected; tampered signature rejected; a refresh token presented as an access token rejected |
| `integration/test_auth_routes.py` | register → login → refresh → logout; rotation issues a new token; **reusing a revoked refresh token burns the chain**; password change invalidates live access tokens |
| `integration/test_route_protection.py` | **Walks `app.routes` and asserts every route outside an allowlist carries the auth dependency.** The test that survives future endpoints |
| `integration/test_ownership.py` | User A gets 404 for user B's analysis; list endpoints never leak another user's rows |
| `integration/test_quota.py` | 429 once the daily ceiling is hit; cached calls are not counted |

The existing 511 tests must keep passing. Most call services directly; the
fallout is confined to route tests, which gain a signed-in client fixture — added
in Block C, *before* the routers are locked down, or they all fail at once.

---

## 10. Settings

```
AUTH_SECRET_KEY=<openssl rand -hex 32>    # required; startup refuses a weak or missing key
AUTH_ACCESS_TOKEN_MINUTES=30
AUTH_REFRESH_TOKEN_DAYS=14
AUTH_ALLOW_REGISTRATION=true
AUTH_COOKIE_SECURE=false                  # true in any deployed environment
AUTH_COOKIE_SAMESITE=lax                  # "none" + secure when the frontend is on another domain
QUOTA_ANALYSES_PER_DAY=20
QUOTA_LLM_CALLS_PER_DAY=150
QUOTA_TOKENS_PER_DAY=1000000
SEED_ADMIN_EMAIL=
SEED_ADMIN_PASSWORD=
```

`AUTH_SECRET_KEY` is validated at startup the way the embedding dimension
already is: fail fast with a message that says how to generate one, rather than
silently signing tokens anyone can forge.

---

## 11. Build blocks

| Block | Work | Est. | State |
|---|---|---|---|
| **A** | Deps, settings, `core/security.py`, `User` + `RefreshToken`, user columns on three tables, hand-written migration | 1.5h | **done** |
| **B** | `auth_service` + `/auth/*`, rotation, reuse detection, seed admin at startup | 2h | **done** |
| **C** | `api/deps.py`, apply to every router, ownership scoping in both services, signed-in test fixture | 1.5h | **done** |
| **D** | `usage_service`, `user_id` through to the recorder, `enforce_quota`, `/auth/me` | 1h | **done** |
| **E** | Frontend: auth context, api-client refresh-and-retry, login/register, route guard, usage on the profile | 2h | **done** |
| **F** | Tests (§9), README section, `.env.example` | 1h | **done** |
| | **Total** | **~9h** | |

A–D are independently shippable: the API is fully protected at the end of C, and
D only adds ceilings on top.

### Migration order (Block A, already written)

1. Create `users`, `refresh_tokens`.
2. Add the three columns **nullable**.
3. Backfill: insert a `legacy@insightflow.local` owner (unusable password hash),
   assign existing analyses to it, copy `user_id` onto their `llm_calls`.
4. `ALTER analyses.user_id SET NOT NULL`, then add the foreign keys and indexes.
5. Leave `data_sources.owner_id` NULL — existing sources become shared, which is
   exactly right for the seeded demo database.

Autogenerate emits the NOT NULL before the backfill and fails on a populated
database, which is why this one is written by hand.

---

## 12. Cut from the full design — and the seam that keeps each cheap

| Cut | Seam |
|---|---|
| `user_usage_daily` rollup table | `usage_service.today()` is a function; its body becomes a rollup read when the query stops being instant |
| `user_quotas` table (per-user overrides) | `over_quota()` reads limits from settings; it gains a per-user lookup without touching callers |
| Admin routes (list users, set quota, disable) | `is_admin` column ships now; a `require_admin` dependency and a router are additive |
| Login lockout after N failures | Lives entirely inside `auth_service.authenticate()`; two columns and a check |
| Email verification | One column + one setting + a mail provider. No provider is wired up, and adding one is not this feature's job |
| `citext` extension | Emails are lowercased in `normalise_email()` before store and lookup |
| 30-day usage chart page | `/auth/me` returns today's figures; a series endpoint plus the existing `ChartCard` is an afternoon |

---

## 13. Status: implemented

All six blocks are built and verified.

- **Backend:** 564 tests pass (53 new). The route-protection test was mutation-checked: removing
  a router-level guard from `/datasources` made it fail and name `POST /datasources/test`.
- **Live check:** register, login, refresh via the httpOnly cookie, a real analysis attributed to
  the account (3 calls, ~6.7k tokens), and a second account getting 404 plus an empty history.
- **Frontend:** `/login`, `/register`, `/account` (usage meters, change password), a route guard,
  and sign-out. Builds and lints clean. Not yet clicked through in a browser.
- **Found by the tests:** the pwdlib API has `verify_and_update`, not `check_needs_rehash`; login
  would have crashed for every correct password.
- **Migration applied.** The 1,457 pre-existing analyses belong to `legacy@insightflow.local`
  (cannot log in); evaluation runs and CLI tools are charged to `system@insightflow.local`.

### Known limits

- Data source names are unique across all accounts, so a name clash reveals that someone has a
  source by that name. Per-owner uniqueness is a constraint change plus a migration.
- No per-IP limit on `/auth/login` yet; worth adding before any public deployment.
- Quota is checked before a run, so a burst can overshoot a daily ceiling by up to two analyses.

---

## 14. Risks

| Risk | Mitigation |
|---|---|
| Existing route tests break en masse | The signed-in fixture lands in Block C, before the routers are locked |
| Refresh cookie blocked cross-site once deployed | Decide the domain layout before deploying; `AUTH_COOKIE_SAMESITE=none` + `SECURE=true` works, same-site is better |
| Five polling requests each try to refresh at once and four lose the rotation race | The frontend shares a single in-flight refresh promise (Block E) |
| Open registration drains the free LLM quota | `AUTH_ALLOW_REGISTRATION=false` for a public demo, or leave it on and rely on the §7 per-account ceilings |
| Secret key committed by accident | `.env` is already git-ignored; startup rejects an empty or placeholder key |
