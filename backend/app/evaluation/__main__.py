"""Run the evaluation suite.

    uv run python -m app.evaluation                       # full run, live model calls
    uv run python -m app.evaluation --retrieval-only      # no model calls, runs in seconds
    uv run python -m app.evaluation --cases revenue_june_2026,gmv_march
    uv run python -m app.evaluation --category business_rule
    uv run python -m app.evaluation --cache               # reuse cached model replies

A headline score should come from a run with the cache off, because a cached run
re-judges old model output and says nothing about how the model behaves today. The
cache is for iterating on code that sits downstream of the model.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.core.config import get_settings
from app.db.session import get_engine
from app.evaluation.cases import load_suite
from app.evaluation.report import write_report
from app.evaluation.runner import CaseResult, EvalReport, run_retrieval_only, run_suite, summarise
from app.rag import build_embedding_provider

REPORTS = Path(__file__).resolve().parents[3] / "reports"


def _line(result: CaseResult, position: int, total: int) -> None:
    note = f"  {result.reasons[0][:70]}" if result.reasons and not result.passed else ""
    print(
        f"[{position:>2}/{total}] {'PASS' if result.passed else 'FAIL'}  {result.id:<28}"
        f"{result.latency_ms / 1000:>5.0f}s {result.llm_calls:>2} calls{note}",
        flush=True,
    )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--suite", default="core")
    parser.add_argument("--source", default="ShopSphere (Postgres)")
    parser.add_argument("--cases", help="comma-separated case ids")
    parser.add_argument("--category", help="comma-separated categories")
    parser.add_argument("--retrieval-only", action="store_true")
    parser.add_argument("--cache", action="store_true", help="reuse cached model replies")
    parser.add_argument(
        "--no-save", action="store_true", help="do not store the run in the database"
    )
    args = parser.parse_args()

    settings = get_settings()
    suite = load_suite(args.suite)
    cases = suite.select(
        ids=args.cases.split(",") if args.cases else None,
        categories=args.category.split(",") if args.category else None,
    )
    print(f"suite {suite.name}: {len(cases)} case(s) against {args.source}")

    if args.retrieval_only:
        provider = build_embedding_provider(settings)
        results = await run_retrieval_only(suite, args.source, cases, provider, settings)
        summary = summarise(results)
        print(f"\nretrieval over {len(results)} questions with {provider.model}")
        for label, key in (
            ("all needed tables found", "retrieval_all_tables"),
            ("mean table recall", "table_recall"),
            ("decisive definition found", "retrieval_definition"),
            ("decoy table kept out", "retrieval_no_decoy"),
        ):
            value = summary[key]
            print(f"  {label:<28}{'n/a' if value is None else f'{value:.0%}'}")
        for r in results:
            if not r.passed:
                print(f"  MISS {r.id}: {r.reasons[0] if r.reasons else ''}")
    else:
        print(f"llm cache: {'on' if args.cache else 'off, every call is live'}\n")
        report: EvalReport = await run_suite(
            suite,
            args.source,
            cases,
            settings=settings,
            use_llm_cache=args.cache,
            on_result=_line,
            persist=not args.no_save,
        )
        path = write_report(report, REPORTS)
        s = report.summary
        print(f"\n{s['passed']}/{s['cases']} passed ({s['accuracy']:.0%})   report: {path}")

    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
