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

Groq leads the chain on purpose: its free tier allows 1,000 requests a day against
OpenRouter's 50, and those 50 are reserved for embeddings.

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
uv run python -m app.scripts.eval_retrieval         # retrieval quality, 21 questions
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
