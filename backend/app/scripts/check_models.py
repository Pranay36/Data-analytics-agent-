"""Live check of the LLM layer against the real configured models.

Free-tier models appear, vanish and change behaviour without notice, so this is
how to find out what works *today*. It spends real (free) quota, so it is a
script you run on purpose rather than part of the test suite.

    uv run python -m app.scripts.check_models           # one end-to-end question
    uv run python -m app.scripts.check_models --probe   # every model x strategy (~9 calls)

The end-to-end run is the meaningful one: it asks the real client for a query,
validates it with the SQL guard, executes it against the demo database and
compares the answer with the known ground truth.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from urllib.parse import urlparse

from pydantic import BaseModel

from app.connectors import create_connector
from app.core.config import get_settings
from app.llm import ChatMessage, LLMClient
from app.llm.capabilities import strategies_for
from app.llm.errors import ProviderError
from app.llm.registry import build_providers
from app.llm.structured import build_schema, parse_structured, with_schema_instructions
from app.llm.types import ChainEntry, LLMRequest, StructuredSpec
from app.observability.recorder import DbLlmCallRecorder
from app.sql_guard import validate_sql

CONTEXT = """Dialect: PostgreSQL. Today is 2026-06-30.
DEFINITION Revenue = SUM(orders.total_amount) WHERE orders.status = 'SUCCESS'.
TABLE orders(id, customer_id, order_date timestamp, status [SUCCESS,FAILED,CANCELLED,PENDING],
             shipping_region [North,South,East,West], total_amount numeric)
TABLE customers(id, name, region)"""

SYSTEM = (
    "You write one read-only SQL query that answers the question. "
    "Use only the tables given and apply definitions exactly."
)
QUESTION = "What was total revenue in June 2026 for the South region?"
TRUTH_SQL = (
    "SELECT round(sum(total_amount), 2) FROM orders WHERE status = 'SUCCESS' "
    "AND shipping_region = 'South' AND order_date >= DATE '2026-06-01' "
    "AND order_date < DATE '2026-07-01'"
)


class QueryAnswer(BaseModel):
    """A SQL query answering the question."""

    can_answer: bool
    sql: str
    explanation: str


def _demo_connector():
    url = urlparse(get_settings().demo_analytics_url)
    return create_connector(
        "postgres",
        {
            "host": url.hostname,
            "port": url.port,
            "database": url.path.lstrip("/"),
            "username": "insightflow_ro",
            "schemas": ["public"],
        },
        "insightflow_ro",
    )


async def end_to_end() -> bool:
    client = LLMClient.from_settings(recorder=DbLlmCallRecorder())
    print(f"chain: {' -> '.join(e.key for e in client.chain_for('query'))}")
    print(f"question: {QUESTION}\n")

    started = time.perf_counter()
    result = await client.generate_structured(
        agent="query",
        messages=[
            ChatMessage(role="system", content=SYSTEM),
            ChatMessage(role="user", content=f"{CONTEXT}\n\nQuestion: {QUESTION}"),
        ],
        schema=QueryAnswer,
    )
    elapsed = int((time.perf_counter() - started) * 1000)

    print(f"answered by : {result.provider}:{result.model}")
    print(f"strategy    : {result.strategy}")
    print(f"llm calls   : {result.llm_calls}{'  (served from cache)' if result.cached else ''}")
    print(f"tokens      : {result.usage.total}{' (estimated)' if result.usage.estimated else ''}")
    print(f"latency     : {elapsed} ms")
    print(f"\ngenerated SQL:\n  {result.value.sql}\n")

    verdict = validate_sql(
        result.value.sql, dialect="postgres", allowed_tables={"public.orders", "public.customers"}
    )
    print(f"guard       : {'approved' if verdict.ok else 'REJECTED - ' + verdict.error_text}")
    if not verdict.ok:
        return False

    connector = _demo_connector()
    with connector:
        got = connector.execute(verdict.safe_sql).rows[0][0]
        expected = connector.execute(TRUTH_SQL).rows[0][0]
    correct = round(float(got), 2) == round(float(expected), 2)
    print(f"result      : {got}   expected {expected}   -> {'CORRECT' if correct else 'WRONG'}")
    return correct


async def probe() -> None:
    settings = get_settings()
    chain = [ChainEntry.parse(item) for item in settings.llm_fallback_chain]
    providers = build_providers(settings, chain)
    schema = build_schema(QueryAnswer)
    base = [ChatMessage(role="system", content=SYSTEM),
            ChatMessage(role="user", content=f"{CONTEXT}\n\nQuestion: {QUESTION}")]

    print(f"{'model':<52} {'strategy':<12} result")
    for entry in chain:
        provider = providers.get(entry.provider)
        if provider is None:
            print(f"{entry.key:<52} {'-':<12} no credentials")
            continue
        for strategy in ("tool_call", "json_schema", "prompt_json"):
            messages = with_schema_instructions(base, schema) if strategy == "prompt_json" else base
            request = LLMRequest(
                model=entry.model, messages=messages, max_tokens=800, timeout_seconds=90,
                structured=StructuredSpec(strategy=strategy, name="answer", json_schema=schema),
            )
            try:
                response = await provider.complete(request)
                parse_structured(response.content, QueryAnswer)
                outcome = f"ok ({response.latency_ms} ms)"
            except ProviderError as exc:
                outcome = f"{type(exc).__name__}: {exc.message[:60]}"
            except Exception as exc:  # noqa: BLE001 - diagnostic output
                outcome = f"invalid output: {str(exc)[:60]}"
            declared = "declared" if strategy in strategies_for(entry) else "not in models.yaml"
            print(f"{entry.key:<52} {strategy:<12} {outcome}  [{declared}]")
            await asyncio.sleep(1.5)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="test every model and strategy")
    args = parser.parse_args()

    if args.probe:
        await probe()
    else:
        ok = await end_to_end()
        raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
