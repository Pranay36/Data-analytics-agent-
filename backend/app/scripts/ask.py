"""Ask a question from the command line and watch the pipeline work.

    uv run python -m app.scripts.ask "What was revenue last month?"
    uv run python -m app.scripts.ask --source "ShopSphere (CSV)" "Which region is largest?"

Prints what retrieval supplied, every query the model attempted (including the ones
the guard rejected), and the result.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.db.models import DataSource
from app.db.session import get_engine, get_sessionmaker
from app.observability.rate_limits import get_rate_limit_tracker
from app.services.analysis_runner import run_analysis


async def resolve(name: str) -> DataSource:
    async with get_sessionmaker()() as session:
        source = await session.scalar(select(DataSource).where(DataSource.name == name))
    if source is None:
        raise SystemExit(f"No data source named {name!r}. Run app.scripts.bootstrap first.")
    return source


def show(result, verbose: bool) -> None:
    state = result.state
    context = state.get("retrieved")

    if context is not None:
        print(f"\nretrieval ({context.confidence} confidence)")
        print("  tables     :", ", ".join(c.title.split(".")[-1] for c in context.tables))
        print("  definitions:", ", ".join(c.title for c in context.definitions) or "-")
        if verbose:
            for chunk in context.all_chunks:
                print(f"    {chunk.kind:<14} {chunk.score:.3f}  {chunk.title}  [{chunk.reason}]")

    for query in state.get("queries", []):
        print(f"\nattempt {query.attempt}: {query.status.upper()}"
              + (f"  ({query.execution_ms} ms)" if query.execution_ms is not None else ""))
        print("  " + (query.sql or query.original_sql).replace("\n", "\n  "))
        if query.error:
            print(f"  -> {query.error}")
        if query.status == "succeeded":
            print(f"  -> {query.row_count} row(s): {query.columns}")
            for row in query.rows[:8]:
                print("    ", row)
            if query.row_count > 8:
                print(f"     ... {query.row_count - 8} more")

    for number, round_ in enumerate(state.get("analysis_rounds", []), start=1):
        print(f"\nanalysis {number} ({round_.confidence} confidence)")
        print(f"  {round_.summary}")
        for finding in round_.findings:
            print(f"  - {finding.statement}")
        if round_.needs_drilldown and round_.drilldown:
            drill = round_.drilldown
            print(f"  => drill into {drill.dimension} "
                  f"within {drill.focus_value or '(overall)'}: {drill.rationale}")

    if state.get("drilldown_depth"):
        print(f"\ninvestigated {state['drilldown_depth']} level(s): "
              f"{' -> '.join(state.get('used_dimensions', []))}")
    for note in state.get("notes", []):
        print(f"note   : {note}")

    print(f"\nresult : {state.get('stop_reason')}", end="")
    if (error := state.get("error")) is not None:
        print(f"  [{error.code}] {error.message}", end="")
    print(f"\nstats  : {result.llm_calls} LLM call(s), {result.tokens} tokens, "
          f"{result.latency_ms} ms")
    for provider, status in get_rate_limit_tracker().snapshot().items():
        print(f"limits : {provider}: {status}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--source", default="ShopSphere (Postgres)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    source = await resolve(args.source)
    print(f"{source.name}: {args.question}")
    result = await run_analysis(source.id, args.question)
    show(result, args.verbose)
    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
