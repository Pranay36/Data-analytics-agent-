# InsightFlow — How It Works

A complete explanation of the project in plain language. Read top to bottom the first time;
after that, use it as a reference.

---

## Table of contents

1. [The problem we are solving](#1-the-problem-we-are-solving)
2. [The big picture](#2-the-big-picture)
3. [Two databases, and why](#3-two-databases-and-why)
4. [What happens when you ask a question](#4-what-happens-when-you-ask-a-question)
5. [Step 1 — Retrieval (RAG)](#5-step-1--retrieval-rag)
6. [Step 2 — The Query Agent](#6-step-2--the-query-agent)
7. [Step 3 — The SQL Guard](#7-step-3--the-sql-guard)
8. [Step 4 — Running the query](#8-step-4--running-the-query)
9. [Step 5 — The Profiler](#9-step-5--the-profiler)
10. [Step 6 — The Analysis Agent and the drill-down loop](#10-step-6--the-analysis-agent-and-the-drill-down-loop)
11. [Step 7 — The Visualization Agent](#11-step-7--the-visualization-agent)
12. [The LLM layer](#12-the-llm-layer)
13. [Authentication and multi-user](#13-authentication-and-multi-user)
14. [The database tables](#14-the-database-tables)
15. [The evaluation suite](#15-the-evaluation-suite)
16. [The frontend](#16-the-frontend)
17. [Deployment](#17-deployment)
18. [The design rule behind everything](#18-the-design-rule-behind-everything)

---

## 1. The problem we are solving

A business person wants to know *"Why did revenue fall last month?"*

Normally they ask a data analyst. The analyst writes SQL, looks at the result, writes more SQL
to dig deeper, and comes back in two days with a chart and an explanation.

We are automating that analyst.

### Why this is harder than it sounds

The obvious approach is: give the question and the database schema to an AI, let it write SQL,
run it. That approach **fails badly**, for one specific reason:

> The AI writes SQL that runs perfectly and returns the wrong number.

Here is a real example from our own data:

```sql
SELECT SUM(total_amount) FROM orders          -- ₹272,169,980   ← WRONG
SELECT SUM(total_amount) FROM orders
WHERE status = 'SUCCESS'                      -- ₹251,928,208   ← correct
```

The difference is **₹20 million**. The first query is valid SQL. It does not crash. It returns
a clean, plausible number. The only problem is that it counts orders that were cancelled or
failed.

Nothing in the database schema says "revenue excludes failed orders". That rule lives in
someone's head. This is the failure this entire project is built around:
**a wrong answer that looks right**.

### The three things we must get right

| Must | Why |
|---|---|
| The SQL must use the **right business rules** | Otherwise the number is silently wrong |
| The SQL must be **safe** | An AI must never be able to delete your data |
| The investigation must **stop** | An AI left to "keep digging" will dig forever and cost money |

---

## 2. The big picture

```
┌──────────────┐
│   Browser    │  Next.js app — login, ask a question, see the dashboard
└──────┬───────┘
       │ HTTPS
┌──────▼───────────────────────────────────────────────────────┐
│                      FastAPI backend                         │
│                                                              │
│   ┌────────┐   ┌─────────┐   ┌───────────┐   ┌───────────┐   │
│   │  Auth  │   │  RAG    │   │ LangGraph │   │ SQL Guard │   │
│   │  JWT   │   │ search  │   │ pipeline  │   │  sqlglot  │   │
│   └────────┘   └─────────┘   └───────────┘   └───────────┘   │
└───────┬───────────────────────────┬──────────────────────────┘
        │                           │
┌───────▼─────────┐        ┌────────▼──────────┐      ┌──────────────┐
│  App database   │        │ Customer database │      │  LLM APIs    │
│  (our data)     │        │ (their data)      │      │ Groq/Gemini  │
│  Postgres +     │        │ Postgres, read    │      │ /OpenRouter  │
│  pgvector       │        │ only              │      │ (free tiers) │
└─────────────────┘        └───────────────────┘      └──────────────┘
```

**In one sentence:** the browser sends a question, the backend finds relevant context, asks an
AI to write SQL, checks that SQL is safe, runs it on the customer's database, does the maths
itself, asks the AI what it means, and sends back a dashboard.

---

## 3. Two databases, and why

This is one of the most important design decisions, and interviewers like it.

```
   SYSTEM PLANE                        ANALYTICAL PLANE
   "our data"                          "their data"

┌────────────────────┐              ┌────────────────────┐
│  appdb             │              │  customer database │
│  (Postgres 16 +    │              │  (Postgres)        │
│   pgvector)        │              │                    │
│                    │              │                    │
│  • users           │              │  • orders          │
│  • analyses        │              │  • customers       │
│  • SQL we ran      │              │  • products        │
│  • embeddings      │              │  • payments        │
│  • LLM call logs   │              │                    │
│                    │              │                    │
│  we READ and WRITE │              │  we only READ      │
│  full access       │              │  read-only role    │
└────────────────────┘              └────────────────────┘
```

**Why separate them?**

1. **Safety.** We never copy the customer's data into our system. We query it where it lives
   and only keep a small preview of the results. If our database is breached, their business
   data is not in it.
2. **Permissions.** We connect to their database as a role called `insightflow_ro` that is
   only allowed to `SELECT`. Even if every other safety check failed, the database itself
   would refuse a write.
3. **Reality.** In production, our database would be a managed service (RDS, Cloud SQL) and
   theirs would be their own warehouse. Keeping them separate from day one means no rewrite
   later.

In local development these are two separate Docker containers on ports 5432 and 5433.

---

## 4. What happens when you ask a question

This is the heart of the project. The pipeline is built with **LangGraph**, which is a library
for building state machines — a set of steps where each step can decide which step runs next.

```
                      "Why did revenue fall in June?"
                                  │
                                  ▼
                        ┌──────────────────┐
                        │  load context    │  which database? what are its tables?
                        └────────┬─────────┘
                                 ▼
                        ┌──────────────────┐
                        │ retrieve context │  RAG: find relevant tables + business rules
                        └────────┬─────────┘
                                 ▼
                        ┌──────────────────┐
                        │   Query Agent    │  🤖 AI writes the SQL
                        └────────┬─────────┘
                                 ▼
                        ┌──────────────────┐
                        │    SQL Guard     │  🔒 code checks the SQL is safe
                        └────────┬─────────┘
                           ok ↓     ↓ rejected
                              │     └──→ send error back to Query Agent (max 2 repairs)
                              ▼
                        ┌──────────────────┐
                        │   execute SQL    │  run it on the customer database
                        └────────┬─────────┘
                                 ▼
                        ┌──────────────────┐
                        │    Profiler      │  🧮 code calculates all the numbers
                        └────────┬─────────┘
                                 ▼
                        ┌──────────────────┐
                        │  Analysis Agent  │  🤖 AI explains what it means
                        └────────┬─────────┘
                                 ▼
                         needs to dig deeper?
                          ┌──────┴───────┐
                       yes│              │no
                          │              ▼
                          │     ┌──────────────────┐
                          │     │  Visualization   │  🤖 AI designs the dashboard
                          │     │     Agent        │
                          │     └────────┬─────────┘
                          │              ▼
                          │     ┌──────────────────┐
                          │     │    finalize      │  save everything
                          │     └──────────────────┘
                          │
                          └──→ back to Query Agent with a narrower question
                               (allowed at most 3 times — see §10)
```

**🤖 = an AI call. 🔒🧮 = plain code.** There are only three AI calls in a simple question, and
the number is capped at 12 for an investigation.

---

## 5. Step 1 — Retrieval (RAG)

**RAG** stands for Retrieval-Augmented Generation. In plain terms: *before asking the AI, go
find the relevant information and put it in the prompt.*

### Why we need it

A real database might have 200 tables. We cannot put all of them in the prompt — it would be
too long and expensive. So we find the 5–10 that matter for this question.

### What we store

We break knowledge into small pieces called **chunks**. There are three kinds:

| Kind | Example |
|---|---|
| `table` | "Table `orders`: columns id, customer_id, order_date, status (values: SUCCESS, FAILED, CANCELLED)…" |
| `definition` | "**Revenue** = SUM(orders.total_amount) WHERE status = 'SUCCESS'. Synonyms: sales, GMV, turnover." |
| `example_query` | "Q: What was revenue last month? → SELECT SUM(total_amount) FROM orders WHERE…" |

**The `definition` kind is the most important idea in this project.** It is what stops the
₹20 million mistake from §1. A schema dump alone would never tell the AI about the status
filter.

### How the search works

```
 "What was our turnover in May?"
            │
            ▼
   ┌─────────────────┐
   │ Gemini embedding│   turns the text into 1024 numbers that capture its meaning
   └────────┬────────┘
            ▼
   ┌─────────────────────────────────┐
   │ pgvector similarity search      │   finds chunks whose numbers are closest
   │ (HNSW index, cosine distance)   │
   └────────┬────────────────────────┘
            ▼
   ┌─────────────────────────────────┐
   │ relative cutoff                 │   keep only chunks scoring near the best match
   └────────┬────────────────────────┘
            ▼
   ┌─────────────────────────────────┐
   │ deterministic expansion         │   code adds tables the vectors missed
   └────────┬────────────────────────┘
            ▼
      context for the prompt
```

**Embeddings** are how a computer compares meaning. "Turnover" and "revenue" are different
words, but their 1024-number lists end up close together, so searching for one finds the other.

**HNSW** is the index type. Comparing against every stored chunk would be slow; HNSW builds a
graph that makes the search near-instant.

### The clever part: deterministic expansion

Vector search alone is not enough. Ask *"What is the refund rate by category?"* and similarity
returns `refunds` and `products` — but **misses `order_items`**, which is the only table that
connects them.

So after the vector search, plain code adds more tables for three reasons:

| Reason | What it does |
|---|---|
| `definition_link` | a definition mentions tables → pull those tables in |
| `join_bridge` | two tables need a third to connect → pull the bridge in |
| `required` | always include these |

Similarity cannot know about joins. The schema's foreign keys can.

### Making re-indexing cheap

Embedding costs quota. Two caches prevent waste:

1. Each chunk stores a **SHA-256 hash** of its own text. A re-sync only re-embeds chunks whose
   text changed — or whose embedding came from a *different model*, because vectors from
   different models cannot be compared.
2. A **disk cache** keyed by model + text, which also covers question embeddings (those are
   never stored as chunks).

---

## 6. Step 2 — The Query Agent

The first AI. Its only job: **turn a question into one SQL query.**

It receives:
- the question
- the tables found by retrieval (with column names and sample values)
- the business definitions found by retrieval
- verified example queries
- today's date (our demo data ends 2026-06-30, so "last month" means May 2026)

It returns a structured object, not free text:

```json
{
  "can_answer": true,
  "sql": "SELECT SUM(total_amount) AS revenue FROM orders WHERE status = 'SUCCESS' AND ...",
  "explanation": "Sums successful orders in May 2026.",
  "question_type": "metric"
}
```

### Key rules in its prompt

- Use only the tables given. Never invent a column.
- Business definitions are **authoritative** — apply them exactly.
- Match literal values shown in the samples (`SUCCESS`, not `completed`).
- One statement only. No `SELECT *`.
- If the data cannot answer the question, set `can_answer: false` and say why.
- A **vague** question is not an unanswerable one — pick the most sensible reading.

That last rule was added after testing: asking *"give me recent payments"* was being refused as
"too ambiguous" when it was perfectly answerable.

### The comparison contract

For "why did X change" questions, the agent is told to return a specific shape:

| segment | previous_value | current_value |
|---|---|---|
| South | 5,966,000 | 3,866,000 |
| North | 4,100,000 | 4,026,000 |

Fixing this shape is what lets the next steps compute contributions **no matter how the SQL was
written**.

---

## 7. Step 3 — The SQL Guard

Before any generated SQL touches a database, plain code checks it. No AI involved.

### Why string checking is not enough

All three of these look harmless to a naive check:

```sql
SELECT 1; DROP TABLE orders                                 -- starts with SELECT ✓
WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x  -- starts with WITH ✓
SELECT * FROM (SELECT id FROM orders LIMIT 5) t             -- "has a LIMIT" ✓
```

### What we actually do

We parse the SQL into a **syntax tree** using `sqlglot`, then inspect the tree.

```
          SELECT 1; DROP TABLE orders
                     │
                parse into a tree
                     │
                     ▼
         ┌───────────┴───────────┐
    ┌────▼────┐             ┌────▼────┐
    │ Select  │             │  Drop   │  ← found a forbidden node anywhere in the tree
    └─────────┘             └─────────┘
                                 │
                            ✗ REJECTED
```

The guard enforces:

| Check | Catches |
|---|---|
| One statement only | `SELECT 1; DROP TABLE orders` |
| No write nodes **anywhere** in the tree (19 types) | `DELETE` hidden inside a CTE |
| Table allow-list | querying a table the user cannot see |
| Function denylist (70 across 3 dialects) | `pg_sleep`, `read_csv`, ClickHouse's `url`/`remote` |
| Rewrite the **outer** LIMIT | an inner LIMIT leaves the outer query unbounded |
| No `SELECT *` | accidental huge results |

If the guard rejects a query, the error goes back to the Query Agent, which gets **two repair
attempts**. After that the run fails cleanly.

### Defence in depth

The guard is only the first layer:

```
Layer 1   SQL Guard         rejects dangerous SQL before it is sent
Layer 2   read-only role    the database itself refuses writes
Layer 3   statement timeout a runaway query is killed by the server
Layer 4   row cap           at most 500 rows come back
```

---

## 8. Step 4 — Running the query

The query runs on the customer's database through a **connector**. There are two:

| Connector | For |
|---|---|
| `PostgresConnector` | PostgreSQL databases |
| `DuckDBConnector` | uploaded CSV files |

Both implement the same interface (`DataConnector`), so the rest of the system does not know or
care which one it is talking to. That is why adding ClickHouse later is a new file, not a
rewrite.

**CSV uploads** are converted into a DuckDB file once, at upload time. After that they go
through exactly the same SQL pipeline as a real database. File access is switched on only
during import and off for all queries.

---

## 9. Step 5 — The Profiler

**This is the most important idea in the project.**

AI models are unreliable at arithmetic. Show one a table of numbers and ask "which region
explains most of the drop?" and it will often name a plausible one **without actually doing the
sum**.

So we never ask it to. Plain Python computes every number:

```
Raw SQL result:
┌────────┬────────────────┬───────────────┐
│ segment│ previous_value │ current_value │
├────────┼────────────────┼───────────────┤
│ South  │   5,966,000    │   3,866,000   │
│ North  │   4,100,000    │   4,026,000   │
│ East   │   3,200,000    │   2,980,000   │
│ West   │   2,900,000    │   2,967,000   │
└────────┴────────────────┴───────────────┘
                   │
                   ▼  profiler.py does the maths
┌─────────────────────────────────────────────────────┐
│ Overall: 16,166,000 → 13,839,000 (−2,327,000, −14%) │
│ Material change: yes                                │
│                                                     │
│ Segments, biggest contributor first:                │
│   South: −2,100,000  (−35.2%)  = 90% of the change  │
│   East:    −220,000   (−6.9%)  =  9% of the change  │
│   North:    −74,000   (−1.8%)  =  3% of the change  │
│   West:     +67,000   (+2.3%)  = −3% of the change  │
│                                                     │
│ Dominant segment: South                             │
└─────────────────────────────────────────────────────┘
                   │
                   ▼
          given to the AI as FACTS to interpret
```

### How "dominant" is decided

Not by the AI. By a rule in code:

- A segment is dominant if it explains **≥ 50%** of the change, **or**
- it explains **≥ 35%** *and* is at least **1.5×** bigger than the runner-up.

Deliberately conservative. Chasing a segment that does not really dominate sends the whole
investigation down a blind alley, which is worse than stopping one level early.

---

## 10. Step 6 — The Analysis Agent and the drill-down loop

The second AI. It reads the profiler's facts and writes the explanation. It can also **ask to
dig deeper**.

### How digging deeper works

```
Level 0   "Why did revenue fall?"
          → total revenue fell 11.4%
          → AI says: break this down by region

Level 1   "Revenue by region, May vs June"
          → South fell 35.2%, that is 85% of the total drop
          → AI says: look inside South, by category

Level 2   "Revenue by category, within South only"
          → Electronics fell 57.2%, that is 80% of the total drop
          → AI says: look inside Electronics, by channel

Level 3   "Revenue by channel, within South + Electronics"
          → no single channel dominates
          → AI says: done. STOP.
```

### The critical safety design

The AI does **not** run queries. It returns a *proposal*:

```json
{
  "needs_drilldown": true,
  "drilldown": { "dimension": "products.category", "focus_value": "South" }
}
```

Then **plain code decides** whether to allow it:

```
          AI proposes a drill-down
                     │
                     ▼
        ┌────────────────────────┐
        │   drilldown.py checks  │
        ├────────────────────────┤
        │ • depth < 3?           │
        │ • calls left in budget?│
        │ • tokens left?         │
        │ • dimension exists?    │
        │ • not already used?    │
        │ • segment moved with   │
        │   the overall change?  │
        └──────────┬─────────────┘
              ok ↓    ↓ no
                 │    └──→ stop and summarise
                 ▼
          run the next query
```

**Why this matters:** if the AI could run queries itself, no limit would be enforceable. A
prompt saying "stop when you have enough" is a *request*, not a guarantee. Putting the decision
in code makes the limit real. There are adversarial tests that use a fake AI which *always*
asks to drill deeper, proving the caps hold.

### The reconciliation check

A subtle bug we found: a breakdown must add up to the figure it broke down. If "revenue by
region" sums to ₹30M but total revenue was ₹16M, the SQL has a **fan-out** bug — usually a
join multiplying rows. Code checks this automatically and stops the investigation rather than
reporting nonsense.

---

## 11. Step 7 — The Visualization Agent

The third AI. It designs the dashboard: which KPIs, which charts, which table, which insights.

It returns a **spec**, not a picture:

```json
{
  "title": "June revenue decline",
  "kpis":   [{ "label": "June revenue", "query_seq": 1, "value_column": "revenue" }],
  "charts": [{ "type": "bar", "x": "segment", "y": ["current_value"], "query_seq": 2 }],
  "insights": [{ "title": "South drove the decline", "severity": "negative" }]
}
```

Notice the KPI has **no value in it**. It says *"take column `revenue` from query 1"*. The
server fills in the real number afterwards. The AI can never invent a figure, because it is not
allowed to write one.

### Validation and fallback

```
   AI produces a spec
          │
          ▼
  ┌─────────────────┐
  │   validator     │  does every column it names actually exist?
  └────────┬────────┘
     ok ↓     ↓ some widgets invalid
        │     └──→ drop just those widgets
        ▼
  is anything left?
     yes ↓      ↓ no
        │       └──→ build a dashboard with plain rules instead
        ▼
   show the dashboard
```

So a bad AI response degrades to a simpler dashboard. It never produces a broken page.

---

## 12. The LLM layer

We use **only free AI tiers**: Groq, Gemini and OpenRouter. Free models are unreliable in three
specific ways, and the LLM layer exists to handle each.

### Problem 1: the model is unavailable

Solution: a **fallback chain**.

```
  ask Groq (gpt-oss-120b)
         │
    fails/rate-limited
         ▼
  ask OpenRouter (nemotron)
         │
       fails
         ▼
  ask OpenRouter (qwen)
         │
       fails
         ▼
  give up: "no model could answer"
```

Plus **cooldowns**, so a dead model is not retried on every single request:

| Failure | Skip for |
|---|---|
| model withdrawn / bad credentials | 15 minutes |
| rate limited | whatever the provider says, else 60 seconds |
| returned unusable output 3 times | 10 minutes |

### Problem 2: the model returns text instead of JSON

We need structured data, not prose. Three strategies are tried in order:

```
1. tool_call     ← best. Define one "function" whose parameters are our schema,
                    and force the model to call it. Its arguments ARE the answer.
2. json_schema   ← the provider enforces a schema. Only some models support it.
3. prompt_json   ← last resort: paste the schema into the prompt and ask nicely.
```

Which strategies a model supports is recorded in `models.yaml`, discovered by actually probing
five models rather than trusting documentation.

**Important:** this is tool-calling used as a *formatting trick*, not agentic tool use. The
model never chooses among tools and we never execute one.

### Problem 3: the JSON is malformed or incomplete

- The parser is **tolerant about packaging**: it strips `<think>…</think>` blocks from reasoning
  models and ```json code fences before parsing.
- If validation still fails, we send **one repair turn** containing the bad reply and the exact
  error ("sql is required when can_answer is true").
- Only **validated** replies are cached, so a bad reply can never be replayed.

### Budgets

Every run carries a `CallBudget`: at most **12 AI calls** and **80,000 tokens**. Checked before
every call. This is what makes "how much can one question cost?" a question with an answer.

---

## 13. Authentication and multi-user

### Signing in

```
 Browser                          Server
    │   email + password            │
    ├──────────────────────────────►│  check with Argon2id
    │                               │
    │   access token (30 min)       │  ← kept in memory only
    │   + refresh cookie (14 days)  │  ← httpOnly: JavaScript cannot read it
    │◄──────────────────────────────┤
    │                               │
    │   every request:              │
    │   Authorization: Bearer ...   │
    ├──────────────────────────────►│
```

**Why two tokens?** The access token is short-lived because it cannot be cancelled once issued.
The refresh token lives in the database, so signing out is real.

**Why httpOnly?** If someone injects JavaScript into the page, it cannot read an httpOnly
cookie. Storing a long-lived token in `localStorage` would mean one XSS = permanent account
takeover.

### Refresh rotation and reuse detection

Each refresh token is **single-use**. Using it revokes it and issues a replacement.

```
  token A ──used──► revoked, replaced by token B
                                    │
  someone presents token A again ───┘
                                    │
                                    ▼
                    a copy leaked. We cannot tell thief from owner.
                    → revoke EVERY token for that account
```

There is a 10-second grace window so that two browser tabs refreshing at the same moment are
not treated as theft.

### Who can see what

```
 Analysis belongs to user A
          │
    user B requests it
          │
          ▼
      404 Not Found     ← not 403 Forbidden
```

A `403` would confirm the analysis exists. `404` reveals nothing.

Data sources follow a similar rule: `owner_id = you` **or** `owner_id IS NULL` (a shared demo
source everyone can query but only an admin can change).

### Usage limits

Every account has daily ceilings — analyses, AI calls, and tokens. Checked by a FastAPI
*dependency* before any analysis starts, so a new endpoint cannot forget it.

Cached AI calls are excluded from the count, because a cache hit spent no quota.

---

## 14. The database tables

Our own database (`appdb`) has 12 tables:

```
users ───────────┬──────────► refresh_tokens      sign-in sessions
                 │
                 ├──────────► analyses ──────────► analysis_queries   every SQL we ran
                 │                │
                 │                └──────────────► dashboards         the spec we built
                 │
                 ├──────────► llm_calls            one row per AI request attempt
                 │
                 └──────────► data_sources ──┬───► catalog_tables ──► catalog_columns
                                             ├───► table_relationships
                                             └───► knowledge_chunks   the embeddings

evaluation_runs                                    benchmark history
```

**`analysis_queries` is the audit log.** Every statement we generated is stored — including the
ones the guard rejected and the ones that failed. That is what makes the "how this was worked
out" panel possible, and it is why a wrong answer can be diagnosed instead of guessed at.

**`llm_calls` has 19 columns** recording provider, model, attempt number, position in the
fallback chain, tokens in and out, latency, whether it succeeded, whether the output was valid,
the error type, and whether it was served from cache.

---

## 15. The evaluation suite

How do we know the system is actually right? We measure it.

### How a case works

```yaml
- id: revenue_last_month
  question: What was total revenue last month?
  gold_sql: |
    SELECT SUM(total_amount) FROM orders
    WHERE status = 'SUCCESS' AND order_date >= DATE '2026-05-01'
      AND order_date < DATE '2026-06-01'
  comparison: scalar
```

The **gold SQL is run live** against the database when the suite runs. We never hardcode the
expected number, because then the test would drift from the data.

### What is checked

| Check | Question it answers |
|---|---|
| Value comparison | is the number right? (compares **values**, not column names) |
| SQL AST rules | did it filter `status='SUCCESS'`? did it avoid the decoy table? |
| Investigation path | did it find South → Electronics? |
| Refusal | did it correctly refuse an impossible question? |
| No-write | did a destructive request change nothing? |
| Groundedness | does every number in the summary appear in the data? |

**No AI grades another AI.** Every check is code.

### The planted patterns

The demo data is generated from a fixed seed with deliberate traps:

| Pattern | Tests |
|---|---|
| June revenue falls 11.4%, concentrated in South → Electronics | can it investigate two levels deep? |
| Home & Kitchen refund rate jumps 3% → 15.7% | does it use the right denominator? |
| 7.6% of orders never complete (₹20M gap) | does it apply the business rule? |
| `orders_legacy` — a deprecated archive with identical columns | does retrieval avoid the decoy? |

**Result:** 25 of 28 cases passed (89%) on the last full run. The suite is small and was written
knowing the system's weak spots, so that number is a development signal, not a benchmark claim.

---

## 16. The frontend

Next.js 16 with React and TypeScript.

```
 /login, /register   sign in
 /analyze            pick a data source, type a question
 /analyses/[id]      live progress, then the dashboard
 /history            past analyses
 /datasources        connect Postgres or upload CSVs
 /account            usage against your daily limits
```

### Why polling, not waiting

An analysis takes 10–60 seconds. Holding an HTTP request open that long is fragile — proxies
time out and a page refresh loses everything.

```
 POST /analyses  ──► 202 Accepted {"id": "abc"}   returns immediately
                     (work continues in the background)

 GET /analyses/abc ─► {"status":"running","stage":"generating_sql"}   poll every 1s
 GET /analyses/abc ─► {"status":"running","stage":"analyzing"}
 GET /analyses/abc ─► {"status":"completed","dashboard":{...}}        stop polling
```

Because every run is a database row, a page refresh loses nothing and history is free.

### What the result page shows

- the summary, a confidence badge, and any caveats
- the dashboard: KPIs, charts, a table, insight cards
- **"How this was worked out"** — every query, including rejected ones, with its SQL
- **"What the model was given"** — the retrieved context and its scores
- **"Run details"** — AI calls, tokens, time taken

That transparency is the point. If the answer is wrong, you can see exactly why.

---

## 17. Deployment

```
 Browser
    │ HTTPS
    ▼
 Vercel (Next.js)                 ← the only address the browser knows
    │  rewrites /api/v1/*
    │ HTTPS
    ▼
 EC2 machine
   ├── Caddy        handles HTTPS, forwards /api/* only
   ├── api          the FastAPI container
   ├── seed         one-shot: migrate, load demo data, index (runs each deploy)
   ├── appdb        our Postgres + pgvector
   └── demo-analytics   the sample warehouse
```

**Why the proxy?** The browser only ever talks to the Vercel domain, so the login cookie is
first-party. Without it, the cookie would be third-party and blocked by Safari and Chrome, and
users would be logged out on every refresh.

**GitHub Actions** builds the image, pushes it to a registry, restarts the stack over SSH,
checks the public URL, and rolls back to the previous image if the check fails.

> Status: the deployment files are written and tested locally, but nothing is live yet.

---

## 18. The design rule behind everything

If you remember one sentence from this document:

> **Models choose. Code computes and enforces.**

| The AI decides | Code decides |
|---|---|
| what SQL to write | whether that SQL is safe to run |
| which dimension to investigate next | whether the investigation may continue |
| what the numbers mean | what the numbers are |
| which chart suits the data | whether the chart's columns exist |

Every safety property of this system — bounded cost, no writes, no invented numbers — comes
from putting the *decision* in code and leaving only the *judgement* to the model.

That is also the answer to "how is this different from wrapping ChatGPT in an API?"
