#!/bin/sh
# One-shot setup for a fresh stack: schema, demo data, data sources, retrieval index.
# Every step is safe to repeat, so this runs on every `docker compose up`.
set -eu

echo "== migrating the application database"
alembic upgrade head

echo "== generating demo data (seeded, so identical every time)"
python -m app.scripts.generate_demo_data

echo "== loading the demo warehouse"
python -m app.scripts.load_postgres --if-empty

echo "== registering the demo data sources"
python -m app.scripts.bootstrap

# Embedding needs an API key. Without one the stack should still come up (and you can still
# browse schemas); questions just will not retrieve context until this is run again.
echo "== indexing business knowledge"
python -m app.scripts.index_knowledge || \
    echo "!! indexing skipped: set GEMINI_API_KEY in .env, then run: docker compose --profile app run --rm seed"

echo "== done"
