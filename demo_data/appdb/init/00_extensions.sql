-- Enabled once at first container start. Alembic owns the schema itself;
-- extensions must exist before any migration that references `vector`.
CREATE EXTENSION IF NOT EXISTS vector;
