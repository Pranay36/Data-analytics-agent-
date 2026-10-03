"""Load the generated ShopSphere CSVs into a PostgreSQL database.

Deliberately a script rather than a Docker init hook: init hooks only run on a
container's very first boot, whereas this is re-runnable and works just as well
against a managed database (RDS, Cloud SQL, Neon) when we deploy.

It also creates the read-only role the agent connects as. That role is the
outermost layer of the SQL safety model (PROJECT_PLAN §14.1): even if every
other guard failed, the database itself would still refuse a write.

Usage:
    uv run python -m app.scripts.load_postgres
    uv run python -m app.scripts.load_postgres --url postgresql://user:pass@host/db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

from app.core.config import get_settings

REPO_DIR = Path(__file__).resolve().parents[3]
CSV_DIR = REPO_DIR / "demo_data" / "generated"
SCHEMA_SQL = REPO_DIR / "demo_data" / "postgres" / "init" / "00_schema.sql"

# Load order matters: parents before children, because of foreign keys.
TABLES = ["customers", "products", "orders", "order_items", "payments", "refunds"]

READONLY_USER = "insightflow_ro"
READONLY_PASSWORD = "insightflow_ro"  # local demo only; override in any real deployment


def load(url: str, readonly_password: str) -> None:
    missing = [name for name in TABLES if not (CSV_DIR / f"{name}.csv").exists()]
    if missing:
        sys.exit(
            f"Missing CSVs: {', '.join(missing)}\n"
            "Run:  uv run python -m app.scripts.generate_demo_data"
        )

    with psycopg.connect(url, autocommit=True) as conn:
        print(f"Connected to {url.split('@')[-1]}")

        print("Creating schema …")
        conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))

        for table in TABLES:
            csv_path = CSV_DIR / f"{table}.csv"
            with csv_path.open("r", encoding="utf-8") as handle:
                header = handle.readline().rstrip("\n")
                copy_sql = f"COPY {table} ({header}) FROM STDIN WITH (FORMAT csv, NULL '')"
                with conn.cursor().copy(copy_sql) as copy:
                    while chunk := handle.read(1 << 20):
                        copy.write(chunk)

            count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # type: ignore[index]
            print(f"  {table:<12} {count:>7,} rows")

        print(f"Granting read-only access to {READONLY_USER} …")
        conn.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{READONLY_USER}') THEN
                    CREATE ROLE {READONLY_USER} LOGIN PASSWORD '{readonly_password}';
                END IF;
            END $$;
            """
        )
        current_db = conn.execute("SELECT current_database()").fetchone()[0]  # type: ignore[index]
        for statement in (
            f"GRANT CONNECT ON DATABASE {current_db} TO {READONLY_USER}",
            f"GRANT USAGE ON SCHEMA public TO {READONLY_USER}",
            f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {READONLY_USER}",
            # Tables created later are covered automatically.
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {READONLY_USER}",
        ):
            conn.execute(statement)

        revenue = conn.execute(
            "SELECT sum(total_amount) FROM orders WHERE status = 'SUCCESS'"
        ).fetchone()[0]  # type: ignore[index]
        print(f"\nLoaded. Total revenue (SUCCESS only): {revenue:,.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=get_settings().demo_analytics_url,
        help="Target database URL (defaults to DEMO_ANALYTICS_URL).",
    )
    parser.add_argument("--readonly-password", default=READONLY_PASSWORD)
    args = parser.parse_args()

    if not args.url:
        sys.exit("No target database. Set DEMO_ANALYTICS_URL in .env or pass --url.")

    load(args.url, args.readonly_password)


if __name__ == "__main__":
    main()
