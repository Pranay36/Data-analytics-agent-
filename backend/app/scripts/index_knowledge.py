"""Build (or refresh) the retrieval index for a data source.

    uv run python -m app.scripts.index_knowledge                # every data source
    uv run python -m app.scripts.index_knowledge --name "ShopSphere (Postgres)"

Unchanged chunks are skipped, so re-running costs almost nothing.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import select

from app.db.models import DataSource
from app.db.session import get_engine, get_sessionmaker
from app.rag import build_embedding_provider, reindex_data_source

SEED_PATH = Path(__file__).resolve().parents[3] / "demo_data" / "knowledge" / "shopsphere.yaml"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", help="only this data source")
    parser.add_argument("--no-seed", action="store_true", help="skip definitions and examples")
    args = parser.parse_args()

    provider = build_embedding_provider()
    print(f"embedding with {provider.model} ({provider.dimension} dimensions)\n")

    async with get_sessionmaker()() as session:
        query = select(DataSource).order_by(DataSource.name)
        if args.name:
            query = query.where(DataSource.name == args.name)
        sources = (await session.scalars(query)).all()
        if not sources:
            raise SystemExit("No matching data sources. Run app.scripts.bootstrap first.")

        for source in sources:
            result = await reindex_data_source(
                session, source.id, provider,
                seed_path=None if args.no_seed else SEED_PATH,
            )
            print(f"{source.name:<26} {result.total:>3} chunks  "
                  f"{result.embedded:>3} embedded  {result.skipped:>3} unchanged  "
                  f"{result.deleted:>2} removed")
        await session.commit()

    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
