#!/usr/bin/env python3
"""Create insightflow database on Aiven and run all schema setup."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"


def main() -> None:
    base_url = os.environ.get("AVEN_BASE_URL")
    if not base_url:
        sys.exit("Set AVEN_BASE_URL (postgres://user:pass@host:port/defaultdb?sslmode=require)")

    admin = psycopg.connect(base_url, autocommit=True)
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = 'insightflow'")
        if cur.fetchone() is None:
            print("Creating database insightflow …")
            cur.execute("CREATE DATABASE insightflow")
        else:
            print("Database insightflow already exists")

    # App + demo both use insightflow on Aiven.
    app_url = base_url.replace("/defaultdb", "/insightflow")
    print(f"Connecting to {app_url.split('@')[-1]}")

    with psycopg.connect(app_url, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        print("pgvector extension enabled")

    env = os.environ.copy()
    env["DATABASE_URL"] = app_url.replace("postgres://", "postgresql+asyncpg://").replace(
        "sslmode=require", "ssl=require"
    )
    env["DEMO_ANALYTICS_URL"] = app_url

    if not env.get("DATASOURCE_ENCRYPTION_KEY"):
        from cryptography.fernet import Fernet

        env["DATASOURCE_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    if not env.get("AUTH_SECRET_KEY"):
        import secrets

        env["AUTH_SECRET_KEY"] = secrets.token_hex(32)

    print("Running alembic migrations …")
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True)

    print("Generating demo data …")
    subprocess.run(
        ["uv", "run", "python", "-m", "app.scripts.generate_demo_data"],
        cwd=BACKEND,
        env=env,
        check=True,
    )

    print("Loading demo warehouse tables …")
    subprocess.run(
        ["uv", "run", "python", "-m", "app.scripts.load_postgres", "--if-empty"],
        cwd=BACKEND,
        env=env,
        check=True,
    )

    print("Registering demo datasources …")
    subprocess.run(
        ["uv", "run", "python", "-m", "app.scripts.bootstrap"],
        cwd=BACKEND,
        env=env,
        check=True,
    )

    print("Indexing business knowledge for retrieval …")
    subprocess.run(
        ["uv", "run", "python", "-m", "app.scripts.index_knowledge"],
        cwd=BACKEND,
        env=env,
        check=True,
    )

    with psycopg.connect(app_url) as conn:
        tables = conn.execute(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
            ORDER BY tablename
            """
        ).fetchall()
        print(f"\nDone — {len(tables)} tables in insightflow:")
        for (name,) in tables:
            print(f"  - {name}")

    print("\nAdd these to your .env:")
    print(f"DATABASE_URL={env['DATABASE_URL']}")
    print(f"DEMO_ANALYTICS_URL={env['DEMO_ANALYTICS_URL']}")
    print(f"DATASOURCE_ENCRYPTION_KEY={env['DATASOURCE_ENCRYPTION_KEY']}")
    print(f"AUTH_SECRET_KEY={env['AUTH_SECRET_KEY']}")


if __name__ == "__main__":
    main()
