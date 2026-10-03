"""LLMClient behaviour, driven by a scripted provider.

Each test pins one decision the client makes under failure. They run the real
retry, repair and fallback logic against deterministic fake replies, so none of
this costs a call.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.llm import CallBudget, ChainEntry, ChatMessage, LLMClient
from app.llm.budget import BudgetExceeded
from app.llm.cache import ResponseCache
from app.llm.errors import (
    BadRequest,
    LLMUnavailable,
    ModelUnavailable,
    ProviderTimeout,
    RateLimited,
)
from app.llm.fake import FakeLLMProvider
from app.observability.recorder import CallRecord


class Answer(BaseModel):
    """A query answer."""

    sql: str


GOOD = '{"sql": "SELECT 1"}'
MESSAGES = [ChatMessage(role="user", content="revenue?")]


class ListRecorder:
    def __init__(self) -> None:
        self.records: list[CallRecord] = []

    async def record(self, record: CallRecord) -> None:
        self.records.append(record)


async def no_sleep(_: float) -> None:
    return None


def make_client(*providers: FakeLLMProvider, models=None, **kwargs) -> LLMClient:
    # Unknown models default to [tool_call, prompt_json].
    chain = [ChainEntry(provider=p.name, model=m)
             for p, m in zip(providers, models or ["m"] * len(providers), strict=True)]
    return LLMClient({p.name: p for p in providers}, chain, sleep=no_sleep, **kwargs)


async def ask(client: LLMClient, **kwargs):
    return await client.generate_structured(
        agent="query", messages=MESSAGES, schema=Answer, **kwargs
    )


# ── Happy path ───────────────────────────────────────────────────────────────
async def test_returns_a_validated_object() -> None:
    provider = FakeLLMProvider([GOOD])
    result = await ask(make_client(provider))

    assert result.value.sql == "SELECT 1"
    assert result.llm_calls == 1
    assert result.usage.total == 120


async def test_requests_tool_call_mode_first() -> None:
    """Probing showed tool-call working on every model; JSON-schema on only some."""
    provider = FakeLLMProvider([GOOD])
    await ask(make_client(provider))

    spec = provider.requests[0].structured
    assert spec.strategy == "tool_call"
    assert spec.json_schema["properties"]["sql"]


# ── Repair ───────────────────────────────────────────────────────────────────
async def test_invalid_output_is_repaired_with_the_specific_error() -> None:
    provider = FakeLLMProvider(['{"wrong": 1}', GOOD])
    result = await ask(make_client(provider))

    assert result.value.sql == "SELECT 1"
    assert result.llm_calls == 2

    repair = provider.requests[1].messages
    assert repair[-2].role == "assistant"
    assert repair[-2].content == '{"wrong": 1}'
    assert "sql" in repair[-1].content, "the repair prompt must name the missing field"


async def test_a_second_invalid_reply_moves_to_the_next_strategy() -> None:
    """tool_call fails twice, so prompt_json is tried on the same model."""
    provider = FakeLLMProvider(["nope", "still nope", GOOD])
    result = await ask(make_client(provider))

    assert result.strategy == "prompt_json"
    assert [r.structured.strategy for r in provider.requests] == [
        "tool_call", "tool_call", "prompt_json"
    ]


async def test_prompt_json_puts_the_schema_in_the_prompt() -> None:
    provider = FakeLLMProvider([BadRequest("no tools"), GOOD])
    await ask(make_client(provider))

    prompt_json = provider.requests[1]
    assert prompt_json.structured.strategy == "prompt_json"
    assert "JSON Schema" in prompt_json.messages[0].content


# ── Strategy fallback ────────────────────────────────────────────────────────
async def test_a_rejected_request_tries_the_next_strategy_on_the_same_model() -> None:
    """Observed in practice: HTTP 400 for one mode while another works."""
    provider = FakeLLMProvider([BadRequest("json_schema unsupported"), GOOD])
    result = await ask(make_client(provider))

    assert result.strategy == "prompt_json"
    assert {r.model for r in provider.requests} == {"m"}


# ── Retry ────────────────────────────────────────────────────────────────────
async def test_rate_limit_is_retried_on_the_same_model() -> None:
    provider = FakeLLMProvider([RateLimited("429"), GOOD])
    result = await ask(make_client(provider))

    assert result.value.sql == "SELECT 1"
    assert len(provider.requests) == 2


async def test_retry_honours_the_servers_retry_after() -> None:
    waits: list[float] = []

    async def record_sleep(seconds: float) -> None:
        waits.append(seconds)

    provider = FakeLLMProvider([RateLimited("429", retry_after=7.0), GOOD])
    client = LLMClient({"fake": provider}, [ChainEntry(provider="fake", model="m")],
                       sleep=record_sleep)
    await ask(client)

    assert waits == [7.0]


async def test_a_wait_that_is_too_long_moves_on_instead() -> None:
    """A server asking for 60s is not worth waiting for while a user watches."""
    slow = FakeLLMProvider([RateLimited("429", retry_after=60.0)], name="slow")
    backup = FakeLLMProvider([GOOD], name="backup")
    result = await ask(make_client(slow, backup))

    assert result.provider == "backup"
    assert len(slow.requests) == 1


async def test_exhausted_retries_fall_through_to_the_next_model() -> None:
    flaky = FakeLLMProvider([ProviderTimeout("t")] * 3, name="flaky")
    backup = FakeLLMProvider([GOOD], name="backup")
    result = await ask(make_client(flaky, backup, max_retries=2))

    assert result.provider == "backup"
    assert len(flaky.requests) == 3  # one attempt plus two retries


# ── Model fallback ───────────────────────────────────────────────────────────
async def test_a_removed_model_falls_through_without_retrying() -> None:
    """Free models are withdrawn without notice; retrying a 404 is pointless."""
    gone = FakeLLMProvider([ModelUnavailable("404")], name="gone")
    backup = FakeLLMProvider([GOOD], name="backup")
    result = await ask(make_client(gone, backup))

    assert result.provider == "backup"
    assert len(gone.requests) == 1


async def test_a_provider_without_credentials_is_skipped() -> None:
    backup = FakeLLMProvider([GOOD], name="backup")
    chain = [ChainEntry(provider="nokey", model="m"), ChainEntry(provider="backup", model="m")]
    client = LLMClient({"backup": backup}, chain, sleep=no_sleep)

    assert (await ask(client)).provider == "backup"


async def test_when_everything_fails_the_reasons_are_reported_in_order() -> None:
    a = FakeLLMProvider([ModelUnavailable("gone")], name="a")
    b = FakeLLMProvider([RateLimited("429", retry_after=99)], name="b")

    with pytest.raises(LLMUnavailable) as exc_info:
        await ask(make_client(a, b))

    failures = exc_info.value.failures
    assert failures[0].startswith("a:m") and "gone" in failures[0]
    assert failures[1].startswith("b:m") and "429" in failures[1]


async def test_an_agent_specific_model_is_tried_first() -> None:
    default = FakeLLMProvider([GOOD], name="default")
    special = FakeLLMProvider([GOOD], name="special")
    client = LLMClient(
        {"default": default, "special": special},
        [ChainEntry(provider="default", model="m")],
        agent_models={"query": ChainEntry(provider="special", model="big")},
        sleep=no_sleep,
    )

    assert (await ask(client)).provider == "special"
    assert default.requests == []


# ── Budget ───────────────────────────────────────────────────────────────────
async def test_budget_is_enforced_before_calling() -> None:
    provider = FakeLLMProvider([GOOD, GOOD])
    client = make_client(provider)
    budget = CallBudget(max_calls=1, max_tokens=10_000)

    await ask(client, budget=budget)
    with pytest.raises(BudgetExceeded):
        await ask(client, budget=budget)

    assert len(provider.requests) == 1, "the over-budget call must never reach the provider"


async def test_budget_counts_repairs_as_calls() -> None:
    provider = FakeLLMProvider(["bad", GOOD])
    budget = CallBudget(max_calls=5, max_tokens=10_000)
    await ask(make_client(provider), budget=budget)

    assert budget.calls == 2
    assert budget.total_tokens == 240


# ── Cache ────────────────────────────────────────────────────────────────────
async def test_a_repeated_request_is_served_from_cache(tmp_path) -> None:
    provider = FakeLLMProvider([GOOD])
    client = make_client(provider, cache=ResponseCache(tmp_path))

    first = await ask(client)
    second = await ask(client)

    assert len(provider.requests) == 1
    assert not first.cached and second.cached
    assert second.llm_calls == 0, "a cache hit must not count as a provider call"
    assert second.value.sql == "SELECT 1"


async def test_invalid_replies_are_never_cached(tmp_path) -> None:
    """Otherwise a malformed reply would be replayed forever."""
    provider = FakeLLMProvider(["garbage", GOOD])  # invalid, then a valid repair
    client = make_client(provider, cache=ResponseCache(tmp_path))

    await ask(client)

    stored = list(tmp_path.glob("*.json"))
    assert len(stored) == 1, "two replies were received but only the valid one is stored"
    assert "SELECT 1" in stored[0].read_text()
    assert "garbage" not in stored[0].read_text()


async def test_a_disabled_cache_always_calls_the_provider(tmp_path) -> None:
    provider = FakeLLMProvider([GOOD, GOOD])
    client = make_client(provider, cache=ResponseCache(tmp_path, enabled=False))

    await ask(client)
    await ask(client)
    assert len(provider.requests) == 2


async def test_changing_the_prompt_misses_the_cache(tmp_path) -> None:
    provider = FakeLLMProvider([GOOD, GOOD])
    client = make_client(provider, cache=ResponseCache(tmp_path))

    await ask(client)
    await client.generate_structured(
        agent="query", messages=[ChatMessage(role="user", content="different")], schema=Answer
    )
    assert len(provider.requests) == 2


# ── Telemetry ────────────────────────────────────────────────────────────────
async def test_every_attempt_is_recorded() -> None:
    recorder = ListRecorder()
    provider = FakeLLMProvider([RateLimited("429"), '{"bad": 1}', GOOD])
    await ask(make_client(provider, recorder=recorder))

    outcomes = [(r.success, r.structured_output_valid, r.error_type) for r in recorder.records]
    assert outcomes == [
        (False, None, "RateLimited"),   # the 429
        (True, False, "OutputInvalid"),  # replied, but wrong shape
        (True, True, None),              # the repair worked
    ]


async def test_records_carry_model_agent_and_usage() -> None:
    recorder = ListRecorder()
    await ask(make_client(FakeLLMProvider([GOOD]), recorder=recorder))

    record = recorder.records[0]
    assert (record.agent, record.model, record.provider) == ("query", "m", "fake")
    assert record.usage.total == 120
    assert record.fallback_index == 0


async def test_fallback_index_shows_which_model_answered() -> None:
    recorder = ListRecorder()
    a = FakeLLMProvider([ModelUnavailable("gone")], name="a")
    b = FakeLLMProvider([GOOD], name="b")
    await ask(make_client(a, b, recorder=recorder))

    assert [(r.provider, r.fallback_index) for r in recorder.records] == [("a", 0), ("b", 1)]


async def test_missing_usage_is_estimated_and_flagged() -> None:
    from app.llm.types import LLMResponse, Usage

    reply = LLMResponse(content=GOOD, usage=Usage(), model="m", provider="fake")
    result = await ask(make_client(FakeLLMProvider([reply])))

    assert result.usage.estimated
    assert result.usage.total > 0


async def test_an_empty_chain_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="LLM_FALLBACK_CHAIN"):
        LLMClient({}, [])


# ── Cooldown ─────────────────────────────────────────────────────────────────
class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_a_dead_model_is_not_retried_on_every_analysis() -> None:
    """Otherwise each run pays a wasted request and its latency for a model that is gone."""
    gone = FakeLLMProvider([ModelUnavailable("gone")], name="gone")
    backup = FakeLLMProvider([GOOD, GOOD], name="backup")
    client = make_client(gone, backup, clock=Clock())

    await ask(client)
    await ask(client)

    assert len(gone.requests) == 1, "the second analysis must skip the dead model"
    assert len(backup.requests) == 2


async def test_a_cooled_down_model_is_tried_again_after_the_wait() -> None:
    clock = Clock()
    flaky = FakeLLMProvider([ModelUnavailable("gone"), GOOD], name="flaky")
    backup = FakeLLMProvider([GOOD], name="backup")
    client = make_client(flaky, backup, clock=clock)

    await ask(client)
    clock.now += 15 * 60 + 1
    result = await ask(client)

    assert result.provider == "flaky"
    assert len(flaky.requests) == 2


async def test_if_every_model_is_cooling_they_are_tried_anyway() -> None:
    """Failing outright is worse than one more attempt at something that may have recovered."""
    only = FakeLLMProvider([ModelUnavailable("gone"), GOOD], name="only")
    client = make_client(only, clock=Clock())

    with pytest.raises(LLMUnavailable):
        await ask(client)
    assert (await ask(client)).provider == "only"


async def test_cooldown_reasons_appear_in_the_failure_report() -> None:
    gone = FakeLLMProvider([ModelUnavailable("gone")], name="gone")
    also = FakeLLMProvider([ModelUnavailable("also gone"), ModelUnavailable("x")], name="also")
    client = make_client(gone, also, clock=Clock())

    with pytest.raises(LLMUnavailable):
        await ask(client)
