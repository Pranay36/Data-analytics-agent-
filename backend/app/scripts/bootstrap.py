"""Register the demo data sources and discover their schemas.

Idempotent: running it twice leaves one of each, so it is safe to put in a
container entrypoint. If a source already exists it is re-synced instead.

Usage:
    uv run python -m app.scripts.bootstrap
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select

from app.connectors.csv_import import build_duckdb_from_csvs
from app.core.config import get_settings
from app.core.crypto import CryptoError, encrypt_secret
from app.db.models import DataSource
from app.db.session import get_engine, get_sessionmaker
from app.services import datasource_service as service

logger = logging.getLogger(__name__)

REPO_DIR = Path(__file__).resolve().parents[3]
CSV_DIR = REPO_DIR / "demo_data" / "generated"
DEMO_TABLES = ["customers", "products", "orders", "order_items", "payments", "refunds"]
BUSINESS_CONTEXT = {"as_of_date": "2026-06-30", "currency": "INR"}

POSTGRES_NAME = "ShopSphere (Postgres)"
CSV_NAME = "ShopSphere (CSV)"


def _postgres_config() -> tuple[dict, str]:
    """Connect as the read-only role, never the owner that loaded the data."""
    url = urlparse(get_settings().demo_analytics_url)
    return (
        {
            "host": url.hostname or "localhost",
            "port": url.port or 5432,
            "database": url.path.lstrip("/"),
            "username": "insightflow_ro",
            "schemas": ["public"],
        },
        "insightflow_ro",
    )


async def main() -> None:
    settings = get_settings()
    if not settings.demo_analytics_url:
        raise SystemExit("DEMO_ANALYTICS_URL is not set.")

    try:
        encrypt_secret("probe")
    except CryptoError as exc:
        raise SystemExit(f"{exc}\nThen add it to .env and re-run.") from exc

    async with get_sessionmaker()() as session:
        existing = {
            s.name: s
            for s in (await session.scalars(select(DataSource))).all()
        }

        if POSTGRES_NAME in existing:
            await service.sync_data_source(session, existing[POSTGRES_NAME].id)
            print(f"re-synced   {POSTGRES_NAME}")
        else:
            config, password = _postgres_config()
            source = await service.create_data_source(
                session, name=POSTGRES_NAME, source_type="postgres",
                config=config, password=password, business_context=BUSINESS_CONTEXT,
            )
            print(f"registered  {POSTGRES_NAME}  ({source.id})")

        if CSV_NAME in existing:
            await service.sync_data_source(session, existing[CSV_NAME].id)
            print(f"re-synced   {CSV_NAME}")
        elif (CSV_DIR / "orders.csv").exists():
            target = settings.upload_dir / "demo" / "shopsphere.duckdb"
            loaded = build_duckdb_from_csvs(
                [CSV_DIR / f"{name}.csv" for name in DEMO_TABLES], target
            )
            source = await service.create_data_source(
                session, name=CSV_NAME, source_type="csv",
                config={"path": str(target)}, business_context=BUSINESS_CONTEXT,
            )
            print(f"registered  {CSV_NAME}  ({source.id}) — {sum(loaded.values()):,} rows")
        else:
            print("skipped     CSV source (run app.scripts.generate_demo_data first)")

        await session.commit()

    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
