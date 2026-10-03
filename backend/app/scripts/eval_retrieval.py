"""Measure retrieval quality against questions with known answers.

Retrieval is the one part of the pipeline that can be evaluated honestly without
spending LLM quota: embed a question, see which tables come back, compare with
the tables the question actually needs.

The test set deliberately includes questions whose correct answer is one of the
"distractor" tables (marketing spend, support tickets, stock levels). Measuring
only questions about `orders` would reward a retriever that always returns
`orders`.

    uv run python -m app.scripts.eval_retrieval
    uv run python -m app.scripts.eval_retrieval --verbose   # show every question
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import DataSource
from app.db.session import get_engine, get_sessionmaker
from app.rag import build_embedding_provider
from app.rag.retriever import retrieve


@dataclass
class Case:
    question: str
    expects: set[str]
    """Tables the question genuinely needs. Bare names, no schema."""
    definition: str | None = None
    """A definition that must be retrieved, where one is decisive."""
    forbidden: set[str] = field(default_factory=set)
    """Tables that would be wrong answers — usually a confusable archive."""


CASES = [
    # Core revenue questions. `orders_legacy` is a 2023 archive with the same
    # columns, so ranking it above `orders` would answer from stale data.
    Case("What was total revenue last month?", {"orders"}, "Revenue", {"orders_legacy"}),
    Case("How did revenue change this month compared with last month?", {"orders"},
         "Revenue", {"orders_legacy"}),
    Case("Why did revenue fall in June 2026?", {"orders"}, None, {"orders_legacy"}),
    Case("Which region had the biggest drop in sales?", {"orders"}, "Region"),
    Case("What was our GMV for the year?", {"orders"}, "Revenue", {"orders_legacy"}),
    Case("What is our average order value?", {"orders"}, "Average order value"),
    Case("How many orders were placed through the mobile app?", {"orders"}),
    Case("How has the share of orders from each channel changed?", {"orders"}, "Channel"),
    Case("How many active customers do we have?", {"orders"}, "Active customer"),

    # Questions needing a join: the second and third tables rarely rank on their own.
    Case("Which product category generated the most revenue?", {"order_items", "products"},
         "Revenue by product or category"),
    Case("Who are our top 10 customers by revenue?", {"orders", "customers"}),
    Case("Which products are refunded most often because they are defective?",
         {"refunds", "order_items", "products"}),
    Case("What is the refund rate for Home & Kitchen?", {"refunds", "order_items", "products"},
         "Refund rate"),
    Case("Why did refunds increase recently?", {"refunds"}),

    # Single-table questions about the rest of the schema.
    Case("What is the payment success rate by payment method?", {"payments"},
         "Payment success rate"),
    Case("Which brands have the most expensive products?", {"products"}),

    # Questions whose correct answer IS a table that distracts elsewhere.
    Case("How much did we spend on marketing campaigns?", {"campaign_spend"},
         None, {"orders"}),
    Case("How many support tickets were about delivery problems?", {"support_tickets"}),
    Case("What are current stock levels by warehouse?", {"inventory_snapshots"}),
    Case("How many website sessions converted into a purchase?", {"web_sessions"}),
    Case("Which suppliers have the longest lead times?", {"suppliers"}),
]


def bare(name: str) -> str:
    return name.split(".")[-1].lower()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="ShopSphere (Postgres)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    provider = build_embedding_provider()

    async with get_sessionmaker()() as session:
        source = await session.scalar(select(DataSource).where(DataSource.name == args.name))
        if source is None:
            raise SystemExit(f"No data source named {args.name!r}.")

        total_tables = await session.scalar(
            select(DataSource).where(DataSource.id == source.id)
        )
        del total_tables

        print(f"model     : {provider.model}")
        print(f"source    : {source.name}")
        print(f"cases     : {len(CASES)}\n")

        hits_at = {1: 0, 3: 0, 5: 0}
        full_coverage = 0
        recall_sum = 0.0
        definition_hits = definition_total = 0
        forbidden_in_top1 = forbidden_anywhere = 0
        failures: list[str] = []

        for case in CASES:
            context = await retrieve(
                session, source.id, case.question, provider,
                top_k_tables=settings.rag_top_k_tables,
                top_k_definitions=settings.rag_top_k_definitions,
                top_k_examples=settings.rag_top_k_examples,
                min_similarity=settings.rag_min_similarity,
            )
            ranked = [bare(chunk.title) for chunk in context.tables]

            # Ranking metrics use the vector ordering alone; expansion adds
            # tables deliberately, and crediting those would flatter the score.
            vector_ranked = [bare(c.title) for c in context.tables if c.reason == "vector"]
            for k in hits_at:
                if set(vector_ranked[:k]) & case.expects:
                    hits_at[k] += 1

            found = case.expects & set(ranked)
            recall_sum += len(found) / len(case.expects)
            complete = found == case.expects
            full_coverage += complete

            if case.definition:
                definition_total += 1
                definition_hits += case.definition in {c.title for c in context.definitions}

            if case.forbidden:
                if ranked and ranked[0] in case.forbidden:
                    forbidden_in_top1 += 1
                if case.forbidden & set(ranked):
                    forbidden_anywhere += 1

            if args.verbose or not complete:
                missing = case.expects - set(ranked)
                mark = "ok  " if complete else "MISS"
                print(f"{mark} {case.question}")
                print(f"     got: {ranked}")
                if missing:
                    print(f"     missing: {sorted(missing)}")
                if case.definition:
                    got = [c.title for c in context.definitions]
                    flag = "" if case.definition in got else "   <-- expected definition absent"
                    print(f"     definitions: {got}{flag}")
                if not complete:
                    failures.append(case.question)

        n = len(CASES)
        print(f"\n{'':<34}{'score':>12}")
        print(f"{'top-1 contains a needed table':<34}{hits_at[1]}/{n}  ({hits_at[1]/n:>5.0%})")
        print(f"{'top-3 contains a needed table':<34}{hits_at[3]}/{n}  ({hits_at[3]/n:>5.0%})")
        print(f"{'mean table recall':<34}{'':<5}  ({recall_sum/n:>5.0%})")
        print(f"{'all needed tables retrieved':<34}{full_coverage}/{n}  ({full_coverage/n:>5.0%})")
        if definition_total:
            print(f"{'decisive definition retrieved':<34}{definition_hits}/{definition_total}"
                  f"  ({definition_hits/definition_total:>5.0%})")
        print(f"{'wrong table ranked first':<34}{forbidden_in_top1}/{n}")
        print(f"{'wrong table present at all':<34}{forbidden_anywhere}/{n}")

        if failures:
            print(f"\nincomplete ({len(failures)}):")
            for question in failures:
                print(f"  - {question}")

    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
