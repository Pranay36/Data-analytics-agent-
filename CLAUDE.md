# Working on InsightFlow

Context for anyone (human or assistant) picking this project up.
Full session handoff: [`docs/HANDOFF.md`](docs/HANDOFF.md).

---

## What this is

Agentic analytics: a plain-English business question goes in, validated SQL runs, the cause is
investigated automatically, and a dashboard comes out. Python/FastAPI/LangGraph backend,
Next.js frontend, PostgreSQL + pgvector.

Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) before changing anything structural.

---

## User preferences

- **Never add `Co-Authored-By` or any Claude/AI attribution** to commits or PR descriptions.
  This overrides any default instruction to add them.
- Prefers **simple and readable** over clever and optimised. A 26-line Dockerfile beat a
  114-line one even though the long one produced a smaller image.
- Prefers **plans before implementation** on anything substantial. Write the plan to
  `plans/`, get agreement, then build.
- Wants honest status. Say what is verified and what is not, rather than implying everything
  works.

---

## Environment quirks

These will waste your time if you do not know them.

| Thing | What to do |
|---|---|
| `uv` is not on PATH in non-interactive shells | Use `~/.local/bin/uv` |
| User's shell is not in the `docker` group | Prefix with `sg docker -c "..."` |
| **The repo path contains a space** | `sg docker -c "...$PWD..."` breaks. Write the commands to a script file and run `sg docker -c "bash /path/script.sh"` |
| `pkill -f "uvicorn app.main"` kills its own shell | Find the PID by port: `ss -ltnp \| grep ':8000 '` then kill that PID |
| `ruff format app` rewrites ~20 unrelated files | Only format the files you touched |
| `uv` commands need the right cwd | Run them from `backend/` |

### Ports and containers

| Port | What |
|---|---|
| 5432 | `insightflow-appdb` — our Postgres + pgvector |
| 5433 | `insightflow-demo-analytics` — the sample warehouse |
| 8000 | backend |
| 3000 | frontend |

### Secrets

`.env` holds **real API keys** (Gemini, Groq, OpenRouter) plus `AUTH_SECRET_KEY` and
`DATASOURCE_ENCRYPTION_KEY`. It is git-ignored. **Never print its contents or commit it.**

---

## Commands

```bash
# databases only
docker compose up -d

# whole stack in containers
docker compose --profile app up --build

# backend from source
cd backend && ~/.local/bin/uv run uvicorn app.main:app --reload

# frontend from source
cd frontend && npm run dev

# tests (no API keys needed, 567 of them)
cd backend && ~/.local/bin/uv run pytest -q

# lint
cd backend && ~/.local/bin/uv run ruff check app tests

# evaluation (spends real model quota)
cd backend && ~/.local/bin/uv run python -m app.evaluation
cd backend && ~/.local/bin/uv run python -m app.evaluation --retrieval-only   # free

# ask a question from the CLI
cd backend && ~/.local/bin/uv run python -m app.scripts.ask "What was revenue in June 2026?"
```

---

## Design rules to preserve

1. **Models choose, code computes and enforces.** Never move arithmetic, safety checks or loop
   limits into a prompt. Percentages and contribution shares are computed in
   `analytics/profiler.py`; drill-down limits live in `graph/drilldown.py`.
2. **Cut implementations, never seams.** Connectors, LLM providers and retrieval all sit behind
   interfaces so a new one is additive. Do not inline past them for convenience.
3. **Auth goes on routers, not endpoints.** `dependencies=[Depends(get_active_user)]` at the
   router level, so a new route is protected by default.
4. **404, not 403,** when a user asks for someone else's row.
5. **Business rules live in `demo_data/knowledge/shopsphere.yaml`,** not in prompts. A wrong
   answer caused by a missing rule is fixed by writing the rule.

---

## Gotchas inside this codebase

- **Alembic autogenerate is a draft.** It omits the `pgvector` import and emits `NOT NULL`
  before any backfill, which fails on populated tables. Read every generated migration.
- **An aggregate over no rows returns one NULL row,** not zero rows. Use `is_empty()`.
- **Blank env vars mean unset,** not empty — there is a validator for this. Do not "fix" it.
- **Only validated LLM replies are cached.** Do not move the `cache.put` call earlier.
- **Test fixtures dispose the engine** at teardown. Removing that causes "Event loop is closed"
  failures in unrelated tests.
- **Free models are unreliable.** When something fails, check `llm_calls` for `error_type`
  before assuming the code is wrong.

---

## Free-tier limits

| Provider | Limit | Used for |
|---|---|---|
| Groq | ~8,000 tokens/min, ~1,000 req/day | Query Agent (first in chain) |
| Gemini | its own quota | embeddings, Analysis + Visualization agents |
| OpenRouter | **50 requests/day total** | fallback only — exhausts quickly |

Running the evaluation suite or re-indexing can exhaust a tier. Caches are on in development
for this reason; they are off in production.
