# InsightFlow

Ask a business question in plain English, get a dashboard back.

InsightFlow connects to PostgreSQL, ClickHouse or an uploaded CSV, understands a question like
*"Why did revenue fall in June?"*, writes safe SQL, investigates the cause on its own, and
returns KPIs, charts and a written explanation.

> 🚧 **Early development.** The backend foundation is in place; the agent pipeline is being
> built. Full design: [`plans/PROJECT_PLAN.md`](plans/PROJECT_PLAN.md).

---

## Setup

**Requirements:** Docker and [uv](https://docs.astral.sh/uv/).

**1. Clone and configure**

```bash
git clone git@github.com:Pranay36/Data-analytics-agent-.git
cd Data-analytics-agent-
cp .env.example .env
```

Generate an encryption key and paste it after `DATASOURCE_ENCRYPTION_KEY=` in `.env`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**2. Start the databases**

```bash
docker compose up -d
```

This starts two Postgres servers — `appdb` on port 5432 (our own metadata, with pgvector) and
`demo-analytics` on 5433 (the sample business data we query).

**3. Install dependencies**

```bash
cd backend
uv sync
```

**4. Generate and load the demo dataset**

```bash
uv run python -m app.scripts.generate_demo_data   # synthetic ShopSphere data
uv run python -m app.scripts.load_postgres        # load it, create a read-only role
```

This creates ~25k orders across 12 months with deliberately planted patterns — a
June revenue drop concentrated in one region and category, a refund spike, and a
share of orders that never complete. The true answers are written to
`demo_data/generated/ground_truth.json`, which is what makes evaluation objective.

**5. Create the schema and register the demo sources**

```bash
uv run alembic upgrade head                 # create our own tables
uv run python -m app.scripts.bootstrap      # register + sync the demo data sources
```

This needs `DATASOURCE_ENCRYPTION_KEY` set in `.env` (see step 1). Saved database passwords
are encrypted with it, so keep it — a changed key makes stored credentials unreadable.

**6. Run**

```bash
uv run uvicorn app.main:app --reload
```

**7. Verify**

```bash
curl localhost:8000/health         # {"status":"ok"}
curl localhost:8000/health/ready   # {"status":"ready","checks":{"database":"ok"}}
```

API docs: `localhost:8000/docs`

```bash
curl localhost:8000/api/v1/datasources
```

---

## Asking a question

```bash
uv run python -m app.scripts.index_knowledge     # once: embed schema and definitions
uv run python -m app.scripts.ask "What was total revenue in June 2026?"
```

Prints what retrieval supplied, every query the model attempted (including ones the
SQL guard rejected), the analysis at each level, and the result.

For a "why" question it investigates on its own:

```bash
uv run python -m app.scripts.ask "Why did revenue fall in June 2026 compared with May?"
```

```
total        revenue fell 11.4%
  → region   South fell 35.2% and accounts for 85% of the drop
  → category within South: Electronics fell 57.2%, 80% of South's change
  → channel  within Electronics: no single channel dominates
```

Depth, model-call budget and token budget are all capped in code. The model proposes
each next step and deterministic code decides whether to run it.

## Evaluation

A suite of questions with computed ground truth measures how often the system is right,
rather than relying on spot checks:

```bash
uv run python -m app.evaluation                      # full run: live model calls
uv run python -m app.evaluation --retrieval-only     # retrieval alone: free, seconds
uv run python -m app.evaluation --category business_rule
```

Each case has a gold SQL query that is run against the live database, so "correct" is a
computation, not an opinion. Results are compared on values, not column names, so
`SUM(x) AS revenue` and `SUM(x) AS total` are the same answer. Beyond the final number it
checks the SQL's syntax tree (was revenue filtered to successful orders? was the decoy
archive table avoided?), the investigation path, correct refusals, that a destructive
request changes nothing, and that every number in the written summary appears in the
data. No model grades another model: every check is code.

A report is written to `reports/` and each run is stored with the configuration that
produced it.

## API

Starting an analysis returns immediately; the run continues in the background and writes
its progress to the database, so a client polls and a page refresh loses nothing.

```bash
curl -X POST localhost:8000/api/v1/analyses -H 'content-type: application/json' \
  -d '{"datasource_id": "<id>", "question": "Why did revenue fall in June 2026?"}'
# 202 {"id": "...", "status": "queued"}

curl localhost:8000/api/v1/analyses/<id>
# {"status": "running", "stage": "analyzing", ...}  ->  {"status": "completed", "dashboard": {...}}
```

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/analyses` | start a run (202) |
| `GET /api/v1/analyses/{id}` | progress, then the result, steps taken and dashboard |
| `GET /api/v1/analyses` | history, newest first |
| `POST /api/v1/datasources/csv` | create a source from uploaded CSV files |
| `GET /api/v1/datasources/{id}/examples` | suggested questions |

Interactive docs: `localhost:8000/docs`.

## Choosing models

Models and providers live in [`backend/models.yaml`](backend/models.yaml) — which
providers exist, the LLM fallback chain, which structured-output modes each model
supports, and the embedding model with its vector width. Edit that file to switch
models; no code changes.

```yaml
llm:
  chain:
    - groq:openai/gpt-oss-120b
    - openrouter:nvidia/nemotron-3-super-120b-a12b:free

embeddings:
  default: openrouter:liquid/lfm-2.5-embedding-350m:free
```

Work is split by how scarce each free tier is. OpenRouter allows 50 requests a day
across chat and embeddings combined — one re-index plus one evaluation run exhausts
it — so chat runs on Groq (1,000/day), embeddings on Gemini (a separate allowance),
and OpenRouter stays as a fallback.

When a provider does refuse a request, that is tracked and reported by
`GET /health/ready`, including when the allowance returns:

```json
{"rate_limits": {"openrouter": "rate limited (3x), resets in 4h"}}
```

Environment variables override the file, so a deployment needs no edit:

```bash
LLM_FALLBACK_CHAIN=groq:openai/gpt-oss-120b,openrouter:qwen/qwen3.8-27b:free
EMBEDDING_MODEL=gemini:text-embedding-004
LLM_MODEL_QUERY=groq:openai/gpt-oss-120b     # per-agent override
```

Changing the embedding model to one of a different width needs a migration and a
re-index, since vectors from different models are not comparable. The app refuses
to start on a mismatch rather than failing later mid-index.

Free models are withdrawn without notice, so check what currently works:

```bash
uv run python -m app.scripts.check_models           # one real question, end to end
uv run python -m app.scripts.check_models --probe   # every model x strategy
uv run python -m app.evaluation --retrieval-only    # retrieval quality, no model calls
```

LLM responses and embeddings are both cached on disk, so repeating a question costs
no quota.

## Development

```bash
cd backend
uv run pytest          # tests
uv run ruff check .    # lint
```

Stop the databases with `docker compose down`. Data survives; add `-v` to wipe it.

---

## Safety

Generated SQL is parsed into a syntax tree and inspected before it reaches a database.
Text-matching is not enough — all three of these read as harmless to a prefix check:

```sql
SELECT 1; DROP TABLE orders                              -- starts with SELECT
WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x   -- starts with WITH
SELECT * FROM (SELECT id FROM orders LIMIT 5) t          -- "has a LIMIT"
```

The guard rejects the first as two statements, the second for containing a `DELETE`
anywhere in the tree, and caps the third — whose inner limit leaves the outer query
unbounded. It also blocks functions that escape the database (`pg_sleep`, `read_csv`,
ClickHouse's `url` and `remote`), enforces a table allowlist, and rewrites the outer
`LIMIT`.

Behind it: a read-only database role, a server-side statement timeout, and a row cap.

## Stack

Python 3.12 · FastAPI · LangGraph · PostgreSQL + pgvector · sqlglot · DuckDB · Next.js
