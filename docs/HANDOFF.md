# Session Handoff

Everything a new session (or a future you) needs to continue this project without re-deriving
it. Last updated: **9 October 2026**, at commit `32bd8fa`.

> Start here, then read [`../CLAUDE.md`](../CLAUDE.md) for environment quirks and working
> agreements, and [`ARCHITECTURE.md`](ARCHITECTURE.md) for how the system works.

---

## 1. Status at a glance

| | |
|---|---|
| **Repo** | `git@github.com:Pranay36/Data-analytics-agent-.git` (branch `main`) |
| **Working tree** | clean, everything pushed |
| **Tests** | 567 passing, no API keys required |
| **Lint** | clean (`ruff check app tests`) |
| **Deployed** | **No.** Files are written and tested locally; nothing is live |
| **Last eval score** | 25/28 (89%) — not re-run since several fixes |

**The product works end to end.** Ask a question in the browser, get a dashboard with the
investigation behind it, with accounts and usage limits. It runs from source and in Docker.

---

## 2. What is built, and what is actually verified

Be careful with this distinction — it is the most useful part of this document.

### Verified working (I ran it and checked the output)

| Thing | Evidence |
|---|---|
| Full Docker stack from scratch | Seed generated 24,970 orders, loaded them, registered both sources, indexed 58 chunks |
| Real analysis in containers | "Total revenue in June 2026" returned ₹19,191,285.85 — matches a direct DB query to the paisa |
| Auth end to end | register → login → refresh via httpOnly cookie → authenticated call, live over HTTP |
| Multi-tenant isolation | Second account got 404 for the first account's analysis and an empty history |
| Usage attribution | One analysis recorded 3 model calls / 6,546 input + 554 output tokens against that account |
| CORS with credentials | Correct headers for `localhost:3000`; other origins refused |
| Vercel proxy design | Built with `BACKEND_URL`, served, and completed a full login flow through the rewrite |
| Production compose + Caddy | Booted locally: HTTPS, `/docs` returns 404, protected routes 401, cookie carries `Secure` |
| Seed job is repeatable | Re-ran it; data load skipped, user and analysis survived |
| Test suite catches real breakage | Mutation-tested: removed a router guard → test failed naming the exposed route |
| 567 tests with no model keys | Ran with `GEMINI_API_KEY= GROQ_API_KEY= OPENROUTER_API_KEY=` |
| Migration on populated DB | Applied to a database with 1,457 existing analyses |

### Written but NOT verified

| Thing | Why it matters |
|---|---|
| **GitHub Actions CI** | Never run. The first real run is the next push. Steps were tested locally but the workflow itself is unproven |
| **Deployment** | No EC2 instance exists. `deploy/` files are untested against a real server |
| **The deploy workflow** | Build/push/SSH/health-check/rollback logic has never executed |
| **Frontend in a browser** | I tested the API and the build. Nobody has clicked through login, the dashboard or the charts visually |
| **Visualization Agent output quality** | It runs and produces valid specs, but nobody has judged whether the charts are *good* |

---

## 3. Decisions made, and why

Knowing *why* matters more than knowing *what* — it stops a future session from "fixing"
something deliberate.

### Architecture

| Decision | Why | Rejected |
|---|---|---|
| Two separate databases (ours vs customer's) | Never copy customer data; read-only role is a real safety boundary | One database with schemas |
| LangGraph state machine | Flow has branches and a cycle; routing is testable without model calls | A plain loop with if-statements |
| Code computes, model interprets | Models are unreliable at arithmetic and named wrong segments | Letting the model read tables of numbers |
| Model *proposes*, code *decides* on drill-down | A prompt cannot enforce a limit | Trusting "stop when you have enough" |
| 202 + polling, background task | 30-second requests are fragile; a run is a DB row so refresh is safe | Holding the request open, or WebSockets |
| In-process `asyncio.create_task`, not a queue | Jobs are short and need not survive restart; `reconcile_orphans` covers the gap | Celery/RQ — a second service for no benefit yet |

### Auth (chosen from three options; user picked "lean + refresh tokens")

| Decision | Why |
|---|---|
| Access token in memory + refresh in httpOnly cookie | XSS can read neither. `localStorage` would make one XSS a permanent takeover |
| Refresh rotation with reuse detection | A replayed token means a leak; cheap now, impossible to retrofit credibly |
| Argon2id via `pwdlib` | OWASP first choice; bcrypt silently truncates at 72 bytes; `passlib` unmaintained since 2020 |
| `token_version` integer on users | The only way to revoke a stateless access token before it expires |
| **No** `user_usage_daily` rollup table | Premature at this scale; `usage_service.today()` is the seam if it is ever needed |
| **No** `user_quotas` table | Settings defaults are enough; `over_quota()` can gain a lookup later |
| `is_admin` column but no admin routes | One boolean now saves a migration later |

### Deployment (user picked "option B — Vercel proxy")

| Decision | Why |
|---|---|
| Vercel rewrites `/api/v1/*` to EC2 | The browser only sees one domain, so the login cookie is first-party. A cross-site cookie would be blocked by Safari/Chrome and users would be logged out every refresh |
| Caddy for HTTPS | Gets and renews Let's Encrypt certificates automatically |
| `sslip.io` hostname if no domain | `13-233-10-20.sslip.io` resolves to that IP — free, and Caddy can get a real certificate for it |
| Single-stage Dockerfile (26 lines) | User explicitly asked for readable over clever. Costs ~30 MB; worth it |
| Caddy exposes only `/api/*` and `/health` | `/docs` and `/openapi.json` are not public |

---

## 4. Known bugs and limitations

### Real bugs, not yet fixed

**1. No warning when a time window exceeds the data's coverage.**
"What was our revenue last year?" returns ₹127,081,035 for calendar 2025. The SQL is correct,
but the dataset starts 2025-07-01, so that is six months labelled as a year. The true 12-month
figure is ₹251,928,208.

The system *did* say "(calendar year 2025)" in its summary, so it disclosed the interpretation —
it just never warned that half the window is empty.

*Fix:* the catalog knows each table's date range and `business_context.as_of_date` is
2026-06-30. Add a coverage check and attach a caveat. A caveat mechanism already exists
(`period_length_warning`) — this is its sibling. **Also add it as evaluation case 34**, because
the existing 33 all use windows inside the data range, which is why they missed it.

**2. `why_refunds_rose` evaluation case still fails.**
The system sees refunds rose but picks an uneven baseline and does not reliably drill into the
right dimension. Two planned fixes, in order:
   - compare against the preceding period of the **same length**
   - decompose by **every** dimension in parallel and let the profiler rank which explains the
     most change, instead of making the model guess which to try

**3. Loose wording in summaries.**
One run said Electronics "contributed 80% of the overall revenue decrease". By the actual
arithmetic it is ~69% of the total decline and ~82% of the South decline. The numbers are right;
the sentence is imprecise.

**4. `LLM_CACHE_MODE=record` is a no-op.**
The setting accepts `off | read_write | record`, but the client only checks `!= "off"`, so
`record` behaves like `read_write`. Either implement write-only mode or remove the option.

### Accepted limitations

| Limitation | Context |
|---|---|
| Retrieval is vector-only | `orders_legacy` decoy appears ~50% of the time but has never been used in generated SQL. `search_tsv` column already exists for a keyword arm |
| No per-IP login rate limit | Per-user daily quotas exist, but nothing stops password guessing. **Needed before any public deploy** |
| Data source names are globally unique | A name clash reveals that another account has a source by that name |
| Quota checked before a run, not during | Worst-case overshoot is 2 runs × 12 calls = 24 calls |
| Single instance only | Concurrency semaphore, model cooldowns and rate-limit tracker are all in-process. Needs Redis to scale out |
| No ClickHouse connector | The SQL guard has a ClickHouse dialect policy, but no connector exists. Do not claim it works |

---

## 5. Open work, in suggested order

### A. Before anything public

1. **Per-IP rate limit on `/auth/login`** — roughly 1 hour including tests.
2. **Global daily cap** across all users, so many accounts cannot drain the free LLM quota.
3. **Click through the frontend in a browser** — login, dashboard, charts. Never done.

### B. Deployment (user's stated plan: EC2 + Vercel, option B)

Everything is written. What is needed:

1. An EC2 instance — Ubuntu 24.04, **t3.small or larger** (1 GB RAM is not enough for the API
   plus two Postgres containers), with an Elastic IP.
2. Security group: 80 and 443 open, 22 from your IP only, **never 5432/5433**.
3. Run `deploy/setup-ec2.sh` (installs Docker, adds 2 GB swap).
4. Create `/opt/insightflow/deploy/.env` from `deploy/.env.prod.example`.
5. GitHub repo secrets: `EC2_HOST`, `EC2_USER`, `EC2_SSH_KEY`, `API_HOST`.
6. Vercel: import repo, Root Directory = `frontend`, env var `BACKEND_URL=https://<API_HOST>`.
7. Put the Vercel URL into `WEB_ORIGIN` on the server and redeploy.

Full steps: [`../deploy/README.md`](../deploy/README.md).

### C. Quality work (deferred by agreement during the MVP push)

1. **Re-run the full evaluation with cache off** — the 89% predates several fixes, so the real
   "after" number is unknown.
2. **The RCA improvement** (parallel dimension decomposition). The user was shown three scope
   options and never picked one.
3. **Hybrid retrieval** — keyword arm + fusion.
4. **Business definition additions:** a note that line-item totals are pre-discount while order
   totals are post-discount (a 1.4% gap that could confuse), and an explicit "campaigns cannot
   be attributed to orders" definition.

### D. Nice to have

- ClickHouse connector (~2–3h; proves the connector seam)
- Dashboard refresh without re-running the LLM (`analysis_queries` already stores every
  statement, so no schema change needed)
- README screenshots once the UI has been looked at

---

## 6. Things that are easy to get wrong here

Beyond the environment quirks in `CLAUDE.md`:

**Testing against a fresh database finds different bugs.** The demo database's first-boot SQL
granted access to a role that did not exist yet. It had been broken since the first commit and
nobody noticed, because every test ran against a database where the role already existed.

**The evaluation suite is optimistic.** 33 cases, written by the person who built the system,
knowing its weak spots, on one dataset and one engine. It is excellent for regression and weak
as a benchmark claim. Describe it that way.

**Free models fail constantly.** In one logged run, OpenRouter's nemotron returned 13 invalid
replies out of 17 calls. Before assuming a code bug, check `llm_calls.error_type`.

**Groq rate-limits aggressively.** ~8,000 tokens/min. A multi-step investigation hits it easily,
and the error says "rate limited, resets in 18m". That is the free tier, not a bug.

**The user's `.env` holds real keys.** During this session a `grep` accidentally printed the
Gemini key to the transcript. It is only on the local machine, but **if that transcript is ever
shared, rotate the key.**

---

## 7. How the project was built (for context)

| Phase | What happened |
|---|---|
| Planning | `plans/PROJECT_PLAN.md` (architecture) and `plans/SPRINT_2_DAY.md` (what to cut). Governing rule: *cut implementations, never seams* |
| Blocks 1–8 | Scaffold, demo data, connectors, SQL guard, app DB + catalog, LLM layer, RAG, Query Agent + first graph |
| Evaluation | Built next, at the user's request ("evaluation first"). First full run: 25/28. It immediately found 4 real bugs |
| Blocks 9–12 | Profiler + Analysis Agent + drill-down, Visualization Agent, analysis API, Next.js frontend |
| Auth | Planned (three scope options), user chose "lean + refresh tokens", built in 6 blocks |
| Infra | Dockerfiles, compose `app` profile, seed job, CI workflow |
| Deploy prep | Vercel proxy, EC2 compose + Caddy, GitHub Actions deploy pipeline, demo-mode guard |
| Docs | `docs/ARCHITECTURE.md`, `docs/CHALLENGES.md`, `docs/INTERVIEW_PREP.md` |

The 2-day sprint (blocks 1–12) is complete. Everything after it was additive, as the plan
intended.

---

## 8. Verified facts (so you need not re-derive them)

| Fact | Value |
|---|---|
| Tests | 567 (21 unit files, 6 integration, 10 graph) |
| Code size | 15,686 lines app · 6,119 lines tests |
| SQL guard | 19 forbidden AST node types · 70 denied functions (24 postgres / 23 duckdb / 31 clickhouse) |
| Evaluation | 33 cases across 10 categories (28 live + 5 retrieval-only) |
| Database | 12 tables, 5 migrations, 20 API endpoints |
| `llm_calls` | 19 columns of per-attempt telemetry |
| Graph | 10 nodes, 3 LLM agents |
| Budgets | depth 3 · 12 calls · 80,000 tokens per analysis · 2 SQL repairs |
| Quotas | 20 analyses / 150 calls / 1,000,000 tokens per user per day |
| Auth | access 30 min · refresh 14 days · 10s reuse grace |
| Cooldowns | gone 15 min · busy 60s (or provider's `retry_after`) · invalid 10 min after 3 strikes |
| Dominance | ≥50% of change, or ≥35% with a 1.5× lead over the runner-up |
| Demo data | 24,970 orders, Jul 2025 – Jun 2026, as-of date 2026-06-30 |
| Correct revenue | ₹251,928,207.60 (SUCCESS only) vs ₹272,169,980 if status is ignored |
| June 2026 revenue | ₹19,191,285.85 |
| May 2026 revenue | ₹21,665,720.50 |

---

## 9. If you are an assistant picking this up

1. Read [`../CLAUDE.md`](../CLAUDE.md) first — the environment quirks will cost you time
   otherwise, especially the space in the repo path and `uv` not being on PATH.
2. Do not add attribution lines to commits. The user has asked twice.
3. Check `git log --oneline | head -5` and `git status` before assuming this document is current.
4. Before claiming anything works, run it. Section 2 of this document separates what was
   verified from what was only written — keep that distinction going.
5. When the user asks for something substantial, write a plan to `plans/` and get agreement
   before building. That is how auth and deployment were handled, and it worked well.
