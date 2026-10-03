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

**3. Install and run**

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload
```

**4. Verify**

```bash
curl localhost:8000/health         # {"status":"ok"}
curl localhost:8000/health/ready   # {"status":"ready","checks":{"database":"ok"}}
```

API docs: `localhost:8000/docs`

---

## Development

```bash
cd backend
uv run pytest          # tests
uv run ruff check .    # lint
```

Stop the databases with `docker compose down`. Data survives; add `-v` to wipe it.

---

## Stack

Python 3.12 · FastAPI · LangGraph · PostgreSQL + pgvector · sqlglot · DuckDB · Next.js
