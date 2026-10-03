"""Validating and running generated SQL. Deterministic: no model involved.

The model never reaches a database. Its SQL passes the guard, and only the guard's
*rewritten* output (with the row cap applied) is executed.
"""

from __future__ import annotations

import asyncio
import logging

from app.connectors import ConnectionFailed, ConnectorError
from app.graph.deps import GraphDeps
from app.graph.persistence import save_query, set_stage
from app.graph.state import AnalysisState, ExecutedQuery, RunError
from app.sql_guard import validate_sql

logger = logging.getLogger(__name__)

ZERO_ROWS_HINT = (
    "The query ran but returned no data (zero rows, or only NULL). Check that every "
    "literal matches one of the values shown in brackets in the TABLES section, and "
    "that the date range matches the question and today's date."
)


def is_empty_result(rows: list[list]) -> bool:
    """True when a result carries no information.

    An aggregate over nothing does not return zero rows. `SELECT SUM(x) ... WHERE
    status = 'completed'`, with no such status, returns *one* row holding NULL.
    Checking only the row count would wave through the most common kind of empty
    answer — the wrong-literal mistake this check exists to catch.
    """
    if not rows:
        return True
    return all(all(value is None for value in row) for row in rows)


def _purpose(state: AnalysisState) -> str:
    return "drilldown" if state.get("mode") == "drilldown" else "primary"


def _seq(state: AnalysisState) -> int:
    return len(state.get("queries", [])) + 1


async def validate_sql_node(state: AnalysisState, deps: GraphDeps) -> AnalysisState:
    await set_stage(deps, state.get("analysis_id"), "validating_sql")

    pending = state["pending"]
    result = validate_sql(
        pending.sql or "",
        dialect=state["datasource"].dialect,
        allowed_tables=set(state["allowed_tables"]),
        known_columns={t: set(c) for t, c in state["known_columns"].items()},
        max_rows=deps.settings.sql_max_rows,
    )

    if result.ok:
        return {"validation": result}

    # Rejected: keep the attempt for the audit trail, and hand the specific reason
    # back to the model. Naming the field or table is what makes a retry converge.
    query = ExecutedQuery(
        seq=_seq(state),
        attempt=state["attempt"],
        purpose=_purpose(state),  # type: ignore[arg-type]
        step_question=state["current_question"],
        original_sql=pending.sql or "",
        status="rejected",
        error=result.error_text,
        explanation=pending.explanation,
    )
    await save_query(deps, state.get("analysis_id"), query)
    logger.info("sql rejected", extra={"attempt": state["attempt"], "reason": result.error_text})

    return {
        "validation": result,
        "queries": [query],
        "last_error": result.error_text,
        "attempt": state["attempt"] + 1,
    }


async def execute_sql(state: AnalysisState, deps: GraphDeps) -> AnalysisState:
    await set_stage(deps, state.get("analysis_id"), "executing_sql")

    pending = state["pending"]
    validation = state["validation"]
    base = {
        "seq": _seq(state),
        "attempt": state["attempt"],
        "purpose": _purpose(state),
        "step_question": state["current_question"],
        "original_sql": pending.sql or "",
        "sql": validation.safe_sql,
        "tables_used": validation.tables,
        "explanation": pending.explanation,
    }

    try:
        result = await asyncio.to_thread(
            deps.connector.execute,
            validation.safe_sql or "",
            max_rows=deps.settings.sql_max_rows,
            timeout_seconds=deps.settings.sql_timeout_seconds,
        )
    except ConnectionFailed as exc:
        # Not the SQL's fault, so asking the model to rewrite it would be pointless.
        query = ExecutedQuery(**base, status="failed", error=exc.message)  # type: ignore[arg-type]
        await save_query(deps, state.get("analysis_id"), query)
        return {
            "queries": [query],
            "stop_reason": "error",
            "error": RunError(
                code="DATASOURCE_UNAVAILABLE",
                message="Could not reach the database. Check that it is running.",
            ),
        }
    except ConnectorError as exc:
        query = ExecutedQuery(**base, status="failed", error=exc.message)  # type: ignore[arg-type]
        await save_query(deps, state.get("analysis_id"), query)
        logger.info("sql failed", extra={"attempt": state["attempt"], "error": exc.message})
        return {
            "queries": [query],
            "last_error": exc.message,
            "attempt": state["attempt"] + 1,
        }

    query = ExecutedQuery(
        **base,  # type: ignore[arg-type]
        status="succeeded",
        columns=result.column_names,
        rows=result.rows,
        row_count=result.row_count,
        truncated=result.truncated,
        execution_ms=result.execution_ms,
    )
    await save_query(deps, state.get("analysis_id"), query)

    update: AnalysisState = {"queries": [query], "last_error": None}

    # Zero rows is valid SQL but often means a wrong literal or date. Give the model
    # exactly one more try, with the likely causes, then accept the empty answer —
    # "no data for that period" is a legitimate result.
    if is_empty_result(result.rows) and state["attempt"] == 1 and deps.max_attempts > 1:
        update["last_error"] = ZERO_ROWS_HINT
        update["attempt"] = state["attempt"] + 1
    return update
