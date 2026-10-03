# InsightFlow — 2-Day Sprint Plan (extendable slice)

> Companion to [`PROJECT_PLAN.md`](PROJECT_PLAN.md), which stays the architecture reference.
> This document is the build checklist for the first 2 days, and the proof that
> everything deferred is **additive**.

---

## 1. The governing rule

> **Cut implementations, never seams.**

In a 2-day build the temptation is to inline everything — call `psycopg` directly from the
graph, call OpenRouter directly from the agent, hardcode `postgres` in the prompt. That
saves perhaps 90 minutes and costs a rewrite the moment you add the second engine.

So the sprint keeps **every interface** from the full plan and drops only the
**implementations behind them**. Concretely:

| Seam kept on day 2 | Implementations on day 2 | Added later with no rewrite |
|---|---|---|
| `DataConnector` ABC + `ConnectorFactory` | Postgres, DuckDB | ClickHouse, Redshift |
| `dialect: str` threaded through prompt + guard + examples | `postgres`, `duckdb` | `clickhouse` = one prompt file + one policy entry + example queries |
| `LLMProvider` ABC + `LLMClient` chokepoint | OpenRouter, Fake | Gemini, Groq, OpenAI = one subclass or just config |
| `EmbeddingProvider` ABC | fastembed (local) | hosted embedding API if the free host is RAM-tight |
| `Retriever` returning ranked, scored chunks | vector arm only | keyword arm + RRF fusion = one new arm, same return type |
| `knowledge_chunks.kind` discriminator | `table`, `definition`, `example_query` | `doc` chunks, column-level chunks |
| Graph nodes as pure `(state) -> state` fns + separate routing fns | 9 nodes | new nodes are additive edges |
| `DashboardSpec` widget `type` discriminator | kpi, line, bar, table, insight | area, pie, new widget types |
| `analysis_queries` row per executed SQL | written every run | refresh-without-LLM reads these; no schema change |
| Alembic migrations | initial migration | every later table is a new revision |
| `services/` boundary between API and graph | — | API contract stays stable as internals change |

**One deliberate exception to "keep the seam":** no LangGraph checkpointer and no job
queue. Both are replaceable at the service boundary (`AnalysisService` owns how a run is
executed), so adding them later touches one file.

### What is cut from the 2 days

ClickHouse · evaluation suite · hybrid retrieval · dashboard refresh · deployment · CI ·
history page · knowledge editor UI · datasource CRUD UI · auth. Each has an entry in §5
showing exactly what it costs to add.

---

## 2. Day 1 — foundation to first grounded SQL

**Target:** `python -m app.graph.cli --datasource demo_pg "What was revenue last month?"`
prints the retrieved context, the generated SQL, and the correct number.

### Block 1 — Scaffold (~1h)

- `backend/` via `uv init`; FastAPI app factory + `GET /health`; `core/config.py`
  (pydantic-settings, every var from PROJECT_PLAN §8.3); JSON logging with an
  `analysis_id` context filter.
- `docker-compose.yml`: `appdb` (pgvector/pgvector:pg16) + `demo-postgres` (postgres:16).
  Two services, not one — the two-planes story is the architecture, and it costs 10 lines.
- `.env.example`, `.gitignore`, ruff config.

**Files:** `backend/pyproject.toml`, `app/main.py`, `app/core/{config,logging}.py`,
`app/api/routes/health.py`, `docker-compose.yml`

**Done:** `docker compose up -d` healthy; `/health` returns 200; `ruff check` clean.

### Block 2 — Demo data (~1h)

- `scripts/generate_demo_data.py`: fixed seed, 6 tables, ~25k orders over 12 months
  (Jul 2025 – Jun 2026). Plant **only 3 patterns**: June 2026 South→Electronics revenue
  drop; Home & Kitchen refund spike; ~8% non-`SUCCESS` orders.
- Compute `ground_truth.json` **from** the generated data — never hardcode the numbers.
- `demo_data/postgres/init/{00_schema,01_load,02_readonly_role}.sql`.

**Extendability note:** the generator takes a `--patterns` list and writes CSVs. Adding the
seasonality / concentration / payment-failure patterns later is new functions, and the
same CSVs load into ClickHouse unchanged.

**Done:** `psql` shows 6 tables; a hand-written query reproduces each `ground_truth.json` fact.

### Block 3 — Connectors (~2h)

- `connectors/types.py` (`SchemaSnapshot`, `QueryResult`, `ColumnProfile`, `ForeignKey`),
  `base.py` (ABC), `normalize.py`, `errors.py` (typed exceptions), `factory.py`.
- `PostgresConnector`: read-only session + `statement_timeout` at connect, introspection
  (information_schema + comments + FKs), `fetchmany(max_rows+1)`, simple profiling.
- `csv_import.py` (CSV → `.duckdb`, external access on only for import) +
  `DuckDBConnector` (read_only, `enable_external_access=false`).
- **Write the contract test suite now**, parametrized over connector type. This is the
  thing that makes ClickHouse a 2-hour add instead of a gamble.

**Files:** `app/connectors/*`, `tests/unit/test_connector_contract.py`

**Done:** both connectors pass the same contract tests; demo CSVs in DuckDB match Postgres results.

### Block 4 — SQL guard (~1.5h)

- `sql_guard/{validator,policy,suggestions}.py` per PROJECT_PLAN §14.2.
- `policy.py` is a **dict keyed by dialect** — adding ClickHouse's denylist
  (`url`, `file`, `s3`, `remote`, `mysql`) is one entry, not a code change.
- Table-driven tests: ≥30 cases across allowed (CTEs, windows, unions, subqueries) and
  rejected (multi-statement, DML-in-CTE, DDL, `pg_sleep`, `read_csv`, unknown table,
  `SELECT *`), plus LIMIT-rewrite cases.

**Done:** `validate_sql()` returns safe SQL + extracted tables; tests green.
**This is the highest value-per-hour block in the sprint — do not trim it.**

### Block 5 — App DB, datasources, catalog sync (~2.5h)

- SQLAlchemy models + **Alembic initial migration** for `data_sources`,
  `catalog_tables`, `catalog_columns`, `table_relationships`, `knowledge_chunks`,
  `analyses`, `analysis_queries`, `llm_calls`, `dashboards`.
  **Create all of them now**, including `knowledge_chunks.search_tsv` (unused until
  hybrid retrieval) and `dashboards.refreshed_at` (unused until refresh). Empty columns
  cost nothing; a later migration on a live demo costs an afternoon.
- `core/crypto.py` (Fernet); `DatasourceService` (create / test-then-save / build_connector);
  `catalog/{sync,profiling,relationships}.py`.
- `scripts/bootstrap.py`: idempotent — registers `demo_pg` + `demo_csv`, syncs both.

**Skipped:** datasource HTTP routes. The bootstrap script is enough for 2 days; the
service layer is already there, so adding routes later is ~40 min.

**Done:** `python -m app.scripts.bootstrap` registers and syncs both sources; a re-sync
preserves a hand-edited description.

### Block 6 — LLM layer (~1.5h)

- `llm/{types,base,openai_compatible,fake,structured,client}.py` + `models.yaml`.
- `LLMClient.generate_structured(schema)` with the three strategies (tool-call /
  json-schema / prompt-JSON), per-field repair prompt, fallback chain, tenacity retries,
  `asyncio.Semaphore(2)`, and an `llm_calls` row per attempt.
- `scripts/check_models.py` to verify which free models actually support tools today.

**Done:** structured output works against 2 free models; a forced-404 model falls back;
`FakeLLMProvider` drives tests with zero network.

### Block 7 — RAG (~2h)

- `rag/{embeddings,chunks,indexer,retriever,context}.py`; vector-only retrieval with
  per-kind quotas, similarity floor, and deterministic relationship expansion.
- `retriever.search()` returns `list[ScoredChunk]` with a `method` field — the exact
  signature hybrid will return, so the caller never changes.
- Seed `demo_data/knowledge/`: table descriptions, ~8 definitions (Revenue, Net revenue,
  Orders, AOV, Refund rate, Active customer, Region, Category), ~6 example queries.
- Indexing hooked into sync; `POST /datasources/{id}/retrieve` debug route.

**Done:** "revenue last month" retrieves `orders` + the Revenue definition;
"refunds by category" retrieves `refunds`/`order_items`/`products` + join edges.

### Block 8 — Query Agent + first graph slice (~2h)

- `agents/schemas.py`, `agents/query_agent.py`, prompts
  (`query_agent.md`, `dialects/postgres.md`, `dialects/duckdb.md`).
- `graph/state.py`; nodes `load_context`, `retrieve_context`, `query_agent`,
  `validate_sql`, `execute_sql`, temporary `finalize`; `graph/routing.py` with the repair loop.
- `graph/cli.py`.

**Done — Day 1 checkpoint:** the CLI answers 6–8 of 10 hand-tried questions correctly,
and repair attempts are visible in `analysis_queries`.

---

## 3. Day 2 — drill-down, dashboard, UI

**Target:** "Why did revenue fall in June 2026?" produces a dashboard in the browser with
the South → Electronics investigation visible.

### Block 9 — Profiler + Analysis Agent + drill-down loop (~3h)

- `analytics/profiler.py` (stats, comparison contract, contribution shares, time series)
  and `analytics/fallback_findings.py`.
- `agents/analysis_agent.py` + prompt; nodes `profile_result`, `analysis_agent`,
  `plan_drilldown`; `graph/budget.py`; drill-down mode in the Query Agent prompt.
- Graph tests with an **adversarial FakeLLM that always requests a drill-down** — proves
  depth and budget caps hold regardless of model behaviour.

**Done:** the June question finds South → Electronics; the loop never exceeds depth 3 or
the call budget.

### Block 10 — Visualization Agent + dashboard (~1.5h)

- `dashboard/{spec,validator,fallback_builder,hydrate}.py`;
  `agents/visualization_agent.py` + prompt; nodes `visualization_agent`,
  `build_dashboard`, `finalize_success`, `finalize_failure`.
- Widgets reference `analysis_queries.id` — which is why refresh later needs no new schema.

**Done:** every tried question yields a valid spec (LLM or fallback); no hallucinated
column ever reaches the output.

### Block 11 — Analysis API (~1.5h)

- `AnalysisService`: create → `asyncio.create_task` → `report_stage` → rollups; startup
  reconciliation of orphaned `running` rows.
- Routes `POST /analyses` (202), `GET /analyses/{id}`, `GET /analyses`.

**Extendability note:** the service owns *how* a run executes. Swapping
`asyncio.create_task` for a job queue later is one method body.

**Done:** curl can start a run, poll it, and fetch the hydrated dashboard.

### Block 12 — Frontend (~4h)

Two pages only:

- `/analyze` — datasource select (from `GET /datasources`), question box, example questions.
- `/analyses/[id]` — TanStack Query polling at 1s, `ProgressStepper`, `DashboardView`
  (KpiCard / ChartWidget switch / DataTableWidget / InsightCard), `SqlPanel`,
  `InvestigationTimeline`, `RetrievedContextPanel`, error state.

Scaffold with Next.js + TS + Tailwind + shadcn/ui + Recharts; all fetches through
`lib/api-client.ts`; feature-sliced folders exactly as PROJECT_PLAN §25.3 — so
`/history`, `/datasources` and `/evaluations` drop in as new folders later.

**Done — Day 2 checkpoint:** the demo runs end-to-end in the browser.

---

## 4. Honest risk list for a 2-day build

| Risk | Mitigation |
|---|---|
| **Free-model daily cap** (small unless ~$10 credit purchased) stops you mid-build | Run `check_models.py` in Block 6 and decide then; `FakeLLMProvider` means all non-agent work proceeds without quota |
| Free models fail structured output | Three strategies + repair + fallback chain are built in Block 6, before any agent depends on them |
| Drill-down doesn't trigger reliably | The profiler computes contribution shares deterministically; the agent only *chooses*, so the signal is always present |
| Block 12 overruns (frontend always does) | Ship `/analyses/[id]` first; `/analyze` can be a form posting to the same page. Worst case, demo via CLI on day 2 and finish UI day 3 |
| Prompt iteration eats hours | Timebox to 2 iterations per agent; the Day 3 eval suite is the principled way to improve prompts, not guesswork |

---

## 5. Extension ledger — what each deferred item costs

Each row is additive. None requires touching the graph, the agents, or the API contract.

| Deferred item | Files touched | New code? | Est. |
|---|---|---|---|
| **ClickHouse connector** | `connectors/clickhouse.py` (new), `sql_guard/policy.py` (+1 dict entry), `agents/prompts/dialects/clickhouse.md` (new), compose service, example queries YAML | Additive only — passes the existing contract suite | ~2–3h |
| **Evaluation suite** | `evaluation/*` (new), `evaluation_runs` migration | Reads existing `analyses`/`analysis_queries`; uses `sql_guard` for table extraction | ~4h |
| **Hybrid retrieval** | `rag/retriever.py` (add keyword arm + RRF), `rag/fusion.py` (new) | `search()` signature unchanged; `search_tsv` column already exists | ~2h |
| **Dashboard refresh** | `dashboard/refresh.py` (new), one route | Reads SQL from `analysis_queries`; `refreshed_at` column already exists | ~2h |
| **Datasource + history + knowledge UI** | New folders under `features/`, new routes | Service layer already complete | ~4h |
| **Deployment** | Dockerfile, entrypoint, Neon setup, Vercel | Config only — no app code | ~4h |
| **CI** | `.github/workflows/ci.yml` | Tests already exist | ~1h |
| **Extra demo patterns** | `generate_demo_data.py` | New pattern functions | ~1h |

**Recommended day 3–4 order:** evaluation suite → ClickHouse → deployment → CI → hybrid
retrieval. Evals first, because they tell you whether prompt changes help, which makes
every later hour more productive.

---

## 6. Day-2 definition of done

1. ☐ `docker compose up` + `python -m app.scripts.bootstrap` gives two working datasources (Postgres + CSV/DuckDB)
2. ☐ A question typed in the browser returns a dashboard with KPIs, a chart, a table and insights
3. ☐ "Why did revenue fall in June 2026?" drills to South → Electronics with a visible timeline
4. ☐ Every executed SQL passed the sqlglot guard and is visible in the UI
5. ☐ Retrieved RAG context is visible per analysis
6. ☐ `llm_calls` + `analysis_queries` populated; run stats shown
7. ☐ SQL guard, routing and profiler tests pass
8. ☐ Adding a third connector requires no change to `agents/` or `graph/`
