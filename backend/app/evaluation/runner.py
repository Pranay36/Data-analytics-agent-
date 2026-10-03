"""Running an evaluation suite.

Each case goes through the real system (retrieval, agents, guard, database), and the
outcome is judged against computed ground truth rather than by eye. The aim is a score
that is objective and a failure that is diagnosable: not just "case 14 failed" but
"it retrieved the right tables, wrote SQL that ran, and returned the wrong number
because the status filter was missing".

What is deliberately *not* done: no model grades another model's work. Every check
here is code. That is slower to write than asking an LLM to judge, and it is the only
way to get a number that means the same thing on every run.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.config import Settings
from app.core.model_registry import get_registry
from app.db.models import DataSource, EvaluationRun
from app.db.session import get_sessionmaker
from app.evaluation.cases import Case, Suite
from app.evaluation.checks import check_grounding, check_rules, tables_in
from app.evaluation.compare import compare_results
from app.graph.state import ExecutedQuery
from app.llm import LLMClient
from app.rag import build_embedding_provider
from app.rag.embeddings import EmbeddingProvider
from app.rag.retriever import retrieve
from app.services import datasource_service as sources
from app.services.analysis_runner import run_analysis

logger = logging.getLogger(__name__)


def _bare(name: str) -> str:
    return name.split(".")[-1].lower()


@dataclass
class CaseResult:
    id: str
    category: str
    question: str
    passed: bool = False
    checks: dict[str, bool | None] = field(default_factory=dict)
    """True/False where a check applied, None where it did not."""
    reasons: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    latency_ms: int = 0
    llm_calls: int = 0
    tokens: int = 0
    attempts: int = 0
    depth: int = 0
    generated_sql: str | None = None
    gold_sql: str | None = None
    summary: str | None = None
    retrieved_tables: list[str] = field(default_factory=list)
    table_recall: float | None = None
    error: str | None = None


# ── Retrieval ────────────────────────────────────────────────────────────────
def _retrieval_checks(case: Case, tables: list[str], definitions: list[str]) -> dict[str, Any]:
    got = {_bare(t) for t in tables}
    out: dict[str, Any] = {}

    if case.expected_tables:
        expected = {_bare(t) for t in case.expected_tables}
        out["table_recall"] = len(expected & got) / len(expected)
        out["retrieval_all_tables"] = expected <= got

    if case.forbid_tables:
        out["retrieval_no_decoy"] = not (({_bare(t) for t in case.forbid_tables}) & got)

    if case.decisive_definition:
        out["retrieval_definition"] = case.decisive_definition in definitions
    return out


# ── Judging one case ─────────────────────────────────────────────────────────
def _final_query(queries: list[ExecutedQuery]) -> ExecutedQuery | None:
    return next((q for q in reversed(queries) if q.status == "succeeded"), None)


def _path_check(case: Case, queries: list[ExecutedQuery]) -> tuple[bool, str]:
    """Did the investigation visit the expected dimensions and find the expected segments?

    The segment found at each step is read from the data (the biggest contributor in that
    breakdown), not from what the model said, so it cannot be argued into passing.
    """
    steps = [q for q in queries if q.status == "succeeded" and q.dimension]
    for index, expected in enumerate(case.expect_path):
        if index >= len(steps):
            return False, f"stopped before reaching {expected.dimension}"
        step = steps[index]
        if step.dimension != expected.dimension:
            return (
                False,
                f"step {index + 1} broke down by {step.dimension}, expected {expected.dimension}",
            )
        if expected.segment is None:
            continue
        comparison = (step.profile or {}).get("comparison") or {}
        top = (comparison.get("segments") or [{}])[0].get("segment", "")
        if top.lower() != expected.segment.lower():
            return False, f"step {index + 1} found {top!r}, expected {expected.segment!r}"
    return True, ""


def judge(
    case: Case,
    state: dict[str, Any],
    gold_rows: list[list[Any]] | None,
    *,
    dialect: str,
    data_unchanged: bool | None,
) -> CaseResult:
    queries: list[ExecutedQuery] = state.get("queries", [])
    final = _final_query(queries)
    rounds = state.get("analysis_rounds") or []
    summary = None
    if rounds:
        summary = " ".join([rounds[-1].summary, *(f.statement for f in rounds[-1].findings)])

    result = CaseResult(
        id=case.id,
        category=case.category,
        question=case.question,
        stop_reason=state.get("stop_reason"),
        attempts=len(queries),
        depth=state.get("drilldown_depth", 0),
        generated_sql=(final.sql if final else None),
        gold_sql=case.gold_sql,
        summary=summary,
    )

    retrieved = state.get("retrieved")
    if retrieved is not None:
        result.retrieved_tables = [_bare(t) for t in retrieved.table_names]
        result.checks.update(
            _retrieval_checks(case, retrieved.table_names, [d.title for d in retrieved.definitions])
        )
        result.table_recall = result.checks.get("table_recall")  # type: ignore[assignment]

    required: list[str] = []

    # ── Questions that should be refused, or must not change anything ─────────
    if case.expected_outcome == "cannot_answer":
        refused = state.get("stop_reason") == "cannot_answer"
        result.checks["refused"] = refused
        required.append("refused")
        if not refused:
            result.reasons.append(
                f"should have said it cannot answer; instead: {state.get('stop_reason')}"
            )

    elif case.expected_outcome == "no_write":
        result.checks["data_unchanged"] = data_unchanged
        required.append("data_unchanged")
        if data_unchanged is not True:
            result.reasons.append("the data changed")

    # ── Questions with an answer ─────────────────────────────────────────────
    else:
        executed = final is not None
        result.checks["executed"] = executed
        required.append("executed")
        if not executed:
            result.reasons.append(f"no query succeeded ({state.get('stop_reason')})")
        else:
            first = next((q for q in queries if q.attempt == 1), None)
            result.checks["first_try"] = first is not None and first.status == "succeeded"

        if case.gold_sql and executed:
            comparison = compare_results(
                gold_rows or [],
                final.rows,
                case.compare or "rows_unordered",
                tolerance=case.tolerance,
                allow_percent_scale=case.allow_percent_scale,
                top_k=case.top_k,
            )
            result.checks["result_correct"] = comparison.ok
            required.append("result_correct")
            if not comparison.ok:
                result.reasons.append(f"wrong result: {comparison.detail}")

        if case.rules and executed:
            failed = [r for r in check_rules(final.sql or "", case.rules, dialect) if not r.ok]
            result.checks["rules"] = not failed
            required.append("rules")
            result.reasons += [f"rule not met: {r.rule} ({r.detail})" for r in failed]

        if case.forbid_tables and executed:
            used = tables_in(final.sql or "", dialect) & {t.lower() for t in case.forbid_tables}
            result.checks["no_decoy_table"] = not used
            required.append("no_decoy_table")
            if used:
                result.reasons.append(f"queried a table it should not have: {sorted(used)}")

        if case.expect_path:
            ok, detail = _path_check(case, queries)
            result.checks["investigation_path"] = ok
            required.append("investigation_path")
            if not ok:
                result.reasons.append(f"investigation: {detail}")

        if case.expect_mentions:
            text = (summary or "").lower()
            missing = [m for m in case.expect_mentions if m.lower() not in text]
            result.checks["mentions"] = not missing
            required.append("mentions")
            if missing:
                result.reasons.append(f"summary does not mention {missing}")

        if summary and executed:
            grounding = check_grounding(summary, queries)
            result.checks["grounded"] = grounding.ok  # diagnostic: not required to pass
            if not grounding.ok:
                result.reasons.append(
                    f"ungrounded numbers in the summary: {grounding.ungrounded[:4]}"
                )

    result.passed = all(result.checks.get(name) is True for name in required)
    return result


# ── Running a suite ──────────────────────────────────────────────────────────
@dataclass
class EvalReport:
    suite: str
    source: str
    config: dict[str, Any]
    results: list[CaseResult]
    started_at: datetime
    duration_s: float
    summary: dict[str, Any] = field(default_factory=dict)


def _rate(values: list[bool | None]) -> float | None:
    applicable = [v for v in values if v is not None]
    return sum(applicable) / len(applicable) if applicable else None


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(pct / 100 * (len(ordered) - 1))))]


def summarise(results: list[CaseResult]) -> dict[str, Any]:
    llm = [r for r in results if r.category != "retrieval_only"]
    answers = [r for r in llm if "executed" in r.checks]

    def collect(name: str, pool: list[CaseResult]) -> list[bool | None]:
        return [r.checks.get(name) for r in pool]

    by_category: dict[str, dict[str, int]] = defaultdict(lambda: {"passed": 0, "total": 0})
    for r in llm:
        by_category[r.category]["total"] += 1
        by_category[r.category]["passed"] += int(r.passed)

    latencies = [r.latency_ms / 1000 for r in llm if r.latency_ms]
    return {
        "cases": len(llm),
        "passed": sum(r.passed for r in llm),
        "accuracy": _rate([r.passed for r in llm]),
        "sql_execution": _rate(collect("executed", answers)),
        "first_try_valid": _rate(collect("first_try", answers)),
        "result_correct": _rate(collect("result_correct", llm)),
        "business_rules": _rate(collect("rules", llm)),
        "no_decoy_table": _rate(collect("no_decoy_table", llm)),
        "investigation_path": _rate(collect("investigation_path", llm)),
        "mentions": _rate(collect("mentions", llm)),
        "correct_refusals": _rate(collect("refused", llm)),
        "data_untouched": _rate(collect("data_unchanged", llm)),
        "grounded_summaries": _rate(collect("grounded", llm)),
        "retrieval_all_tables": _rate(collect("retrieval_all_tables", results)),
        "retrieval_no_decoy": _rate(collect("retrieval_no_decoy", results)),
        "retrieval_definition": _rate(collect("retrieval_definition", results)),
        "table_recall": (
            statistics.mean(r.table_recall for r in results if r.table_recall is not None)
            if any(r.table_recall is not None for r in results)
            else None
        ),
        "latency_p50_s": _percentile(latencies, 50),
        "latency_p95_s": _percentile(latencies, 95),
        "llm_calls_mean": statistics.mean(r.llm_calls for r in llm) if llm else None,
        "tokens_mean": statistics.mean(r.tokens for r in llm) if llm else None,
        "tokens_total": sum(r.tokens for r in llm),
        "by_category": dict(by_category),
    }


def config_snapshot(settings: Settings, *, use_cache: bool) -> dict[str, Any]:
    """What was in force, so a score can always be traced back to its configuration."""
    registry = get_registry()
    return {
        "llm_chain": [ref.key for ref in registry.chain],
        "agent_models": {k: v.key for k, v in registry.agent_overrides.items()},
        "embedding_model": registry.embedding.ref.key,
        "rag": {
            "top_k_tables": settings.rag_top_k_tables,
            "top_k_definitions": settings.rag_top_k_definitions,
            "top_k_examples": settings.rag_top_k_examples,
            "relative_cutoff": settings.rag_relative_cutoff,
        },
        "limits": {
            "max_drilldown_depth": settings.max_drilldown_depth,
            "max_sql_repair_attempts": settings.max_sql_repair_attempts,
            "max_llm_calls": settings.max_llm_calls_per_analysis,
            "sql_max_rows": settings.sql_max_rows,
        },
        "llm_response_cache": use_cache,
    }


async def _count_orders(connector) -> int | None:
    try:
        result = await asyncio.to_thread(connector.execute, "SELECT COUNT(*) FROM orders")
        return int(result.rows[0][0])
    except Exception:  # noqa: BLE001 - a non-orders source simply has nothing to compare
        return None


async def run_retrieval_only(
    suite: Suite,
    source_name: str,
    cases: list[Case],
    embeddings: EmbeddingProvider,
    settings: Settings,
) -> list[CaseResult]:
    """Measure retrieval alone: no model calls, so it costs no quota and runs in seconds."""
    results: list[CaseResult] = []
    async with get_sessionmaker()() as session:
        source = await session.scalar(select(DataSource).where(DataSource.name == source_name))
        if source is None:
            raise ValueError(f"No data source named {source_name!r}")
        for case in cases:
            if not case.expected_tables:
                continue
            context = await retrieve(
                session,
                source.id,
                case.question,
                embeddings,
                top_k_tables=settings.rag_top_k_tables,
                top_k_definitions=settings.rag_top_k_definitions,
                top_k_examples=settings.rag_top_k_examples,
                min_similarity=settings.rag_min_similarity,
                relative_cutoff=settings.rag_relative_cutoff,
                max_tables=settings.rag_max_tables,
            )
            result = CaseResult(id=case.id, category=case.category, question=case.question)
            result.retrieved_tables = [_bare(t) for t in context.table_names]
            result.checks = _retrieval_checks(
                case, context.table_names, [d.title for d in context.definitions]
            )
            result.table_recall = result.checks.get("table_recall")
            result.passed = all(v for v in result.checks.values() if isinstance(v, bool))
            if not result.passed:
                result.reasons.append(f"retrieved {result.retrieved_tables}")
            results.append(result)
    return results


async def run_suite(
    suite: Suite,
    source_name: str,
    cases: list[Case],
    *,
    settings: Settings,
    use_llm_cache: bool = False,
    on_result: Callable[[CaseResult, int, int], None] | None = None,
    llm: LLMClient | None = None,
    embeddings: EmbeddingProvider | None = None,
    persist: bool = True,
) -> EvalReport:
    """Run `cases` end to end against the real system."""
    run_settings = settings.model_copy(update={"llm_cache_enabled": use_llm_cache})
    client = llm or LLMClient.from_settings(run_settings)
    provider = embeddings or build_embedding_provider(run_settings)

    async with get_sessionmaker()() as session:
        source = await session.scalar(select(DataSource).where(DataSource.name == source_name))
        if source is None:
            raise ValueError(f"No data source named {source_name!r}. Run app.scripts.bootstrap.")
        source_id = source.id

    config = config_snapshot(settings, use_cache=use_llm_cache)
    started = datetime.now(UTC)
    clock = time.perf_counter()

    record_id = None
    if persist:
        async with get_sessionmaker()() as session:
            record = EvaluationRun(suite=suite.name, data_source_id=source_id, config=config)
            session.add(record)
            await session.commit()
            record_id = record.id

    results: list[CaseResult] = []
    async with get_sessionmaker()() as session:
        source = await sources.get_data_source(session, source_id)

    async with sources.open_connector(source) as connector:
        for position, case in enumerate(cases, start=1):
            if case.is_retrieval_only:
                continue

            gold_rows = None
            if case.gold_sql:
                gold = await asyncio.to_thread(
                    connector.execute, case.gold_sql, max_rows=settings.sql_max_rows
                )
                gold_rows = gold.rows

            before = await _count_orders(connector) if case.expected_outcome == "no_write" else None
            began = time.perf_counter()
            try:
                run = await run_analysis(
                    source_id, case.question, settings=run_settings, llm=client, embeddings=provider
                )
            except Exception as exc:  # noqa: BLE001 - a crashed case is a failed case, not a failed suite
                logger.exception("case crashed", extra={"case": case.id})
                result = CaseResult(
                    id=case.id,
                    category=case.category,
                    question=case.question,
                    error=f"{type(exc).__name__}: {exc}",
                    reasons=[f"crashed: {type(exc).__name__}"],
                    latency_ms=int((time.perf_counter() - began) * 1000),
                )
            else:
                after = await _count_orders(connector) if before is not None else None
                result = judge(
                    case,
                    run.state,
                    gold_rows,
                    dialect=connector.dialect,
                    data_unchanged=(before == after) if before is not None else None,
                )
                result.latency_ms = run.latency_ms
                result.llm_calls = run.llm_calls
                result.tokens = run.tokens

            results.append(result)
            if on_result:
                on_result(result, position, len(cases))

    # Retrieval-only cases cost no model calls, so they always run.
    results += await run_retrieval_only(
        suite, source_name, [c for c in cases if c.is_retrieval_only], provider, settings
    )

    report = EvalReport(
        suite=suite.name,
        source=source_name,
        config=config,
        results=results,
        started_at=started,
        duration_s=time.perf_counter() - clock,
        summary=summarise(results),
    )

    if persist and record_id is not None:
        async with get_sessionmaker()() as session:
            record = await session.get(EvaluationRun, record_id)
            if record is not None:
                record.status = "completed"
                record.summary = report.summary
                record.results = [asdict(r) for r in results]
                record.completed_at = datetime.now(UTC)
                await session.commit()

    return report
