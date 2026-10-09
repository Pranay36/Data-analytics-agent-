# Challenges We Hit While Building This

Every real problem we ran into, in simple language: what happened, why it mattered, how we
fixed it, and what it taught us.

These are good interview material. Saying *"the refund rate came out as exactly 100% for every
category and it took me a while to see why"* is far more convincing than *"I used RAG."*

---

## Table of contents

1. [Wrong answers that looked right](#1-wrong-answers-that-looked-right)
2. [Making unreliable AI models behave](#2-making-unreliable-ai-models-behave)
3. [Stopping the agent from running forever](#3-stopping-the-agent-from-running-forever)
4. [Working with free tiers](#4-working-with-free-tiers)
5. [Database and migration problems](#5-database-and-migration-problems)
6. [Testing problems](#6-testing-problems)
7. [Configuration traps](#7-configuration-traps)
8. [Docker and deployment problems](#8-docker-and-deployment-problems)
9. [Things still not solved](#9-things-still-not-solved)
10. [The lessons underneath](#10-the-lessons-underneath)

---

## 1. Wrong answers that looked right

This is the category that matters most. These bugs do not crash. They give you a clean number
that happens to be wrong.

### 1.1 The refund rate was always exactly 100%

**What happened**

We asked for the refund rate by product category. Every single category came back as 100%.

**Why it happened**

A refund rate is *refunded money ÷ total money sold*. The AI wrote SQL that started from the
`refunds` table and joined to `order_items`:

```sql
FROM refunds r JOIN order_items oi ON oi.id = r.order_item_id
```

A join like that keeps **only items that were refunded**. So the numerator and the denominator
became the same rows. Refunded ÷ refunded = 1. Always.

```
   WRONG                              RIGHT
   start from refunds                 start from everything sold
   ┌──────────┐                       ┌──────────────────┐
   │ refunded │ ÷ │ refunded │        │ refunded │ ÷ │ all items sold │
   └──────────┘                       └──────────────────┘
      = 100% always                      = the real rate
```

**Why it was dangerous**

The SQL was valid. The query ran. The dashboard rendered. The number was a clean round 100%.
The only clue was that it was 100% for *everything*, which a tired person could easily miss.

**How we fixed it**

Rewrote the business definition to spell out the trap explicitly:

> "The denominator must include every sold line item, refunded or not. Starting from refunds
> and joining to order_items keeps only refunded items, so the numerator and denominator become
> the same rows and the rate is always 100%. Start from `order_items` (joined to successful
> orders) and LEFT JOIN refunds."

We also added a verified example query showing the correct shape, and an evaluation case so it
can never come back silently.

**What we learned**

Business rules are not optional context — they are the difference between right and wrong. And
the fix was *better written knowledge*, not a better model.

---

### 1.2 Revenue counted orders that never completed

**What happened**

`SELECT SUM(total_amount) FROM orders` gives ₹272,169,980. The correct revenue is ₹251,928,208.

**Why it happened**

7.6% of orders in the data are `FAILED`, `CANCELLED` or `PENDING`. They should never count as
revenue. Nothing in the database schema says that.

**How we fixed it**

A business definition that states the filter, plus the status column's actual values in the
retrieved context so the AI matches `SUCCESS` and not a guess like `completed`.

**What we learned**

This is *the* reason the project indexes definitions alongside schema. A ₹20 million gap, and
the query that produces it looks perfectly reasonable.

---

### 1.3 The breakdown did not add up (fan-out)

**What happened**

Total revenue was ₹16 million. The breakdown by region added up to ₹30 million.

**Why it happened**

A **fan-out**. When you join `orders` to `order_items`, one order with three items becomes three
rows. If you then sum the *order total* instead of the *line amount*, you count that order's
full value three times.

```
 orders                      after joining to order_items
 ┌────┬────────┐             ┌────┬────────┬──────┐
 │ #1 │ ₹1000  │   ──────►   │ #1 │ ₹1000  │ item1│
 └────┴────────┘             │ #1 │ ₹1000  │ item2│   SUM = ₹3000 ✗
                             │ #1 │ ₹1000  │ item3│
                             └────┴────────┴──────┘
```

**How we fixed it**

A **reconciliation check** in code: after any breakdown, the parts must add up to roughly the
whole they broke down. If they do not, the investigation stops rather than reporting nonsense.
The tolerance is deliberately loose — discounts and nulls cause small legitimate gaps, while
fan-out errors are multiples.

**What we learned**

Some errors are detectable by arithmetic alone. You do not need the AI to notice; you need code
that checks the result is internally consistent.

---

### 1.4 Comparing four months against two

**What happened**

For "why did refunds rise", the system compared January–April against May–June and reported the
difference as if those were comparable.

**Why it happened**

Nobody told it the periods had to be the same length. "Earlier months" is vague, and it took the
widest possible reading.

**How we fixed it**

Added a `period_length_warning` and a caveat on the final answer, plus a prompt rule that a
comparison question must state both periods explicitly.

**What we learned**

When a question is vague about time, the system must either pick a defensible default **and say
so**, or warn. Silence is the bug.

---

### 1.5 "Last year" returned six months of data

**What happened**

We asked "What was our revenue last year?" It answered ₹127,081,035 for calendar year 2025.

The SQL was completely correct — right status filter, right half-open date range. But our
dataset starts on **1 July 2025**. So calendar 2025 is only half covered, and the answer was six
months of revenue presented as a year. The true full-year figure is almost exactly double.

```
 data we actually have:        Jul 2025 ─────────────► Jun 2026
 what "calendar 2025" asks:  Jan 2025 ──────► Dec 2025
                             └──── empty ────┘└─ real ─┘
```

**Why it was only half a bug**

It *did* say "(calendar year 2025)" in the summary, so it disclosed its interpretation. That is
the right instinct. What it never said was that half the window has no data at all.

**How we would fix it**

The system already knows the data's date range and the dataset's "today". One check before
answering: if the requested window extends beyond the data's coverage, attach a caveat —
"Data begins 2025-07-01, so this covers 6 of the 12 months requested."

**What we learned**

Our evaluation suite did not catch this, because every test case uses a window *inside* the data
range. Manual exploratory testing found it. Tests confirm what you thought of; exploring finds
what you did not.

---

## 2. Making unreliable AI models behave

We used only free models. They fail far more often than paid ones, and in more ways.

### 2.1 The models kept returning garbage instead of JSON

**What happened**

We needed structured data back — a JSON object with specific fields. Instead we got, from the
logs of one real run:

| Model | Calls | Invalid replies |
|---|---|---|
| Groq gpt-oss-120b | 18 | 0 |
| OpenRouter nemotron | 17 | **13** |
| OpenRouter qwen | 15 | **9** |

Actual failures recorded: *"The reply was empty"*, *"no JSON object found in the reply"*, and
*"sql is required when can_answer is true"* — meaning the model said it could answer but gave no
query.

**How we fixed it — four layers**

```
1. Ask properly    → tool-calling: define one function whose parameters are our
                     schema, and force the model to call it

2. If unsupported  → try JSON-schema mode, then as a last resort paste the
                     schema into the prompt

3. If malformed    → strip <think> blocks and ```json fences, then try again
                     ONE repair turn with the exact error message

4. If still bad    → move to the next model in the chain, and put this one
                     on a 10-minute cooldown after 3 strikes
```

**What we learned**

Reliability engineering, not AI engineering, was the hard part. None of the above is clever —
it is retries, fallbacks and timeouts, which is exactly what makes any unreliable dependency
usable.

---

### 2.2 One bad model was burning the whole budget

**What happened**

A model that could not produce valid output was being retried on *every single step* of *every
run*. Each attempt took up to 24 seconds. One question sat generating SQL for minutes.

**Why it happened**

We had a fallback chain, but no memory. Every request rediscovered that the bad model was bad.

**How we fixed it**

Two limits:

- **Strategy cap** — at most 2 output strategies per model per request. Trying all three on a
  model returning garbage costs six provider calls to learn nothing new.
- **Strike cooldown** — three failed requests in a row and the model is skipped for 10 minutes.
  A single success resets the count.

**What we learned**

A fallback chain without memory is a fallback chain that pays the same tax forever. Failure
state has to persist between requests.

---

### 2.3 A dead model returned the wrong kind of error

**What happened**

OpenRouter withdrew a free model. Requests to it returned **HTTP 400 Bad Request** with a
message like "not a valid model ID".

**Why it was a problem**

HTTP 400 normally means *your request was malformed* — so our code treated it as something to
repair, and retried with the same dead model.

**How we fixed it**

Inspect the error body, not just the status code. If it says the model is unknown, classify it
as `ModelUnavailable` and put it on a 15-minute cooldown rather than retrying.

**What we learned**

HTTP status codes from AI providers are not reliable signals. You have to read the body.

---

### 2.4 The model refused questions it could easily answer

**What happened**

Asking *"give me recent payment"* was refused with: *"The request is ambiguous and does not
specify what information is needed."*

But the data has a `payments` table with a `paid_at` column. It is obviously answerable.

**Why it happened**

Two prompt rules fighting each other. One said "aggregate, do not return raw rows". Another
said "if the tables cannot answer, refuse". A request to *list* recent records had no permitted
path.

**How we fixed it**

Added an explicit rule:

> "A vague question is not an unanswerable one. Only refuse when the data lacks what is being
> asked about, never because the wording is loose. For a request to list or show recent records,
> return the latest rows ordered by the date column, limit 20. This is the one case where raw
> rows are right."

After the fix, the same question returned the 20 most recent payments correctly.

**What we learned**

Over-refusal is as much a bug as a wrong answer, and it is easier to miss because a refusal
*feels* like the system being careful.

---

### 2.5 The model contradicted itself

**What happened**

The Analysis Agent returned `needs_drilldown: false` while *also* providing a drill-down
proposal. Our code trusted the flag, so it stopped investigating even though the model clearly
wanted to continue.

**How we fixed it**

A validator on the schema normalises the contradiction: if a proposal is present, the flag is
forced to true.

**What we learned**

Never trust a model to keep two fields consistent. Make the schema enforce the invariant.

---

### 2.6 A smaller model mangled a complex field

**What happened**

The drill-down proposal originally had a `focus_filter` field that could hold a structured
filter object. Lighter models kept producing malformed versions of it.

**How we fixed it**

Simplified the schema. The proposal now carries only `focus_value` — a plain string like
`"South"` — and code builds the filter from it.

**What we learned**

Every field you ask a model to fill is a chance for it to get something wrong. Ask for the
minimum and derive the rest in code.

---

## 3. Stopping the agent from running forever

### 3.1 The prompt was not a guarantee

**The problem**

Our first version told the model "stop when you have enough information". That works until it
does not — and when it does not, it costs money and time with no upper bound.

**How we fixed it**

The model no longer decides. It *proposes*; code decides.

```
   AI: "I want to drill into South by category"
                    │
                    ▼
   code checks:  depth < 3?         ✓
                 calls left?        ✓
                 tokens left?       ✓
                 dimension exists?  ✓
                 already used it?   ✗ no
                 segment moved the
                 same way as total? ✓
                    │
                    ▼
                 allowed → run it
```

Any single failure stops the loop and the system summarises what it has.

**How we proved it works**

A test with a deliberately adversarial fake model that **always** asks to drill deeper. The
caps hold regardless, because the model's opinion is not what is being consulted.

**What we learned**

A prompt is a request, not a constraint. If a limit matters, it has to live in code.

---

### 3.2 The investigation refused to start

**What happened**

After adding the drill-down rules, the system stopped drilling down *at all* for the first step.

**Why it happened**

Our prompt said to drill into the *dominant segment*. But at the very first step there are no
segments yet — there is just one headline number. So the rule could never be satisfied, and the
investigation ended immediately.

**How we fixed it**

Rewrote the prompt to cover both cases explicitly:

- If the result is a single total, the next step is to **break it down by a dimension** and
  leave the focus value empty.
- If the result is already broken down and one segment dominates, drill **into** that segment
  using a different dimension.

Plus a code flag (`last_was_headline`) so the guard knows which situation it is in.

**What we learned**

Rules written for the middle of a process often break at the start of it. Check the first
iteration and the last, not just the typical one.

---

### 3.3 Stopping too early on a single dimension

**What happened**

If no region dominated the change, the system concluded "the change is broad-based" and stopped
— even though splitting by *category* might have shown a very clear cause.

**How we fixed it**

A rule that one dimension showing nothing only tells you about *that* dimension. The system must
try a second dimension before concluding the change is genuinely spread out.

**What we learned**

"No signal here" is not the same as "no signal anywhere". That distinction matters for anything
that searches a space.

---

## 4. Working with free tiers

### 4.1 Running out of quota mid-development

**What happened**

OpenRouter's free tier allows **50 requests per day across chat and embeddings combined**. One
re-index plus one evaluation run exhausted it. Development stopped.

**How we fixed it — three things**

1. **Split the work by provider.** Chat moved to Groq (1,000/day), embeddings to Gemini (its own
   allowance). OpenRouter became a fallback only.
2. **Cached LLM replies to disk.** Most development happens *after* the model answers — the
   profiler, the dashboard, the whole frontend. With a cache, re-running costs nothing unless the
   prompt itself changed.
3. **Cached embeddings to disk**, keyed by model and text — which also covers *question*
   embeddings, so re-running the retrieval evaluation is free.

**What we learned**

A constraint forced a better architecture. Without the free-tier limit we would never have built
the fallback chain or the caching, and the system would be less robust.

---

### 4.2 Re-indexing burned quota for no reason

**What happened**

Every deploy and every `docker compose up` re-embedded all 33 knowledge chunks, even when
nothing had changed.

**How we fixed it**

Each chunk stores a SHA-256 hash of its own text. A re-sync only re-embeds chunks whose hash
changed.

But there is a subtle third condition. A chunk is also re-embedded if its vector came from a
**different model**, because vectors from different models cannot be compared. Mixing them does
not throw an error — it silently returns nonsense results.

**What we learned**

The dangerous version of this bug is the silent one. Catching "wrong model" mattered more than
catching "text changed".

---

### 4.3 The embedding library was 200 MB

**What happened**

We first tried `fastembed` for local embeddings. Installing it pulled in `onnxruntime` —
hundreds of megabytes — and `uv sync` appeared to hang.

**How we fixed it**

Dropped local embeddings entirely and used a hosted API (Gemini) instead. Smaller image, faster
install, and no CPU cost at query time.

**What we learned**

"Free and local" is not automatically cheaper than "free and hosted" once you count install
time, image size and memory.

---

## 5. Database and migration problems

### 5.1 I dropped every table in the database

**What happened**

An Alembic migration failed halfway. I ran the command piped through `tail` to shorten the
output, which hid the failure, and then ran a chained `downgrade -1`. The downgrade dropped
every table.

**Why it happened**

Two mistakes compounding. Piping through `tail` hid the error, and `cmd1 | tail && cmd2` does not
stop when `cmd1` fails — it reports the exit status of `tail`, which succeeded.

**How we fixed it**

Rebuilt the schema from the migrations (which is exactly why migrations exist). And from then
on, used `set -eo pipefail` so a failure inside a pipeline actually stops the script.

**What we learned**

Never hide the output of a command that changes a database. The shortcut that saves three lines
of scrollback can cost an afternoon.

---

### 5.2 Autogenerated migrations were broken

**What happened**

Alembic's autogenerate produced a migration that referenced `pgvector` types without importing
them. Running it failed with `NameError`.

**How we fixed it**

Added the import to Alembic's migration template, so every generated file includes it.

**What we learned**

Autogenerate is a draft, not a finished migration. Always read it.

---

### 5.3 Adding a NOT NULL column to a table with 1,457 rows

**What happened**

Adding user accounts meant every existing analysis needed an owner. Autogenerate emits:

```python
op.add_column("analyses", sa.Column("user_id", sa.UUID(), nullable=False))
```

That fails immediately — existing rows have no value to put there.

**How we fixed it**

A hand-written migration in the correct order:

```
1. create the users table
2. add user_id as NULLABLE
3. insert a "legacy" account with an unusable password hash
4. assign all existing analyses to it
5. NOW apply NOT NULL
6. add the foreign keys and indexes
```

**What we learned**

Schema changes on populated tables are a sequencing problem. The order is the whole job.

---

### 5.4 An empty result was not actually empty

**What happened**

Our code checked "did the query return zero rows?" to detect no data. It kept reporting data
where there was none.

**Why it happened**

An aggregate over nothing does not return zero rows. It returns **one row containing NULL**:

```sql
SELECT SUM(total_amount) FROM orders WHERE 1=0;
-- returns: one row, value NULL   (not zero rows)
```

**How we fixed it**

An `is_empty` helper that treats both cases as empty: no rows at all, *or* rows where every
value is NULL.

**What we learned**

A classic SQL gotcha. Worth knowing cold, because it comes up in interviews.

---

### 5.5 A fresh database would not start

**What happened**

Testing Docker from scratch, the demo database container crashed on first boot:

```
ERROR: role "insightflow_ro" does not exist
```

**Why it happened**

The startup SQL script ended with `GRANT SELECT ... TO insightflow_ro`, but that role was created
by a separate Python script that runs *later*. On an existing database the role already existed,
so nobody had ever noticed.

**How we fixed it**

Create the role inside the SQL script itself, before the GRANT, guarded by an existence check so
it is safe to run twice.

**What we learned**

"Works on my machine" often means "works on my machine's *existing state*". Testing from a
genuinely blank slate found a bug that had been there from the beginning.

---

## 6. Testing problems

### 6.1 Adding authentication broke every existing test

**What happened**

The moment auth dependencies were added to the routers, every test that called an endpoint
started failing with 401.

**How we fixed it**

A test fixture that creates a real user and returns a client with the `Authorization` header
already set — added *before* the routers were locked down, not after.

**What we learned**

Sequencing again. Build the test fixture first, then add the constraint, and you have a clean
run instead of a wall of red.

---

### 6.2 Tests failing with "Event loop is closed"

**What happened**

Running several async database tests together produced confusing failures deep inside the
database driver.

**Why it happened**

Each test gets its own event loop. A pooled database connection created in one test was being
reused in another, after the loop it belonged to had closed.

**How we fixed it**

Dispose the engine at the end of the fixture, so no connection outlives its loop.

**What we learned**

Connection pools and per-test event loops do not mix. The symptom appears far from the cause.

---

### 6.3 How do you know the tests actually test anything?

**The worry**

A test that passes whether or not the code works is worse than no test, because it creates false
confidence.

**What we did**

Mutation testing by hand on the security-critical parts. We deliberately broke the code and
checked the tests failed.

| What we broke | What happened |
|---|---|
| Removed auth from the data sources router | Route-protection test failed and **named** the exposed endpoint: `POST /datasources/test` |
| Removed `WHERE user_id = ...` from the analysis query | Isolation test failed: "expected 404, got 200" |

Both caught it. Then we put the code back.

**What we learned**

For anything security-critical, verify the test fails when the protection is removed. It takes
two minutes and it is the only real proof.

---

### 6.4 A library method that did not exist

**What happened**

The password library we chose, `pwdlib`, does not have a `check_needs_rehash` method — that is
`passlib`'s API. Our code called it anyway.

**Why it mattered**

The crash happened *after* a successful password check, so **every correct login would have
crashed**. A wrong password would have worked fine, in the sense that it was correctly rejected.

**How it was caught**

An integration test — "changing a password retires every existing token" — failed with
`AttributeError`. The real API is `verify_and_update`, which returns both the result and an
upgraded hash if the parameters have been strengthened.

**What we learned**

Test the happy path, not just the error path. This bug lived entirely in the success branch.

---

## 7. Configuration traps

### 7.1 A blank environment variable silently erased the defaults

**What happened**

A `.env` file containing this:

```ini
LLM_FALLBACK_CHAIN=
LLM_MODEL_QUERY=
```

wiped out the carefully chosen defaults from `models.yaml`, leaving the chain empty. The app
then failed with a confusing error about no models being configured.

**Why it happened**

An empty string is a *value*. The settings library treated `""` as "the user set this to
nothing" rather than "the user did not set this".

**How we fixed it**

A validator that treats blank as unset, so defaults survive.

**What we learned**

In configuration, "empty" and "absent" are different states and must be handled differently.

---

### 7.2 The settings library crashed on a comma-separated list

**What happened**

`CORS_ORIGINS=http://localhost:3000` crashed the app at startup, because pydantic-settings tried
to parse a `list[str]` field as JSON and the value was not valid JSON.

**How we fixed it**

Marked the field `NoDecode` and wrote a small validator that splits on commas — so the `.env`
file stays human-readable instead of requiring `["http://localhost:3000"]`.

**What we learned**

Config files are read and written by people. Make them forgiving.

---

### 7.3 Secrets nearly ended up in git

**What happened**

The embedding cache directory got committed. It contained no credentials, but it was 25 MB of
machine-generated JSON that had no business in the repository.

**How we fixed it**

`git rm --cached` and a `.gitignore` entry. We also wrote `.dockerignore` so `.env` can never be
copied into a built image.

**What we learned**

Add the ignore rules when you create the directory, not after you notice it in `git status`.

---

### 7.4 A formatter rewrote twenty unrelated files

**What happened**

Running `ruff format` on the whole `app` directory reformatted around 20 files that had nothing
to do with the change in progress, turning a clean 8-file diff into a 28-file one.

**How we fixed it**

Reverted the unrelated files with `git checkout` and re-applied only the intended edits.

**What we learned**

Run formatters on the files you touched, not the whole project — otherwise the real change
becomes impossible to review.

---

## 8. Docker and deployment problems

### 8.1 The Dockerfile was far too clever

**What happened**

The first Dockerfile was 114 lines with three build stages, two build targets, a binary copied
from a GitHub container registry, and four environment variables whose purpose was not obvious.

**Why it was a problem**

Nobody reading it could tell what it did or why. For a portfolio project that is a real cost —
the code is meant to be read.

**How we fixed it**

Rewrote it as 26 lines, one stage, using `pip install uv`. The seed job now uses the same image
with a different command instead of a separate build target.

The trade-off, stated honestly: the image is about 30 MB bigger because the demo-data tools ship
with it.

**What we learned**

Optimisation you cannot explain is not worth the complexity. 30 MB is a worse problem on paper
and a better one in practice.

---

### 8.2 The build failed because of the network

**What happened**

`uv sync` failed inside Docker with:

```
Failed to download `ruff==0.16.10`
cause: Failed to download distribution due to network timeout
```

**Why it happened**

Nothing to do with our code — a slow connection and a 30-second default timeout. The first
successful build took about 25 minutes.

**What we learned**

Distinguish "my code is broken" from "my network is slow" before changing anything. Retrying was
the correct fix.

---

### 8.3 Build features that need a newer Docker

**What happened**

The Dockerfile used `RUN --mount=type=cache` to speed up repeated dependency installs. The local
Docker had BuildKit disabled, so every build failed immediately.

**How we fixed it**

Removed the cache mounts. They only speed up rebuilds; without them the Dockerfile works on any
builder.

**What we learned**

A build file that only works on *your* machine is not much of a build file.

---

### 8.4 Killing the server killed the shell

**What happened**

Twice, a command like `pkill -f "uvicorn app.main:app"` terminated the shell that ran it.

**Why it happened**

The pattern matched the `pkill` command's own process, because the command line contained the
search string.

**How we fixed it**

Find the process ID by the listening port instead, then kill that specific ID.

**What we learned**

A small one, but a real time-waster: `pkill -f` is dangerous because the pattern can match
itself.

---

## 9. Things still not solved

Being honest about these is more impressive than pretending they do not exist.

### 9.1 Open-ended investigation quality

**The problem**

One evaluation case — *"Why did refunds increase?"* — still fails. The system correctly spots
that refunds rose, but it picks an uneven baseline and does not reliably drill into the right
dimension.

**Why it is hard**

Nothing in the question says where to look. It has to choose a dimension — category, product,
reason, region — with no hint. And the planted cause is two levels deep: Home & Kitchen, then
one defective product.

**The planned fix**

1. Compare against the preceding period of the **same length**, not everything before.
2. Break down by **every** dimension in parallel and let the profiler rank which one explains
   the most change — instead of asking the model to guess which dimension to try first.

That second change is the important one: it replaces a guess with a computation.

---

### 9.2 The decoy table still appears in retrieval

**The problem**

`orders_legacy` is a deliberately planted trap — a deprecated archive with columns identical to
`orders`. It appears in retrieved context roughly half the time.

**Why it is not urgent**

It has never once been used in generated SQL. The table's description says "DEPRECATED… do not
use for reporting", and the model reads that.

**The planned fix**

Hybrid retrieval — add a keyword search arm alongside the vector one and fuse the results. The
`search_tsv` column already exists for it.

---

### 9.3 Data coverage warnings

As described in §1.5 — the system does not yet warn when a requested time window extends beyond
the data it has.

---

### 9.4 No rate limit on login

There are per-user daily quotas, but nothing stops someone hammering the login endpoint to guess
passwords. It needs a per-IP limit before any public deployment.

---

## 10. The lessons underneath

If you strip away the specifics, the same few ideas keep coming back.

### Silent wrongness is the real enemy

A crash tells you something is wrong. A wrong number does not. Almost every serious bug in this
project produced a plausible-looking answer: 100% refund rates, ₹20 million of phantom revenue,
a breakdown adding to twice the total, six months reported as a year.

That is why the evaluation suite exists, why the profiler computes everything, and why the
reconciliation check is there.

### A prompt is a request, not a guarantee

"Stop when you have enough" works until it does not. Every limit that actually matters —
depth, calls, tokens, which tables are readable — lives in code, where the model's cooperation
is not required.

### Constraints produced better engineering

The free-tier limit forced a fallback chain, three output strategies, caching and budgets. A
paid API key would have produced a simpler and much more fragile system.

### Measure before you tune

The evaluation suite was built *after* the pipeline, and immediately found bugs that had been
there for days. With a non-deterministic system you cannot tell whether a change helped by
looking at one output. Build the measurement first.

### Test from a blank slate

The fresh-database crash had existed since the first commit. It was invisible because every test
ran against a database that already had the role. Starting from nothing is a different test from
starting from your machine.

### Simplicity you can explain beats cleverness you cannot

The 114-line Dockerfile was objectively better optimised than the 26-line one. The 26-line one
is better, because someone can read it and know what it does.
