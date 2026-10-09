# InsightFlow

**Ask a business question in plain English. Get a dashboard, and the reasoning behind it.**

InsightFlow is an agentic analytics platform. You connect a PostgreSQL database or upload a
CSV, ask something like *"Why did revenue fall in June?"*, and it writes safe SQL, investigates
the cause on its own, and returns KPIs, charts and a written explanation — with every query it
ran visible underneath.

It is not a chatbot. The output is a dashboard, and every number in it traces back to SQL you
can read.

```
Q: "Why did revenue fall in June 2026 compared with May?"

  Revenue fell 11.4% (-₹2,474,435)
    → South region fell 35.2% and accounts for 85% of the drop
    → within South, Electronics fell 57.2% — 80% of the total decline
    → within Electronics, no single channel dominates

  4 queries run · 3 model calls · dashboard with 3 KPIs, 2 charts, 4 insights
```

---

## What it can do

**Ask anything, in your own words**
Questions are matched against your schema *and* your business definitions, so "turnover",
"GMV" and "sales" all reach the same metric — and the SQL applies the rules that definition
states, like excluding cancelled orders.

**Investigates on its own**
For a "why" question it does not stop at the headline number. It breaks the change down by
region, then by category, then deeper — choosing each next step from computed contribution
shares, up to a hard depth and budget limit. The whole path is shown.

**Writes SQL that cannot hurt you**
Every generated query is parsed into a syntax tree and checked before it runs: single
statement only, no writes anywhere in the tree, table allow-list, forced row limit, no
functions that reach outside the database. Behind that sits a read-only database role and a
statement timeout.

**Shows its work**
Every run records the SQL it tried (including rejected attempts), what retrieval gave the
model, how many model calls it cost, and why it stopped. A wrong answer can be traced to its
cause instead of guessed at.

**Knows when it cannot answer**
If the data cannot support the question, it says so and explains what is missing, rather than
inventing a plausible join.

**Multi-user, with real limits**
Email sign-in with JWT and rotating refresh tokens. Each account sees only its own analyses
and data sources, and has a daily ceiling on analyses, model calls and tokens.

**Measured, not vibe-checked**
A 33-case evaluation suite with computed ground truth scores correctness, SQL structure,
investigation path, correct refusals and whether every number in the summary actually appears
in the data. No model grades another model.

**Costs nothing to run**
Works entirely on free LLM tiers (Groq, Gemini, OpenRouter) with an automatic fallback chain
when a provider is rate-limited or returns unusable output.

---

## How it works

```
Question
   │
   ├─ Retrieval ........ schema + business definitions + verified example queries (pgvector)
   ├─ Query Agent ...... writes SQL                                           [LLM]
   ├─ SQL Guard ........ parses, validates, rewrites, or rejects         [deterministic]
   ├─ Execute .......... read-only role, row cap, statement timeout
   ├─ Profiler ......... computes the arithmetic: deltas, shares, dominance [deterministic]
   ├─ Analysis Agent ... interprets the facts, proposes a drill-down            [LLM]
   │      └── loop back, bounded by depth / call budget / reconciliation checks
   └─ Visualisation .... dashboard spec, validated against real columns         [LLM]
                            └── falls back to a rule-built dashboard if invalid
```

The division is the point: **models choose, code computes and enforces.** Percentages,
contribution shares and "which segment dominates" are calculated in Python, not by the model,
because models are unreliable at arithmetic. Depth limits, budgets and SQL safety are
enforced in code, because a prompt is a request and not a guarantee.

---

## Tech stack

| | |
|---|---|
| **Backend** | Python 3.12, FastAPI, LangGraph, SQLAlchemy 2 (async), Alembic, Pydantic v2 |
| **Data** | PostgreSQL 16, pgvector (HNSW), DuckDB (CSV), sqlglot |
| **AI** | Groq, Gemini, OpenRouter — structured output with fallback chain; RAG with 1024-dim embeddings |
| **Frontend** | Next.js 16, React 19, TypeScript, Tailwind v4, TanStack Query, Recharts |
| **Auth** | JWT (PyJWT), Argon2id, rotating refresh tokens with reuse detection |
| **Infra** | Docker Compose, GitHub Actions CI, Caddy (HTTPS) |

---

## Quick start

**Requirements:** Docker, and a free [Gemini API key](https://aistudio.google.com/apikey)
(for embeddings) plus a free [Groq key](https://console.groq.com/keys).

```bash
git clone https://github.com/Pranay36/Data-analytics-agent-.git
cd Data-analytics-agent-
cp .env.example .env
```

Fill in three values in `.env`:

```bash
# Signs login tokens
openssl rand -hex 32
# Encrypts saved database passwords
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```ini
AUTH_SECRET_KEY=<the first one>
DATASOURCE_ENCRYPTION_KEY=<the second one>
GEMINI_API_KEY=<your key>
GROQ_API_KEY=<your key>
```

Then start everything:

```bash
docker compose --profile app up --build
```

Open **http://localhost:3000**, create an account, and ask a question. The sample
ShopSphere dataset — 25,000 orders with deliberately planted patterns — is loaded and indexed
automatically on first start.

<details>
<summary><b>Running from source instead</b> (for development)</summary>

```bash
docker compose up -d                 # just the two databases

cd backend
uv sync
uv run alembic upgrade head
uv run python -m app.scripts.generate_demo_data
uv run python -m app.scripts.load_postgres
uv run python -m app.scripts.bootstrap
uv run python -m app.scripts.index_knowledge
uv run uvicorn app.main:app --reload

# in another terminal
cd frontend && npm install && npm run dev
```

Ask from the command line without the UI:

```bash
uv run python -m app.scripts.ask "Why did revenue fall in June 2026 compared with May?"
```

</details>

---

## The sample dataset

`ShopSphere` is generated from a fixed seed, so it is identical every time, and its true
answers are computed from the data rather than written by hand — which is what makes the
evaluation objective. Three patterns are planted in it:

| Pattern | What it tests |
|---|---|
| June revenue falls 11.4%, concentrated in South → Electronics | whether the investigation finds a cause two levels down |
| Home & Kitchen refund rate jumps from 3% to 15.7% | whether a rate is computed against the right denominator |
| 7.6% of orders never complete (₹20M difference) | whether the business rule "revenue excludes failed orders" is applied |

It also includes deliberate distractors — a deprecated `orders_legacy` archive with identical
columns, and tables whose names suggest relevance they do not have.

---

## Testing and evaluation

```bash
cd backend
uv run pytest                                  # 567 tests, no API keys needed
uv run python -m app.evaluation                # full suite (uses live model calls)
uv run python -m app.evaluation --retrieval-only   # retrieval accuracy, free and fast
```

The evaluation suite runs each case's gold SQL against the live database and compares
**values**, not column names — so two differently-written queries that produce the same answer
both pass. Beyond the final number it checks the SQL's syntax tree (was revenue filtered to
successful orders? was the decoy table avoided?), the investigation path, correct refusals,
that a destructive request changes nothing, and that every number in the written summary
appears in the data.

Last full-suite run: **25 of 28 cases (89%)**. The suite is small and was written knowing the
system's weak spots, so treat that as a development signal, not a benchmark claim.

---

## Configuration

Models are configuration, not code — free-tier availability changes weekly.
[`backend/models.yaml`](backend/models.yaml) holds the providers, the fallback chain, which
structured-output modes each model supports, and the embedding model with its vector width.
Environment variables override it, so a deployment needs no file edit:

```bash
LLM_FALLBACK_CHAIN=groq:openai/gpt-oss-120b,openrouter:qwen/qwen3.8-27b:free
LLM_MODEL_ANALYSIS=gemini:gemini-flash-lite-latest   # per-agent override
QUOTA_TOKENS_PER_DAY=1000000                         # per-account daily ceiling
DEMO_MODE=true                                       # public demo: no custom DB connections
```

---

## Project status

Working end to end locally and in Docker: ask a question, get a dashboard, with accounts and
usage limits. Deployment files (EC2 + Vercel) are written but **not yet deployed**.

| Next | |
|---|---|
| Deploy | EC2 backend behind Caddy, frontend on Vercel |
| ClickHouse connector | the connector interface and SQL-guard dialect policy are already in place for it |
| Hybrid retrieval | add a keyword arm alongside the vector one |
| Investigation quality | same-length period baselines, parallel dimension decomposition |

## Documentation

| | |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it all works, in plain language with diagrams |
| [docs/CHALLENGES.md](docs/CHALLENGES.md) | Real problems hit while building, and how they were solved |
| [docs/INTERVIEW_PREP.md](docs/INTERVIEW_PREP.md) | Questions and answers about the design |

Design documents: [`plans/PROJECT_PLAN.md`](plans/PROJECT_PLAN.md) ·
[`plans/AUTH_AND_USAGE.md`](plans/AUTH_AND_USAGE.md)
