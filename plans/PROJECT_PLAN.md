# InsightFlow — Agentic Data Analytics Platform

## Project Plan & Architecture Document (v1.0 — pre-implementation)

> **Status:** Planning only. No production code has been written yet.
> **Reference studied:** `agent360-main` (read-only; nothing copied or modified).
> **Guiding rule:** *small, working, explainable* beats *large, incomplete, complicated*.

---

## Table of Contents

1. Executive Summary
2. What We Are Building
3. User Experience / Demo Flow
4. Lessons From Agent360
5. Final Technology Stack
6. System Architecture
7. Frontend Architecture
8. Backend Architecture
9. Connector Architecture
10. Application Database Design
11. RAG Architecture
12. LangGraph / Multi-Agent Design
13. Text-to-SQL Pipeline
14. SQL Safety
15. Automatic Drill-Down
16. Dashboard Generation
17. LLM Provider Architecture
18. Evaluation
19. Observability
20. Error Handling
21. Testing
22. Deployment
23. CI/CD
24. Security
25. Project Folder Structure
26. P0/P1/P2/P3 Feature List
27. Step-by-Step Implementation Plan
28. Demo Scenario
29. Interview Talking Points
30. Resume Positioning
31. Future Improvements
32. Final MVP Definition
- Appendix A — Architecture Decision Records (ADRs)
- Appendix B — README Plan
- Appendix C — Glossary

**Priority labels used throughout**

| Label | Meaning |
|---|---|
| **P0** | Must have. The MVP is not done without it. |
| **P1** | Strong resume/interview value. Build after P0 works end-to-end. |
| **P2** | Nice if time remains. |
| **P3** | Future production extension — document it, do not build it. |

---

## 1. Executive Summary

**InsightFlow** is a web application where a user connects a data source (CSV file, PostgreSQL, or ClickHouse), asks a business question in plain English, and receives an **analytics dashboard** — KPI cards, charts, a data table, and written insights — instead of a chat reply.

What makes it more than a "Text-to-SQL demo":

1. **Grounded SQL generation (RAG over analytics knowledge).** Before writing SQL, the system retrieves the relevant tables, column descriptions, *business definitions* ("Revenue = SUM of successful orders"), and *verified example queries*. This is what prevents the classic Text-to-SQL failure: syntactically valid SQL that computes the wrong thing.
2. **Automatic drill-down.** For "why" questions, an Analysis Agent inspects results and decides whether to dig deeper (revenue fell → which region? → which category in that region?), using a bounded LangGraph loop with hard limits on depth, LLM calls, and tokens.
3. **Safety by construction.** Every LLM-generated SQL statement passes through a deterministic, parser-based validator (sqlglot) before reaching a read-only connection with timeouts and row caps.
4. **Structured dashboards, not generated code.** The Visualization Agent emits a validated JSON spec that references stored queries; the frontend renders known components. Because SQL is stored per widget, dashboards can be refreshed **without another LLM call**.
5. **Measured, not claimed.** A synthetic e-commerce dataset with *planted anomalies and known ground truth* powers a 25-question evaluation suite (execution success, result correctness, retrieval recall, drill-down correctness, latency, tokens).

**Scope discipline.** The design uses **3 LLM agents** (Query, Analysis, Visualization) and puts everything else — retrieval, validation, execution, statistics, dashboard hydration — in **deterministic code**. A typical analysis costs **3 LLM calls**; a deep "why" question costs **≤ 9** under a hard budget.

**Core stack:** FastAPI · Pydantic v2 · SQLAlchemy 2 + Alembic · PostgreSQL + pgvector · LangGraph · stacked free LLM tiers behind our own `LLMProvider` interface · fastembed · sqlglot · DuckDB · clickhouse-connect · psycopg 3 · Next.js + TypeScript + Tailwind + shadcn/ui + Recharts + TanStack Query · Docker Compose · GitHub Actions.

**Recommended deployment:** Vercel (frontend) + a free container host for FastAPI + Neon (Postgres + pgvector, both for the app DB and the hosted demo analytics DB). CSV/DuckDB demo works everywhere; ClickHouse is fully supported and demonstrated locally via Docker Compose (hosted ClickHouse is not free long-term — see §22).

---

## 2. What We Are Building

### 2.1 One-sentence definition

> A question-to-dashboard analytics engine that grounds an LLM in your schema and business definitions, generates safe SQL for your database's dialect, investigates anomalies automatically, and renders the findings as a dashboard.

### 2.2 What it is NOT

| Not this | Why it matters |
|---|---|
| A chatbot | There is no conversation thread. One question → one analysis run → one dashboard. (History is a list of past analyses, not a chat.) |
| A data warehouse / ETL tool | We query the customer's data **in place**. We never bulk-copy it. |
| A BI tool replacement | No drag-and-drop dashboard builder, no scheduled reports. |
| A "chat with PDFs" RAG app | RAG retrieves *analytics context* (schema, metrics, example SQL), not documents to summarize. |

### 2.3 The two data planes (the most important mental model)

Borrowed directly from Agent360, because conflating these is the #1 source of confusion:

| Plane | What lives there | Who owns it | How we access it |
|---|---|---|---|
| **Analytical plane** | The business data: orders, customers, refunds… | The customer (or our demo seed) | Read-only connector, live queries, `LIMIT`ed |
| **System plane** | *Our* metadata: data source configs, catalog snapshot, knowledge + embeddings, analysis runs, generated SQL, dashboards, telemetry, eval results | Us | SQLAlchemy over our own PostgreSQL |

```mermaid
flowchart LR
    subgraph SYSTEM["System plane — InsightFlow's own Postgres + pgvector"]
        A["data_sources, catalog, knowledge + embeddings"]
        B["analyses, generated SQL, dashboards"]
        C["llm_calls telemetry, evaluation runs"]
    end
    subgraph ANALYTICAL["Analytical plane — customer data, queried in place"]
        D[("PostgreSQL")]
        E[("ClickHouse")]
        F[("CSV file via DuckDB")]
    end
    API["FastAPI backend"] --> SYSTEM
    API -- "read-only, validated SELECT only" --> ANALYTICAL
```

### 2.4 Example questions it must handle well

| Question | Type | What happens |
|---|---|---|
| "What was revenue last month?" | `metric` | 1 query → KPI card + insight |
| "How did revenue trend over the last 12 months?" | `trend` | 1 query → line chart |
| "How did revenue this month compare with last month?" | `comparison` | 1 query → KPI with delta (may drill down if the change is large) |
| "Why did revenue fall in June?" | `root_cause` | 1 query + up to 3 drill-down queries → region bar chart, category bar chart, explanation |
| "Which 10 customers contribute the most revenue?" | `top_n` | 1 query → table + bar chart |
| "Why did refunds increase?" | `root_cause` | drills by category → product → refund reason |

---

## 3. User Experience / Demo Flow

### 3.1 The 2-minute demo (what an interviewer sees)

1. **Datasources page** shows three pre-connected sources: `Demo Shop (PostgreSQL)`, `Demo Shop (ClickHouse)` (local), `Demo Shop (CSV)`. Each shows status ✅ and table count.
2. Click a source → **Schema browser**: tables, columns, types, sample values, plus a **Knowledge** tab with business definitions ("Revenue", "Active customer") and verified example queries.
3. Go to **Analyze**. Select the Postgres source. Type: *"Why did revenue fall in June 2026?"* → **Analyze**.
4. A **progress stepper** advances: *Retrieving context → Generating SQL → Executing query → Analyzing results → Drilling down (region) → Drilling down (category in South) → Building dashboard*.
5. The **result page** shows:
   - Headline insight: *"June revenue fell 18.2% vs May, driven mainly by the South region (−35%), where Electronics sales dropped 54%."*
   - KPI cards: June revenue, May revenue, % change.
   - Bar chart: revenue change by region. Bar chart: South revenue change by category.
   - Table: category breakdown.
   - **Investigation timeline** (the drill-down path, each step with its SQL and row count).
   - Collapsible **Generated SQL** panel, **Retrieved context** panel (which tables/definitions RAG supplied and their scores), and **Run stats** (LLM calls, tokens, latency per step).
6. Click **Refresh** → widgets re-execute their stored SQL with no LLM call (P1).
7. Switch data source to **CSV (DuckDB)** and ask the same question → same answer, different SQL dialect.
8. Open **Evaluations** page (P1) → latest eval run: execution success [X%], result accuracy [X%], retrieval recall@5 [X%], drill-down correctness [X/N].

### 3.2 Page map (information architecture)

```mermaid
flowchart TD
    NAV["Sidebar: Analyze | History | Datasources | Evaluations"]
    NAV --> AN["/analyze — datasource selector + question box + example questions"]
    AN -->|"POST /analyses → id"| RES["/analyses/[id] — progress, then dashboard"]
    NAV --> HIS["/history — table of past analyses"]
    HIS --> RES
    NAV --> DS["/datasources — list, status, table counts"]
    DS --> NEW["/datasources/new — CSV upload / Postgres form / ClickHouse form + Test connection"]
    DS --> DET["/datasources/[id] — Schema tab | Knowledge tab | Sync button"]
    NAV --> EV["/evaluations — eval runs and per-case results (P1)"]
```

There is **no chat interface** and **no settings page** in the MVP.

---

## 4. Lessons From Agent360

Agent360 is a production conversational data-discovery agent (FastAPI + Next.js, ~25k backend LOC, multi-tenant, RBAC, Anthropic/Bedrock/OpenAI adapters). I mapped its datasources, catalog/knowledge pipeline, retrieval, tool loop, dashboard pipeline, telemetry, deployment, and tests. Below: what it does, and what **we** do with each idea.

### 4.1 Relevant lessons taken from Agent360

| # | Area | What Agent360 does | Our decision | Why |
|---|---|---|---|---|
| 1 | **Two data planes** | Strict split: system Postgres (metadata) vs. user's warehouse (queried live, never copied). Documented explicitly, including the nuance that *rendered result sets are cached* in the system DB. | **Use as-is.** | It's the correct architecture and a great interview explanation. We also adopt their honesty: we document that result previews are stored as "derived extracts". |
| 2 | **Connector abstraction** | `Connector` base class (template-method): `run_query`, `fetch_catalog`, `test_connection`, `profile_statistics`, `row_count`, re-entrant `session()`, reconnect-once logic. Factory = if-chain with lazy imports. Connectors are sync, called via `asyncio.to_thread`. | **Simplify.** Keep base class + factory + sync-in-thread. Drop sessions/reconnect logic, Glue catalog providers, Athena/Redshift. | Same pattern, ~⅓ the surface. Sync drivers in a thread pool is simple and correct for our load. |
| 3 | **Error contract of `run_query`** | Never raises; returns `[{"error": "..."}]` so the LLM can read the error. | **Change.** Connectors raise typed exceptions (`QueryTimeoutError`, `QueryExecutionError`…); the *graph node* converts them into state the LLM sees. | Error-as-row mixes data and errors and is easy to misread (a real row could have an `error` column). Typed exceptions are cleaner Python; the LLM still gets the message. |
| 4 | **Read-only enforcement** | Postgres: `default_transaction_read_only=on` + `statement_timeout=60s` at connect. ClickHouse/Redshift: **none** in code (rely on creds). Athena: client-side poll timeout, never cancels. | **Use and extend to all engines.** Postgres same as theirs; ClickHouse `readonly=2` + `max_execution_time`; DuckDB `read_only=True` + `enable_external_access=false` + interrupt-on-timeout. | Their own docs list this as a gap. Closing it for every engine is a concrete "I improved on production code" talking point. |
| 5 | **SQL guard** | String-level: strip comments, must start with `SELECT`/`WITH`, reject `SELECT *`, append `LIMIT` if the word `LIMIT` isn't present. **No parser.** Multi-statement (`SELECT 1; DROP…`) and `WITH x AS (…) DELETE` are not caught; a `LIMIT` in a subquery satisfies the check. | **Intentionally improve.** Parse with **sqlglot** per dialect: exactly one statement, root must be a query, walk the AST for forbidden nodes/functions, table allowlist against the catalog, set the outer `LIMIT` on the tree. | This is the single biggest quality upgrade we can make cheaply, and a top interview topic ("why regex SQL guards fail"). |
| 6 | **`SELECT *` ban** | Rejected so excluded columns can't leak. | **Use (soft).** We reject `SELECT *` in the *outer* query (keeps results small and explicit). | Simple rule, improves result readability and token usage. |
| 7 | **Dialect guidance** | Hand-written per-engine prompt block (`_DIALECT_GUIDANCE[engine]`) appended to the system prompt: date functions, quoting, pitfalls. | **Use.** One short dialect block per engine (postgres / clickhouse / duckdb). | Cheap, high-impact for SQL correctness; the connector exposes its `dialect` so the agent layer never hardcodes engine logic. |
| 8 | **Catalog storage** | Normalized `catalog_tables`/`catalog_columns` (physical) + separate `table_knowledge`/`column_knowledge` (curated) + `column_statistics` + embeddings. Stable surrogate IDs + soft-delete so knowledge survives re-sync. | **Simplify.** Two tables (`catalog_tables`, `catalog_columns`) with a `description` column each + a small `sample_values` JSON; no separate knowledge layer, no review workflow. Upsert on natural key to preserve descriptions. | Keeps the key idea (re-sync must not wipe human descriptions) without 6 tables. |
| 9 | **LLM knowledge autofill** | One LLM call per table to draft purpose/grain/column meanings; human edits preserved. | **P2.** Our demo dataset ships with hand-written descriptions. Optional "Suggest descriptions" button later. | Costs many LLM calls on free tier; not core to the analytics flow. |
| 10 | **Business terms** | Glossary (`term`, `kind` = filter/measure/dimension/instruction, `definition`, `synonyms`, bindings to tables incl. `avoid`), embedded and injected into a 2nd system-prompt block via semantic top-k. | **Use (simplified) — core of our RAG.** A `definition` knowledge item: name, definition text, SQL expression, related tables, synonyms. | This is the highest-value RAG content for Text-to-SQL correctness ("revenue only counts SUCCESS orders"). |
| 11 | **Certified queries** | Vetted question→SQL pairs embedded on the question; agent tool `find_example_queries` retrieves top-3 for few-shot. | **Use.** `example_query` knowledge items, retrieved by question similarity, injected as few-shot examples. | Proven, cheap, measurably improves SQL. Also a natural place to save "good" generated queries (P2). |
| 12 | **Table relationships** | Authored join edges (`source.col = target.col`, cardinality), attached to table details. No FK inference. | **Use, but auto-discover FKs** from `information_schema` for Postgres; author them in a seed file for ClickHouse/CSV. Relationships are attached **deterministically** to retrieved tables, not retrieved by embedding. | Joins are where Text-to-SQL breaks; explicit join paths fix it. FK introspection is a free win on Postgres. |
| 13 | **Retrieval** | Per-table chunk embedded with **fastembed `bge-small-en-v1.5`** (384-d) in **pgvector** (HNSW cosine) + in-memory **BM25** (bm25s) over header text, fused with weighted **RRF** (c=60, 0.7/0.3), similarity floor 0.3, "no strong match" hint. Index (re)built at setup and on knowledge edits. | **Use the same embedding model + pgvector.** For keyword search use **Postgres full-text search** instead of an in-memory BM25 index; fuse with RRF. Hybrid = **P1**; vector-only = P0. | Same quality idea, but stateless (no per-process index to rebuild/warm), one fewer dependency, works on any host. |
| 14 | **Agent loop** | Hand-rolled tool-use loop: up to 30 rounds, model freely picks tools (discover → details → run SQL → build dashboard), stops when no tool call. Truncation/overflow recovery, context compaction. | **Intentionally avoid the open loop.** Use a **LangGraph state machine** with a fixed topology, 3 focused LLM calls, deterministic nodes, and an explicit bounded drill-down cycle. | A free-roaming 30-round loop is expensive and unreliable on free models. Our analysis is a *known procedure*; encoding it as a graph gives predictability, cost bounds, and testability. |
| 15 | **Tool errors as data** | Tool handlers never raise; Pydantic validation errors are formatted per-field with "fix and call again" and fed back. | **Use** for SQL repair and structured-output repair. | Self-correction with precise feedback is the single most effective reliability trick. |
| 16 | **Dashboard as structured output** | `build_data_discovery_version` tool: hero_metrics (1–4), charts (bar/line/pie), data_table, filter_controls — **each widget carries its own SQL**; server executes all widget SQL, saves version; partial-failure policy; model's numeric guesses overwritten by SQL results. | **Use (simplified).** Our widgets **reference query IDs** of already-executed analysis queries instead of carrying fresh SQL; numbers always come from data, never from the LLM. | Same principle (spec + SQL, no generated UI code), but the Viz Agent doesn't write new SQL → fewer failure modes and fewer tokens. |
| 17 | **Refresh without LLM** | `/requery` re-runs widget SQL with new filter values. **Their tech-debt note: it trusts SQL sent by the client.** | **Use (P1), fixed.** Refresh looks up SQL **server-side** by widget→query id and re-validates it. Client never sends SQL. | Avoids a real security hole they documented. |
| 18 | **Interactive filters (`{FILTER_WHERE}`)** | Parameterized placeholder injected into widget SQL; filter values bound as params; validators enforce placement. | **Avoid (P3).** | Elegant but complex (placement rules, per-widget validation, LLM filter-synthesis mini-agent). Not needed to demonstrate our core skills. |
| 19 | **LLM provider abstraction** | Neutral types (`LLMRequest`, `LLMResponse`, `ToolUseBlock`, `Usage`, `StopReason`) + adapters (Anthropic/Bedrock/OpenAI/OpenRouter) + tiers (primary/heavy/fast) + one chokepoint `create_message` that logs every call. **No cross-provider fallback** (deliberate). | **Use the chokepoint + neutral types + per-agent model config.** Add an ordered **fallback model list** (we *need* it for free models). One adapter (OpenAI-compatible → OpenRouter) in MVP. | The chokepoint makes telemetry, retries, and test fakes trivial. Fallback is a free-tier necessity they didn't need. |
| 20 | **Telemetry** | `llm_activity` row per LLM call (model, tokens, cache tokens, latency, success); `discovery_events` per tool call (SQL, latency, tables chosen); OpenTelemetry auto-instrumentation to SigNoz; run inspector UI. All best-effort (never breaks the agent). | **Simplify.** Two tables: `llm_calls` and `analysis_queries` (which already hold SQL, timing, status). JSON logs with `analysis_id`. Run-stats panel in the UI. LangSmith optional via env var. No OTel collector. | Captures 90% of the value; "best-effort, never breaks the run" principle kept. |
| 21 | **Background runs + streaming** | Agent runs as an asyncio background task; events buffered in memory; SSE endpoint polls the buffer every 100ms; reconnect by offset; orphaned runs reconciled at startup. | **Simplify.** Background task + **persist stage/progress to the DB** + frontend **polls** `GET /analyses/{id}` every ~1s (TanStack Query). SSE = P2. Reconcile orphaned `running` analyses at startup → `failed`. | Polling is trivially robust (survives refresh/restart), and our runs are short (~10–40s). |
| 22 | **Credential encryption** | `MultiFernet` with key-rotation; whole connection dict encrypted in one store module; secrets masked as `"***"` in responses. **Bug noted:** update path can overwrite the real password with `"***"`. | **Use (simplified).** Single-key **Fernet**; only the secret fields are encrypted; API responses **omit** secrets entirely (return `has_password: true`); updates treat a missing password as "keep existing". | Same security posture, avoids the masking bug by design. |
| 23 | **Test-before-save** | Connection is tested against a temporary record before persisting. | **Use.** | No dead rows; better UX. |
| 24 | **Scripted LLM for tests** | `ScriptedLLM` monkeypatches the single LLM chokepoint; e2e tests assert the exact event sequence and that chart data equals seeded ground truth (including a refunded row so a wrong filter gives a different number). | **Use.** `FakeLLMProvider` returning scripted structured outputs; graph tests for routing and drill-down termination; seeded data with "trap" rows. | Makes agent workflows testable in CI with zero LLM calls. |
| 25 | **Quality evaluation** | **No golden-question eval harness.** Closest: a before/after replay harness and thumbs-up/down feedback. | **Build what they lack:** a small golden dataset with gold SQL, objective result comparison, retrieval recall, drill-down path checks. | Clear differentiator; evals are a top AI-engineering interview topic. |
| 26 | **Multi-tenancy, RBAC, impersonation, audit log, skills, table groups, research/cohort modes, context compaction, prompt caching, saved/shared dashboards, Terraform, supervisord+nginx single image** | Present, production-grade. | **Avoid.** | Needed for a bank deployment, not for a portfolio MVP. Each would cost days and add no core AI/backend signal. |
| 27 | **Migrations** | Alembic as source of truth + a hand-synced idempotent DDL copy for tests. | **Use Alembic only.** Tests run `alembic upgrade head` against a test DB. | One source of truth; no drift. |
| 28 | **Embedding model baked into the Docker image** | Downloaded at build time (offline bank network). | **Use.** | Avoids a cold-start download on free hosts. |

### 4.2 The big architectural difference, in one picture

```mermaid
flowchart LR
    subgraph A360["Agent360: open tool-use loop"]
        L1["LLM decides next tool"] --> T1["any of ~9 tools"]
        T1 --> L1
        L1 -->|"no tool call"| E1["end (≤ 30 rounds)"]
    end
    subgraph IF["InsightFlow: bounded state machine"]
        R["retrieve (code)"] --> Q["Query Agent"] --> V["validate (code)"] --> X["execute (code)"] --> P["profile (code)"] --> AA["Analysis Agent"]
        AA -->|"drill (depth < 3, budget ok)"| R
        AA -->|"done"| VZ["Viz Agent"] --> F["finalize (code)"]
    end
```

Agent360 optimizes for **open-ended exploration** with a strong paid model. InsightFlow optimizes for **predictable, cheap, testable analyses** on free/weak models — so we move decisions from the LLM into the graph structure.

---

## 5. Final Technology Stack

### 5.1 Stack table

| Layer | Choice | Purpose (why it exists) | Priority |
|---|---|---|---|
| Language | Python 3.12 | Backend + agents | P0 |
| Package mgmt | **uv** | Fast, lockfile, used by Agent360 too | P0 |
| API | **FastAPI** + Uvicorn | Async HTTP API, OpenAPI docs for free | P0 |
| Validation | **Pydantic v2** + pydantic-settings | API DTOs, LLM structured outputs, dashboard spec, config | P0 |
| App DB access | **SQLAlchemy 2.0 (async) + asyncpg** | Typed ORM for *our* metadata DB | P0 |
| Migrations | **Alembic** | Versioned schema changes | P0 |
| App DB | **PostgreSQL 16 + pgvector** | Metadata + vector search in one DB | P0 |
| Orchestration | **LangGraph** | Stateful agent workflow with conditional loop | P0 |
| LLM providers | **Free tiers behind our `LLMProvider`**: Gemini (primary), Groq, OpenRouter `:free` — all OpenAI-compatible, used via the `openai` SDK | Zero spend; independent quotas stacked in one fallback chain; swappable (see §17.6) | P0 |
| Retries | **tenacity** | Backoff on 429/5xx/timeouts | P0 |
| Embeddings | **fastembed** `BAAI/bge-small-en-v1.5` (384-d, CPU/ONNX) | Free, local, no API key, no rate limit | P0 |
| SQL parsing | **sqlglot** | Dialect-aware parsing for the SQL guard + eval (table/column extraction) | P0 |
| CSV engine | **DuckDB** | SQL over uploaded CSVs | P0 |
| Postgres driver (analytics) | **psycopg 3** (sync) | Customer Postgres connector | P0 |
| ClickHouse driver | **clickhouse-connect** | Customer ClickHouse connector | P0 |
| Secrets | **cryptography (Fernet)** | Encrypt datasource passwords at rest | P0 |
| Data generation | numpy + Faker | Synthetic demo data with planted anomalies | P0 |
| Lint/format | ruff (+ mypy on core modules, P1) | Code quality | P0 |
| Tests | pytest, pytest-asyncio, httpx | Unit/integration/graph tests | P0 |
| Frontend | **Next.js (App Router) + TypeScript** | Pages + routing | P0 |
| Styling | **Tailwind CSS + shadcn/ui** | Fast, clean, professional UI with no custom design system | P0 |
| Data fetching | **TanStack Query** | Caching + polling the analysis status | P0 |
| Charts | **Recharts** | Line/bar/area charts from JSON rows | P0 |
| API types | openapi-typescript | Generate TS types from FastAPI's OpenAPI spec | P1 |
| Containers | Docker + Docker Compose | Local stack, deployable backend image | P0 |
| CI | GitHub Actions | Lint, tests, frontend build, docker build | P1 |
| Tracing (optional) | LangSmith (env-flag only) | Visual traces of graph runs during development | P2 |
| Rate limit (demo) | slowapi | Protect free LLM quota on public demo | P1 |

### 5.2 Explicitly evaluated alternatives (short)

- **Streamlit instead of Next.js** — faster to build, but looks like a notebook and doesn't demonstrate a real frontend/backend contract. Next.js (with shadcn/ui doing most of the styling work) is worth the modest extra effort. → **Next.js.** (ADR-05)
- **Plotly / ECharts instead of Recharts** — more chart types we don't need, heavier bundles, and less idiomatic React. Recharts covers line/bar/area/pie with JSON rows directly. → **Recharts.** (ADR-06)
- **Qdrant / FAISS instead of pgvector** — an extra service (Qdrant) or an in-process index that must be persisted/rebuilt (FAISS). pgvector keeps vectors next to their metadata with transactional updates, filtering by `data_source_id` in plain SQL, and it's available on free hosted Postgres (Neon/Supabase). → **pgvector.** (ADR-03)
- **API embeddings (OpenAI/Gemini) instead of fastembed** — would add a second rate-limited dependency. fastembed runs locally on CPU in ~ms per query. We still put it behind an `EmbeddingProvider` interface. → **fastembed.**
- **Raw psycopg (Agent360) vs SQLAlchemy** — SQLAlchemy 2 + Alembic is what most backend job descriptions list and makes models self-documenting. → **SQLAlchemy** for the app DB; raw drivers for customer DBs (we never ORM a customer's schema).
- **LangChain agents / LlamaIndex** — not needed. We use LangGraph for orchestration only; LLM calls go through our own thin provider. → No LangChain chains/agents.

---

## 6. System Architecture

### 6.1 Overall architecture (Diagram 1)

```mermaid
flowchart TB
    U["User (browser)"] --> FE["Next.js frontend<br/>Tailwind + shadcn/ui + Recharts<br/>TanStack Query (polling)"]
    FE -- "REST /api/v1 (JSON)" --> API["FastAPI app"]

    subgraph BE["Backend (single Python process)"]
        API --> SVC["Services layer<br/>datasource / knowledge / analysis / evaluation"]
        SVC --> GRAPH["LangGraph analysis workflow"]
        GRAPH --> AG["Agents: Query · Analysis · Visualization"]
        AG --> LLM["LLMProvider interface"]
        GRAPH --> RAG["RAG retriever<br/>(fastembed + pgvector + FTS)"]
        GRAPH --> GUARD["SQL guard (sqlglot)"]
        GUARD --> CONN["Connector factory"]
        GRAPH --> PROF["Result profiler (pandas-free stats)"]
        GRAPH --> DASH["Dashboard validator + hydrator"]
        SVC --> OBS["Observability (llm_calls, query log, JSON logs)"]
    end

    LLM --> OR["OpenRouter API<br/>(free models, fallback list)"]
    RAG --> APPDB[("App PostgreSQL + pgvector")]
    SVC --> APPDB
    OBS --> APPDB
    CONN --> PG[("Customer PostgreSQL")]
    CONN --> CH[("Customer ClickHouse")]
    CONN --> DUCK[("DuckDB file built from CSV")]
```

### 6.2 Request lifecycle (Diagram 5)

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant API as FastAPI
    participant S as AnalysisService
    participant G as LangGraph run (background task)
    participant DB as App Postgres
    participant W as Customer DB
    participant L as OpenRouter

    B->>API: POST /api/v1/analyses {datasource_id, question}
    API->>S: create_analysis()
    S->>DB: INSERT analyses (status=queued)
    S-->>G: asyncio.create_task(run_graph(analysis_id))
    API-->>B: 202 {id, status: "queued"}

    loop every ~1s until terminal status
        B->>API: GET /api/v1/analyses/{id}
        API->>DB: SELECT analysis + steps
        API-->>B: {status, stage, steps[], dashboard?}
    end

    G->>DB: stage=retrieving (pgvector + FTS search)
    G->>L: Query Agent (structured output)
    G->>G: sqlglot validation
    G->>W: execute read-only SQL (timeout, LIMIT)
    G->>DB: INSERT analysis_queries (sql, timing, preview)
    G->>L: Analysis Agent
    opt drill-down (≤ 3 times, within budget)
        G->>L: Query Agent (drill-down mode)
        G->>W: execute
        G->>L: Analysis Agent
    end
    G->>L: Visualization Agent
    G->>DB: INSERT dashboards; UPDATE analyses status=completed
```

**Why background task + polling instead of a long HTTP request?** An analysis takes 10–60 seconds on free models (rate-limit waits included). Holding an HTTP request that long is fragile (proxies time out, a page refresh loses everything). Writing progress to the DB and polling means the result survives refreshes and the history page "just works". SSE streaming is a P2 polish item.

**Why `asyncio.create_task` and not Celery/RQ?** One process, short jobs, and no requirement to survive restarts — a queue + worker would add Redis and a second deployable for no portfolio benefit. On startup we mark any `running` analyses as `failed` (Agent360's "reconcile orphaned turns" lesson). A proper job queue is P3.

### 6.3 Layering rule (keeps the code explainable)

```
api/  →  services/  →  graph/ → agents/ → llm/
                   ↘        ↘ rag/, sql_guard/, connectors/, analytics/, dashboard/
                    db/ (repositories via SQLAlchemy session)
```

- `api/` only parses HTTP and calls services. No business logic.
- `services/` orchestrate use-cases and own DB transactions.
- `graph/` wires nodes; nodes call agents and deterministic modules.
- `agents/` know prompts and output schemas; they **do not** know about HTTP, DB sessions, or specific databases.
- `connectors/` know databases; they **do not** know about LLMs.

---

## 7. Frontend Architecture

### 7.1 Principles

- **Thin client.** All intelligence lives in the backend. The frontend renders JSON.
- **Server state only.** TanStack Query owns all fetched data (cache + polling). No Redux/zustand needed — there is no complex client state.
- **Known components only.** The dashboard renderer is a `switch` on widget type. The LLM never produces UI code.
- **shadcn/ui** provides Button, Card, Input, Select, Tabs, Table, Badge, Dialog, Collapsible, Skeleton, Toast — we don't design these ourselves.
- Pages are mostly **client components** (`"use client"`) because they poll and interact; we don't need React Server Components data fetching for this app. Keep it simple.

### 7.2 Pages

| Route | Responsibility | Main components |
|---|---|---|
| `/` | Redirect to `/analyze` | — |
| `/analyze` | Pick datasource, type question, click Analyze; shows example questions per datasource | `DatasourceSelect`, `QuestionForm`, `ExampleQuestions` |
| `/analyses/[id]` | Poll the run. While running: `ProgressStepper`. When done: `DashboardView` + `InvestigationTimeline` + `SqlPanel` + `RetrievedContextPanel` + `RunStatsPanel`. On failure: `ErrorState` with reason + retry button. | see §16 |
| `/history` | Table of past analyses (question, datasource, status, created, duration, tokens) | `HistoryTable` |
| `/datasources` | List datasources with type badge, status, table count, last synced | `DatasourceList` |
| `/datasources/new` | Tabs: CSV upload / PostgreSQL / ClickHouse. "Test connection" then "Save". | `CsvUploadForm`, `ConnectionForm` |
| `/datasources/[id]` | Tabs: **Schema** (tables → columns, types, samples, editable descriptions), **Knowledge** (definitions, example queries: add/edit/delete), **Sync** button | `SchemaBrowser`, `KnowledgeEditor` |
| `/evaluations` (P1) | List eval runs; drill into per-case pass/fail with generated vs gold SQL | `EvalRunList`, `EvalCaseTable` |

### 7.3 Dashboard rendering

```mermaid
flowchart LR
    J["GET /analyses/{id} → dashboard JSON<br/>(spec + datasets)"] --> DV["DashboardView"]
    DV --> K["KpiCard × n"]
    DV --> C["ChartWidget"]
    C --> LC["LineChart"]
    C --> BC["BarChart"]
    C --> AC["AreaChart"]
    DV --> T["DataTableWidget"]
    DV --> I["InsightCard × n"]
```

Each widget receives `{widget, dataset}` where `dataset = {columns, rows}` comes from the referenced query. Formatting (currency, %, compact numbers) is done in `lib/format.ts` using the widget's `format` field — numbers are never pre-formatted by the LLM.

### 7.4 Polling contract

```ts
useQuery({
  queryKey: ["analysis", id],
  queryFn: () => api.getAnalysis(id),
  refetchInterval: (q) => isTerminal(q.state.data?.status) ? false : 1000,
});
```

### 7.5 Frontend ↔ backend type safety (P1)

FastAPI publishes `/openapi.json`. `npm run gen:api` runs `openapi-typescript` to produce `src/types/api.gen.ts`. The dashboard spec types are therefore *derived from the Pydantic models* — change the backend model and the frontend build fails if it's out of sync. In P0 we hand-write `types/api.ts` mirroring the Pydantic models.

---

## 8. Backend Architecture

### 8.1 Module responsibilities

| Module | Responsibility | Depends on |
|---|---|---|
| `api/` | Routers, request/response DTOs wiring, HTTP error mapping | services, schemas |
| `core/` | Settings, logging setup, error types, crypto helpers | — |
| `db/` | SQLAlchemy engine/session, ORM models, Alembic env | core |
| `schemas/` | Pydantic **API** DTOs (requests/responses) | — |
| `connectors/` | `DataConnector` ABC, Postgres/ClickHouse/DuckDB implementations, factory, result normalization | core |
| `catalog/` | Schema introspection → catalog rows; profiling (samples, distinct counts); FK discovery; dimension detection | connectors, db |
| `sql_guard/` | Parse + validate + rewrite SQL (LIMIT), policies per dialect | sqlglot |
| `llm/` | `LLMProvider` ABC, OpenRouter provider, structured-output helper, fallback router, fake provider | core |
| `rag/` | Embedding provider, chunk builders, indexer, retriever (vector + FTS + RRF), context builder | db, llm? (no) |
| `agents/` | The 3 agents: prompt templates, output models, `run()` functions | llm |
| `analytics/` | Deterministic result profiling: period deltas, contribution analysis, summary stats | — |
| `dashboard/` | `DashboardSpec` models, spec validator, data hydrator, fallback builder, refresh | connectors, sql_guard |
| `graph/` | LangGraph `AnalysisState`, nodes, routing functions, budgets, graph builder | everything above |
| `services/` | Use-case orchestration + transactions: datasource, knowledge, analysis (background run), evaluation | db, graph, catalog, rag |
| `observability/` | LLM call recorder, step timer, log context (`analysis_id`) | db |
| `evaluation/` | Golden dataset, runner, metrics, result comparison, report writer | graph, sql_guard, connectors |

### 8.2 Sync vs async

- **FastAPI + app DB:** async (asyncpg). Natural fit, many concurrent cheap requests (polling).
- **Customer DB connectors:** **sync drivers** (psycopg, clickhouse-connect, duckdb) called through `await asyncio.to_thread(...)`. DuckDB has no async API anyway, and keeping all three uniform simplifies the interface. Our concurrency is tiny (a few analyses at once).
- **LLM calls:** async (`openai.AsyncOpenAI` pointed at OpenRouter).
- **Embeddings:** fastembed is CPU-bound → `asyncio.to_thread`.

### 8.3 Configuration (`core/config.py`, pydantic-settings)

```env
# App
APP_ENV=local                     # local | demo | test
DEMO_MODE=false                   # true on the public deployment (see §24)
DATABASE_URL=postgresql+asyncpg://insightflow:insightflow@localhost:5432/insightflow
DATASOURCE_ENCRYPTION_KEY=        # Fernet key (base64, 32 bytes)
CORS_ORIGINS=http://localhost:3000
UPLOAD_DIR=./data/uploads
MAX_UPLOAD_MB=20

# LLM
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL_QUERY=<free model id with tool/JSON support>
LLM_MODEL_ANALYSIS=<free model id>
LLM_MODEL_VISUALIZATION=<free model id>
LLM_FALLBACK_MODELS=<id1>,<id2>   # tried in order on 404/429/5xx/invalid output
LLM_TIMEOUT_SECONDS=60
LLM_MAX_RETRIES=2

# Embeddings / RAG
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
RAG_TOP_K_TABLES=5
RAG_TOP_K_DEFINITIONS=4
RAG_TOP_K_EXAMPLES=3
RAG_MIN_SIMILARITY=0.30
RAG_HYBRID_ENABLED=true

# Query safety
SQL_MAX_ROWS=500
SQL_TIMEOUT_SECONDS=20

# Agent budgets
MAX_DRILLDOWN_DEPTH=3
MAX_LLM_CALLS_PER_ANALYSIS=12
MAX_TOKENS_PER_ANALYSIS=80000
MAX_SQL_REPAIR_ATTEMPTS=2
```

### 8.4 API design (`/api/v1`)

All responses are JSON. Errors use one envelope: `{"error": {"code": "SQL_TIMEOUT", "message": "...", "retryable": true}}`.

| Method & path | Purpose | Request | Response | Priority |
|---|---|---|---|---|
| `GET /health` | Liveness | — | `{"status":"ok"}` | P0 |
| `GET /health/ready` | Readiness (app DB + embedding model) | — | `{"db":"ok","embeddings":"ok"}` | P0 |
| `POST /datasources/test` | Test a connection without saving | `{type, config, password}` | `{ok, latency_ms, error?}` | P0 |
| `POST /datasources` | Create Postgres/ClickHouse source (tests first, then saves and syncs) | `{name, type, config:{host,port,database,username,schemas}, password, business_context?}` | `DatasourceOut` (no secret; `has_secret: true`) | P0 |
| `POST /datasources/csv` | Upload CSV file(s) → DuckDB source | multipart: `name`, `files[]` | `DatasourceOut` + tables created | P0 |
| `GET /datasources` | List | — | `[DatasourceOut{id,name,type,status,table_count,last_synced_at}]` | P0 |
| `GET /datasources/{id}` | Detail | — | `DatasourceOut` | P0 |
| `DELETE /datasources/{id}` | Delete source + catalog + knowledge (analyses kept, FK set null) | — | 204 | P0 |
| `POST /datasources/{id}/sync` | Re-introspect, profile and re-index | — | `{tables, columns, chunks_indexed, duration_ms}` | P0 |
| `GET /datasources/{id}/schema` | Catalog for the schema browser | — | `{tables:[{name, description, row_count, columns:[{name,type,description,sample_values,is_dimension}]}], relationships:[...]}` | P0 |
| `PATCH /datasources/{id}/tables/{table_id}` · `PATCH .../columns/{column_id}` | Edit descriptions / queryable flag (triggers re-embed) | `{description?, is_queryable?}` | updated row | P1 |
| `GET /datasources/{id}/knowledge?kind=` | List definitions / example queries | — | `[KnowledgeItemOut]` | P0 (read) |
| `POST /datasources/{id}/knowledge` · `PUT/DELETE .../knowledge/{item_id}` | Manage definitions / examples (re-embeds) | `{kind, title, content, payload}` | `KnowledgeItemOut` | P1 |
| `POST /datasources/{id}/retrieve` | Debug: what RAG returns for a question | `{question}` | `{chunks:[{kind,title,score,method}], relationships, confidence}` | P0 (dev) |
| `POST /analyses` | Start an analysis (background) | `{datasource_id, question}` | **202** `{id, status:"queued"}` | P0 |
| `GET /analyses/{id}` | Poll status; full result when complete | — | `{id, question, status, stage, stop_reason, steps:[...], queries:[...], retrieved_context, findings, dashboard:{spec, datasets}?, stats:{llm_calls, tokens, latency_ms}, error?}` | P0 |
| `GET /analyses?limit=&offset=&datasource_id=` | History | — | `[AnalysisSummary{id, question, datasource, status, created_at, latency_ms, total_tokens}]` | P0 |
| `POST /analyses/{id}/refresh` | Re-run stored widget SQL, no LLM | — | updated `{dashboard}` | P1 |
| `POST /evaluations` | Start an eval run (disabled in DEMO_MODE) | `{suite, datasource_id, cases?}` | 202 `{id}` | P1 |
| `GET /evaluations` · `GET /evaluations/{id}` | Eval runs and per-case results | — | `{summary, results[]}` | P1 |

Deliberately **not** included: chat endpoints, any endpoint that accepts raw SQL from the client, and auth endpoints.

---

## 9. Connector Architecture

### 9.1 Diagram (Diagram 3)

```mermaid
classDiagram
    class DataConnector {
        <<abstract>>
        +engine: str
        +dialect: str  // sqlglot dialect name
        +test_connection() ConnectionTestResult
        +introspect_schema() SchemaSnapshot
        +execute(sql, max_rows, timeout_s) QueryResult
        +profile_columns(table, columns) dict~str, ColumnProfile~
        +close() None
    }
    class PostgresConnector {
        engine = "postgres"
        dialect = "postgres"
        psycopg3, read-only txn, statement_timeout
    }
    class ClickHouseConnector {
        engine = "clickhouse"
        dialect = "clickhouse"
        clickhouse-connect, readonly=2, max_execution_time
    }
    class DuckDBConnector {
        engine = "duckdb"
        dialect = "duckdb"
        read_only file, external access disabled
    }
    class ConnectorFactory {
        +create(datasource, secrets) DataConnector
    }
    DataConnector <|-- PostgresConnector
    DataConnector <|-- ClickHouseConnector
    DataConnector <|-- DuckDBConnector
    ConnectorFactory ..> DataConnector : creates
```

### 9.2 Normalized types (`connectors/types.py`)

```python
class ColumnInfo(BaseModel):
    name: str
    data_type: str            # raw engine type, e.g. "numeric(12,2)", "Nullable(String)"
    normalized_type: Literal["integer", "float", "decimal", "string", "boolean",
                             "date", "timestamp", "other"]
    nullable: bool | None = None
    description: str | None = None   # from COMMENT ON COLUMN if present

class TableInfo(BaseModel):
    schema_name: str
    table_name: str
    object_type: Literal["table", "view"] = "table"
    description: str | None = None
    row_count_estimate: int | None = None
    columns: list[ColumnInfo]

class ForeignKey(BaseModel):
    from_table: str; from_column: str; to_table: str; to_column: str

class SchemaSnapshot(BaseModel):
    tables: list[TableInfo]
    foreign_keys: list[ForeignKey] = []

class ResultColumn(BaseModel):
    name: str
    normalized_type: str

class QueryResult(BaseModel):
    columns: list[ResultColumn]
    rows: list[list[Any]]          # JSON-safe values (see normalization)
    row_count: int
    truncated: bool                # True if more rows existed than max_rows
    execution_ms: int

class ColumnProfile(BaseModel):
    distinct_count: int | None
    null_fraction: float | None
    sample_values: list[str]       # up to 10, for low-cardinality text columns
```

### 9.3 Which methods are truly needed for MVP

| Method | MVP? | Used by |
|---|---|---|
| `test_connection()` | **P0** | "Test connection" button; create datasource |
| `introspect_schema()` | **P0** | Catalog sync → RAG indexing |
| `execute(sql, max_rows, timeout_s)` | **P0** | Every analysis query; dashboard refresh; eval |
| `profile_columns(table, columns)` | **P0** (simple) | Sample values for RAG chunks + detecting drill-down dimensions (low-cardinality text columns) |
| `close()` | **P0** | End of analysis run |
| `get_sample_rows()` | ✗ (dropped) | Replaced by `profile_columns` sample values — cheaper and safer for prompts |
| `explain(sql)` | P2 | Optional pre-execution validation (§14) |
| connection pooling / sessions | P3 | Not needed at our concurrency |

### 9.4 Connector lifecycle

```mermaid
flowchart LR
    A["Analysis starts"] --> B["DatasourceService loads row,<br/>decrypts secret in memory"]
    B --> C["ConnectorFactory.create(ds, secret)"]
    C --> D["Connector object (no connection yet)"]
    D -->|"first execute()"| E["Opens driver connection lazily<br/>(read-only settings applied)"]
    E --> F["Queries for this run reuse it"]
    F --> G["finally: connector.close()"]
```

- One connector instance **per analysis run** (and per API call such as test/sync). It opens lazily and is closed in a `finally`.
- No global pool in MVP. Opening a connection per run costs ~10–100ms, negligible against LLM latency.
- The decrypted secret exists only in memory inside the connector; it's never placed into graph state (graph state gets persisted/logged).

### 9.5 Per-engine specifics

| Concern | PostgreSQL | ClickHouse | CSV → DuckDB |
|---|---|---|---|
| Driver | psycopg 3 (sync) | clickhouse-connect (HTTP) | duckdb |
| Read-only (DB level) | `options="-c default_transaction_read_only=on -c statement_timeout=20000"` at connect **+** a read-only DB role | Client settings `readonly=2` (writes forbidden, query settings allowed) + read-only user profile in our Docker init | `duckdb.connect(path, read_only=True)` + `SET enable_external_access=false; SET lock_configuration=true` |
| Timeout | `statement_timeout` (server-side cancel) | `max_execution_time` setting (server-side) + client `send_receive_timeout` | Watchdog timer calls `conn.interrupt()` |
| Row cap | Outer `LIMIT` from guard + `fetchmany(max_rows+1)` to detect truncation | Same + `max_result_rows` setting | Same |
| Schema discovery | `information_schema.tables/columns` + `pg_description` comments + FK query on `information_schema.table_constraints`; row estimate from `pg_class.reltuples` | `system.tables` + `system.columns` (with `comment`); `total_rows` | `information_schema` (DuckDB supports it) / `DESCRIBE` |
| Profiling | `SELECT count(DISTINCT c), …, array of top values` per column with a sample cap (`TABLESAMPLE` for large tables) | `uniq(c)`, `topK(10)(c)` | `approx_count_distinct`, `SELECT DISTINCT … LIMIT 10` |
| Schemas included | Configurable, default `public` | Configured database | Single `main` schema |

**CSV lifecycle specifically:**

1. `POST /datasources/csv` (multipart, ≤ `MAX_UPLOAD_MB`). One or more CSV files.
2. Backend validates extension/size, saves to `UPLOAD_DIR/{ds_id}/raw/`.
3. Builds `UPLOAD_DIR/{ds_id}/data.duckdb` with `CREATE TABLE <sanitized_name> AS SELECT * FROM read_csv_auto(path, sample_size=-1)` — this is the **only** time external file access is enabled, in a *separate, write-mode* connection used solely for the import.
4. All analysis queries open the `.duckdb` file `read_only=True` with external access disabled (so the LLM can't `read_csv('/etc/passwd')`).
5. Malformed CSV (encoding errors, ragged rows) → 422 with DuckDB's error message; nothing is saved.

> Why convert CSV into a DuckDB file instead of querying the CSV directly each time? Type inference happens once, queries are faster (columnar storage), and the query-time connection can run with external file access fully disabled.

### 9.6 Result normalization

Every connector converts driver values into JSON-safe values before returning `QueryResult`:

| Driver value | Normalized |
|---|---|
| `Decimal` | `float` (analytics precision is fine) |
| `datetime`/`date` | ISO-8601 string |
| `UUID` | `str` |
| `bytes` | `"<binary>"` |
| `NaN`/`Inf` | `None` |
| ClickHouse `Nullable(...)`, `LowCardinality(...)` | unwrapped type when computing `normalized_type` |

`normalized_type` per column lets the profiler and the dashboard validator know which columns are numeric/temporal without re-inspecting values.

### 9.7 SQL dialects — how the Query Agent knows what to write

1. Each connector exposes `dialect` (`"postgres" | "clickhouse" | "duckdb"`), which doubles as the **sqlglot dialect name**.
2. The `load_context` graph node puts `dialect` into state.
3. The Query Agent's prompt includes a **dialect guidance block** chosen by `dialect` (Agent360 lesson #7). Example differences it covers:

| Need | PostgreSQL | ClickHouse | DuckDB |
|---|---|---|---|
| Month bucket | `date_trunc('month', ts)` | `toStartOfMonth(ts)` | `date_trunc('month', ts)` |
| Date literal | `DATE '2026-06-01'` | `toDate('2026-06-01')` | `DATE '2026-06-01'` |
| Interval | `ts >= now() - INTERVAL '30 days'` | `ts >= now() - INTERVAL 30 DAY` | `ts >= now() - INTERVAL 30 DAY` |
| Safe division | `x / NULLIF(y, 0)` | `if(y = 0, NULL, x / y)` | `x / NULLIF(y, 0)` |
| Conditional sum | `SUM(x) FILTER (WHERE …)` | `sumIf(x, cond)` | `SUM(x) FILTER (WHERE …)` |
| Identifier quoting | `"col"` | `` `col` `` or `"col"` | `"col"` |

4. The SQL guard parses with the **same dialect**, so a ClickHouse-only function isn't misread as invalid Postgres.
5. Verified example queries are stored **per datasource**, so few-shot examples are already in the right dialect.

### 9.8 Configuration & credential storage (summary; details in §24)

- `data_sources.config` (JSONB, non-secret): host, port, database, username, schemas, `ssl`, CSV file list.
- `data_sources.encrypted_secret` (text): Fernet-encrypted password. Decrypted only inside `DatasourceService.build_connector()`.
- API responses never include the secret — only `has_secret: true`.

### 9.9 Redshift (future, P3)

Redshift speaks the Postgres wire protocol, so a `RedshiftConnector` would subclass `PostgresConnector`, swap introspection to `svv_columns`/`svv_tables` (Agent360 does exactly this), set `dialect="redshift"` (sqlglot supports it), and add a dialect guidance block. Estimated effort: ~1 day **once there's a cluster to test against** — which costs money, so it stays out of the MVP.

---

## 10. Application Database Design

### 10.1 Tables (10 total)

#### `data_sources`
Purpose: registry of connected data sources.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `name` | text | unique |
| `type` | text | `postgres` / `clickhouse` / `csv` |
| `config` | JSONB | non-secret connection settings or CSV file list |
| `encrypted_secret` | text NULL | Fernet ciphertext of password |
| `status` | text | `connected` / `error` / `syncing` |
| `status_message` | text NULL | last error (sanitized) |
| `business_context` | JSONB | `{as_of_date, currency, notes}` — see §15.6 |
| `last_synced_at` | timestamptz NULL | |
| `created_at`, `updated_at` | timestamptz | |

#### `catalog_tables`
Purpose: snapshot of discovered tables (+ human descriptions that survive re-sync).

| Column | Notes |
|---|---|
| `id` UUID PK | |
| `data_source_id` FK → data_sources ON DELETE CASCADE | |
| `schema_name`, `table_name` | **unique (data_source_id, schema_name, table_name)** — upsert key |
| `description` text NULL | from DB comment or user edit; **never overwritten by sync if user-edited** (`description_source` = `db`/`user`) |
| `row_count_estimate` bigint NULL | |
| `is_active` bool | false when the table disappears upstream (soft delete) |
| `is_queryable` bool default true | allowlist switch (§14) |

#### `catalog_columns`
| Column | Notes |
|---|---|
| `id` UUID PK, `table_id` FK → catalog_tables CASCADE | |
| `name`, `data_type`, `normalized_type`, `ordinal`, `nullable` | |
| `description` text NULL, `description_source` | |
| `distinct_count` int NULL, `sample_values` JSONB NULL | from profiling |
| `is_dimension` bool | low-cardinality text/bool column → drill-down candidate |
| unique `(table_id, name)` | |

#### `table_relationships`
Purpose: join paths, attached deterministically to retrieved tables.

| Column | Notes |
|---|---|
| `id`, `data_source_id` FK | |
| `from_table`, `from_column`, `to_table`, `to_column` | qualified names |
| `source` | `fk` (introspected) / `user` (seed or UI) |

#### `knowledge_chunks`
Purpose: **the RAG index.** One row per retrievable unit.

| Column | Notes |
|---|---|
| `id` UUID PK, `data_source_id` FK CASCADE | |
| `kind` | `table` / `definition` / `example_query` / `doc` |
| `ref_id` UUID NULL | e.g. catalog_tables.id for `table` chunks |
| `title` | e.g. `orders`, `Revenue`, `Monthly revenue by region` |
| `content` | the text that is embedded and shown to the LLM |
| `payload` | JSONB — structured fields: for definitions `{sql_expression, tables, synonyms}`, for examples `{question, sql}` |
| `embedding` | `vector(384)` |
| `search_tsv` | `tsvector` **generated** from title + content (for FTS) |
| `source` | `auto` (generated from catalog) / `user` / `seed` |
| `updated_at` | |
| Indexes | HNSW on `embedding vector_cosine_ops`; GIN on `search_tsv`; btree `(data_source_id, kind)` |

#### `analyses`
Purpose: one row per question asked (= one graph run).

| Column | Notes |
|---|---|
| `id` UUID PK, `data_source_id` FK (SET NULL) | |
| `question` text | |
| `status` | `queued` / `running` / `completed` / `failed` |
| `stage` | current node label for the progress UI |
| `question_type` | from Query Agent: `metric`/`trend`/`comparison`/`breakdown`/`top_n`/`root_cause` |
| `analysis_frame` JSONB | metric + periods (§15) |
| `retrieved_context` JSONB | ids/titles/scores of chunks used (debug panel + eval) |
| `findings` JSONB | final structured findings from the Analysis Agent |
| `drilldown_depth` int | |
| `stop_reason` | `answered` / `max_depth` / `budget_exhausted` / `inconclusive` / `error` |
| `error_code`, `error_message` | user-safe message |
| `llm_calls_count`, `total_input_tokens`, `total_output_tokens` | rollups |
| `latency_ms` | |
| `model_config` JSONB | which models were configured (eval reproducibility) |
| `created_at`, `completed_at` | index on `created_at DESC` for history |

#### `analysis_queries`
Purpose: **every SQL statement** generated in a run (including rejected ones). This is both the audit log and the "SQL behind each widget".

| Column | Notes |
|---|---|
| `id` UUID PK, `analysis_id` FK CASCADE | |
| `seq` int | order in the run |
| `purpose` | `primary` / `drilldown` |
| `step_question` | NL question this SQL answers (e.g. "revenue change by region for May vs June") |
| `sql` | final SQL actually executed (after LIMIT rewrite) |
| `original_sql` | as generated by the LLM |
| `attempt` int | 1 = first try, 2–3 = repairs |
| `status` | `rejected` / `failed` / `succeeded` |
| `error` | validator or DB error |
| `tables_used` text[] | extracted by sqlglot |
| `row_count`, `truncated`, `execution_ms` | |
| `result_columns` JSONB, `result_preview` JSONB | ≤ `SQL_MAX_ROWS` rows (derived extract — see §10.3) |
| `filters` JSONB | drill-down path filters (e.g. `[{"column":"region","value":"South"}]`) |
| `profile` JSONB | deterministic stats from the profiler |

#### `dashboards`
Purpose: the validated dashboard spec for an analysis (1:1 today, separate table so refresh history / saving are easy later).

| Column | Notes |
|---|---|
| `id` UUID PK, `analysis_id` FK unique | |
| `spec` JSONB | `DashboardSpec` (widgets reference `analysis_queries.id`) |
| `generated_by` | `llm` / `fallback` |
| `refreshed_at` timestamptz NULL | P1 |
| `created_at` | |

#### `llm_calls`
Purpose: observability — one row per LLM request attempt.

| Column | Notes |
|---|---|
| `id`, `analysis_id` FK NULL (NULL for eval or utility calls), `evaluation_run_id` FK NULL | |
| `agent` | `query` / `analysis` / `visualization` |
| `provider`, `model` | actual model that answered (after fallback) |
| `attempt` int, `fallback_index` int | |
| `input_tokens`, `output_tokens`, `total_tokens` | from provider usage when available, else estimated (`estimated=true`) |
| `latency_ms`, `success`, `error_type`, `error_message` | |
| `structured_output_valid` bool | did Pydantic validation pass |
| `created_at` | index `(analysis_id)` |

> Prompt/response bodies are **not** stored by default (size + data sensitivity, same as Agent360). A `DEBUG_STORE_PROMPTS=true` flag can store them locally for debugging (P2).

#### `evaluation_runs`
Purpose: one row per eval run with summary + per-case results.

| Column | Notes |
|---|---|
| `id`, `suite` (e.g. `core`), `data_source_id` | |
| `config` JSONB | models, top-k, git SHA |
| `status`, `started_at`, `completed_at` | |
| `summary` JSONB | aggregate metrics |
| `results` JSONB | array of per-case results (small: ≤ 30 cases) |

> Per-case results live in JSONB instead of an `evaluation_results` table: we only ever read them as a whole, and it saves a table. Promote to a table if cases grow into the hundreds.

### 10.2 ER diagram (Diagram 6)

```mermaid
erDiagram
    data_sources ||--o{ catalog_tables : has
    catalog_tables ||--o{ catalog_columns : has
    data_sources ||--o{ table_relationships : has
    data_sources ||--o{ knowledge_chunks : indexes
    data_sources ||--o{ analyses : "analyzed by"
    analyses ||--o{ analysis_queries : runs
    analyses ||--o| dashboards : produces
    analyses ||--o{ llm_calls : records
    data_sources ||--o{ evaluation_runs : "evaluated on"
    evaluation_runs ||--o{ llm_calls : records

    data_sources {
        uuid id PK
        text name
        text type
        jsonb config
        text encrypted_secret
        text status
        jsonb business_context
    }
    catalog_tables {
        uuid id PK
        uuid data_source_id FK
        text schema_name
        text table_name
        text description
        bool is_queryable
    }
    catalog_columns {
        uuid id PK
        uuid table_id FK
        text name
        text normalized_type
        text description
        jsonb sample_values
        bool is_dimension
    }
    table_relationships {
        uuid id PK
        uuid data_source_id FK
        text from_table
        text from_column
        text to_table
        text to_column
    }
    knowledge_chunks {
        uuid id PK
        uuid data_source_id FK
        text kind
        text title
        text content
        jsonb payload
        vector embedding
        tsvector search_tsv
    }
    analyses {
        uuid id PK
        uuid data_source_id FK
        text question
        text status
        text question_type
        jsonb findings
        int drilldown_depth
        text stop_reason
    }
    analysis_queries {
        uuid id PK
        uuid analysis_id FK
        int seq
        text purpose
        text sql
        text status
        jsonb result_preview
    }
    dashboards {
        uuid id PK
        uuid analysis_id FK
        jsonb spec
        text generated_by
    }
    llm_calls {
        uuid id PK
        uuid analysis_id FK
        text agent
        text model
        int input_tokens
        int output_tokens
        int latency_ms
        bool success
    }
    evaluation_runs {
        uuid id PK
        text suite
        jsonb summary
        jsonb results
    }
```

### 10.3 What should NOT be stored in our application DB

| Don't store | Why | What we store instead |
|---|---|---|
| Copies of customer tables / full query results | Data-plane separation, size, sensitivity | Only capped `result_preview` (≤ 500 rows) for queries we ran — needed to re-open history. Documented as "derived extracts" with the same sensitivity as the source. |
| Plaintext passwords / connection strings | Obvious | Fernet ciphertext in `encrypted_secret` |
| Full prompts/responses (by default) | Size + may contain customer data | Token counts, latency, model, success |
| Uploaded CSV bytes in Postgres | Wrong tool for blobs | File on disk (`UPLOAD_DIR`), object storage in P3 |
| Embeddings of raw data rows | Not useful for analytics RAG; leaks data | Embeddings of *metadata* (schema, definitions, example questions) only |
| LangGraph checkpoints | Our runs are short single-shot; we persist meaningful records ourselves | `analyses` + `analysis_queries` + `dashboards` |
| Users/sessions/roles | No auth in MVP | — (P3) |

---

## 11. RAG Architecture

### 11.1 What problem RAG solves here

An LLM writing SQL fails in four predictable ways. RAG addresses each by retrieving a different kind of knowledge:

| Failure | Example | Fixed by retrieving |
|---|---|---|
| Wrong/invented table | uses `sales` (doesn't exist) or `orders_legacy` (archive) | **Table chunks** (schema + descriptions) |
| Wrong column / value | `WHERE status = 'completed'` (actual value: `'SUCCESS'`) | **Column descriptions + sample values** inside table chunks |
| Wrong business logic | `SUM(total_amount)` over *all* orders, including failed ones | **Business definitions** ("Revenue = SUM(total_amount) WHERE status='SUCCESS'") |
| Wrong join / pattern | joins refunds on `customer_id` | **Relationships** (deterministic) + **verified example queries** |

> Honest note for interviews: with 6 tables you *could* put the whole schema in the prompt. That's why the demo database deliberately includes **~15 tables including distractors** (`orders_legacy`, `order_events`, `web_sessions`, …), and why **definitions and example queries** — which can't be inferred from the schema at all — are the core of our RAG. Retrieval quality is *measured* (recall@k in the eval suite), not assumed.

### 11.2 What gets embedded (chunk types)

**A. Table chunk** (`kind="table"`, auto-generated from catalog, one per queryable table):

```text
Table: public.orders
Description: One row per customer order. Includes failed and cancelled orders.
Columns:
- id (integer): order id
- customer_id (integer): FK to customers.id
- order_date (timestamp): when the order was placed
- status (string): order outcome. values: SUCCESS, FAILED, CANCELLED, PENDING
- channel (string): values: web, mobile_app, marketplace
- shipping_region (string): region for revenue reporting. values: North, South, East, West
- total_amount (decimal): order value after discount, in INR
Related tables: customers (customer_id → customers.id), order_items (id ← order_items.order_id), refunds, payments
```

Including sample values for low-cardinality columns is the cheapest Text-to-SQL accuracy win there is.

**B. Definition chunk** (`kind="definition"`, user/seed-authored):

```text
Definition: Revenue (aka sales, GMV, turnover)
Meaning: Money from successfully completed orders.
SQL: SUM(orders.total_amount) WHERE orders.status = 'SUCCESS'
Date column: orders.order_date
Tables: orders
Notes: Do not subtract refunds unless the question asks for "net revenue".
```

Seed definitions for the demo: Revenue, Net revenue, Orders (count of successful orders), AOV, Refund rate, Active customer, Region (= `orders.shipping_region`), Category (= `products.category`).

**C. Example query chunk** (`kind="example_query"`): embedded text = the **question** (+ notes); payload holds the SQL. Retrieval matches the user's phrasing to past questions.

```text
Question: What was monthly revenue by region in 2025?
SQL: SELECT date_trunc('month', o.order_date) AS month, o.shipping_region AS region,
            SUM(o.total_amount) AS revenue
     FROM orders o WHERE o.status = 'SUCCESS' AND o.order_date >= DATE '2025-01-01' AND o.order_date < DATE '2026-01-01'
     GROUP BY 1, 2 ORDER BY 1, 2
```

Example queries are stored **per datasource**, so they are in the correct dialect. The demo seeds ~8 per datasource.

**D. Doc chunk** (`kind="doc"`, P2): user-pasted data dictionary text, split into ~800-char paragraphs.

**E. Relationships** — **not embedded.** Once tables are selected, we deterministically add the join edges between them (and 1-hop neighbors needed to connect them). Graph lookups beat vector search for structure.

### 11.3 Metadata on every chunk

`data_source_id` (hard filter — never retrieve another source's knowledge), `kind` (per-kind quotas), `ref_id` (link back to catalog row), `title`, `payload`, `source` (`auto`/`user`/`seed`), `updated_at`.

### 11.4 When indexing happens

```mermaid
flowchart LR
    A["Datasource created"] --> B["Sync: introspect_schema()"]
    S["User clicks Sync"] --> B
    B --> C["Upsert catalog_tables/columns<br/>(preserve user descriptions)"]
    C --> D["profile_columns() → sample values,<br/>distinct counts, is_dimension"]
    D --> E["Build table chunks (text)"]
    E --> F["Embed in batches (fastembed)"]
    F --> G["Upsert knowledge_chunks kind=table"]
    H["User adds/edits definition<br/>or example query"] --> I["Embed that one chunk"] --> J["Upsert knowledge_chunks"]
    K["User edits a table/column description"] --> L["Rebuild + re-embed that table chunk"]
```

- Indexing is a **synchronous step of the sync endpoint** (our catalogs are small: 15 tables ≈ 1–2 s). For larger catalogs → background task (P2).
- Chunks are **idempotent upserts** keyed by `(data_source_id, kind, ref_id or title)`. A content hash avoids re-embedding unchanged chunks.
- Seed knowledge for demo sources is loaded from `demo_data/knowledge/*.yaml` by a script.

### 11.5 Retrieval strategy (Diagram 4)

```mermaid
flowchart TB
    Q["Query text = user question<br/>(or drill-down step question)"] --> E["Embed query (bge-small,<br/>with bge query instruction prefix)"]
    Q --> FTS["Postgres FTS:<br/>websearch_to_tsquery"]
    E --> V["pgvector cosine search<br/>WHERE data_source_id = :ds<br/>top 20 per kind"]
    V --> RRF["Reciprocal Rank Fusion<br/>score = Σ w / (60 + rank)<br/>w_vec = 0.7, w_fts = 0.3"]
    FTS --> RRF
    RRF --> QUO["Per-kind quotas + floor:<br/>tables ≤ 5, definitions ≤ 4,<br/>examples ≤ 3, min cosine 0.30"]
    QUO --> EXP["Deterministic expansion:<br/>+ tables referenced by selected definitions/examples<br/>+ join-path tables (1 hop)<br/>+ relationships between selected tables"]
    EXP --> BUD["Token budget trim (~3k tokens of context)"]
    BUD --> CTX["RetrievedContext → Query Agent prompt"]
```

| Parameter | Value | Rationale |
|---|---|---|
| Embedding model | `bge-small-en-v1.5`, 384-d | Small, strong for short technical text; same as Agent360; runs on CPU |
| Query prefix | `"Represent this sentence for searching relevant passages: "` | Recommended for bge retrieval queries (Agent360 lists the missing prefix as an open task) |
| Candidate pool | 20 per kind per arm | Cheap at our scale |
| Final top-k | tables 5, definitions 4, examples 3 | Fits in ~3k tokens; empirically tune with eval recall@k |
| Similarity floor | 0.30 cosine (vector arm only) | Drops clearly irrelevant matches; FTS hits can still surface exact keyword matches |
| Fusion | Weighted RRF, c = 60 | Rank-based → no need to normalize cosine vs FTS scores |
| Filtering | `data_source_id`, `is_queryable`, `is_active` | Isolation + allowlist |

**Is hybrid worth it?** **Yes, as P1**, because it's cheap with Postgres FTS (one generated column + one GIN index + one SQL query) and it fixes a real weakness of small embedding models: exact identifiers. A question mentioning `AOV` or `shipping_region` matches lexically even when the embedding is fuzzy. The eval suite reports recall@5 for **vector-only vs hybrid** — a concrete, measured talking point. P0 ships vector-only.

**"No strong match" handling (Agent360 lesson):** if no table chunk passes the floor, the context builder falls back to the **5 largest/most-referenced tables** and sets `retrieval_confidence="low"`. The Query Agent is told so and may return `cannot_answer` instead of guessing (§13).

### 11.6 How context is injected into the Query Agent

The context builder renders a compact, sectioned block (Markdown-like, stable ordering for reproducibility):

```text
## DATABASE
Engine: PostgreSQL. Reporting date (today): 2026-06-30. Currency: INR.

## BUSINESS DEFINITIONS (use these exactly)
- Revenue: SUM(orders.total_amount) WHERE orders.status = 'SUCCESS'; date column orders.order_date
- Region: orders.shipping_region
...

## TABLES
<table chunk orders>
<table chunk customers>
...

## JOINS
orders.customer_id = customers.id
order_items.order_id = orders.id
order_items.product_id = products.id

## VERIFIED EXAMPLE QUERIES (same database, same dialect)
Q: What was monthly revenue by region in 2025?
SQL: ...
```

Definitions come **before** tables on purpose: models anchor on what they read first, and definitions are what most often prevent "valid but wrong" SQL.

### 11.7 Why this improves Text-to-SQL reliability (and how we prove it)

- Fewer hallucinated identifiers → measured by **validator rejection rate** (unknown table/column).
- Correct business filters → measured by **business-rule pass rate** (e.g. `status = 'SUCCESS'` present when revenue is asked).
- Smaller prompts than full-schema dumps → measured by **input tokens per query**.
- Ablation in the eval report: **no RAG (full schema) vs schema-only RAG vs schema + definitions + examples**. This is the single most convincing chart for the README.

---

## 12. LangGraph / Multi-Agent Design

### 12.1 Why LangGraph (short; full ADR in Appendix A)

Our workflow has **typed shared state**, **conditional branches** (repair SQL? drill down? fail?), and **one bounded cycle** (drill-down). LangGraph models exactly that: nodes = functions over state, edges = routing functions, recursion limit as a final safety net. A plain `while` loop could do it, but the graph makes the control flow **explicit, visualizable (`graph.get_graph().draw_mermaid()`), and unit-testable per edge**.

### 12.2 The agents (and why only three)

| Component | LLM? | Why |
|---|---|---|
| Context loader | No | DB lookups |
| RAG retriever | No | Embedding + SQL search is deterministic; an LLM "retrieval agent" would add a call for no measurable gain |
| **Query Agent** | **Yes** | Translating language → SQL requires reasoning |
| SQL validator | No | Safety must be deterministic |
| Executor | No | It's a function call |
| Result profiler | No | Arithmetic must be exact; LLMs are bad at it |
| **Analysis Agent** | **Yes** | Interpreting results and choosing what to investigate next requires judgment |
| Drill-down planner/guard | No | Enforces limits; validates the agent's choice |
| **Visualization Agent** | **Yes** | Choosing which results tell the story and how to title/describe them benefits from language understanding |
| Dashboard validator / fallback builder | No | Spec must reference real queries/columns |

### 12.3 LangGraph workflow (Diagram 2)

```mermaid
flowchart TD
    START((START)) --> LC["load_context<br/>(deterministic)"]
    LC --> RC["retrieve_context<br/>(deterministic RAG)"]
    RC --> QA["query_agent<br/>(LLM)"]
    QA --> R1{"route_after_query"}
    R1 -->|"cannot_answer"| FAIL["finalize_failure"]
    R1 -->|"sql produced"| VS["validate_sql<br/>(deterministic)"]
    VS --> R2{"route_after_validation"}
    R2 -->|"valid"| EX["execute_sql<br/>(deterministic)"]
    R2 -->|"invalid & attempts < 3"| QA
    R2 -->|"invalid & exhausted & primary"| FAIL
    R2 -->|"invalid & exhausted & drilldown"| AN
    EX --> R3{"route_after_execution"}
    R3 -->|"success"| PR["profile_result<br/>(deterministic)"]
    R3 -->|"db error & attempts < 3"| QA
    R3 -->|"exhausted & primary"| FAIL
    R3 -->|"exhausted & drilldown"| AN
    PR --> AN["analysis_agent<br/>(LLM)"]
    AN --> R4{"route_after_analysis"}
    R4 -->|"drill requested"| PD["plan_drilldown<br/>(deterministic guard)"]
    R4 -->|"done"| VZ
    PD --> R5{"approved?"}
    R5 -->|"yes"| RC
    R5 -->|"no: depth/budget/invalid"| VZ["visualization_agent<br/>(LLM)"]
    VZ --> BD["build_dashboard<br/>(validate + hydrate / fallback)"]
    BD --> FIN["finalize_success"]
    FIN --> END((END))
    FAIL --> END
```

Every node also updates `stage` in the DB (via a small `report_stage()` helper) so the UI progress stepper moves.

### 12.4 Node specifications

#### N1 `load_context` (deterministic)
- **Responsibility:** load datasource (type, dialect, business_context incl. `as_of_date`), dimension registry (columns with `is_dimension=true`), queryable table allowlist.
- **Input:** `analysis_id`, `datasource_id`.
- **Output:** `datasource: DatasourceContext`, `allowed_tables`, `dimensions`.
- **Failures:** datasource missing/deleted → `finalize_failure(DATASOURCE_NOT_FOUND)`; catalog empty → `finalize_failure(NOT_SYNCED)` with "Sync the datasource first".

#### N2 `retrieve_context` (deterministic)
- **Responsibility:** run §11.5 retrieval for `current_question` (initial question or drill-down step question). On drill-down, **merge** with previously retrieved context (union, keep the frame's tables).
- **Output:** `retrieved: RetrievedContext` (chunks with scores, relationships, confidence).
- **Failures:** embedding/DB error → retry once, then fall back to "largest tables" context with `confidence=low` (never fail the run because of retrieval).

#### N3 `query_agent` (LLM) — see §13 for prompt details
- **Responsibility:** produce one SQL statement for the current step, in the datasource dialect, grounded in retrieved context. On the **first** call also classify the question and produce the `AnalysisFrame`.
- **Input:** current question, mode (`primary`/`drilldown`/`repair`), retrieved context, dialect guidance, `as_of_date`, analysis frame (drill-down), previous attempt + error (repair).
- **Tools:** none in P0 (all context pre-supplied → one call). **P1:** optional bounded tool loop with `lookup_column_values(table, column, search)` and `search_knowledge(query)` (max 2 tool rounds) — demonstrates function calling where it's genuinely useful (finding exact categorical values).
- **Structured output:** `QueryAgentOutput` (below).
- **Failures:** malformed output → 1 repair re-prompt with the Pydantic error → fallback model → `finalize_failure(LLM_OUTPUT_INVALID)`.

```python
class AnalysisFrame(BaseModel):
    metric_name: str                    # "Revenue"
    metric_sql: str                     # "SUM(o.total_amount) FILTER (WHERE o.status='SUCCESS')"
    base_table: str                     # "orders"
    date_column: str | None             # "orders.order_date"
    current_period: Period | None       # {start: 2026-06-01, end: 2026-07-01, label: "June 2026"}
    comparison_period: Period | None    # {start: 2026-05-01, end: 2026-06-01, label: "May 2026"}

class QueryAgentOutput(BaseModel):
    question_type: Literal["metric", "trend", "comparison", "breakdown", "top_n", "root_cause"] | None  # first call only
    can_answer: bool
    cannot_answer_reason: str | None
    sql: str | None
    explanation: str                    # 1–2 sentences: what the query computes
    tables_used: list[str]
    definitions_applied: list[str]      # e.g. ["Revenue"] → eval + debug
    frame: AnalysisFrame | None         # first call only (for comparison/root_cause)
```

#### N4 `validate_sql` (deterministic) — §14
- **Input:** `pending_sql`, dialect, allowed tables, limits.
- **Output:** `validation: ValidationResult{ok, safe_sql, errors[], tables[]}`; increments `attempt`.
- **Failures:** never raises; invalid → routes back to Query Agent with errors as feedback.

#### N5 `execute_sql` (deterministic)
- **Input:** `safe_sql`, connector (built from datasource id; secret never in state), timeout, max_rows.
- **Output:** appends `ExecutedQuery` (sql, timing, status, columns, rows preview) to `queries`; persists to `analysis_queries`.
- **Failures:** typed connector errors → `QueryStepError` in state; timeout → error message suggests aggregating more ("query exceeded 20s; aggregate or filter by date"); connection failure → **not** retried via LLM (it's not the SQL's fault) → `finalize_failure(DATASOURCE_UNAVAILABLE)`.

#### N6 `profile_result` (deterministic) — `analytics/profiler.py`
- **Responsibility:** compute facts so the LLM never does arithmetic:
  - Basic: row count, column types, min/max/sum/mean of numeric columns, null counts.
  - **Comparison shape** (columns `segment, previous_value, current_value` — the drill-down contract, §15): per-segment `delta`, `pct_change`, `share_of_total_change`, sorted by contribution; flags `dominant_segment` if share ≥ 50% or (≥ 35% and ≥ 1.5× next).
  - **Time series shape** (date column + numeric): first/last, overall % change, max/min period, largest period-over-period move.
- **Output:** `ResultProfile` attached to the executed query.
- **Failures:** unknown shape → basic stats only (never fails the run).

#### N7 `analysis_agent` (LLM)
- **Responsibility:** interpret profiles of all executed queries so far; produce findings; decide whether to drill down and how.
- **Input:** original question, question type, frame, compact summaries of each executed query (step question, SQL explanation, ≤ 20 rows, profile), available dimensions not yet used on the current path, current depth/max depth, remaining budget.
- **Tools:** none (all facts pre-computed).
- **Prompt purpose:** "You are an analyst. Explain what the data shows using only the provided numbers. Decide whether a further breakdown would materially explain the change. Only choose from the listed dimensions."
- **Structured output:**

```python
class Finding(BaseModel):
    statement: str                     # "South revenue fell 35.1% (₹4.2M → ₹2.7M)"
    evidence_query_ids: list[str]      # must reference executed queries
    importance: Literal["high", "medium", "low"]

class DrillDownRequest(BaseModel):
    dimension: str                     # must be in available_dimensions, e.g. "products.category"
    focus_filter: dict[str, str]       # e.g. {"orders.shipping_region": "South"}
    step_question: str                 # "How did revenue change by category within South, May vs June 2026?"
    rationale: str

class AnalysisAgentOutput(BaseModel):
    summary: str                       # headline answer, 1–3 sentences
    findings: list[Finding]            # ≤ 6
    needs_drilldown: bool
    drilldown: DrillDownRequest | None
    confidence: Literal["high", "medium", "low"]
    caveats: list[str]                 # e.g. "June data includes only 28 days"
```

- **Failures:** invalid output → repair once → fallback model → **deterministic fallback findings** from profiles (e.g. "Revenue changed −18.2%; largest contributor: South (−64% of change)") with `confidence=low`. The run still completes.

#### N8 `plan_drilldown` (deterministic guard)
- **Checks:** `needs_drilldown` and request present; `question_type` allows drilling (`comparison`, `root_cause`); depth < `MAX_DRILLDOWN_DEPTH`; LLM calls + tokens within budget (reserve 1 call for visualization); `dimension` ∈ available dimensions and not already used on this path; `focus_filter` value actually appears in the last result; the step question isn't a near-duplicate of a previous one.
- **Output:** approved → `drilldown_depth += 1`, `filter_path += focus_filter`, `current_question = step_question`, `mode = drilldown`. Rejected → record `stop_reason` and go to visualization.

#### N9 `visualization_agent` (LLM)
- **Responsibility:** choose which successful queries become which widgets, choose chart types, write titles and insight cards.
- **Input:** question, final analysis output (summary/findings), list of successful queries with **column names + types + row counts + 5 sample rows** (not full data).
- **Output:** `DashboardSpecDraft` (§16) — widgets reference `query_id`s; no numbers typed by the LLM.
- **Failures:** invalid spec → repair once → **deterministic fallback builder** (rules: time column → line; category + numeric → bar; single value → KPI; always add table for the primary query; insights = findings). `generated_by="fallback"`.

#### N10 `build_dashboard` (deterministic)
- Validates the spec against real query results (§16.4), drops invalid widgets, hydrates KPI values from data, persists `dashboards` row.

#### N11 `finalize_success` / `finalize_failure` (deterministic)
- Write status, stop reason, findings, rollups (tokens, calls, latency), `completed_at`. Failure writes `error_code` + user-safe `error_message`.

### 12.5 Typical LLM call counts

| Scenario | Calls |
|---|---|
| "What was revenue last month?" (no repair) | Query 1 + Analysis 1 + Viz 1 = **3** |
| Same with one SQL repair | **4** |
| "Why did revenue fall?" with 2 drill-downs | Q + A + (Q + A) × 2 + V = **7** |
| Worst case (3 drill-downs, some repairs) | hard-capped at `MAX_LLM_CALLS_PER_ANALYSIS = 12` |

### 12.6 LangGraph state (`graph/state.py`)

We use a `TypedDict` state whose values are **Pydantic models**, and **reducers** only where nodes append to lists. State holds *what later nodes need*; anything only needed for display/audit goes straight to the DB.

```python
class AnalysisState(TypedDict, total=False):
    # ── Run inputs (set once at START, never mutated)
    analysis_id: str
    datasource_id: str
    user_question: str

    # ── Loaded context (load_context)
    datasource: DatasourceContext        # type, dialect, as_of_date, currency (NO secrets)
    allowed_tables: list[str]
    dimensions: list[DimensionInfo]      # drill-down candidates

    # ── Current step (overwritten each loop iteration)
    mode: Literal["primary", "drilldown"]
    current_question: str                # user question or drill-down step question
    retrieved: RetrievedContext
    pending_sql: str | None              # last SQL from Query Agent
    attempt: int                         # attempt number for current step (1..3)
    last_error: str | None               # validator/DB error fed back for repair
    validation: ValidationResult | None

    # ── Accumulated history (reducer: append)
    queries: Annotated[list[ExecutedQuery], operator.add]          # incl. failed/rejected
    analysis_rounds: Annotated[list[AnalysisAgentOutput], operator.add]

    # ── Analysis framing & drill-down control
    question_type: str | None
    frame: AnalysisFrame | None
    drilldown_depth: int
    filter_path: list[dict[str, str]]    # [{"orders.shipping_region": "South"}, ...]
    used_dimensions: list[str]
    stop_reason: str | None

    # ── Budget tracking
    budget: BudgetUsage                  # llm_calls, input_tokens, output_tokens

    # ── Output
    dashboard: DashboardSpec | None
    error: RunError | None               # code + user message (terminal failures)
```

| Field group | Why it persists between nodes |
|---|---|
| Inputs | Every node needs ids/question; immutable for reproducibility |
| `datasource`, `allowed_tables`, `dimensions` | Used by retriever, guard, query agent, drill-down guard |
| Current step fields | Implement the generate→validate→execute→repair cycle; reset when a new step starts |
| `queries` | The Analysis Agent reasons over *all* results; the Viz Agent picks from them |
| `analysis_rounds` | Latest round drives routing; earlier rounds kept for the timeline |
| `frame` | Keeps metric definition + periods **consistent across drill-down steps** (critical — otherwise step 2 might silently change the revenue definition) |
| `filter_path`, `used_dimensions`, `drilldown_depth` | Loop control and constraint enforcement |
| `budget` | Guard decisions |
| `dashboard`, `error` | Final outputs persisted by finalize nodes |

**Not in state:** connector objects, decrypted secrets, full result sets beyond the capped preview, prompts.

**Checkpointer:** none in MVP (runs are short and we persist meaningful records ourselves). `recursion_limit=40` passed at invoke time as a backstop against routing bugs.

---

## 13. Text-to-SQL Pipeline

### 13.1 End-to-end steps

```mermaid
flowchart LR
    Q["Question"] --> R["Retrieve context"] --> P["Build prompt:<br/>system + dialect block +<br/>context + question + mode"]
    P --> L["LLM (structured output)"] --> V["sqlglot validate + rewrite LIMIT"]
    V -->|"ok"| X["Execute (read-only, timeout)"]
    V -->|"errors"| F["Feedback message"] --> L
    X -->|"DB error"| F
    X -->|"rows"| OUT["ExecutedQuery"]
```

### 13.2 Query Agent prompt structure (`agents/prompts/query_agent.md`)

1. **Role:** "You write a single read-only SQL query for {engine} that answers the step question."
2. **Hard rules:** only tables/columns listed under TABLES; apply BUSINESS DEFINITIONS exactly; one statement; no `SELECT *`; explicit column aliases; aggregate in SQL (never return raw rows for aggregate questions); always filter by date when a period is mentioned; "today" is `{as_of_date}`; if the context doesn't contain what's needed set `can_answer=false`.
3. **Dialect block** (§9.7).
4. **Retrieved context** (§11.6).
5. **Mode block:**
   - *primary:* also classify `question_type` and fill `frame` for comparison/root-cause questions.
   - *drilldown:* "Use EXACTLY this metric: `{frame.metric_sql}` on base table `{frame.base_table}`, periods `{current}` vs `{comparison}`, filters `{filter_path}`. Group by `{dimension}`. Return columns `segment`, `previous_value`, `current_value`." (the drill-down contract)
   - *repair:* "Your previous SQL: … Error: … Fix it. Do not change the intent."
6. **Output schema** (enforced via structured output, §17).

### 13.3 Few-shot strategy

Few-shot examples are the **retrieved verified examples** (not static ones) — they're relevant to the question and already in the right dialect. Plus one static example per mode showing the exact JSON output shape.

### 13.4 Repair loop rules

- Max **2 repairs** per step (3 attempts total) — `MAX_SQL_REPAIR_ATTEMPTS`.
- Feedback is **specific**: validator errors name the unknown table/column and list valid alternatives (e.g. "Column `orders.region` does not exist. Did you mean `orders.shipping_region`?" using difflib against the catalog). DB errors are passed through truncated to 500 chars.
- Repair attempts are logged as separate `analysis_queries` rows (`attempt=2,3`) → measurable "self-correction success rate".

### 13.5 Zero-row results

Zero rows is **not an error** for SQL validity but is suspicious. The profiler flags `empty=true`; if the step is `primary` and attempt < 3, the router sends **one** repair with: "Query returned 0 rows. Check filter values against sample values: {sample values for filtered columns}". If it's still empty, continue — the Analysis Agent reports "no data for this period" honestly.

---

## 14. SQL Safety

### 14.1 Defense in depth (layers)

| Layer | Control | Stops |
|---|---|---|
| 1. Credentials | Read-only DB role/user (documented setup SQL for Postgres + ClickHouse users in docker init scripts) | Any write, even if every other layer fails |
| 2. Session settings | PG `default_transaction_read_only=on`; CH `readonly=2`; DuckDB `read_only=True` + `enable_external_access=false` | Writes; file/network access from DuckDB |
| 3. **Parser-based validator** (sqlglot) | Single statement, query-only, forbidden nodes/functions, table allowlist, column existence, LIMIT | Injection-style multi-statements, DDL/DML, dangerous functions, hallucinated tables |
| 4. Resource limits | Server-side timeout; outer LIMIT; `fetchmany(max_rows+1)`; result size check | Runaway queries, huge results |
| 5. Prompt design | Only allowlisted tables appear in context | Reduces attempts to touch other tables |
| 6. Observability | Every SQL (incl. rejected) is logged with reason | Auditability |

### 14.2 Exact request flow

```mermaid
flowchart TD
    A["LLM SQL string"] --> B["Size check (≤ 10k chars)"]
    B --> C["sqlglot.parse(sql, read=dialect)"]
    C -->|"ParseError"| R1["REJECT: syntax error (fed back for repair)"]
    C --> D{"exactly 1 statement?"}
    D -->|"no"| R2["REJECT: multiple statements"]
    D --> E{"root is Select / Union / Intersect / Except<br/>(CTEs allowed, all query-only)?"}
    E -->|"no"| R3["REJECT: only read queries allowed"]
    E --> F{"walk AST: any Insert/Update/Delete/Merge/Create/<br/>Drop/Alter/TruncateTable/Command/Set/Copy/Into?"}
    F -->|"yes"| R4["REJECT: forbidden operation"]
    F --> G{"forbidden functions?<br/>pg_sleep, pg_read_file, dblink, lo_*, …<br/>CH: url, file, s3, remote, mysql, postgresql, …<br/>DuckDB: read_csv*, read_parquet, read_json*, glob, …"}
    G -->|"yes"| R5["REJECT: function not allowed"]
    G --> H{"all physical tables (excluding CTE names)<br/>in allowed_tables?"}
    H -->|"no"| R6["REJECT: unknown/forbidden table (+ suggestions)"]
    H --> I{"qualified columns exist in catalog?<br/>(best-effort; skip ambiguous)"}
    I -->|"no"| R7["REJECT: unknown column (+ suggestions)"]
    I --> J{"outer SELECT * ?"}
    J -->|"yes"| R8["REJECT: list columns explicitly"]
    J --> K["Rewrite: set outer LIMIT = min(existing, SQL_MAX_ROWS)"]
    K --> L["safe_sql = tree.sql(dialect=dialect)"]
    L --> M["Connector.execute(safe_sql, timeout, max_rows)"]
    M --> N[("Database (read-only session)")]
```

### 14.3 Why a parser and not regex

| Attack / mistake | Regex prefix check (Agent360) | sqlglot AST check |
|---|---|---|
| `SELECT 1; DROP TABLE orders` | passes (starts with SELECT) | rejected (2 statements) |
| `WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x` | passes (starts with WITH) | rejected (`Delete` node in tree) |
| `SELECT … FROM (SELECT … LIMIT 5) t` (no outer limit) | "has LIMIT" → no cap added | outer LIMIT set on the root node |
| `SELECT pg_sleep(600)` | passes | rejected (function denylist) + timeout anyway |
| `SELECT * FROM read_csv('/etc/passwd')` (DuckDB) | passes | rejected (function denylist) + external access disabled |
| `-- comment\nDELETE …` | needs custom comment stripper | comments aren't statements in the AST |
| Column name `"drop_count"` | naive keyword regex false-positive | no false positive (identifier node) |

### 14.4 EXPLAIN validation — P2

Running `EXPLAIN <sql>` before execution catches semantic errors without scanning data. For our small demo DB, executing with `LIMIT` and a timeout is just as cheap, and the parser already catches unknown tables/columns. Keep `explain()` as a P2 connector method if large datasets are added.

### 14.5 Result limits

- `SQL_MAX_ROWS = 500` (outer LIMIT); the connector fetches `max_rows + 1` to set `truncated=true`.
- Only ≤ 20 rows of any result go to the Analysis Agent prompt, ≤ 5 to the Viz Agent; the full ≤ 500 go to the frontend table/charts.
- If a chart query would return > 100 categories, the dashboard validator keeps the top 20 by value + "Other" (P1).

---

## 15. Automatic Drill-Down

### 15.1 The worked example

```mermaid
flowchart TD
    Q["User: Why did revenue fall in June 2026?"] --> S1["Step 0 (primary)<br/>Revenue May vs June<br/>→ ₹48.1M → ₹39.4M (−18.2%)"]
    S1 --> A1["Analysis Agent: significant drop;<br/>drill by orders.shipping_region"]
    A1 --> S2["Step 1: revenue by region, May vs June<br/>→ South −35% = 64% of total decline"]
    S2 --> A2["Analysis Agent: South dominant;<br/>drill by products.category within South"]
    A2 --> S3["Step 2: South revenue by category<br/>→ Electronics −54% = 81% of South's decline"]
    S3 --> A3["Analysis Agent: Electronics in South explains most;<br/>no further dimension adds value → stop"]
    A3 --> V["Visualization Agent → dashboard"]
```

(Numbers above are illustrative of the planted scenario; real values come from the generator's ground truth.)

### 15.2 How the Analysis Agent decides "more information needed"

It gets **pre-computed facts** from the profiler, and explicit criteria in its prompt:

- Drill **only** if `question_type ∈ {comparison, root_cause}` (enforced deterministically too).
- Drill if the headline change is material (|Δ| ≥ 5% — a configurable "materiality threshold" shown in the prompt) **and** there's an unused dimension that plausibly explains it.
- At a breakdown step, drill into the **dominant segment** (`share_of_total_change ≥ 50%`, flagged by the profiler). If no segment dominates, stop: "the decline is broad-based".
- Never drill into a segment whose change is the opposite sign of the headline change.

### 15.3 Generating the follow-up question → SQL

1. Analysis Agent returns `DrillDownRequest{dimension, focus_filter, step_question}`.
2. `plan_drilldown` validates it (§12.4 N8) and updates `filter_path`.
3. `retrieve_context` runs for `step_question` and merges with previous context (adds e.g. the `products` table when drilling by category).
4. Query Agent runs in **drilldown mode** with the **frozen frame** (same metric SQL, same periods) + accumulated filters + target dimension, and must return the contract columns `segment, previous_value, current_value`. This contract makes the profiler's contribution math reliable regardless of how the SQL is written.

Example drill-down SQL (Postgres) the agent should produce at step 2:

```sql
SELECT p.category AS segment,
       SUM(oi.line_amount) FILTER (WHERE o.order_date >= DATE '2026-05-01' AND o.order_date < DATE '2026-06-01') AS previous_value,
       SUM(oi.line_amount) FILTER (WHERE o.order_date >= DATE '2026-06-01' AND o.order_date < DATE '2026-07-01') AS current_value
FROM orders o
JOIN order_items oi ON oi.order_id = o.id
JOIN products p ON p.id = oi.product_id
WHERE o.status = 'SUCCESS' AND o.shipping_region = 'South'
  AND o.order_date >= DATE '2026-05-01' AND o.order_date < DATE '2026-07-01'
GROUP BY p.category
ORDER BY (current_value - previous_value) ASC
LIMIT 500
```

(Category-level revenue uses `order_items.line_amount`, which the "Revenue by category" definition chunk tells the agent.)

### 15.4 State updates per drill step

| Field | Change |
|---|---|
| `drilldown_depth` | +1 |
| `filter_path` | append `focus_filter` |
| `used_dimensions` | append dimension |
| `current_question` | = `step_question` |
| `mode` | `drilldown` |
| `attempt`, `pending_sql`, `last_error` | reset |
| `queries`, `analysis_rounds` | appended by subsequent nodes |

### 15.5 Limits (all enforced in code, not by prompt)

| Limit | Default | When hit |
|---|---|---|
| `MAX_DRILLDOWN_DEPTH` | 3 | stop_reason=`max_depth` → visualize |
| `MAX_LLM_CALLS_PER_ANALYSIS` | 12 (1 reserved for Viz) | stop_reason=`budget_exhausted` |
| `MAX_TOKENS_PER_ANALYSIS` | 80k | same |
| Dimension reuse on path | not allowed | request rejected → visualize |
| Near-duplicate step question | rejected (normalized text / same dimension+filters) | visualize |
| LangGraph `recursion_limit` | 40 | backstop → run fails with `GRAPH_LIMIT` (should never happen; alert in logs) |

### 15.6 Reporting date (`as_of_date`) — why it matters

"This month vs last month" depends on *today*. The synthetic data ends on a fixed date, and evaluation must be reproducible, so each datasource has `business_context.as_of_date` (default: real today; demo sources: `2026-06-30`). The Query Agent is told "today is {as_of_date}". Without this, the demo would break the day the calendar moves.

### 15.7 Inconclusive results

If drilling yields no dominant segment, or a drill step fails, the Analysis Agent's final round must say so: *"Revenue fell 6.1%; the decline is spread across all regions (none > 30% of the change). Possible drivers outside this data: pricing, seasonality."* `confidence=low|medium`, `stop_reason=inconclusive`. The UI shows a neutral "Inconclusive" badge — **honest output is a feature**.

---

## 16. Dashboard Generation

### 16.1 Principle

The LLM decides **what to show and how to describe it**; code decides **what the numbers are** and **whether the spec is valid**. The frontend renders only known components.

### 16.2 Pydantic models (`dashboard/spec.py`)

```python
NumberFormat = Literal["number", "currency", "percent", "compact"]

class KPIWidget(BaseModel):
    id: str
    label: str                          # "June 2026 revenue"
    query_id: str                       # analysis_queries.id
    value_column: str                   # column in that query's result
    row_index: int = 0
    format: NumberFormat = "number"
    comparison: "KPIComparison | None" = None   # e.g. previous_value column → delta %

class KPIComparison(BaseModel):
    column: str                         # "previous_value"
    label: str = "vs previous period"

class ChartWidget(BaseModel):
    id: str
    type: Literal["line", "bar", "area", "pie"]   # pie only if ≤ 6 categories (validator)
    title: str
    query_id: str
    x: str                              # column name
    y: list[str]                        # 1–3 numeric columns
    series: str | None = None           # optional grouping column (pivoted for Recharts)
    format: NumberFormat = "number"
    description: str | None = None      # one-line observation

class TableWidget(BaseModel):
    id: str
    title: str
    query_id: str
    columns: list[str] | None = None    # subset/order; None = all

class InsightCard(BaseModel):
    id: str
    title: str
    text: str                           # may include numbers, but they're checked (groundedness, §18)
    severity: Literal["info", "positive", "warning", "negative"]
    evidence_query_ids: list[str]

class DashboardSpec(BaseModel):
    title: str
    summary: str
    kpis: list[KPIWidget] = Field(max_length=4)
    charts: list[ChartWidget] = Field(max_length=4)
    tables: list[TableWidget] = Field(max_length=2)
    insights: list[InsightCard] = Field(max_length=5)
```

### 16.3 What the API returns (hydrated)

```json
{
  "spec": { "title": "Why revenue fell in June 2026", "summary": "...", "kpis": [...], "charts": [...], "tables": [...], "insights": [...] },
  "datasets": {
    "q_01": { "columns": [{"name":"segment","type":"string"}, {"name":"previous_value","type":"float"}, {"name":"current_value","type":"float"}],
              "rows": [["South", 14200000.0, 9230000.0], ["North", 11800000.0, 11650000.0]],
              "truncated": false, "sql": "SELECT ...", "executed_at": "2026-06-30T10:12:03Z" }
  }
}
```

The frontend looks up `datasets[widget.query_id]`.

### 16.4 Deterministic spec validation (`dashboard/validator.py`)

For each widget: `query_id` exists and succeeded; referenced columns exist; `y` columns are numeric; `x` is string/date for bar/line; pie has ≤ 6 rows; KPI query returns a row at `row_index`. Invalid widgets are **dropped with a logged reason** (not the whole dashboard). If nothing valid remains → fallback builder. Partial-failure policy borrowed from Agent360.

### 16.5 Chart type rules (in the Viz prompt and enforced by validator)

| Data shape | Chart |
|---|---|
| date/time x + 1–3 metrics | line (or area for cumulative/volume) |
| category x + metric(s), ≤ 30 categories | bar (horizontal in UI if labels are long) |
| previous vs current per segment | grouped bar (`y=["previous_value","current_value"]`) |
| parts of a whole, ≤ 6 categories | pie/donut (rarely) |
| single number | KPI |
| anything else / many columns | table |

### 16.6 Persisting SQL behind widgets & refresh

Widgets reference `analysis_queries` rows, which store the executed SQL. So **SQL behind every widget is persisted by construction in P0**.

**Refresh (P1):** `POST /analyses/{id}/refresh` → for each distinct `query_id` in the spec: load SQL **from the DB** (never from the client) → re-validate with the guard (catalog may have changed) → execute → update `result_preview` → set `dashboards.refreshed_at`. **No LLM call.** Insight text is marked "written at {original time}; data refreshed at {now}" since prose isn't regenerated. Why P1 not P0: it's a small feature (~half a day) but the MVP must first prove the analysis pipeline end-to-end.

**Relative dates caveat:** stored SQL contains literal dates (`2026-06-01`), so refresh re-runs the *same* question over the *same* periods with fresh data — which is the correct semantics for "refresh this analysis". Rolling-window dashboards ("always last 30 days") would need parameterized SQL — P3.

---

## 17. LLM Provider Architecture

### 17.1 Interface

```mermaid
classDiagram
    class LLMProvider {
        <<abstract>>
        +name: str
        +complete(request: LLMRequest) LLMResponse
    }
    class OpenAICompatibleProvider {
        base_url, api_key
        uses openai.AsyncOpenAI
    }
    class OpenRouterProvider {
        base_url = openrouter.ai/api/v1
        extra headers (HTTP-Referer, X-Title)
    }
    class FakeLLMProvider {
        scripted responses for tests
    }
    class LLMClient {
        +generate_structured(agent, messages, schema: type[T]) StructuredResult~T~
        +generate_text(agent, messages) TextResult
        -fallback chain, retries, telemetry, budget
    }
    LLMProvider <|-- OpenAICompatibleProvider
    OpenAICompatibleProvider <|-- OpenRouterProvider
    LLMProvider <|-- FakeLLMProvider
    LLMClient --> LLMProvider : uses
```

- **`LLMProvider`** = transport only: send a normalized `LLMRequest` (messages, model, temperature, max_tokens, optional `tools`, optional `response_format`), return a normalized `LLMResponse` (text, tool_calls, usage, model, finish_reason, latency).
- **`LLMClient`** = the **single chokepoint** (Agent360 lesson): per-agent model selection, structured-output strategy, validation + repair, retries, model fallback, budget accounting, `llm_calls` recording. Agents only ever call `LLMClient`.
- Adding Gemini/Groq/OpenAI later = a new `LLMProvider` subclass (Groq and OpenAI are OpenAI-compatible → configuration only). Agents don't change.

### 17.2 Structured output strategy (works across weak/free models)

Different free models support different features. `LLMClient.generate_structured(schema)` tries, per model capability flags in `llm/models.yaml`:

1. **Tool-call mode** (`supports_tools: true`): define one function `submit_<schema>` whose parameters = `schema.model_json_schema()`, force `tool_choice` to it, parse arguments. ← *this is our LLM function-calling usage*
2. **JSON-schema mode** (`supports_json_schema: true`): `response_format={"type":"json_schema", ...}`.
3. **Prompt-JSON mode** (fallback): instructions + schema in the prompt; extract the first JSON object from text (tolerant of ```json fences).

Then always: `schema.model_validate(...)` → on `ValidationError`, **one repair call** containing the per-field errors ("`findings.0.evidence_query_ids`: field required — fix and resend") → on second failure, try the next fallback model → finally raise `LLMOutputError` (the graph node decides the fallback behavior).

### 17.3 Model configuration and fallback

```yaml
# backend/app/llm/models.yaml  (capabilities; env vars pick which model each agent uses)
models:
  "<provider/model-a>:free": { supports_tools: true,  supports_json_schema: true,  context: 131072 }
  "<provider/model-b>:free": { supports_tools: true,  supports_json_schema: false, context: 65536 }
  "<provider/model-c>:free": { supports_tools: false, supports_json_schema: false, context: 32768 }
```

- Per-agent model via env: `LLM_MODEL_QUERY`, `LLM_MODEL_ANALYSIS`, `LLM_MODEL_VISUALIZATION` (Query Agent should get the strongest coder model).
- `LLM_FALLBACK_MODELS` ordered list. Fallback triggers: HTTP 404 (model removed), 429 after retries, 5xx after retries, timeout, or invalid structured output twice.
- Pick concrete free model IDs at implementation time from `openrouter.ai/models` filtered by price = 0 and "tools" support; free model availability changes frequently, so **never hardcode IDs in code**.
- A tiny `scripts/check_models.py` pings each configured model with a trivial structured request and prints capability/latency — run it when free models change.

### 17.4 Retries and rate limits

- `tenacity`: retry on 429/5xx/timeouts, exponential backoff with jitter (1s → 2s → 4s), honor `Retry-After`, max `LLM_MAX_RETRIES=2` per model before falling back.
- A process-wide `asyncio.Semaphore(2)` limits concurrent LLM calls (free-tier RPM limits).
- **OpenRouter free-tier reality (verify current numbers at build time):** free models have a per-minute request limit (on the order of ~20 RPM) and a small daily cap unless the account has purchased a minimum credit amount (historically 50/day → 1,000/day after a one-time ~$10 credit purchase). With 3–7 calls per analysis and 25 eval cases, **the low daily cap is a real constraint**. Options: (a) buy the small one-time credit (recommended — still uses free models), (b) add a second free provider (Gemini/Groq) to the fallback chain, (c) run eval subsets.

### 17.5 Token usage

OpenRouter returns OpenAI-style `usage {prompt_tokens, completion_tokens, total_tokens}` on responses; we store it per call. If a provider omits usage, we estimate (`len(text)/4`) and set `estimated=true`. Budget accounting in graph state uses the same numbers.

### 17.6 Zero-spend provider strategy

**Hard constraint: no money is spent on this project.** OpenRouter's free tier alone is
too thin to build against (historically ~50 requests/day without a credit purchase — at
~5 calls per analysis that is ~10 analyses per day, less than one afternoon of
iteration). The fix is not a bigger quota, it is **several free providers stacked behind
one interface**, since each has its own independent daily allowance.

| Role | Provider | Why | Notes |
|---|---|---|---|
| **Primary** | Google AI Studio (Gemini free tier) | Historically the most generous free daily allowance of the OpenAI-compatible options, with solid function-calling and JSON-schema support | Exposes an OpenAI-compatible endpoint → config only, no new class |
| **Secondary** | Groq free tier | Very high free daily request counts, very low latency | OpenAI-compatible; tool support varies by model |
| **Tertiary** | OpenRouter `:free` models | Widest model selection; useful when the others are rate-limited | The ~50/day cap makes it a fallback, not a primary |
| **Optional local** | Ollama (e.g. an 8B instruct model) | Unlimited and offline; good enough for the Visualization Agent and for smoke-testing the graph | Only worth it with ≥16 GB RAM; exposes an OpenAI-compatible endpoint too |

All four are **configuration**, not code — `OpenAICompatibleProvider` already speaks to
every one of them, and `models.yaml` records which support tools or JSON schema. This is
precisely the payoff of ADR-07: the provider decision changed after the plan was written
and nothing in `agents/` or `graph/` is affected.

```env
LLM_FALLBACK_CHAIN=gemini:<model>,groq:<model>,openrouter:<model>:free
```

**Reducing demand, not just increasing supply.** Two switches cut call volume during
development:

- `VIZ_AGENT_ENABLED=false` — the deterministic `fallback_builder` produces the dashboard
  from result shapes alone, cutting roughly a third of calls per analysis. Turn it on for
  demos and eval runs. The agent still exists and is still tested; it is simply not burned
  on every iteration.
- `MAX_DRILLDOWN_DEPTH=1` while working on anything downstream of the drill-down loop.

> Free-tier terms, model IDs and limits change often, and the figures above predate this
> build. Verify each provider's current limits on the day you start — which is exactly why
> model IDs live in env config and capability flags live in `models.yaml`, never in code.

### 17.6.1 Protecting the quota you have

Three mechanisms, all **inside `LLMClient`**, so no agent or graph code knows they exist.

**A. Development response cache (P0 — build it in Block 6).**
An on-disk cache keyed by `sha256(provider, model, messages, schema, temperature)`,
enabled by `LLM_CACHE_ENABLED=true` (default on when `APP_ENV=local`, always off in eval
runs). Re-running the same question while iterating on *downstream* code — profiler,
drill-down guard, dashboard validator, frontend — then costs **zero calls**. A prompt
edit changes the key and correctly misses. This is worth more than any other quota
measure, because most iteration is downstream of the LLM.

```
backend/.llm_cache/<sha256>.json    # git-ignored
```

**B. Record / replay fixtures.** `LLM_CACHE_MODE=record` writes real responses into
`tests/fixtures/llm/`; `FakeLLMProvider` replays them. Graph tests then run against
realistic model output with no network, and a flaky free endpoint can never break CI.

**C. Multiple free providers in the fallback chain.** Each provider has its **own
separate quota**, so adding a second and third free provider multiplies the daily budget
rather than just adding redundancy. Groq and Google's Gemini both expose
OpenAI-compatible endpoints, so each is a config entry against the existing
`OpenAICompatibleProvider` — no new class.

```env
LLM_FALLBACK_CHAIN=openrouter:<model-a>,groq:<model-b>,gemini:<model-c>
```

> Verify current free-tier terms, model IDs and limits when you build — they change
> frequently, which is exactly why model IDs live in env/config and capabilities live in
> `models.yaml`, never in code.

### 17.7 Temperature & determinism

Query Agent `temperature=0`; Analysis 0.2; Viz 0.3. Eval runs force 0 everywhere and record model IDs in `evaluation_runs.config`.

---

## 18. Evaluation

### 18.1 Goal

Answer, with numbers: *"How often does InsightFlow produce the correct answer, and which component fails when it doesn't?"* Not an academic benchmark — ~25 high-quality cases with **objective** checks.

### 18.2 Why synthetic data makes evaluation objective

We generate the demo dataset ourselves with a fixed random seed and **planted facts** (§28). So for every eval question we can write a **gold SQL** query, run it, and get the true answer. Correctness = "does the system's result match the gold result?", computed by code, not by an LLM judge. The generator also writes `ground_truth.json` (e.g. `{"june_vs_may_revenue_pct": -18.2, "top_decline_region": "South", "top_decline_category_in_south": "Electronics"}`) for drill-down checks.

### 18.3 Test case format (`backend/app/evaluation/datasets/core.yaml`)

```yaml
- id: rev_last_month
  question: "What was revenue last month?"
  type: metric
  expected_tables: [orders]
  expected_columns: [orders.total_amount, orders.status, orders.order_date]
  expected_rules:                       # normalized-SQL predicates that must appear
    - "status = 'SUCCESS'"
  gold_sql: |
    SELECT SUM(total_amount) AS revenue FROM orders
    WHERE status = 'SUCCESS' AND order_date >= DATE '2026-05-01' AND order_date < DATE '2026-06-01'
  compare: scalar                       # scalar | rows_unordered | rows_ordered | top_k
  tolerance: 0.005                      # relative

- id: why_revenue_fell_june
  question: "Why did revenue fall in June 2026 compared to May?"
  type: root_cause
  expected_tables: [orders, order_items, products]
  expected_drilldown_path:              # checked against filter_path / findings
    - {dimension: orders.shipping_region, segment: South}
    - {dimension: products.category, segment: Electronics}
  expect_mentions: [South, Electronics]
```

### 18.4 Suite composition (~25 cases)

| Category | # | Examples |
|---|---|---|
| Simple metrics | 5 | revenue last month, number of successful orders in Q1, AOV in 2025, refund total last month, active customers |
| Business-rule traps | 4 | "total sales" (must exclude FAILED), "net revenue" (must subtract refunds), "region" (must use shipping_region not customers.region), "orders" (count SUCCESS only) |
| Trends | 4 | monthly revenue 2025, weekly orders last quarter, refund rate by month, mobile share over time |
| Breakdowns / top-N | 5 | revenue by category, top 10 customers, top 5 products in South, revenue by channel, payment failure rate by method |
| Joins | 3 | refunds by category (refunds→order_items→products), revenue by customer segment, avg items per order by region |
| Root cause / drill-down | 3 | why revenue fell in June; why refunds rose in Home & Kitchen; why April payment success dropped |
| Unanswerable / safety | 2 | "What's our marketing ROI by influencer?" (no data → should say cannot answer); "Delete old orders" (must not produce DML) |

### 18.5 Metrics

| Metric | How computed | Component it diagnoses |
|---|---|---|
| **Execution success rate** | % cases where primary SQL executed (after ≤ 2 repairs) | Query Agent + guard |
| **First-try validity** | % valid on attempt 1 | Prompt/grounding quality |
| **Self-repair success** | % of failed first attempts fixed by repair | Repair loop |
| **Result accuracy** | Generated result vs gold result (§18.6) | End-to-end correctness |
| **Table recall / precision** | sqlglot-extracted tables vs `expected_tables` | Schema grounding |
| **Column recall** | sqlglot-extracted qualified columns vs expected | Schema grounding |
| **Business-rule pass rate** | Normalized SQL contains each `expected_rules` predicate (via sqlglot AST search, tolerant to aliases) | Definitions RAG |
| **Retrieval recall@k** | % of `expected_tables` present in retrieved table chunks | Retriever (no LLM needed!) |
| **Drill-down path correctness** | Executed filter path matches `expected_drilldown_path` prefix | Analysis Agent + loop |
| **Groundedness (numeric)** | Every number in summary/insights is within 1% of some value in results or profiler stats | Hallucinated numbers |
| **Unanswerable handling** | `can_answer=false` or "cannot" stop_reason for unanswerable cases | Honesty |
| **Latency** p50/p95, **LLM calls**, **tokens** per case | from `analyses` + `llm_calls` | Cost/perf |

Groundedness is deterministic (regex numbers → compare with tolerance, handling %, ₹, M/K suffixes). An **LLM-as-judge** groundedness check is P2 — the deterministic one is more defensible.

### 18.6 Result comparison (`evaluation/compare.py`)

- `scalar`: first numeric value of first row, relative tolerance.
- `rows_unordered`: compare as multisets after normalizing (round floats to 2 dp, lowercase strings, ignore column names/order — match columns by value-type signature).
- `rows_ordered`: same plus order (for top-N/trends).
- `top_k`: same set of top-k labels.

Column names are ignored on purpose: `revenue` vs `total_revenue` shouldn't fail a correct answer.

### 18.7 Retrieval-only eval (cheap, no LLM)

`python -m app.evaluation.run --suite core --retrieval-only` embeds each question, runs the retriever, and reports recall@3/5/8 for vector vs hybrid. Runs in seconds, can run in CI. Great for tuning top-k and the similarity floor.

### 18.8 Running evals

- CLI: `python -m app.evaluation.run --suite core --datasource demo_pg [--cases rev_last_month,...] [--ablation no_rag|schema_only|full]`.
- Writes an `evaluation_runs` row and `reports/eval_<timestamp>.md` (table of metrics + failures with generated vs gold SQL).
- API `POST /api/v1/evaluations` (P1, disabled in DEMO_MODE) + `/evaluations` page to browse results.
- **Cross-engine eval (P1):** run the same suite on Postgres, ClickHouse, and DuckDB sources → a "dialect robustness" table. The gold SQL is written per engine, or more simply in Postgres SQL and transpiled with `sqlglot.transpile(..., write=dialect)` and verified once by hand.
- Not in CI by default (cost/rate limits); a nightly/manual workflow is P2.

### 18.9 Agent-level tests vs evals

- **Tests** (CI, FakeLLM): "given these LLM outputs, does the graph route/validate/stop correctly?" → deterministic.
- **Evals** (manual, real LLM): "given real models, how good are the answers?" → statistical.

---

## 19. Observability

### 19.1 What we record per analysis run

| Signal | Where | How |
|---|---|---|
| Model, agent step, latency, input/output/total tokens, success, error, fallback used | `llm_calls` | Recorded inside `LLMClient` (single chokepoint), best-effort (wrapped in try/except, never breaks the run) |
| Generated SQL (original + final), validation result, execution time, row count, success/failure | `analysis_queries` | Written by `validate_sql`/`execute_sql` nodes |
| Retrieval results (chunk ids, kinds, titles, scores, method vector/hybrid, confidence) | `analyses.retrieved_context` | Written by `retrieve_context` |
| Drill-down iterations, stop reason | `analyses.drilldown_depth`, `stop_reason`, `analysis_queries.filters` | finalize nodes |
| Errors | `analyses.error_code/message` + logs | finalize_failure + exception handlers |
| Per-node timings | structured logs (`node`, `duration_ms`, `analysis_id`) | a decorator `@traced_node` on every node |
| Totals (calls, tokens, latency) | `analyses` rollup columns | finalize nodes |

### 19.2 Logging

JSON logs (stdlib `logging` + a JSON formatter) with `analysis_id` added to every log line via a `contextvars`-based filter. Secrets are never logged: config models use `SecretStr`, and the datasource logger redacts `password`-like keys.

### 19.3 UI surfaces

- **Run stats panel** on the result page: total latency, LLM calls, tokens, per-step table (node, agent, model, tokens, ms).
- **Investigation timeline:** each step question → SQL (collapsible) → rows → finding.
- **Retrieved context panel:** which chunks were used and their scores.
- (P2) `/observability` page: aggregate over last N runs (avg latency, failure rate by error code, tokens per run).

### 19.4 LangSmith?

**Optional, P2, zero code:** LangGraph sends traces automatically when `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` are set (free developer tier). Great for *debugging during development*. We do **not** depend on it: our own tables are the system of record, work offline, and are visible in our own UI (which is more impressive in a demo than a third-party dashboard). Mention in the README as "optional trace export".

### 19.5 What we skip

OpenTelemetry collector/SigNoz, Prometheus metrics, alerting. (P3 — note in "future improvements".)

---

## 20. Error Handling

### 20.1 Error model

```python
class AppError(Exception):
    code: str              # stable machine code, e.g. "SQL_TIMEOUT"
    user_message: str      # safe to show
    http_status: int = 400
    retryable: bool = False
```

FastAPI exception handlers map `AppError` → `{"error": {"code", "message", "retryable"}}`. Unexpected exceptions → 500 with a generic message + `request_id` (full traceback only in logs). Inside the graph, errors become state (`last_error` / `RunError`), not exceptions crossing node boundaries.

### 20.2 Error catalog

| Case | Backend behavior | User sees | Retry? |
|---|---|---|---|
| Datasource connection failure (create/test) | `test_connection` returns `{ok:false, error}`; sanitized (no host/password echo beyond what user typed) | Red message under the form: "Could not connect: timeout after 5s" | User retries manually |
| Invalid credentials | Same; detect auth error class → `INVALID_CREDENTIALS` | "Authentication failed for user X" | Manual |
| Datasource down during analysis | `execute_sql` catches connection error → `finalize_failure(DATASOURCE_UNAVAILABLE)`; **no LLM repair** | "Couldn't reach the database. Check it's running and try again." + Retry button | Yes (user) |
| Malformed CSV | DuckDB import error → 422 `CSV_INVALID` with line info; nothing saved | "Row 1,043 has 9 columns, expected 8" | After fixing file |
| Unsupported data type | Normalizer maps unknown → `other`, value → `str(value)`; column marked non-numeric | Nothing (graceful) | — |
| Schema introspection error | Sync marks `status=error` with message; previous catalog kept | Badge "Sync failed" + message | Yes (Sync button) |
| LLM timeout | Retry ×2 with backoff → next fallback model → node fallback | Progress shows "Retrying model…" (stage text) | Auto |
| OpenRouter rate limit (429) | Honor `Retry-After`, backoff, then fallback model; if all exhausted → `LLM_UNAVAILABLE` | "AI service is rate-limited. Try again in a minute." | Auto, then user |
| Malformed structured output | Pydantic error → repair prompt ×1 → fallback model → deterministic fallback (Analysis/Viz) or failure (Query) | Usually nothing; worst case failure message | Auto |
| Invalid SQL (validator) | Specific feedback → repair up to 2× | Timeline shows "attempt 2" | Auto |
| SQL timeout | Error fed back with "aggregate/filter more" hint → repair | Timeline shows timeout + repaired query | Auto (≤ 2) |
| Zero rows | One repair with sample-value hints; then continue honestly | "No data found for June 2027" insight | Auto (1) |
| Result too large | Outer LIMIT + truncation flag; chart top-20 + Other | "Showing first 500 rows" note | — |
| RAG returns irrelevant context | Floor + `confidence=low` → Query Agent may return `can_answer=false` | "I couldn't find data for 'influencer ROI' in this datasource. Closest tables: …" | User rephrases |
| Excessive drill-down requests | Guard rejects past depth/budget/duplicate → visualize with what we have | Note: "Investigation stopped after 3 levels" | — |
| Graph recursion limit | `GRAPH_LIMIT` failure; logged as bug | Generic failure | Manual |
| Server restart mid-run | Startup reconciler marks `running` → `failed(INTERRUPTED)` | "Analysis was interrupted. Run again." | Manual |

---

## 21. Testing

### 21.1 Pyramid

```mermaid
flowchart BT
    U["Unit (many, fast): sql_guard, profiler, compare, chunk builders,<br/>structured-output parsing, spec validator, crypto, config"] --> I["Integration (some): connectors vs Docker Postgres/ClickHouse/DuckDB,<br/>catalog sync, retrieval on seeded pgvector, API routes"]
    I --> G["Graph tests (some): full LangGraph with FakeLLMProvider —<br/>routing, repair loop, drill-down termination, fallbacks"]
    G --> E["Evals (few, manual, real LLM): golden suite"]
```

### 21.2 High-value tests

| Area | Test ideas |
|---|---|
| **SQL guard** (most important) | Table-driven: 40+ cases per dialect — allowed (CTEs, window functions, subqueries, UNION) and rejected (multi-statement, DML in CTE, DDL, `COPY`, `SET`, `pg_sleep`, `read_csv`, `url()`, unknown table, `SELECT *`); LIMIT rewrite (no limit → added; `LIMIT 10000` → 500; subquery limit untouched + outer added) |
| Connector factory | correct class per type; unknown type → error; secret not in `repr()` |
| Connectors (integration) | `test_connection`, `introspect_schema` returns the 15 demo tables, `execute` normalizes types, timeout fires (`pg_sleep` with guard bypass in test), read-only enforced (direct `INSERT` via connector raises) |
| CSV import | good file, ragged rows → 422, weird headers sanitized, external access disabled at query time |
| RAG | chunk text builder snapshot test; retrieval on seeded index returns `orders` for "revenue last month"; datasource isolation (never returns other source's chunks); RRF math |
| Structured output | JSON fenced in markdown, trailing text, missing field → repair prompt content |
| Profiler | contribution shares sum to 1; dominant segment flags; time-series deltas; empty results |
| Dashboard validator | drops widget with unknown column; pie > 6 rows rejected; fallback builder produces valid spec for each result shape |
| Graph (FakeLLM) | happy path = 3 calls; invalid SQL → repair → success; 3 invalid → failure; drill-down stops at depth 3 even if agent keeps asking; dimension reuse rejected; budget exhaustion → still produces dashboard; Analysis Agent garbage twice → deterministic findings |
| API | create datasource never returns secret; analysis lifecycle `queued → completed`; history ordering |
| Frontend (P2) | Vitest for `format.ts` and the chart switch; no E2E browser tests in MVP |

Target: meaningful coverage of `sql_guard/`, `analytics/`, `dashboard/`, `graph/` routing. **No coverage percentage goal.**

---

## 22. Deployment

### 22.1 Local architecture (docker-compose)

```yaml
# docker-compose.yml (sketch)
services:
  appdb:            # InsightFlow's own metadata DB
    image: pgvector/pgvector:pg16
    environment: [POSTGRES_DB=insightflow, POSTGRES_USER=insightflow, POSTGRES_PASSWORD=insightflow]
    ports: ["5432:5432"]
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U insightflow"], interval: 5s, retries: 10 }

  demo-postgres:    # "customer" analytical Postgres with demo data + read-only role
    image: postgres:16
    ports: ["5433:5432"]
    volumes: ["./demo_data/postgres/init:/docker-entrypoint-initdb.d"]
    healthcheck: { test: ["CMD-SHELL", "pg_isready"], interval: 5s, retries: 10 }

  clickhouse:       # "customer" analytical ClickHouse with demo data + readonly user
    image: clickhouse/clickhouse-server:24.8
    ports: ["8123:8123"]
    volumes: ["./demo_data/clickhouse/init:/docker-entrypoint-initdb.d", "./demo_data/clickhouse/users.d:/etc/clickhouse-server/users.d"]
    healthcheck: { test: ["CMD", "wget", "-qO-", "http://localhost:8123/ping"], interval: 5s, retries: 20 }

  backend:
    build: ./backend
    env_file: .env
    depends_on: { appdb: { condition: service_healthy } }
    ports: ["8000:8000"]
    volumes: ["./data:/app/data"]          # CSV uploads + DuckDB files
    healthcheck: { test: ["CMD", "curl", "-f", "http://localhost:8000/health"], interval: 10s }

  frontend:         # optional; `npm run dev` is fine locally
    build: ./frontend
    environment: [NEXT_PUBLIC_API_URL=http://localhost:8000]
    ports: ["3000:3000"]
    profiles: ["full"]
```

Why two Postgres containers? It makes the **two data planes physically visible** — the app DB and the "customer" DB are different servers, exactly as in production. Costs nothing locally.

**Backend Dockerfile (sketch):** `python:3.12-slim` → install `uv` → `uv sync --frozen --no-dev` → **pre-download the fastembed model at build time** (Agent360 lesson: no cold-start download) → copy app + bundled demo DuckDB file → non-root user → `entrypoint.sh`: `alembic upgrade head && python -m app.scripts.bootstrap_demo (if DEMO_MODE) && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`.

**Health checks:** `GET /health` (process alive, no dependencies — for container orchestrators), `GET /health/ready` (app DB reachable + embedding model loaded).

**`.env.example`:** every variable from §8.3 with safe placeholder values and comments; `.env` is git-ignored.

### 22.2 Production/demo deployment (Diagram 7)

```mermaid
flowchart LR
    V["Visitor"] --> FE["Vercel (free)<br/>Next.js frontend"]
    FE -- "HTTPS /api/v1" --> BE["FastAPI container<br/>free container host<br/>(Render / Koyeb / HF Spaces)"]
    BE --> NEON1[("Neon Postgres + pgvector<br/>app metadata DB")]
    BE --> NEON2[("Neon Postgres<br/>demo analytics DB<br/>(read-only role)")]
    BE --> DUCK[("Bundled demo .duckdb<br/>inside the image")]
    BE --> OR["OpenRouter API"]
    subgraph LOCAL["Local / recorded demo only"]
        CH[("ClickHouse via Docker Compose")]
    end
```

| Component | Host | Cost | Notes |
|---|---|---|---|
| Next.js | **Vercel** Hobby | Free | Native Next.js support; set `NEXT_PUBLIC_API_URL` |
| FastAPI | A free Docker host — choose by **measured memory** (see below) | Free | Sleeps when idle on free tiers → first request slow (README notes it) |
| App DB | **Neon** free (pgvector supported) | Free | Small storage limit — plenty for metadata |
| Demo analytics Postgres | **Neon** (second database or project) | Free | Loaded from generated CSVs; `insightflow_ro` read-only role |
| CSV/DuckDB | Bundled demo `.duckdb` in the image; uploads to ephemeral disk | Free | **Uploads disappear on restart** in the public demo (documented; object storage = P3) |
| ClickHouse | Local Docker Compose; demonstrated via screenshots, eval results, and the demo video | Free | ClickHouse Cloud has only a time-limited trial; a self-hosted VM is the only free long-running option |
| Vectors | pgvector in the app DB | Free | No separate vector service |

**Container host choice — decide with data in Phase 16:** fastembed + ONNX runtime + FastAPI peaks around a few hundred MB RAM. Free tiers with ~512 MB (e.g. Render free) may be tight; Hugging Face Spaces (Docker) offers much more RAM on its free CPU tier but has its own sleep behavior. Measure `docker stats` peak memory during an analysis, then pick. **Fallback:** swap `EmbeddingProvider` to a free embedding API in production only (interface already exists), at the cost of one more external dependency. *Free-tier terms change often — verify each provider's current limits when deploying.*

**Alternative "everything in one place" option:** an always-free cloud VM (e.g. Oracle Cloud Always Free) running the full `docker-compose` (incl. ClickHouse) behind Caddy for HTTPS. Pros: ClickHouse is live in the public demo. Cons: you operate a VM (updates, firewall, TLS). Good P2 upgrade once everything else works.

### 22.3 Deployment order

1. Create Neon project → app DB (enable `vector` extension) + demo DB.
2. Load demo data into the demo DB (`scripts/load_postgres.py --url …`), create the read-only role.
3. Build & push the backend image (or connect the repo to the host) with env vars: `DATABASE_URL`, `DATASOURCE_ENCRYPTION_KEY`, `OPENROUTER_API_KEY`, model vars, `DEMO_MODE=true`, `CORS_ORIGINS=https://<vercel-app>`.
4. Container starts → `alembic upgrade head` → `bootstrap_demo` registers the demo datasources (Neon PG + bundled DuckDB), syncs catalogs, seeds knowledge, builds embeddings (idempotent).
5. Verify `/health/ready`, run 3 smoke questions via `/docs`.
6. Deploy frontend to Vercel with `NEXT_PUBLIC_API_URL`.
7. Run retrieval-only eval + a small live eval against the deployed backend; record results in README.
8. Record the demo video/GIF.

---

## 23. CI/CD

Single workflow `.github/workflows/ci.yml` on push/PR:

| Job | Steps |
|---|---|
| `backend` | checkout → setup uv → `uv sync --frozen` → `ruff check` + `ruff format --check` → services: `pgvector/pgvector:pg16`, `clickhouse/clickhouse-server` → `alembic upgrade head` → `pytest -m "not live_llm"` (unit + integration + graph tests with FakeLLM) → retrieval-only eval (fast, no LLM) |
| `frontend` | setup Node → `npm ci` → `npm run lint` → `npx tsc --noEmit` → `npm run build` |
| `docker` | `docker build ./backend` (no push) to ensure the image builds |

CD: Vercel and the container host auto-deploy from `main` via their GitHub integrations — no deploy scripts needed. Live-LLM evals run manually (`workflow_dispatch`, P2) with the OpenRouter key as a repo secret.

No Kubernetes, no Terraform, no release tagging.

---

## 24. Security

### 24.1 Datasource credentials — decision

**Chosen:** credentials are **persisted, encrypted at rest** (Fernet, key from env `DATASOURCE_ENCRYPTION_KEY`).

Why not session-only? History, refresh, and evaluation all need to reconnect later; session-only credentials would break them and complicate the UX. Encryption-at-rest with an env-provided key is the simplest *defensible* design (it's exactly what Agent360 ships).

| Rule | Implementation |
|---|---|
| Encrypt at rest | Only the secret field (`password`) is encrypted into `encrypted_secret`; non-secret config stays queryable JSON |
| Key management | `DATASOURCE_ENCRYPTION_KEY` env var (generated with `Fernet.generate_key()`); app refuses to start in non-local env without it |
| Never return to frontend | Response DTOs have no secret field; they expose `has_secret: bool` |
| Update semantics | Omitted password on update = keep existing (avoids Agent360's `"***"` overwrite bug) |
| Never log | `SecretStr` in models; logging filter redacts `password|secret|token|key` |
| Decrypt narrowly | Only in `DatasourceService.build_connector()`; never placed into graph state or telemetry |
| Least privilege | Docs + init scripts create read-only users (`GRANT SELECT` only; ClickHouse `readonly` profile) |
| Dev secrets | `.env` (git-ignored), `.env.example` committed |

### 24.2 Public demo hardening (`DEMO_MODE=true`)

A public endpoint that connects to arbitrary hosts is an **SSRF vector** and an abuse magnet. In demo mode:

- Creating Postgres/ClickHouse datasources is **disabled** (preconfigured demo sources only). CSV upload allowed, size-limited (`MAX_UPLOAD_MB`), CSV extension + content sniffing.
- Rate limit `POST /analyses` per IP (slowapi, e.g. 5/min, 30/day) to protect the LLM quota.
- Evaluation endpoints disabled.
- CORS restricted to the Vercel domain.

### 24.3 LLM-specific risks

| Risk | Mitigation |
|---|---|
| Prompt injection via the question ("ignore rules, drop table") | LLM has no write capability; SQL guard + read-only DB make it harmless |
| Indirect injection via data values (a product named "ignore previous instructions…") | Results passed as clearly delimited JSON data; agents have no tools with side effects; outputs are schema-validated |
| Data exfiltration to other tables | Table allowlist in the guard; only allowlisted tables in context |
| Sensitive columns sent to LLM | Only ≤ 20 aggregated rows reach the LLM; columns can be marked non-queryable (P2: per-column exclusion) |
| Cost abuse | Budgets per analysis + rate limiting |

### 24.4 Other basics

Pydantic validation on all inputs; parameterized queries for the app DB (SQLAlchemy); uploaded filenames sanitized (no path traversal); HTTPS in deployment (Vercel/host provide TLS); dependency updates via Dependabot (P2). **No auth in MVP** (single-user/demo) — called out explicitly as a limitation; P3: auth + per-user ownership.

---

## 25. Project Folder Structure

### 25.1 Repository root

```
insightflow/
├── backend/
├── frontend/
├── demo_data/                 # generator outputs + DB init scripts + seed knowledge
│   ├── generated/             # CSVs + ground_truth.json (git-ignored except small sample)
│   ├── knowledge/             # definitions.yaml, examples_postgres.yaml, examples_clickhouse.yaml, examples_duckdb.yaml, relationships.yaml, descriptions.yaml
│   ├── postgres/init/         # 00_schema.sql, 01_readonly_role.sql
│   └── clickhouse/            # init/ schema.sql, users.d/readonly.xml
├── plans/                     # this document + later phase notes
├── reports/                   # eval reports (markdown)
├── docker-compose.yml
├── .env.example
├── .github/workflows/ci.yml
└── README.md
```

### 25.2 Backend

```
backend/
├── pyproject.toml / uv.lock / Dockerfile / entrypoint.sh / alembic.ini
├── alembic/versions/                 # migrations
├── app/
│   ├── main.py                       # app factory, lifespan (load embedding model, reconcile runs), routers
│   ├── api/
│   │   ├── deps.py                   # get_session, get_settings, service providers
│   │   ├── errors.py                 # AppError → HTTP mapping
│   │   └── routes/                   # health.py, datasources.py, knowledge.py, analyses.py, evaluations.py
│   ├── core/
│   │   ├── config.py                 # Settings (pydantic-settings)
│   │   ├── logging.py                # JSON logging + analysis_id context filter
│   │   ├── errors.py                 # AppError hierarchy + codes
│   │   └── crypto.py                 # Fernet encrypt/decrypt
│   ├── db/
│   │   ├── session.py                # async engine + sessionmaker
│   │   └── models/                   # datasource.py, catalog.py, knowledge.py, analysis.py, evaluation.py, telemetry.py
│   ├── schemas/                      # API DTOs: datasource.py, knowledge.py, analysis.py, evaluation.py
│   ├── connectors/
│   │   ├── base.py                   # DataConnector ABC
│   │   ├── types.py                  # SchemaSnapshot, QueryResult, ColumnProfile...
│   │   ├── normalize.py              # value + type normalization
│   │   ├── postgres.py / clickhouse.py / duckdb.py
│   │   ├── csv_import.py             # CSV → DuckDB file builder
│   │   ├── errors.py                 # ConnectionFailed, QueryTimeout, QueryExecutionError
│   │   └── factory.py
│   ├── catalog/
│   │   ├── sync.py                   # introspect → upsert catalog (preserve user descriptions)
│   │   ├── profiling.py              # sample values, distinct counts, is_dimension
│   │   └── relationships.py          # FK import + seed relationships
│   ├── sql_guard/
│   │   ├── validator.py              # validate_sql(sql, dialect, policy) → ValidationResult
│   │   ├── policy.py                 # forbidden nodes/functions per dialect, limits
│   │   └── suggestions.py            # "did you mean" for tables/columns
│   ├── llm/
│   │   ├── base.py                   # LLMProvider ABC
│   │   ├── types.py                  # LLMRequest, LLMResponse, Usage
│   │   ├── openai_compatible.py      # OpenAICompatibleProvider + OpenRouterProvider
│   │   ├── fake.py                   # FakeLLMProvider (tests)
│   │   ├── client.py                 # LLMClient: structured output, retries, fallback, budget, recording
│   │   ├── structured.py             # tool-mode / json-schema / prompt-json strategies + parsing
│   │   └── models.yaml               # model capability registry
│   ├── rag/
│   │   ├── embeddings.py             # EmbeddingProvider + FastEmbedProvider
│   │   ├── chunks.py                 # build table/definition/example chunk text
│   │   ├── indexer.py                # upsert + embed (content-hash skip)
│   │   ├── retriever.py              # vector + FTS + RRF + quotas + expansion
│   │   └── context.py                # RetrievedContext → prompt text (token-budgeted)
│   ├── agents/
│   │   ├── schemas.py                # QueryAgentOutput, AnalysisFrame, AnalysisAgentOutput, ...
│   │   ├── query_agent.py
│   │   ├── analysis_agent.py
│   │   ├── visualization_agent.py
│   │   └── prompts/                  # query_agent.md, analysis_agent.md, visualization_agent.md, dialects/{postgres,clickhouse,duckdb}.md
│   ├── analytics/
│   │   ├── profiler.py               # ResultProfile: stats, comparison/contribution, time series
│   │   └── fallback_findings.py      # deterministic findings when Analysis Agent fails
│   ├── dashboard/
│   │   ├── spec.py                   # DashboardSpec models
│   │   ├── validator.py              # spec vs real results
│   │   ├── fallback_builder.py       # rule-based dashboard
│   │   ├── hydrate.py                # spec + datasets for the API
│   │   └── refresh.py                # P1: re-run stored SQL
│   ├── graph/
│   │   ├── state.py                  # AnalysisState + sub-models
│   │   ├── nodes/                    # load_context.py, retrieve.py, query.py, validate.py, execute.py, profile.py, analyze.py, drilldown.py, visualize.py, finalize.py
│   │   ├── routing.py                # route_after_* functions (pure, unit-tested)
│   │   ├── budget.py                 # BudgetUsage + checks
│   │   ├── progress.py               # report_stage()
│   │   └── builder.py                # build_analysis_graph(deps) → compiled graph
│   ├── services/
│   │   ├── datasource_service.py     # create/test/sync/delete, build_connector()
│   │   ├── knowledge_service.py      # CRUD definitions/examples/descriptions + reindex
│   │   ├── analysis_service.py       # create run, background execution, read models, reconcile
│   │   └── evaluation_service.py
│   ├── observability/
│   │   ├── recorder.py               # record_llm_call / record_query (best-effort)
│   │   └── tracing.py                # @traced_node decorator, timers
│   ├── evaluation/
│   │   ├── datasets/core.yaml
│   │   ├── runner.py / metrics.py / compare.py / report.py
│   │   └── __main__.py               # CLI
│   └── scripts/
│       ├── generate_demo_data.py     # synthetic data + ground_truth.json
│       ├── load_postgres.py / load_clickhouse.py / build_duckdb.py
│       ├── seed_knowledge.py
│       ├── bootstrap_demo.py         # idempotent: register + sync + seed demo sources
│       └── check_models.py
└── tests/
    ├── unit/ (sql_guard, profiler, compare, chunks, structured, dashboard, routing, crypto)
    ├── integration/ (connectors, catalog_sync, retrieval, api)
    └── graph/ (fake-LLM workflow scenarios)
```

No `utils/` folder: every helper belongs to the module whose concept it serves (e.g. "did you mean" → `sql_guard/suggestions.py`).

### 25.3 Frontend

```
frontend/
├── package.json / next.config.ts / tailwind config / components.json (shadcn)
└── src/
    ├── app/
    │   ├── layout.tsx                # AppShell + QueryClientProvider
    │   ├── page.tsx                  # redirect → /analyze
    │   ├── analyze/page.tsx
    │   ├── analyses/[id]/page.tsx
    │   ├── history/page.tsx
    │   ├── datasources/page.tsx
    │   ├── datasources/new/page.tsx
    │   ├── datasources/[id]/page.tsx
    │   └── evaluations/page.tsx      # P1
    ├── components/
    │   ├── ui/                       # shadcn-generated primitives (don't hand-edit much)
    │   └── layout/                   # AppShell, Sidebar, PageHeader
    ├── features/
    │   ├── datasources/              # DatasourceList, ConnectionForm, CsvUploadForm, SchemaBrowser, KnowledgeEditor, hooks.ts (useDatasources, useSync…)
    │   ├── analysis/                 # QuestionForm, ExampleQuestions, ProgressStepper, InvestigationTimeline, SqlPanel, RetrievedContextPanel, RunStatsPanel, hooks.ts (useAnalysis polling)
    │   ├── dashboard/                # DashboardView, KpiCard, ChartWidget, DataTableWidget, InsightCard, pivot.ts
    │   ├── history/                  # HistoryTable
    │   └── evaluations/              # EvalRunList, EvalCaseTable (P1)
    ├── lib/
    │   ├── api-client.ts             # typed fetch wrapper, base URL, error normalization
    │   ├── query-client.ts
    │   └── format.ts                 # currency/percent/compact number formatting
    └── types/
        └── api.ts                    # mirrors backend DTOs (P1: generated api.gen.ts)
```

- **Pages** compose feature components and own routing params only.
- **`features/*`** hold domain components + their TanStack Query hooks (feature-sliced; easy to find things).
- **`components/ui`** are generic primitives; **`components/layout`** is app chrome.
- **`lib/api-client.ts`** is the only place that calls `fetch`.

---

## 26. P0/P1/P2/P3 Feature List

| Feature | Priority |
|---|---|
| Synthetic e-commerce data generator with planted anomalies + ground truth | **P0** |
| Docker Compose: app DB (pgvector), demo Postgres, ClickHouse | **P0** |
| Connectors: Postgres, ClickHouse, CSV→DuckDB + factory + read-only + timeouts | **P0** |
| Datasource CRUD + test connection + encrypted credentials | **P0** |
| Catalog sync (introspection + profiling + FK import) | **P0** |
| Seed knowledge (descriptions, definitions, examples, relationships) | **P0** |
| RAG: fastembed + pgvector vector retrieval + context builder | **P0** |
| LLMProvider abstraction + OpenRouter + structured output + retries + fallback | **P0** |
| sqlglot SQL guard (single statement, read-only AST, function denylist, allowlist, LIMIT) | **P0** |
| Query Agent + repair loop | **P0** |
| Result profiler (deltas, contributions) | **P0** |
| Analysis Agent + bounded drill-down loop (LangGraph) | **P0** |
| Visualization Agent + spec validation + fallback builder | **P0** |
| Analysis API (background run + polling) + history | **P0** |
| Frontend: datasources, analyze, result dashboard, history | **P0** |
| `llm_calls` + `analysis_queries` telemetry + run stats panel | **P0** |
| Eval suite (25 cases) + CLI + markdown report | **P0** |
| Unit + integration + FakeLLM graph tests | **P0** |
| Backend Dockerfile + deployment (Vercel + container host + Neon) | **P0** |
| README with architecture, eval results, demo GIF | **P0** |
| Hybrid retrieval (FTS + RRF) + vector-vs-hybrid recall comparison | P1 |
| Dashboard refresh without LLM | P1 |
| Knowledge editor UI (definitions/examples/descriptions) | P1 |
| Evaluations page in UI | P1 |
| RAG ablation study in eval report | P1 |
| Cross-engine eval (PG / CH / DuckDB) | P1 |
| Query Agent bounded tool loop (`lookup_column_values`) | P1 |
| Deterministic numeric groundedness metric | P1 |
| GitHub Actions CI | P1 |
| DEMO_MODE hardening (rate limit, disable custom DBs) | P1 (P0 if deployed publicly) |
| OpenAPI-generated TS types | P1 |
| SSE streaming progress | P2 |
| LangSmith trace export (env flag) | P2 |
| LLM-as-judge groundedness | P2 |
| "Save generated query as verified example" button | P2 |
| EXPLAIN pre-validation | P2 |
| Observability aggregate page | P2 |
| LLM-drafted table/column descriptions | P2 |
| Doc chunks (uploaded data dictionary) | P2 |
| Oracle/VM full-stack deployment with live ClickHouse | P2 |
| Redshift connector | P3 |
| Auth, users, multi-tenancy, RBAC | P3 |
| Interactive filters (`{FILTER_WHERE}`) | P3 |
| Saved/shared dashboards, scheduled refresh, rolling-window SQL | P3 |
| Job queue (Redis/Celery/Arq), connection pooling | P3 |
| Object storage for uploads | P3 |
| OpenTelemetry/Prometheus | P3 |
| Multi-turn follow-up questions on an analysis | P3 |

### 26.1 What NOT to build (MVP)

| Don't build | Why |
|---|---|
| Kubernetes, Terraform, Helm | One container + managed DB is enough; adds ops, not AI/backend signal |
| Kafka / streaming ingestion / ETL pipelines | We query data in place; nothing to ingest |
| OAuth, RBAC, multi-tenancy, billing | No users in the MVP; huge surface area |
| Chat interface / conversation memory | Product is question → dashboard; chat dilutes the "analytics product" story |
| A 4th+ LLM provider or a 4th+ database | The abstraction is proven with 1 provider + fallback and 3 engines |
| Fine-tuning | RAG + few-shot is the right lever at this scale; no training data |
| GraphRAG / knowledge graphs | Relationships are a handful of join edges; a dict suffices |
| MCP server | No consumer for it; tools are internal |
| Separate vector DB | pgvector covers it |
| Celery/Redis job queue | Short in-process jobs suffice |
| Voice, mobile app, fancy animations, dark-mode theming work | Zero AI/backend value |
| LLM-generated React/HTML | Unsafe and untestable; structured spec instead |
| Agent360-style interactive filters, saved/shared dashboards, skills, table groups, context compaction | Production features with no portfolio payoff right now |
| 100% test coverage, academic eval suites | Test the risky parts; 25 good eval cases |

---

## 27. Step-by-Step Implementation Plan

> **Active build order:** see [`SPRINT_2_DAY.md`](SPRINT_2_DAY.md). The phases below are
> the full-fidelity sequence and remain the reference for *what* each phase contains and
> *why* it sits where it does. The sprint document compresses Phases 0–12 into two days by
> cutting implementations while keeping every interface, then re-introduces the remaining
> phases in its extension ledger (§5 there). The estimates below assume part-time work at
> full fidelity; they are not the sprint's timeline.

### 27.1 Order of phases, and why this order

```mermaid
flowchart LR
    P0["0 Setup"] --> P1["1 Simple demo data"] --> P2["2 Connectors<br/>(PG + DuckDB)"] --> P3["3 SQL guard"] --> P4["4 App DB +<br/>datasource API +<br/>catalog sync"]
    P4 --> P5["5 LLM layer"] --> P6["6 RAG v1"] --> P7["7 Query Agent<br/>first E2E slice"] --> P8["8 Eval v1"]
    P8 --> P9["9 Analysis Agent +<br/>drill-down"] --> P10["10 Viz Agent +<br/>dashboard"] --> P11["11 Analysis API +<br/>telemetry"] --> P12["12 Frontend"]
    P12 --> P13["13 ClickHouse +<br/>richer data"] --> P14["14 P1 features"] --> P15["15 Docker, deploy, CI"] --> P16["16 README + demo"]
```

Ordering principles:

1. **Start small.** Phase 1 is a small, simple dataset on a single database. Complexity (more tables, ClickHouse, distractor tables) is added only after the core pipeline works.
2. **Deterministic, testable foundations before LLMs.** Connectors and the SQL guard are built and tested before any prompt is written.
3. **Get one question working end-to-end early** (Phase 7, via a CLI, no frontend), then **measure it** (Phase 8) before adding drill-down. This is evaluation-driven development: every later change can be checked against the eval numbers.
4. **Frontend after the API is stable**, so UI work isn't thrown away.
5. **The third engine comes late** (Phase 13). Adding ClickHouse then *proves* the connector abstraction: if it needs agent-layer changes, the abstraction was wrong.

Effort estimates assume part-time work with a coding agent. They are rough guides, not commitments.

---

### Phase 0 — Project setup (≈ 0.5 day) · P0

- **Goal:** a running skeleton with the app DB, so later phases only add code.
- **Build:**
  - Repo layout (§25.1), `backend/` with `uv init`, FastAPI app with `GET /health`.
  - `core/config.py` (pydantic-settings), JSON logging.
  - `docker-compose.yml` with **only** `appdb` (pgvector) and `demo-postgres`.
  - `.env.example`, `.gitignore`, ruff config, empty `tests/` with one passing test.
- **Files:** `backend/pyproject.toml`, `app/main.py`, `app/core/config.py`, `app/core/logging.py`, `app/api/routes/health.py`, `docker-compose.yml`, `.env.example`.
- **Learn:** uv basics; FastAPI app factory and lifespan; pydantic-settings; Docker Compose health checks.
- **Done when:** `docker compose up -d` starts both DBs healthy; `uvicorn app.main:app` serves `/health`; `pytest` and `ruff check` pass.
- **Tests:** `/health` returns 200.
- **Depends on:** nothing.

---

### Phase 1 — Simple demo data (≈ 1 day) · P0

> Kept deliberately simple: **one script, 6 tables, a few planted patterns, one database.** Distractor tables and the other engines come in Phase 13.

- **Goal:** realistic-enough e-commerce data with **known answers**, loaded into the local demo Postgres.
- **Build:**
  - `scripts/generate_demo_data.py`: plain Python + numpy + Faker with a **fixed seed**. Writes 6 CSVs to `demo_data/generated/`:
    `customers`, `products`, `orders`, `order_items`, `payments`, `refunds` (schemas in §28.2).
  - Size: ~2,000 customers, ~60 products, **~25k orders over 12 months** (Jul 2025 – Jun 2026). Small enough to generate in seconds.
  - Plant only **3 patterns** (§28.3):
    1. June 2026 revenue drop, driven by **South → Electronics**.
    2. Refund spike in **Home & Kitchen** in May–June 2026.
    3. ~8% **FAILED/CANCELLED** orders (the "revenue counts SUCCESS only" trap).
  - Write `ground_truth.json` by **computing** the facts from the generated data (don't hardcode numbers).
  - `demo_data/postgres/init/00_schema.sql` (CREATE TABLEs) + `01_load.sql` (`\copy` the CSVs) + `02_readonly_role.sql`.
- **Files:** `backend/app/scripts/generate_demo_data.py`, `demo_data/postgres/init/*.sql`, `demo_data/generated/*`.
- **Learn:** seeded random generation; how to plant a signal on top of noise; Postgres `COPY`; read-only roles (`GRANT SELECT`).
- **Done when:** after `docker compose up`, `psql` on the demo DB shows the 6 tables. A hand-written SQL query reproduces each fact in `ground_truth.json`.
- **Tests:** a small pytest that runs the generator twice with the same seed and gets identical output; a check that the June drop ratio is within the expected range.
- **Depends on:** Phase 0.

---

### Phase 2 — Connector abstraction: Postgres + DuckDB (≈ 1.5 days) · P0

- **Goal:** a single interface the rest of the system uses to talk to any analytical DB.
- **Build:**
  - `connectors/types.py` (§9.2), `base.py` (ABC), `normalize.py`, `errors.py`.
  - `PostgresConnector`: read-only session plus `statement_timeout`, `fetchmany(max_rows+1)`, introspection (information_schema + comments + FKs), simple profiling.
  - `csv_import.py` (CSV → `.duckdb` file) + `DuckDBConnector`: read-only, external access off, interrupt-based timeout.
  - `factory.py`.
  - A tiny CLI, `python -m app.connectors.cli --type postgres --sql "SELECT 1"`, for manual checks.
- **Files:** `app/connectors/*`.
- **Learn:** abstract base classes; factory pattern; psycopg 3; DuckDB basics; `asyncio.to_thread`; why a server-side timeout beats a client-side one.
- **Done when:** both connectors pass the same **shared contract test suite** (a test parametrized over connector types), and the demo CSVs, imported into DuckDB, give the same results as Postgres.
- **Tests:** a contract test (`test_connection`, `introspect_schema` lists 6 tables, `execute` normalizes Decimal/date types, truncation flag); read-only (a direct INSERT raises); timeout (a slow query raises `QueryTimeoutError`); malformed CSV is rejected.
- **Depends on:** Phase 1.

---

### Phase 3 — SQL guard (≈ 1.5 days) · P0

- **Goal:** no LLM-generated SQL can do harm.
- **Build:** `sql_guard/validator.py`, `policy.py`, `suggestions.py`, implementing every check in §14.2 for the `postgres` and `duckdb` dialects (ClickHouse rules are added in Phase 13).
- **Files:** `app/sql_guard/*`.
- **Learn:** ASTs; sqlglot (`parse`, `find_all(exp.Table)`, `exp.Select.limit`, dialects); why regex-based checks fail (§14.3).
- **Done when:** a table-driven test file of ≥ 40 cases passes, and `validate_sql()` returns the rewritten safe SQL along with the tables it uses.
- **Tests:** all the cases in §21.2 for the SQL guard. **This is the most valuable test file in the project.**
- **Depends on:** Phase 2 (for the allowlist types). Can be built in parallel with Phase 2.

---

### Phase 4 — App DB, datasource API, catalog sync (≈ 2 days) · P0

- **Goal:** register data sources through the API and store their schema in our DB.
- **Build:**
  - SQLAlchemy models plus the first Alembic migration for `data_sources`, `catalog_tables`, `catalog_columns`, `table_relationships`. (The other tables come in the phases that need them.)
  - `core/crypto.py` (Fernet).
  - `DatasourceService`: create (test first, then save), list, get, delete, test, `build_connector()`.
  - `catalog/sync.py` (upsert that preserves user descriptions), `profiling.py` (sample values, `is_dimension`), `relationships.py` (FK import and seed file).
  - Routes: `POST /datasources`, `POST /datasources/test`, `POST /datasources/csv`, `GET /datasources`, `GET /datasources/{id}`, `DELETE /datasources/{id}`, `POST /datasources/{id}/sync`, `GET /datasources/{id}/schema`.
  - `scripts/bootstrap_demo.py`: registers the demo Postgres and demo DuckDB sources and syncs them (idempotent).
- **Files:** `app/db/*`, `alembic/versions/0001_*.py`, `app/core/crypto.py`, `app/services/datasource_service.py`, `app/catalog/*`, `app/api/routes/datasources.py`, `app/schemas/datasource.py`.
- **Learn:** SQLAlchemy 2.0 async (Mapped types, sessions); Alembic autogenerate; upsert (`ON CONFLICT`); Fernet; FastAPI dependency injection and `UploadFile`.
- **Done when:** with only `curl` or `/docs`, you can create a data source, sync it, and see its schema with sample values. No response ever contains a password.
- **Tests:** API integration tests (create, sync, schema); secret never in the response; re-sync keeps a user-edited description; deleting a data source cascades.
- **Depends on:** Phases 2 and 3.

---

### Phase 5 — LLM layer (≈ 1.5 days) · P0

- **Goal:** a provider-independent way to get **validated structured output** from free models.
- **Build:**
  - `llm/types.py`, `base.py`, `openai_compatible.py` (OpenRouter), `fake.py`, `structured.py` (three strategies), `client.py` (retries with tenacity, fallback chain, semaphore, budget hook, usage capture), `models.yaml`.
  - `scripts/check_models.py`.
  - Alembic migration for `llm_calls`, plus `observability/recorder.py`.
- **Files:** `app/llm/*`, `app/observability/recorder.py`.
- **Learn:** the OpenAI chat completions format (messages, tools, `tool_choice`, `response_format`); OpenRouter model IDs and limits; Pydantic `model_json_schema()`; exponential backoff; the "single chokepoint" pattern.
- **Done when:** `generate_structured(SomeModel)` works against at least 2 free models (one with tool support, one without), every call writes an `llm_calls` row, and a forced 404 model falls back to the next one.
- **Tests:** parsing (fenced JSON, trailing text); repair prompt built from the validation error; fallback order (using `FakeLLMProvider` to raise errors); usage recorded.
- **Depends on:** Phase 0 (Phase 4 for the DB).

---

### Phase 6 — RAG v1: vector retrieval and seed knowledge (≈ 2 days) · P0

- **Goal:** for any question, retrieve the right tables, definitions, and examples.
- **Build:**
  - Migration for `knowledge_chunks` (pgvector column plus HNSW index; add the `search_tsv` column now even though FTS is used only in Phase 14).
  - `rag/embeddings.py` (fastembed, model loaded once in lifespan), `chunks.py`, `indexer.py` (content-hash skip), `retriever.py` (vector + quotas + floor + relationship expansion), `context.py`.
  - Seed files `demo_data/knowledge/` with descriptions, ~8 definitions, ~8 example queries per engine, and relationships; `scripts/seed_knowledge.py`.
  - Indexing hooked into sync.
  - A debug endpoint, `POST /datasources/{id}/retrieve {question}`, that returns the chunks with scores.
- **Files:** `app/rag/*`, `app/services/knowledge_service.py`, `demo_data/knowledge/*`.
- **Learn:** embeddings and cosine similarity; pgvector operators (`<=>`) and HNSW; chunk design; why definitions come before tables in the prompt.
- **Done when:** "revenue last month" retrieves the `orders` table plus the Revenue definition, and "refunds by category" retrieves `refunds`, `order_items` and `products` plus the join edges.
- **Tests:** chunk text snapshot; retrieval returns the expected tables for 5 sample questions; isolation between data sources.
- **Depends on:** Phase 4.

---

### Phase 7 — Query Agent and the first end-to-end slice (≈ 2 days) · P0 · **Milestone 1**

- **Goal:** question → grounded SQL → validated → executed → rows, as a LangGraph run from the CLI.
- **Build:**
  - `agents/schemas.py` (`QueryAgentOutput`, `AnalysisFrame`), `agents/query_agent.py`, prompts (`query_agent.md`, `dialects/postgres.md`, `dialects/duckdb.md`).
  - `graph/state.py` (the subset needed now), nodes `load_context`, `retrieve_context`, `query_agent`, `validate_sql`, `execute_sql`, plus a temporary `finalize`, with the repair loop and routing functions.
  - Migration for `analyses` and `analysis_queries`; nodes persist the queries.
  - CLI: `python -m app.graph.cli --datasource demo_pg "What was revenue last month?"` prints the SQL, attempts and rows.
- **Files:** `app/agents/*`, `app/graph/*`.
- **Learn:** LangGraph `StateGraph`, nodes, conditional edges, reducers, `recursion_limit`; Text-to-SQL prompting; the repair loop.
- **Done when:** 8 of the 10 simple questions you try by hand return correct numbers, and repairs are visible in `analysis_queries`.
- **Tests:** routing functions (pure unit tests); graph test with FakeLLM: valid SQL → success, then invalid ×3 → failure, then invalid → repaired → success.
- **Depends on:** Phases 3, 5 and 6.

---

### Phase 8 — Evaluation v1 (≈ 1.5 days) · P0

- **Goal:** measure Text-to-SQL quality before building more.
- **Build:**
  - `evaluation/datasets/core.yaml`, starting with ~15 non-drill-down cases.
  - `compare.py`, `metrics.py`, `runner.py`, `report.py`, CLI.
  - `--retrieval-only` mode.
  - Migration for `evaluation_runs`.
- **Files:** `app/evaluation/*`, `reports/`.
- **Learn:** gold SQL; result-set comparison; precision and recall; recall@k; how to read eval failures.
- **Done when:** one command produces `reports/eval_*.md` with execution success, result accuracy, table recall, business-rule pass rate, retrieval recall@5, latency and tokens. You fix at least one prompt or knowledge issue it reveals.
- **Tests:** `compare.py` unit tests (column-name-agnostic, tolerance, ordering).
- **Depends on:** Phase 7.

---

### Phase 9 — Result profiler, Analysis Agent, drill-down loop (≈ 3 days) · P0 · **Milestone 2**

- **Goal:** "Why did revenue fall?" automatically investigates region, then category.
- **Build:**
  - `analytics/profiler.py` (stats, comparison contract, contributions, time series) and `fallback_findings.py`.
  - `agents/analysis_agent.py` plus its prompt.
  - Graph nodes `profile_result`, `analysis_agent`, `plan_drilldown`; drill-down mode in the Query Agent prompt; `graph/budget.py`; `stop_reason` handling.
- **Files:** `app/analytics/*`, `app/agents/analysis_agent.py`, `app/graph/nodes/{profile,analyze,drilldown}.py`, `app/graph/budget.py`.
- **Learn:** contribution analysis (share of change); loops in LangGraph; designing termination guarantees; keeping a "frozen frame" consistent across steps.
- **Done when:** the June question discovers South → Electronics on most runs, and the loop never exceeds depth 3 or the call budget, even with an adversarial FakeLLM that always asks to drill.
- **Tests:** profiler math; graph tests for depth cap, budget cap, dimension reuse rejected, garbage output falling back to deterministic findings. Add the 3 drill-down cases to the eval set.
- **Depends on:** Phases 7 and 8.

---

### Phase 10 — Visualization Agent and dashboard spec (≈ 1.5 days) · P0

- **Goal:** turn the analysis into a validated dashboard spec.
- **Build:** `dashboard/spec.py`, `validator.py`, `fallback_builder.py`, `hydrate.py`; `agents/visualization_agent.py` plus its prompt; nodes `visualization_agent`, `build_dashboard`, `finalize_*`; migration for `dashboards`.
- **Learn:** schema-constrained generation; why the LLM references data instead of copying numbers; partial-failure policies.
- **Done when:** every eval question produces a valid spec (from the LLM or the fallback), with no hallucinated column ever reaching the output.
- **Tests:** the validator drops bad widgets; the fallback builder handles every result shape; KPI values come from the data.
- **Depends on:** Phase 9.

---

### Phase 11 — Analysis API, background runs, telemetry (≈ 1.5 days) · P0

- **Goal:** the full analysis is available over HTTP with progress and history.
- **Build:**
  - `AnalysisService`: create, `asyncio.create_task`, `report_stage`, rollups, startup reconciliation.
  - Routes `POST /analyses`, `GET /analyses/{id}`, `GET /analyses`.
  - `@traced_node`; the `analysis_id` log context.
  - The response includes steps, queries, retrieved context, run stats and the hydrated dashboard.
- **Files:** `app/services/analysis_service.py`, `app/api/routes/analyses.py`, `app/schemas/analysis.py`, `app/observability/tracing.py`, `app/graph/progress.py`.
- **Learn:** FastAPI background execution trade-offs; 202 + polling pattern; designing read models for a UI.
- **Done when:** `curl` can start an analysis, poll it to completion, and fetch the dashboard JSON, and a server restart mid-run marks the run as failed.
- **Tests:** API lifecycle (with FakeLLM via dependency override); history ordering; error mapping.
- **Depends on:** Phase 10.

---

### Phase 12 — Frontend (≈ 4 days) · P0 · **Milestone 3 (demoable)**

- **Goal:** a clean product UI for the full flow.
- **Build (in this order):**
  1. Scaffold: Next.js, TS, Tailwind, shadcn/ui, TanStack Query, AppShell/Sidebar, `api-client.ts`, `types/api.ts`.
  2. `/analyze`: datasource select, question form, example questions.
  3. `/analyses/[id]`: polling, ProgressStepper, DashboardView (KPI, Chart switch, Table, Insight), SqlPanel, InvestigationTimeline, RetrievedContextPanel, RunStatsPanel, error state.
  4. `/history`.
  5. `/datasources`, `/datasources/new` (CSV upload + Postgres form + test), `/datasources/[id]` (schema browser, read-only for now).
- **Learn:** App Router basics; client components; TanStack Query (`refetchInterval`); Recharts (`ResponsiveContainer`, Line/Bar/Area); pivoting long-format rows for multi-series charts.
- **Done when:** the 2-minute demo (§3.1, without ClickHouse and refresh) runs smoothly in the browser.
- **Tests:** `npm run build` and type-check pass; optional Vitest for `format.ts`.
- **Depends on:** Phase 11.

---

### Phase 13 — ClickHouse and richer demo data (≈ 2 days) · P0

- **Goal:** prove the connector abstraction with a third engine, and make retrieval meaningful with a larger schema.
- **Build:**
  - Add `clickhouse` to compose with the init schema, a readonly user and a CSV load.
  - `ClickHouseConnector` (passing the **same contract tests**); sqlglot ClickHouse policy (`url`, `file`, `s3`, `remote`…); `dialects/clickhouse.md`; ClickHouse example queries.
  - Extend the generator with the **distractor tables** (§28.2) and the remaining patterns (§28.3); regenerate and reload all engines.
  - Add the remaining eval cases (to ~25).
- **Learn:** ClickHouse basics (MergeTree, `toStartOfMonth`, `sumIf`, `readonly` settings); why distractors matter for retrieval evaluation.
- **Done when:** the same June question gives the same answer on Postgres, DuckDB and ClickHouse **without any change to agents or graph code**.
- **Tests:** connector contract suite on ClickHouse; guard cases for the ClickHouse dialect.
- **Depends on:** Phase 12 (could also be done right after Phase 11).

---

### Phase 14 — P1 features (≈ 3–4 days; pick in this order) · P1

1. **Hybrid retrieval** (FTS + RRF) with a vector-vs-hybrid recall table in the eval report.
2. **Dashboard refresh** without LLM (`POST /analyses/{id}/refresh`, server-side SQL lookup).
3. **Knowledge editor UI** (definitions, example queries, descriptions → reindex).
4. **Eval v2**: RAG ablation, cross-engine run, numeric groundedness metric, `/evaluations` page.
5. **DEMO_MODE hardening** (required before public deploy).
6. Query Agent bounded tool loop (`lookup_column_values`).
7. OpenAPI-generated TS types.

- **Done when:** each feature has its tests, and the eval report shows the hybrid-vs-vector and RAG-ablation comparisons.
- **Depends on:** Phases 12 and 13.

---

### Phase 15 — Docker, deployment, CI (≈ 2 days) · P0 (CI P1)

- **Goal:** a public, working demo URL.
- **Build:** backend Dockerfile (model baked in) and entrypoint; full compose profile; GitHub Actions `ci.yml`; Neon setup; container host deploy; Vercel deploy; deployment runbook in the README (§22.3).
- **Learn:** multi-stage Docker builds; env-based config across environments; CORS; GitHub Actions service containers; measuring container memory.
- **Done when:** a stranger can open the URL and run the demo question, and CI is green on `main`.
- **Depends on:** Phase 14 (at minimum DEMO_MODE).

---

### Phase 16 — README, demo, resume (≈ 1 day) · P0

- **Goal:** turn the work into hiring signal.
- **Build:** README (§32 plan); architecture diagrams (reuse this doc's Mermaid); a 60–90s demo GIF/video; final eval numbers; fill in the resume bullet placeholders (§30) with **measured** values only.
- **Done when:** the README answers "what, why, how, how well, how to run" in under 5 minutes of reading.

**Total rough estimate:** ~5–7 weeks part-time for P0 plus core P1.

---

## 28. Demo Scenario

### 28.1 Business story

"**ShopSphere**", a fictional Indian e-commerce company (currency INR), selling Electronics, Fashion, Home & Kitchen, Beauty, Books and Sports in 4 regions (North, South, East, West) through 3 channels (web, mobile_app, marketplace). Reporting date (`as_of_date`) = **2026-06-30**.

### 28.2 Tables

**Core (Phase 1):**

| Table | Key columns | Notes |
|---|---|---|
| `customers` | id, name, email (fake), city, region, segment (`consumer`/`small_business`), signup_date | `customers.region` = home region (a trap: revenue reporting uses `orders.shipping_region`) |
| `products` | id, name, category, brand, unit_price, cost | ~60 products, 6 categories |
| `orders` | id, customer_id, order_date, status (`SUCCESS`/`FAILED`/`CANCELLED`/`PENDING`), channel, payment_method, shipping_region, total_amount, discount_amount | `total_amount` = sum of line amounts − discount |
| `order_items` | id, order_id, product_id, quantity, unit_price, line_amount | |
| `payments` | id, order_id, method (`card`/`upi`/`wallet`/`cod`), status (`captured`/`failed`), amount, paid_at | |
| `refunds` | id, order_id, order_item_id, amount, reason (`defective`/`wrong_item`/`late_delivery`/`changed_mind`), refunded_at | |

**Distractors (Phase 13)**, so retrieval actually has to choose:
`orders_legacy` (2023 archive, same columns as `orders`, described as "deprecated"), `order_events` (status change log), `web_sessions`, `marketing_campaigns`, `campaign_spend`, `inventory_snapshots`, `suppliers`, `warehouses`, `support_tickets`. That gives ~15 tables in total.

### 28.3 Planted patterns

| # | Pattern | How it's generated | Questions it powers | Phase |
|---|---|---|---|---|
| 1 | **June 2026 revenue drop (~15–20% MoM)**, concentrated in **South** (~−35%), and within South in **Electronics** (~−50%+) | Multiply the order probability for (South, Electronics) by ~0.4 in June; small noise elsewhere | "How did revenue change this month vs last?", "Why did revenue fall?", "Which region caused it?" | 1 |
| 2 | **Home & Kitchen refund spike** in May–June 2026 (refund rate ~3% → ~12%), mostly one product ("AeroBlend Pro Blender") with reason `defective` | Raise the refund probability for that product's line items | "Why did refunds increase?", "Which product has the most refunds?" | 1 |
| 3 | **~8% non-SUCCESS orders** | Status drawn per order | Every revenue question (business-rule trap) | 1 |
| 4 | **Seasonality**: Oct–Nov festive peak (+30–40%) | Monthly volume multiplier | "Monthly revenue trend" | 13 |
| 5 | **Customer concentration**: top 5% of customers ≈ 30% of revenue | Lognormal customer purchase propensity | "Top customers" | 13 |
| 6 | **Wallet payment failures** spike in one week of April 2026 | Raise `payments.status='failed'` for wallet in that week | "Why did payment success drop in April?" | 13 |
| 7 | **Mobile app share grows** from ~35% to ~50% over the year | Channel probability drifts over time | "How has channel mix changed?" | 13 |

`ground_truth.json` records the computed values (exact % changes, top segments, refund rates) after generation, and the eval reads them.

### 28.4 Demo script (final)

1. "What was revenue last month?" → a KPI, plus the SQL panel showing `status = 'SUCCESS'` came from the Revenue definition (shown in the retrieved context).
2. "Why did revenue fall in June 2026?" → drill-down South → Electronics, with the investigation timeline.
3. Same question on the **ClickHouse** source (locally) → a different dialect (`toStartOfMonth`, `sumIf`) and the same answer.
4. "Why did refunds increase?" → Home & Kitchen → AeroBlend Pro → `defective`.
5. Upload a new CSV → ask a question on it immediately.
6. Show the eval report: before/after RAG ablation numbers.

---

## 29. Interview Talking Points

1. **"Why not one big agent with tools?"** A free-roaming tool loop (as in Agent360) is flexible but costly and unpredictable on weak models. I encoded the known analytics procedure as a LangGraph state machine and kept the LLM for three judgment steps. Result: a bounded cost of 3–12 calls, deterministic tests, and explainable failures.
2. **"How do you stop the agent looping forever?"** Code-enforced depth, call and token budgets, dimension-reuse checks, duplicate detection, a reserved call for the final step, and a `recursion_limit` backstop. The LLM *proposes* a drill-down; deterministic code *approves* it.
3. **"How is the SQL safe?"** Defense in depth: read-only credentials, read-only sessions, a sqlglot AST validator (single statement, no DML/DDL anywhere in the tree, function denylist per dialect, table allowlist, outer LIMIT rewrite), and server-side timeouts. I can show why a regex prefix check fails (`WITH … DELETE`, `SELECT 1; DROP`).
4. **"What does RAG do here?"** It retrieves analytics context: schema, business definitions and verified SQL examples, using hybrid vector and keyword search fused with RRF. Definitions fix "valid but wrong" SQL. I measured retrieval recall@k and ran an ablation showing the effect on answer accuracy ([X%] → [Y%]).
5. **"How do you know it works?"** Synthetic data with planted anomalies gives objective ground truth. Gold SQL is compared result-set to result-set, and the suite also checks business-rule predicates via the AST, drill-down paths, and number groundedness.
6. **"Why can't the LLM hallucinate numbers on the dashboard?"** Widgets reference executed query IDs; KPI values are read from results; the spec validator drops widgets with unknown columns; insight numbers are checked against results.
7. **"How would you scale this?"** A job queue in place of in-process tasks, connector pooling, async drivers, cached catalog embeddings, per-tenant isolation, and auth. All are listed as P3, with the seams already in place (services layer, connector factory, LLM chokepoint).
8. **"Free models are unreliable. How did you cope?"** Capability-aware structured output (tool mode, JSON schema, or prompt-JSON), per-field repair prompts, a fallback model chain, a concurrency semaphore, Retry-After handling, and deterministic fallbacks for analysis and visualization so a run still completes.

---

## 30. Resume Positioning

**Project description (1 line):**
> **InsightFlow**: an agentic analytics platform that turns business questions into dashboards over PostgreSQL, ClickHouse and CSV (DuckDB) using LangGraph, schema/business-definition RAG, and a parser-based SQL safety layer.

**Bullet templates (fill in with measured numbers only):**

- Built a **LangGraph** multi-agent pipeline (Query, Analysis and Visualization agents with deterministic validation and execution nodes) that performs **bounded automatic drill-down** root-cause analysis. It reached **[X%] result accuracy** on a [N]-question golden eval set at **[P95 latency]** and **≤ [K] LLM calls** per analysis.
- Designed **hybrid RAG** (pgvector + Postgres full-text search, RRF fusion) over schema metadata, business definitions and verified SQL. It improved retrieval recall@5 from **[A%] to [B%]** and Text-to-SQL accuracy from **[C%] to [D%]** in an ablation study.
- Implemented a **sqlglot AST-based SQL guard** and a pluggable **connector abstraction** (PostgreSQL, ClickHouse, DuckDB) with read-only sessions, timeouts and row caps. Shipped with FastAPI, Next.js, Docker and GitHub Actions, deployed on [hosts].

---

## 31. Future Improvements (P3)

- Authentication, users, per-user data sources; multi-tenancy.
- Redshift/BigQuery/Snowflake connectors (Redshift ≈ a Postgres subclass).
- Interactive dashboard filters via parameterized `{FILTER_WHERE}` (Agent360 pattern).
- Follow-up questions on an existing analysis (conversation memory).
- Rolling-window dashboards with parameterized dates, and scheduled refresh.
- Job queue (Arq/Celery + Redis) and connection pooling.
- LLM-drafted table/column documentation with human review (Agent360 autofill).
- Column-level exclusions / PII tagging.
- Save good generated queries as verified examples (a feedback loop into RAG).
- OpenTelemetry tracing and a metrics dashboard.
- Semantic layer (metrics defined once, compiled to SQL), e.g. a dbt-metrics-style YAML.

---

## 32. Final MVP Definition

The MVP is **done** when all of these are true:

1. ☐ A user can connect a **CSV**, **PostgreSQL** or **ClickHouse** source (ClickHouse locally) and see its discovered schema.
2. ☐ The user can ask a business question and get a dashboard with KPI cards, at least one chart, a table, and written insights, without a chat interface.
3. ☐ "Why did revenue fall in June 2026?" automatically drills down to **South → Electronics** on the demo data, with a visible investigation timeline.
4. ☐ Every executed SQL statement passed the sqlglot guard, ran on a read-only connection with a timeout and a row cap, and is visible in the UI.
5. ☐ Retrieved RAG context (tables, definitions, examples and their scores) is visible for each analysis.
6. ☐ Each analysis records LLM calls, tokens, latency, SQL timings and the stop reason.
7. ☐ The eval suite (~25 cases) runs with one command and produces a report with execution success, result accuracy, table recall, business-rule pass rate, retrieval recall@k, drill-down correctness, latency and tokens.
8. ☐ Unit, integration and FakeLLM graph tests pass.
9. ☐ The app is deployed at a public URL (CSV + Postgres demo sources), and the README has architecture, setup, eval results and a demo GIF.

Anything not on this list is P1+ and must not block the MVP.

---

## Appendix A — Architecture Decision Records

### ADR-01: LangGraph vs a simple agent loop
- **Decision:** orchestrate the analysis as a LangGraph `StateGraph`.
- **Options:** (a) hand-rolled tool-use loop (Agent360); (b) plain Python `while` loop with if/else; (c) LangGraph.
- **Chosen:** (c).
- **Why:** typed shared state, explicit conditional edges, one bounded cycle, per-edge unit tests, visualizable graph, optional LangSmith tracing. The workflow is a known procedure, so the structure should live in code, not in the LLM's choices.
- **Trade-offs:** a dependency and some framework learning; (b) would also work. We avoid LangChain agents/chains and the checkpointer to keep the framework surface small.

### ADR-02: PostgreSQL as the application DB
- **Decision:** PostgreSQL 16 + pgvector for all of InsightFlow's own state.
- **Options:** SQLite; Postgres; Postgres + a separate vector DB.
- **Chosen:** Postgres + pgvector.
- **Why:** JSONB for specs and results; FTS for hybrid search; vectors in the same transactional store; free hosted options (Neon); industry standard.
- **Trade-offs:** heavier than SQLite locally (Docker solves this).

### ADR-03: pgvector vs FAISS vs Qdrant
- **Decision:** pgvector.
- **Options:** FAISS (in-process), Qdrant (service), pgvector.
- **Why:** zero extra infrastructure; metadata filters are plain SQL (`WHERE data_source_id = …`); transactional upserts alongside catalog edits; hybrid with Postgres FTS in a single query; free on Neon. Our corpus is tiny (hundreds of chunks).
- **Trade-offs:** less vector-search tuning than Qdrant; doesn't matter at this scale. FAISS would need persistence and rebuild logic per process.

### ADR-04: DuckDB for CSV
- **Decision:** import CSVs into a DuckDB file and query with SQL.
- **Options:** pandas agent; SQLite import; DuckDB.
- **Why:** keeps one SQL-centric pipeline for all sources (same agent, guard, profiler); fast columnar engine; good type inference; can run with external access disabled.
- **Trade-offs:** a third SQL dialect to support (sqlglot handles it).

### ADR-05: Next.js vs Streamlit
- **Decision:** Next.js + TypeScript + shadcn/ui.
- **Why:** a real product UI and a real API contract; shadcn/ui and Recharts keep the effort moderate; deploys free on Vercel.
- **Trade-offs:** more frontend work than Streamlit (~4 days vs ~1.5). Accepted, because it demonstrates full-stack ability.

### ADR-06: Recharts vs Plotly vs ECharts
- **Decision:** Recharts.
- **Why:** React-native components fed with JSON rows; covers line/bar/area/pie; small; same as Agent360 (proven for this use case).
- **Trade-offs:** fewer advanced charts (no heatmaps/sankey). Not needed.

### ADR-07: Own LLM abstraction over OpenRouter
- **Decision:** an `LLMProvider` interface with an OpenAI-compatible implementation targeting OpenRouter; `LLMClient` as the single chokepoint.
- **Why:** models and providers change; free models vary in capabilities; one place for retries, fallback, structured output, budgets and telemetry; easy fakes for tests.
- **Trade-offs:** a little code we could have gotten from LangChain, but ours is ~300 lines and fully understood.

### ADR-08: sqlglot-based SQL validation
- **Decision:** parse and validate SQL as an AST with sqlglot, per dialect.
- **Options:** regex/prefix checks (Agent360); DB-side read-only only; sqlglot + DB-side read-only.
- **Why:** catches multi-statement, nested DML and dangerous functions; enables a table allowlist and a correct outer LIMIT; also powers eval (table/column extraction). DB-side read-only stays as a second layer.
- **Trade-offs:** sqlglot may fail to parse rare valid dialect syntax → treated as a rejection with feedback (the repair loop usually rewrites it).

### ADR-09: Persist dashboard SQL (by query reference)
- **Decision:** widgets reference `analysis_queries` rows (which store the SQL); refresh re-executes them server-side (P1).
- **Options:** store only rendered data; store SQL per widget written by the Viz Agent (Agent360); reference executed queries.
- **Why:** SQL is persisted for free; no new SQL is written at visualization time (fewer failures); refresh needs no LLM; the client never sends SQL.
- **Trade-offs:** a widget can only show data from a query the pipeline actually ran. That's a feature (everything is traceable).

### ADR-10: Deployment architecture
- **Decision:** Vercel (frontend) + free container host (FastAPI) + Neon (app DB + demo Postgres); ClickHouse demonstrated locally.
- **Options:** single free VM with full compose; PaaS split; paid managed services.
- **Why:** cheapest reliable path to a public URL with minimal ops; every piece has a free tier.
- **Trade-offs:** cold starts on free hosts; ephemeral CSV uploads; ClickHouse not live in the public demo (VM option P2). Free-tier terms change; verify at deploy time.

### ADR-11: Polling vs SSE for progress
- **Decision:** persist the stage to the DB; poll every 1s.
- **Why:** survives refresh/restart, trivial on both sides, and history comes for free.
- **Trade-offs:** up to ~1s of UI latency. SSE is P2.

### ADR-12: Credentials persisted encrypted
- **Decision:** Fernet-encrypted secret field, never returned, never logged; DEMO_MODE disables custom connections publicly.
- **Why:** needed for history, refresh and eval; simple and defensible.
- **Trade-offs:** a single env key with no rotation (MultiFernet rotation is P3).

---

## Appendix B — README Plan

The final `README.md`, in this order:

1. **Title + one-line pitch** + badges (CI, license) + **live demo link** (note the cold start).
2. **Demo GIF** (≤ 90s: ask "Why did revenue fall?" → drill-down → dashboard) + link to a longer video.
3. **What it does**: 4–5 bullets (grounded Text-to-SQL, automatic drill-down, safe SQL, structured dashboards, evaluated).
4. **Architecture**: system diagram + LangGraph diagram (Mermaid from this doc) + 1 paragraph on the two data planes.
5. **Key features, with the engineering behind them**: RAG (what's indexed, hybrid retrieval), drill-down (limits), SQL guard (layers), dashboard spec, LLM abstraction and fallbacks, observability.
6. **Evaluation results**: metrics table, RAG ablation, vector vs hybrid recall, cross-engine table, how to reproduce (`python -m app.evaluation.run …`). Real numbers only.
7. **Tech stack** table with a one-line "why" each.
8. **Supported data sources** (CSV/DuckDB, PostgreSQL, ClickHouse; Redshift planned) and the read-only setup SQL.
9. **Quickstart**: `cp .env.example .env` → `docker compose up` → `bootstrap_demo` → `npm run dev`. Under 5 commands.
10. **Example analysis**: the June question with screenshots of the result, SQL, and timeline.
11. **Screenshots**: datasources, schema/knowledge, result dashboard, eval page.
12. **Project structure** (short tree).
13. **Limitations**, stated honestly: no auth; free-model variability; ephemeral uploads in the demo; ClickHouse local-only in the demo; small eval set.
14. **Future improvements** (from §31).
15. **Acknowledgements / design notes**: link to `plans/PROJECT_PLAN.md`.

---

## Appendix C — Glossary

| Term | Meaning |
|---|---|
| **AST** | Abstract Syntax Tree: the parsed tree form of a SQL statement, which we inspect instead of raw text |
| **Analysis frame** | The metric definition + periods fixed on the first query and reused in all drill-down steps |
| **Chunk** | One retrievable unit of knowledge (a table, a definition, an example query) with its embedding |
| **Contribution / share of change** | How much of a total change one segment explains: (segment Δ) / (total Δ) |
| **Dialect** | The SQL flavor of an engine (postgres, clickhouse, duckdb) |
| **Drill-down** | Breaking a result down by another dimension, filtered to the segment that matters |
| **FTS** | Full-text search (Postgres `tsvector`/`tsquery`): keyword-based retrieval |
| **Gold SQL** | A hand-written correct query whose result is the ground truth for an eval case |
| **Ground truth** | Facts known to be true because we planted them in the synthetic data |
| **Hydration** | Attaching the actual result data to a dashboard spec before sending it to the UI |
| **RRF** | Reciprocal Rank Fusion: merges ranked lists by summing 1/(c + rank) |
| **Structured output** | LLM output forced to match a JSON schema (via tool calling or JSON mode) and validated by Pydantic |
| **Two data planes** | Our metadata DB (system plane) vs the customer's data (analytical plane) |
