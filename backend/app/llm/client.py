"""The single chokepoint for every LLM call in the system.

Agents ask this class for a validated object and never see a provider, a model
id, a retry or a fallback. Everything that makes free-tier models usable lives
here, so it exists once rather than being re-implemented per agent:

    for each model in the fallback chain:
        for each structured-output strategy the model supports:
            call it, retrying transient failures
            parse and validate the reply
            if invalid: send the specific error back once, then re-validate

What happens on each failure is a deliberate choice:

    rate limited / timeout / 5xx   retry the same model, then move to the next
    model not found / bad key      move to the next model immediately
    request rejected (HTTP 400)    try the next *strategy* on the same model
    reply does not match schema    one repair attempt, then next strategy
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.logging import get_analysis_id, get_user_id
from app.llm.base import LLMProvider
from app.llm.budget import BudgetExceeded, CallBudget
from app.llm.cache import ResponseCache, cache_key
from app.llm.capabilities import strategies_for
from app.llm.errors import (
    TRANSIENT,
    BadRequest,
    LLMUnavailable,
    ModelUnavailable,
    OutputInvalid,
    ProviderAuthError,
    ProviderError,
    RateLimited,
)
from app.llm.registry import build_providers
from app.llm.structured import (
    build_schema,
    parse_structured,
    repair_messages,
    with_schema_instructions,
)
from app.llm.types import (
    ChainEntry,
    ChatMessage,
    LLMRequest,
    LLMResponse,
    Strategy,
    StructuredSpec,
    Usage,
)
from app.observability.recorder import CallRecord, DbLlmCallRecorder, LlmCallRecorder

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# A server asking us to wait longer than this is not worth waiting for on a request
# someone is watching, so the next model in the chain is tried instead. Set by
# `llm_max_retry_wait_seconds`; this is only the default.
#
# It was 15s and that was too short. Groq's per-minute token limit asks for waits of
# around 20s, and giving up on it sent the run to free models that returned invalid
# structured output six times over 100 seconds. Waiting for the good model is faster.
MAX_RETRY_WAIT_SECONDS = 45.0
DEFAULT_BACKOFF_SECONDS = 2.0

# After a model fails for a reason retrying cannot fix, skip it for a while rather
# than spending a request on it for every analysis. Free models are withdrawn
# without notice, and a dead first model would otherwise cost one wasted call (and
# its latency) on every single run.
COOLDOWN_GONE_SECONDS = 15 * 60  # model withdrawn, or credentials rejected
COOLDOWN_BUSY_SECONDS = 60  # retries exhausted on a rate limit, timeout or 5xx
COOLDOWN_INVALID_SECONDS = 10 * 60  # repeatedly returned unusable structured output


class StructuredResult[T: BaseModel](BaseModel):
    """A validated object plus how it was obtained."""

    value: T
    provider: str
    model: str
    strategy: str
    usage: Usage
    llm_calls: int
    """Provider calls made for this result, including repairs. Cache hits excluded."""
    cached: bool = False

    model_config = {"arbitrary_types_allowed": True}


class LLMClient:
    def __init__(
        self,
        providers: dict[str, LLMProvider],
        chain: list[ChainEntry],
        *,
        agent_models: dict[str, ChainEntry] | None = None,
        cache: ResponseCache | None = None,
        recorder: LlmCallRecorder | None = None,
        max_retries: int = 2,
        max_concurrency: int = 2,
        timeout_seconds: float = 60.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        max_retry_wait_seconds: float = MAX_RETRY_WAIT_SECONDS,
        max_strategies_per_model: int = 2,
        invalid_strikes: int = 3,
    ) -> None:
        if not chain:
            raise ValueError("The LLM fallback chain is empty. Set LLM_FALLBACK_CHAIN.")
        self._providers = providers
        self._chain = chain
        self._agent_models = agent_models or {}
        self._cache = cache or ResponseCache(directory=None, enabled=False)  # type: ignore[arg-type]
        self._recorder = recorder
        self._max_retries = max_retries
        self._timeout = timeout_seconds
        self._sleep = sleep
        self._max_retry_wait = max_retry_wait_seconds
        self._clock = clock
        self._max_strategies = max(1, max_strategies_per_model)
        self._invalid_strikes = max(1, invalid_strikes)
        self._strikes: dict[str, int] = {}
        self._cooling_until: dict[str, float] = {}
        # Free tiers have low per-minute limits; cap in-flight calls.
        self._semaphore = asyncio.Semaphore(max_concurrency)

    @classmethod
    def from_settings(
        cls, settings: Settings | None = None, *, recorder: LlmCallRecorder | None = None
    ) -> LLMClient:
        from app.core.model_registry import get_registry

        settings = settings or get_settings()
        registry = get_registry()

        chain = [ChainEntry(provider=ref.provider, model=ref.model) for ref in registry.chain]
        agent_models = {
            agent: ChainEntry(provider=ref.provider, model=ref.model)
            for agent, ref in registry.agent_overrides.items()
        }

        cache_on = settings.llm_cache_enabled and settings.llm_cache_mode != "off"
        return cls(
            build_providers(settings, [*chain, *agent_models.values()]),
            chain,
            agent_models=agent_models,
            cache=ResponseCache(settings.llm_cache_dir, enabled=cache_on),
            recorder=recorder or DbLlmCallRecorder(),
            max_retries=settings.llm_max_retries,
            max_concurrency=settings.llm_max_concurrency,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retry_wait_seconds=settings.llm_max_retry_wait_seconds,
            max_strategies_per_model=settings.llm_max_strategies_per_model,
            invalid_strikes=settings.llm_invalid_strikes,
        )

    # ── Public API ───────────────────────────────────────────────────────────
    def chain_for(self, agent: str) -> list[ChainEntry]:
        """The fallback chain, with an agent-specific model promoted to the front."""
        preferred = self._agent_models.get(agent)
        if preferred is None:
            return self._chain
        return [preferred, *(e for e in self._chain if e.key != preferred.key)]

    async def generate_structured(
        self,
        *,
        agent: str,
        messages: list[ChatMessage],
        schema: type[T],
        schema_name: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2000,
        budget: CallBudget | None = None,
    ) -> StructuredResult[T]:
        """Get a validated `schema` instance from the first model that can produce one.

        Raises:
            BudgetExceeded: the per-analysis call or token budget is spent.
            LLMUnavailable: every model in the chain failed; `.failures` says why.
        """
        json_schema = build_schema(schema)
        name = schema_name or schema.__name__
        failures: list[str] = []

        chain = self.chain_for(agent)
        # If every model is cooling down, try them anyway: failing outright when a
        # model might have recovered is worse than one more attempt.
        skip_cooling = any(not self._is_cooling(entry) for entry in chain)

        for index, entry in enumerate(chain):
            provider = self._providers.get(entry.provider)
            if provider is None:
                failures.append(f"{entry.key}: no credentials configured")
                continue
            if skip_cooling and self._is_cooling(entry):
                remaining = int(self._cooling_until[entry.key] - self._clock())
                failures.append(f"{entry.key}: skipped, unavailable for another {remaining}s")
                continue

            invalid_here = False
            for strategy in strategies_for(entry)[: self._max_strategies]:
                context = _Attempt(
                    agent=agent, entry=entry, index=index, provider=provider, strategy=strategy,
                    spec=StructuredSpec(
                        strategy=strategy, name=name, json_schema=json_schema,
                        description=(schema.__doc__ or "").strip().split("\n")[0],
                    ),
                    temperature=temperature, max_tokens=max_tokens, budget=budget,
                )
                try:
                    result = await self._run(context, messages, schema)
                    self._strikes.pop(entry.key, None)
                    return result
                except BudgetExceeded:
                    raise
                except (BadRequest, OutputInvalid) as exc:
                    # This strategy failed here; another may work on the same model.
                    invalid_here = invalid_here or isinstance(exc, OutputInvalid)
                    failures.append(f"{entry.key} [{strategy}]: {_message(exc)}")
                    continue
                except ProviderError as exc:
                    # Retrying cannot help, or already did: move to the next model.
                    failures.append(f"{entry.key}: {_message(exc)}")
                    self._start_cooldown(entry, exc)
                    break
            else:
                if invalid_here:
                    self._record_strike(entry)

        raise LLMUnavailable(failures)

    # ── Internals ────────────────────────────────────────────────────────────
    def _is_cooling(self, entry: ChainEntry) -> bool:
        return self._cooling_until.get(entry.key, 0.0) > self._clock()

    def _record_strike(self, entry: ChainEntry) -> None:
        """A model that cannot produce valid output is skipped after a few requests.

        Without this a run pays for the same bad model at every step.
        """
        strikes = self._strikes.get(entry.key, 0) + 1
        self._strikes[entry.key] = strikes
        if strikes >= self._invalid_strikes:
            self._strikes.pop(entry.key)
            self._cooling_until[entry.key] = self._clock() + COOLDOWN_INVALID_SECONDS

    def _start_cooldown(self, entry: ChainEntry, exc: ProviderError) -> None:
        gone = isinstance(exc, ModelUnavailable | ProviderAuthError)
        seconds = COOLDOWN_GONE_SECONDS if gone else COOLDOWN_BUSY_SECONDS
        if isinstance(exc, RateLimited) and exc.retry_after is not None:
            # The provider said when it will accept requests again. Use that exactly:
            # a flat minute blocked the best model for three times as long as needed.
            seconds = exc.retry_after + 1
        self._cooling_until[entry.key] = self._clock() + seconds

    async def _run(
        self, ctx: _Attempt, messages: list[ChatMessage], schema: type[T]
    ) -> StructuredResult[T]:
        prepared = (
            with_schema_instructions(messages, ctx.spec.json_schema)
            if ctx.strategy == "prompt_json"
            else messages
        )

        calls = 0
        usage = Usage(input_tokens=0, output_tokens=0, total_tokens=0)
        last_problem = ""
        current = prepared

        for attempt in (1, 2):  # the original request, then one repair
            response, key, was_cached = await self._call(ctx, current, attempt)
            calls += 0 if was_cached else 1
            usage = _add(usage, response.usage)

            try:
                value = parse_structured(response.content, schema)
            except OutputInvalid as exc:
                last_problem = exc.message
                await self._record(ctx, response, attempt, success=True, valid=False,
                                   error=exc.message, cached=was_cached)
                logger.info("invalid structured output",
                            extra={"model": ctx.entry.key, "strategy": ctx.strategy,
                                   "problem": exc.message})
                current = repair_messages(prepared, response.content, exc.message)
                continue

            self._cache.put(key, response)  # only validated replies are cached
            await self._record(ctx, response, attempt, success=True, valid=True,
                               cached=was_cached)
            return StructuredResult(
                value=value, provider=ctx.entry.provider, model=response.model,
                strategy=ctx.strategy, usage=usage, llm_calls=calls, cached=was_cached,
            )

        raise OutputInvalid(f"still invalid after a repair attempt: {last_problem}")

    async def _call(
        self, ctx: _Attempt, messages: list[ChatMessage], attempt: int
    ) -> tuple[LLMResponse, str, bool]:
        """One provider call with caching, budget, concurrency and transient retries."""
        request = LLMRequest(
            model=ctx.entry.model, messages=messages, temperature=ctx.temperature,
            max_tokens=ctx.max_tokens, timeout_seconds=self._timeout, structured=ctx.spec,
        )
        key = cache_key(ctx.entry.provider, request)

        if (hit := self._cache.get(key)) is not None:
            return hit, key, True

        for retry in range(self._max_retries + 1):
            if ctx.budget is not None:
                ctx.budget.check()
            try:
                async with self._semaphore:
                    response = await ctx.provider.complete(request)
            except ProviderError as exc:
                await self._record_failure(ctx, exc, attempt)
                if not isinstance(exc, TRANSIENT) or retry == self._max_retries:
                    raise
                wait = _wait_seconds(exc, retry)
                if wait > self._max_retry_wait:
                    raise
                logger.info("retrying", extra={"model": ctx.entry.key, "wait": wait,
                                               "reason": type(exc).__name__})
                await self._sleep(wait)
                continue

            if response.usage.total_tokens is None and response.usage.input_tokens is None:
                response.usage = _estimate(messages, response.content)
            if ctx.budget is not None:
                ctx.budget.record(response.usage)
            return response, key, False

        raise AssertionError("unreachable")  # pragma: no cover

    async def _record(
        self, ctx: _Attempt, response: LLMResponse, attempt: int, *, success: bool,
        valid: bool | None, error: str | None = None, cached: bool = False,
    ) -> None:
        if self._recorder is None:
            return
        await self._recorder.record(
            CallRecord(
                analysis_id=get_analysis_id(), user_id=get_user_id(),
                agent=ctx.agent, provider=ctx.entry.provider,
                model=response.model, strategy=ctx.strategy, attempt=attempt,
                fallback_index=ctx.index, usage=response.usage, latency_ms=response.latency_ms,
                success=success, structured_output_valid=valid, cached=cached,
                error_type=None if valid else "OutputInvalid", error_message=error,
            )
        )

    async def _record_failure(self, ctx: _Attempt, exc: ProviderError, attempt: int) -> None:
        if self._recorder is None:
            return
        await self._recorder.record(
            CallRecord(
                analysis_id=get_analysis_id(), user_id=get_user_id(),
                agent=ctx.agent, provider=ctx.entry.provider,
                model=ctx.entry.model, strategy=ctx.strategy, attempt=attempt,
                fallback_index=ctx.index, success=False,
                error_type=type(exc).__name__, error_message=exc.message,
            )
        )


class _Attempt:
    """The fixed facts of one (model, strategy) attempt, bundled to keep signatures short."""

    def __init__(
        self, *, agent: str, entry: ChainEntry, index: int, provider: LLMProvider,
        strategy: Strategy, spec: StructuredSpec, temperature: float, max_tokens: int,
        budget: CallBudget | None,
    ) -> None:
        self.agent = agent
        self.entry = entry
        self.index = index
        self.provider = provider
        self.strategy = strategy
        self.spec = spec
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.budget = budget


def _message(exc: Exception) -> str:
    return getattr(exc, "message", None) or str(exc)


def _wait_seconds(exc: ProviderError, retry: int) -> float:
    """Honour the server's Retry-After; otherwise back off exponentially."""
    if isinstance(exc, RateLimited) and exc.retry_after is not None:
        return exc.retry_after
    return DEFAULT_BACKOFF_SECONDS * (2**retry)


def _add(a: Usage, b: Usage) -> Usage:
    return Usage(
        input_tokens=(a.input_tokens or 0) + (b.input_tokens or 0),
        output_tokens=(a.output_tokens or 0) + (b.output_tokens or 0),
        total_tokens=a.total + b.total,
        estimated=a.estimated or b.estimated,
    )


def _estimate(messages: list[ChatMessage], content: str | None) -> Usage:
    """Guess usage at ~4 characters per token when a provider reports none."""
    prompt = sum(len(m.content) for m in messages) // 4
    completion = len(content or "") // 4
    return Usage(input_tokens=prompt, output_tokens=completion,
                 total_tokens=prompt + completion, estimated=True)


# Re-exported so callers import from one place.
__all__ = [
    "BudgetExceeded", "LLMClient", "LLMUnavailable", "ModelUnavailable",
    "ProviderAuthError", "StructuredResult",
]
