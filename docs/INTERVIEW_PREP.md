# InsightFlow — Interview Questions and Answers

Questions you are likely to be asked about this project, with answers in plain language.

**How to use this:** read the short answer first — that is what you say out loud. The "if they
push" part is for when they want more. Do not recite; understand the idea and say it in your
own words.

---

## Table of contents

1. [Opening questions](#1-opening-questions)
2. [Architecture and design](#2-architecture-and-design)
3. [AI and LLM questions](#3-ai-and-llm-questions)
4. [RAG and retrieval](#4-rag-and-retrieval)
5. [SQL safety and security](#5-sql-safety-and-security)
6. [Backend engineering](#6-backend-engineering)
7. [Database design](#7-database-design)
8. [Testing and quality](#8-testing-and-quality)
9. [Scaling and production](#9-scaling-and-production)
10. [Hard and curveball questions](#10-hard-and-curveball-questions)
11. [Behavioural questions](#11-behavioural-questions)
12. [Questions to ask them](#12-questions-to-ask-them)

---

## 1. Opening questions

### Q: Tell me about this project.

**Answer (60 seconds):**

> It is an analytics platform where you ask a business question in plain English and get back a
> dashboard, not a chat reply. You connect a Postgres database or upload a CSV, ask something
> like "Why did revenue fall in June?", and it writes SQL, runs it, investigates the cause on
> its own, and returns KPIs, charts and a written explanation — with every query it ran visible
> underneath.
>
> The interesting part is not the AI writing SQL — that is a solved demo. The interesting part
> is everything around it: making sure the SQL is safe, making sure the numbers are actually
> correct, and making sure an autonomous agent stops instead of running forever.

**Then stop talking.** Let them pick a direction.

---

### Q: Why did you build this?

> I wanted to understand the gap between an LLM demo and something you could actually trust
> with a business number. The demo version takes an afternoon. The hard part is that an AI
> writing SQL will confidently give you a wrong number — not an error, a *wrong number* — and
> nothing about the output tells you it is wrong. Everything I built is aimed at that problem.

---

### Q: What is the single hardest problem in a text-to-SQL system?

> A query that runs perfectly and returns the wrong answer.
>
> In my data, `SELECT SUM(total_amount) FROM orders` gives ₹272 million. The correct revenue is
> ₹251 million, because 7.6% of orders were cancelled or failed. That is a ₹20 million
> difference, and nothing in the database schema tells you about that rule. The query does not
> crash, the number looks plausible, and the dashboard renders fine.
>
> That is why I index business metric definitions alongside the schema, and why I built an
> evaluation suite instead of eyeballing the output.

---

## 2. Architecture and design

### Q: Walk me through what happens when a user asks a question.

> Seven steps.
>
> 1. **Retrieval** — search for relevant tables, business definitions and example queries using
>    vector similarity.
> 2. **Query Agent** — an LLM writes one SQL query, given that context.
> 3. **SQL Guard** — plain code parses the SQL into a syntax tree and checks it is safe. If not,
>    the error goes back to the agent for up to two repairs.
> 4. **Execute** — run it on the customer's database as a read-only user, with a row cap and a
>    timeout.
> 5. **Profiler** — plain Python computes the percentages, the change, and which segment
>    dominates.
> 6. **Analysis Agent** — an LLM interprets those facts and can propose digging deeper. If it
>    does, we loop back to step 2 with a narrower question.
> 7. **Visualization Agent** — an LLM designs the dashboard, which is validated against the real
>    result columns before rendering.
>
> Three LLM calls for a simple question. For an investigation it is capped at twelve.

---

### Q: Why LangGraph and not just a loop with if-statements?

> Because the flow is a state machine with branches and a cycle, and I wanted that structure to
> be explicit.
>
> After generating SQL, the next step depends on whether the guard passed, whether the query
> succeeded, whether the budget is exhausted, and whether the analyst wants to drill deeper.
> That is a routing decision, not a straight line. LangGraph lets me write each step as a pure
> function `(state) -> state` and keep routing as separate functions, which means I can test the
> routing logic without running a single model call.
>
> Honestly, I could have written it as a loop. The benefit is testability and that adding a new
> step is a new edge rather than another branch in a growing `if` chain.

---

### Q: Why are there two separate databases?

> Because they hold different things with different trust levels.
>
> Our own database holds users, analyses, the SQL we ran and the vector embeddings. The
> customer's database holds their business data. We never copy their data into ours — we query
> it where it lives and keep only a small preview of the results.
>
> We also connect to theirs as a read-only role. Even if every check in my code failed, the
> database itself would refuse a write. And in production, ours would be a managed Postgres and
> theirs would be their own warehouse, so keeping them separate from day one means no rewrite
> later.

---

### Q: What is the core design principle?

> **Models choose, code computes and enforces.**
>
> The model decides *what SQL to write*; code decides *whether it is safe to run*. The model
> decides *which dimension to investigate next*; code decides *whether the investigation may
> continue*. The model interprets *what the numbers mean*; code decides *what the numbers are*.
>
> Every safety property — bounded cost, no writes, no invented figures — comes from putting the
> decision in code and leaving only the judgement to the model.

---

## 3. AI and LLM questions

### Q: Are you using tool calling?

> Yes, but as a **structured output mechanism**, not as agentic tool use.
>
> I define a single function whose parameters are my Pydantic schema, and force the model to
> call it with `tool_choice`. That function is never executed — its arguments *are* the answer.
> I use the tool-calling protocol because it was the most reliable way to get schema-valid JSON
> across free models.
>
> I deliberately do **not** give the model a menu of tools to pick from. If it could invoke
> tools freely, my depth and token budgets would be unenforceable. Instead it emits a structured
> *proposal* and code decides whether to run it.

---

### Q: What happens when the model returns invalid JSON?

> Three layers of handling.
>
> First, the parser is tolerant about *packaging* but strict about *content*: it strips
> `<think>` blocks from reasoning models and markdown code fences before parsing.
>
> Second, if Pydantic validation fails, I send one repair turn containing the bad reply and the
> exact error — something like "sql is required when can_answer is true". One attempt, not an
> open loop.
>
> Third, if that still fails, I move to the next output strategy, then the next model in the
> chain. And a model that produces invalid output on three separate requests gets skipped for
> ten minutes, so one bad model does not cost every request.

---

### Q: You are using free models. How do you handle them being unreliable?

> Free models fail in three different ways, so there are three different responses.
>
> | Failure | Response |
> |---|---|
> | Model withdrawn or credentials rejected | skip it for 15 minutes |
> | Rate limited | skip for the provider's own `retry_after`, else 60 seconds |
> | Returns unusable output repeatedly | skip for 10 minutes after 3 strikes |
>
> Plus a fallback chain of three models, three output strategies per model, and a cap on how
> many strategies one model gets — because trying all three on a model returning garbage burns
> six requests to learn nothing.

---

### Q: How do you control cost?

> Three levels.
>
> **Per question:** a `CallBudget` of 12 model calls and 80,000 tokens, checked before every
> call.
>
> **Per user per day:** 20 analyses, 150 model calls, 1 million tokens — enforced by a FastAPI
> dependency before an analysis starts, so a new endpoint cannot forget it.
>
> **Globally:** at most 2 analyses run at once, and at most 2 model calls in flight.
>
> Plus a disk cache of validated replies, which does not count against either budget because a
> cache hit spent nothing.

---

### Q: How do you stop the AI hallucinating numbers?

> Two ways.
>
> First, the model never does arithmetic. Python computes the percentages, the deltas and the
> contribution shares, and hands them to the model as facts to interpret. Models are genuinely
> unreliable at mental maths — ask one which region explains a drop and it will name a plausible
> one without summing.
>
> Second, the dashboard spec cannot contain a number. A KPI says "take column `revenue` from
> query 1", and the server fills in the value. The model is not *allowed* to write a figure.
>
> And the evaluation suite has a groundedness check: every number appearing in the written
> summary must appear somewhere in the query results.

---

### Q: Why three separate agents instead of one?

> Different jobs, different prompts, different output schemas, and different models.
>
> Writing SQL needs precision and schema awareness. Interpreting results needs reasoning about
> business meaning. Designing a dashboard needs knowing which chart fits which data shape. One
> prompt doing all three would be long, confusing, and impossible to improve without breaking
> something else.
>
> Separating them also lets me route each to a different model — the Query Agent runs on Groq,
> the other two on Gemini — and evaluate each independently.

---

## 4. RAG and retrieval

### Q: Explain your RAG setup.

> I index three kinds of chunk: table schemas, business metric definitions, and verified
> question-SQL pairs. They go into Postgres with pgvector, 1024-dimension Gemini embeddings,
> HNSW index, cosine distance.
>
> The important part is **what** is indexed, not the mechanics. A schema tells the model that
> `orders.total_amount` exists. It does not tell it that revenue must exclude cancelled orders.
> That rule lives in a definitions file, gets embedded, and comes back with the schema. Without
> it you get SQL that runs fine and is wrong by ₹20 million.

---

### Q: Why pgvector and not Pinecone or a dedicated vector database?

> I already needed Postgres for users, analyses and catalog metadata. Adding pgvector made the
> vectors live next to the data they describe, so one query can join an embedding to its table
> metadata, and there is one database to back up, migrate and operate instead of two.
>
> At my scale — tens of thousands of chunks at most — a dedicated vector database buys nothing.
> If I were doing billions of vectors with heavy filtering, that calculation changes.

---

### Q: Is plain vector search enough?

> No, and that is one of the more interesting bits.
>
> Ask "what is the refund rate by category" and similarity returns `refunds` and `products` —
> and misses `order_items`, which is the **only** table connecting them. The query is then
> impossible to write.
>
> So after the vector search, plain code walks the foreign-key graph and adds tables for three
> reasons: a definition mentions a table, a join needs a bridge table, or the table is always
> required. Similarity cannot know about joins. The schema's foreign keys can.

---

### Q: How do you decide how many chunks to keep?

> A relative cutoff rather than a fixed threshold. The keep-threshold is
> `max(absolute_floor, best_score × ratio)`.
>
> A fixed threshold is wrong in both directions. If the best match scores 0.9 and the rest score
> 0.4, a fixed cutoff of 0.3 lets a lot of noise in. If the whole question is a poor match and
> the best score is 0.5, a fixed cutoff of 0.6 returns nothing.
>
> I also over-fetch first and then apply the cutoff, so the cutoff removes weak matches rather
> than an arbitrary top-k chopping off good ones.

---

### Q: How do you know your retrieval is any good?

> I evaluate it separately from the rest of the pipeline, with no model calls at all — so those
> cases are free and run in seconds.
>
> They measure whether all the needed tables were found, mean table recall, and whether the
> decisive business definition surfaced. And the test database contains a deliberate trap: a
> table called `orders_legacy`, a deprecated archive with columns identical to `orders`.
> Retrieval that cannot tell them apart would silently answer every revenue question from stale
> 2023 data.

---

### Q: What is weak about your retrieval?

*(Be honest. This is a credibility question.)*

> It is vector-only — no keyword or BM25 arm, and no reranker. The decoy `orders_legacy` table
> still appears in retrieved context roughly half the time, though it has never been used in
> generated SQL.
>
> Hybrid retrieval is the next step. The `search_tsv` column already exists in the schema for
> the keyword arm, and the retriever returns scored results with a `method` field, so adding a
> second arm and fusing them does not change anything downstream.

---

## 5. SQL safety and security

### Q: How do you stop the AI deleting your data?

> Four layers, and the first one is the interesting one.
>
> **Layer 1 — the SQL guard.** Every generated query is parsed into a syntax tree with `sqlglot`
> and inspected before it is sent anywhere.
>
> **Layer 2 — a read-only database role.** We connect as a user that only has `SELECT`. Even if
> the guard had a bug, the database would refuse.
>
> **Layer 3 — a statement timeout** set on the connection, so a runaway query gets killed.
>
> **Layer 4 — a row cap**, at most 500 rows.

---

### Q: Why parse the SQL instead of just checking for dangerous words?

> Because string matching fails on real attacks. Three examples that all pass a naive check:
>
> ```sql
> SELECT 1; DROP TABLE orders                                  -- starts with SELECT
> WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x   -- starts with WITH
> SELECT * FROM (SELECT id FROM orders LIMIT 5) t              -- "has a LIMIT"
> ```
>
> The first is two statements. The second hides a `DELETE` inside a CTE. The third has a LIMIT
> on the *inner* query, leaving the outer one unbounded.
>
> Parsing to a tree catches all three: I reject anything with more than one statement, reject
> any write node *anywhere* in the tree, and rewrite the outer LIMIT myself.

---

### Q: What exactly does the guard block?

> - More than one statement
> - 19 forbidden node types — INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, GRANT and
>   so on — found anywhere in the tree, not just at the top
> - 70 denylisted functions across three dialects — things that reach outside the database, like
>   `pg_sleep`, `read_csv`, and ClickHouse's `url` and `remote`
> - Any table not in the allow-list for that data source
> - `SELECT *`
>
> And it rewrites the outer LIMIT rather than just checking one exists.

---

### Q: A user is logged in. How do you stop them seeing someone else's data?

> Every read path filters on `user_id`. It is a `WHERE` clause, not a check after the fact —
> there is no code path that fetches a row and then decides.
>
> And when you request someone else's analysis you get **404, not 403**. A 403 would confirm
> that the analysis exists, which is itself a leak.
>
> There is a test that walks every registered route and calls it without credentials, asserting
> each one returns 401 unless it is on a short allow-list. I verified the test works by
> deliberately removing a router's auth dependency — it failed and named the exposed endpoint.

---

### Q: Walk me through your authentication.

> Email and password, hashed with Argon2id. On login you get two things: a 30-minute access
> token in the response body, and a 14-day refresh token as an httpOnly cookie.
>
> The access token lives only in browser memory. The refresh token is httpOnly so JavaScript
> cannot read it — if someone injects a script into the page, they cannot steal a long-lived
> credential.
>
> Refresh tokens rotate. Each one is single-use: using it revokes it and issues a replacement.
> If an already-revoked token is presented again, that means a copy leaked, and since I cannot
> tell the thief from the owner, I revoke every token for that account.

---

### Q: An access token cannot be revoked. How do you handle a password change?

> Each user row has a `token_version` integer, and every access token carries it as a claim. It
> is checked on every request.
>
> Changing the password increments that integer, so every token issued before the change stops
> being accepted on the very next request — not 30 minutes later when it would have expired
> anyway.

---

### Q: Why Argon2id and not bcrypt?

> Argon2id is OWASP's current first recommendation — it is memory-hard, so it resists GPU
> cracking better.
>
> Bcrypt also has a practical trap: it silently truncates input at 72 bytes. A long passphrase
> gets cut and nobody is told. Argon2 handles any length.
>
> I used the `pwdlib` library rather than `passlib`, because passlib has been effectively
> unmaintained since 2020.

---

## 6. Backend engineering

### Q: An analysis takes 30 seconds. How does your API handle that?

> It does not hold the request open. `POST /analyses` returns **202 Accepted** with an ID
> immediately, and the work continues in a background task. The client polls
> `GET /analyses/{id}` once a second until the status is terminal.
>
> Holding a request open for 30 seconds is fragile — proxies time out and a page refresh loses
> everything. With this design the run is a database row, so a refresh loses nothing, and
> history comes for free because every run is already stored.

---

### Q: What happens if the server restarts mid-analysis?

> Those runs would sit at "running" forever and a polling client would wait forever.
>
> So on startup, a reconciliation step finds any analysis still marked `queued` or `running` and
> marks it failed with a clear message: "The server restarted while this analysis was running.
> Run it again."
>
> That is the honest trade-off of using an in-process background task instead of a job queue.
> The jobs are short and do not need to survive a restart, so a queue would add a second service
> for no benefit. And because the service layer owns *how* a run executes, swapping in a real
> queue later is one method body.

---

### Q: Why FastAPI dependencies for auth instead of middleware or decorators?

> Because they compose and they are hard to forget.
>
> I have a chain: `get_current_user` → `get_active_user` → `enforce_quota`. Each builds on the
> one before, and an endpoint declares only the layer it needs.
>
> Critically, I attach them at the **router** level, not per endpoint. A route added to a
> protected router is protected automatically. Decorating each endpoint fails open the first
> time someone forgets — and that is exactly the kind of mistake that ships.
>
> FastAPI also caches dependency resolution per request, so declaring `user: ActiveUser` in the
> handler does not re-run the check.

---

### Q: How do you track usage per user?

> Every LLM call writes a row to an `llm_calls` table with 19 columns — provider, model, attempt
> number, position in the fallback chain, tokens in and out, latency, success, whether the
> output was valid, the error type, and whether it came from cache.
>
> Usage is then a query over that table filtered by user and today's date. I deliberately did
> **not** build a rollup table — at this scale an indexed range scan is instant, and the
> function that computes it is the one place to change if that stops being true.
>
> One detail worth mentioning: cached calls are excluded. A cache hit carries the *original*
> call's token counts, so counting it would bill someone twice for one request.

---

### Q: How does the user ID reach your telemetry, deep inside the pipeline?

> A `ContextVar` — Python's async-safe equivalent of thread-local storage.
>
> When a run starts, I bind the analysis ID and user ID into context. Any code inside that block
> — including the LLM client, several layers down — can read them without me threading two extra
> parameters through every function signature. The logger picks them up too, so every log line
> from that run is tagged.

---

## 7. Database design

### Q: Walk me through your schema.

> Twelve tables in our own database, in four groups.
>
> **Identity:** `users`, `refresh_tokens`.
>
> **Work:** `analyses` (one per question), `analysis_queries` (one per SQL statement we
> generated, including rejected ones), `dashboards` (the spec).
>
> **Data sources:** `data_sources`, `catalog_tables`, `catalog_columns`,
> `table_relationships`, `knowledge_chunks` (the embeddings).
>
> **Observability:** `llm_calls`, `evaluation_runs`.

---

### Q: Why store rejected SQL?

> It is the audit trail, and it is what makes debugging possible.
>
> If an answer is wrong, I can see exactly which queries were attempted, which the guard
> rejected and why, how many repairs it took, and what retrieval gave the model. Without that,
> diagnosing a bad answer is guesswork.
>
> It also powers the "How this was worked out" panel in the UI, and it means a future
> "refresh this dashboard" feature needs no schema change — the SQL for every widget is already
> stored.

---

### Q: How did you add a `user_id` column to a table that already had rows?

> Carefully, and in a hand-written migration rather than an autogenerated one.
>
> Autogenerate emits the `NOT NULL` constraint immediately, which fails on any table with
> existing rows. So the migration adds the column as nullable, inserts a placeholder "legacy"
> account with an unusable password hash, assigns existing rows to it, and only *then* applies
> the `NOT NULL`.
>
> I tested it against the real database with 1,457 existing analyses.

---

### Q: What is a generated/denormalised column you added, and why?

> `llm_calls.user_id` is denormalised — it is reachable through `analysis_id` → `analyses` →
> `user_id`.
>
> I duplicated it for two reasons. "What did this person spend today" is a hot-path query for
> quota checks and should not need a join. And some LLM calls have no analysis at all —
> evaluation runs and command-line tools — so those rows would be invisible if I relied on the
> join.

---

## 8. Testing and quality

### Q: How do you test an AI system when the output is non-deterministic?

> By separating the parts that must be deterministic from the part that is not.
>
> Most of the system is ordinary code: the SQL guard, the profiler, the drill-down rules, the
> dashboard validator, the retrieval logic. All of that is unit-tested normally.
>
> For the pipeline itself I use a **scripted fake model** that returns pre-written replies. That
> lets me run the real graph, the real guard, the real database — everything except the network
> call — and assert on the behaviour. One test uses an adversarial fake that *always* asks to
> drill deeper, proving the depth cap holds regardless of what the model does.
>
> Then for actual answer quality, the evaluation suite runs against real models and scores
> against computed ground truth.

---

### Q: How many tests do you have?

> 567, across about 6,100 lines of test code against 15,700 lines of application code.
>
> They need no API keys — the whole suite runs without spending any model quota, which matters
> because I am on free tiers.

---

### Q: How do you know your tests are actually catching anything?

*(A great question. Have the specific example ready.)*

> I mutation-tested the critical ones — deliberately broke the code and checked the tests
> failed.
>
> For example, I removed the auth dependency from the data sources router. The route-protection
> test failed and named the exact exposed endpoint: `POST /api/v1/datasources/test`. I also
> removed the ownership filter from the analysis query, and the isolation test failed with
> "expected 404, got 200".
>
> A test that passes whether or not the code works is worse than no test, because it gives false
> confidence.

---

### Q: Tell me about your evaluation suite.

> 33 cases across 10 categories — simple metrics, breakdowns, trends, joins, business-rule
> traps, root-cause investigations, deliberate distractors, unanswerable questions, and one
> destructive request that must change nothing.
>
> Each case has a **gold SQL query that is run live** against the database when the suite runs.
> I never hardcode expected numbers, because then the test drifts from the data.
>
> Results are compared by **value, not by column name**, so two differently-written queries that
> produce the same answer both pass. Beyond the final number it checks the generated SQL's
> syntax tree, the investigation path, correct refusals, and that every number in the summary
> appears in the data.
>
> No model grades another model. Every check is code.

---

### Q: What is your accuracy?

> 25 of 28 cases on the last full run, so 89%.
>
> I would not present that as a benchmark, though. The suite is small and I wrote it knowing
> where the system was weak, so it is optimistic. It is a development signal — useful for
> telling me whether a prompt change helped or hurt, which is what I built it for.

---

### Q: What bugs did your evaluation suite catch?

*(Have these specific. This is your strongest answer.)*

> Four.
>
> **The refund rate was always 100%.** The SQL started from the `refunds` table and joined to
> `order_items`, which keeps only refunded items — so the numerator and denominator were the
> same rows. It looked completely plausible until you noticed every category reported exactly
> 100%.
>
> **An unequal-period comparison.** It was comparing four months against two and reporting the
> difference as if the periods matched.
>
> **A contradictory flag.** The Analysis Agent was returning `needs_drilldown: false` while also
> providing a drill-down proposal.
>
> **A wrong gold case** — my own test expectation was incorrect, which the live gold SQL
> exposed.

---

## 9. Scaling and production

### Q: What breaks if you run two instances of this?

*(Good answer — shows you know your own limits.)*

> Three things, all because they hold state in process memory.
>
> The semaphore limiting concurrent model calls is per-process, so two instances would make four
> concurrent calls and blow the free-tier limit. The model cooldowns are an in-memory dict, so
> each instance would independently rediscover a dead model. And the rate-limit tracker is lost
> on restart.
>
> The fix is Redis — not for speed, but for shared state. That is actually a better reason to
> add Redis than caching, which is what people usually reach for it for.

---

### Q: Would you add Redis for caching?

> Not yet. The cache is disabled in production anyway, because a deployed instance should give
> fresh answers, and on a single machine a local disk is fine.
>
> I would add Redis when I scale horizontally, and I would use it for the concurrency limiter,
> the cooldowns and rate limiting first. The caches would just come along for the ride.

---

### Q: How would you deploy this?

> Backend on a single EC2 machine behind Caddy, which handles HTTPS automatically. Frontend on
> Vercel.
>
> The one design point worth explaining is that Vercel proxies `/api/v1/*` through to the
> backend, so the browser only ever talks to one domain. Without that, my login cookie would be
> a third-party cookie — blocked by Safari and increasingly Chrome — and users would be logged
> out on every page refresh.
>
> Deployment is a GitHub Actions workflow: build the image, push to a registry, restart over
> SSH, health-check the public URL, and roll back to the previous image if that check fails.

---

### Q: What would you change if this had real users?

> Four things, in order.
>
> **Per-IP rate limiting on login** — I have per-user quotas but nothing stopping brute force on
> the login endpoint itself.
>
> **A real job queue** instead of in-process tasks, so a run survives a restart.
>
> **Paid models with a fallback to free**, because the biggest source of failure right now is
> free models returning unusable output.
>
> **Hybrid retrieval**, because vector-only search still pulls in the decoy table about half the
> time.

---

### Q: How would you add support for a new database like ClickHouse?

> It is designed for that. Three additions, no changes to existing code.
>
> A new connector class implementing the same `DataConnector` interface. One entry in the SQL
> guard's dialect policy — the denylist is a dictionary keyed by dialect. And a prompt file with
> ClickHouse-specific SQL guidance.
>
> There is a contract test suite that runs the same assertions against every connector, so a new
> one is proven to behave identically rather than hoped to. Nothing in the agents or the graph
> changes, because they only ever talk to the interface.

---

## 10. Hard and curveball questions

### Q: Is this not just a wrapper around ChatGPT?

*(Stay calm. This is a test of whether you understand your own work.)*

> The model calls are maybe 5% of the code. The other 95% is what makes the output trustworthy.
>
> A wrapper would hand the question and the schema to a model and return whatever SQL came back.
> That version gives you ₹272 million when the answer is ₹251 million, and you would never know.
>
> What I built around the model: retrieval that supplies business rules the schema does not
> contain, a validator that parses the SQL and rejects dangerous or wrong-shaped queries, a
> profiler that does all the arithmetic because models cannot be trusted with it, a bounded loop
> so an autonomous investigation terminates, and an evaluation suite that tells me when any of it
> regresses.
>
> The model is a component. The system is the engineering around it.

---

### Q: What is the weakest part of this project?

*(Never say "nothing". Pick something real and show you understand it.)*

> Open-ended investigation quality.
>
> There is one evaluation case — "why did refunds increase" — that still fails. The system
> correctly identifies that refunds rose, but it compares an uneven baseline and does not
> reliably drill into the right dimension.
>
> I know the two fixes. First, compare against the preceding period of the *same length* rather
> than everything before. Second, decompose by every available dimension in parallel and let the
> profiler rank which one explains the most change, instead of making the model guess which
> dimension to try first.
>
> I deprioritised it to finish the end-to-end product, which I think was the right call, but it
> is the first thing I would return to.

---

### Q: Your benchmark is 28 cases that you wrote yourself. Is that meaningful?

*(They are testing intellectual honesty.)*

> It is meaningful for one purpose and not for another.
>
> It is genuinely useful for regression — if I change a prompt, I know within minutes whether it
> helped or hurt. That is what I built it for, and it has caught four real bugs.
>
> It is not a benchmark I would quote competitively. It is small, I wrote it knowing the
> system's weak spots, and it covers one dataset on one database engine. For a real claim I
> would need something like Spider or BIRD, more datasets, and cases written by someone who did
> not build the system.

---

### Q: You tested "what was revenue last year" and got a wrong-looking number. What happened?

*(This actually happened. It is a great story because you found it yourself.)*

> It returned ₹127 million for "last year". The SQL was correct — it filtered
> `status = 'SUCCESS'` and used a proper half-open date range for calendar 2025.
>
> The problem is that my dataset only starts in July 2025. So calendar year 2025 is half-covered,
> and the system returned six months of data labelled as a year. The real figure for a full year
> is almost exactly double.
>
> It did disclose its interpretation — it said "calendar year 2025" in the summary — but it never
> warned that half the requested window has no data.
>
> The fix is a coverage check: the system knows the data's date range, so if the requested window
> extends beyond it, add a caveat. I already have a caveat mechanism for unequal-period
> comparisons, so this is its sibling case.
>
> What I find useful about this one is that my 33-case evaluation suite did not catch it, because
> every case uses windows inside the data range. Manual exploratory testing found it. That is a
> real answer to "how do you find bugs your tests miss".

---

### Q: What happens if the AI asks for data that does not exist?

> It is supposed to refuse, and it does.
>
> I tested "which marketing channel gave the best return?" It refused, correctly, explaining
> that campaign channels are `display`, `email`, `search` and `social` while order channels are
> `web`, `mobile_app` and `marketplace` — there is no column linking an order to a campaign.
>
> Refusing is the right outcome. Inventing a join would have produced a confident, completely
> fabricated ROI number.
>
> What I would improve is offering the partial answer. Both tables share a `region` column, so it
> could say "I cannot attribute by channel, but here is spend versus revenue by region."

---

### Q: How do you handle a question that is vague?

> This was a bug I had to fix. Asking "give me recent payments" was refused as "too ambiguous",
> which is wrong — the data clearly can answer it.
>
> The rule I added is: a vague question is not an unanswerable one. Only refuse when the data
> lacks what is being asked about, never because the wording is loose. If the question names
> something that exists, choose the most natural reading and answer it.

---

### Q: Show me a time you chose the simpler solution.

> Usage tracking. I originally designed a `user_usage_daily` rollup table with atomic upserts,
> because that is the textbook answer for counters on a hot path.
>
> Then I thought about scale. At tens of analyses a day, `SELECT count(*), sum(tokens) FROM
> llm_calls WHERE user_id = ? AND created_at >= today` with an index on `(user_id, created_at)`
> is instant. The rollup would have been a second source of truth to keep in sync for no gain.
>
> So I dropped it and kept the seam: usage is computed inside one function, and that function's
> body is the single place to change if a rollup ever becomes necessary.
>
> I did the same on authentication — I had designed four tables with per-user quota overrides
> and an admin surface, and cut it to two.

---

## 11. Behavioural questions

### Q: What was the hardest part?

> Making unreliable free models behave predictably enough to build on.
>
> A paid model returns valid structured JSON nearly every time. Free models return empty
> replies, prose wrapped in markdown, `<think>` blocks, or JSON that is missing required fields.
> At one point I logged 13 invalid replies out of 17 calls on one model.
>
> The answer was defence in depth: three output strategies per model, a tolerant parser, one
> repair turn with the specific validation error, a fallback chain, and strike-based cooldowns
> so a consistently bad model gets skipped. None of that is interesting AI work — it is ordinary
> reliability engineering, which is exactly why it was the hard part.

---

### Q: What would you do differently?

> I would write the evaluation suite earlier.
>
> I built it after the pipeline, and it immediately found bugs that had been there for days —
> including the refund rate that was always 100%. If I had written even five cases on day one,
> I would have caught that the same afternoon.
>
> The general lesson is that with non-deterministic systems, you cannot tell whether a change
> helped by looking at one output. You need a measurement before you start tuning, otherwise
> you are just moving the problem around.

---

### Q: What are you most proud of?

> The split between what the model decides and what the code decides.
>
> It started as a bug. The system kept naming a region that was not actually the biggest
> contributor to a decline, because I was asking the model to read a table of numbers and work
> out the percentages. Models are bad at that.
>
> So I moved every calculation into Python and gave the model the computed facts to interpret.
> That one change fixed a class of wrong answers, and it generalised into the rule the whole
> project follows: models choose, code computes and enforces.

---

### Q: How long did this take?

> The core was a focused two-day sprint — I deliberately scoped it to get something working end
> to end rather than perfecting one layer. After that it was iterative: the evaluation suite,
> then authentication and multi-user, then Docker and CI.
>
> The two-day constraint was useful. It forced me to keep every interface I would need later and
> cut only the implementations behind them — which is why adding a third database engine or a
> job queue later is additive rather than a rewrite.

---

## 12. Questions to ask them

Always have two or three. These show you think about systems, not just features.

- How do you currently evaluate LLM features before shipping them? Do you have something like a
  golden dataset, or is it mostly manual review?
- Where do you draw the line between what a model decides and what your code decides?
- What does your fallback look like when a model provider has an outage?
- How do you handle cost attribution per user or per feature?
- What has surprised you most about running LLM features in production?

---

## Quick revision card

| Topic | The one-line version |
|---|---|
| Core problem | SQL that runs fine and returns the wrong number |
| Core principle | Models choose, code computes and enforces |
| SQL safety | Parse to a syntax tree, not string matching — 19 node types, 70 functions |
| Why RAG | Schema does not contain business rules; definitions do |
| Why not vector-only | Similarity cannot know about joins; foreign keys can |
| Stopping the agent | Depth, call and token budgets enforced in code, not the prompt |
| No hallucinated numbers | Python does all arithmetic; the spec cannot contain a value |
| Free model reliability | 3 strategies, 3 providers, repair turn, 3 cooldown classes |
| Long requests | 202 + polling, background task, orphan reconciliation on restart |
| Multi-tenancy | WHERE user_id, and 404 not 403 |
| Testing | 567 tests, scripted fake model, mutation-checked guards |
| Measurement | 33 cases, gold SQL run live, value comparison, no LLM judge |
| Biggest weakness | Open-ended investigation quality; vector-only retrieval |
