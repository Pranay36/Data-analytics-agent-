#!/usr/bin/env python3
"""Ping Postgres so the provider does not treat the database as idle."""

from __future__ import annotations

import os
import sys
from urllib.parse import urlparse, urlunparse

import psycopg

QUERIES = [
    ("ping", "SELECT 1"),
    ("time", "SELECT NOW() AS ts"),
    ("users", "SELECT COUNT(*) FROM users"),
    ("orders", "SELECT COUNT(*) FROM orders"),
]


def to_psycopg_url(url: str) -> str:
    """App uses postgresql+asyncpg; this script uses sync psycopg."""
    if url.startswith("postgresql+asyncpg://"):
        url = "postgresql://" + url.removeprefix("postgresql+asyncpg://")
    parsed = urlparse(url)
    query = parsed.query.replace("ssl=require", "sslmode=require")
    if "sslmode=" not in query and parsed.hostname and "aivencloud.com" in parsed.hostname:
        query = f"{query}&sslmode=require" if query else "sslmode=require"
    return urlunparse(parsed._replace(query=query))


def main() -> int:
    raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1

    url = to_psycopg_url(raw)
    host = urlparse(url).hostname or "?"
    print(f"keepalive → {host}")

    with psycopg.connect(url, connect_timeout=15) as conn:
        for name, sql in QUERIES:
            try:
                row = conn.execute(sql).fetchone()
                print(f"  {name}: OK {row}")
            except psycopg.Error as exc:
                # Missing tables on a fresh DB should not fail the whole job.
                print(f"  {name}: skip ({exc.__class__.__name__})")

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
