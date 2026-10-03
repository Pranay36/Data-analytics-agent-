"""Track rate-limit rejections per provider.

Free tiers fail in a way that is easy to misread. A 429 surfaces as "a request
failed", the client quietly falls through to the next model, and the real cause —
that a daily allowance is gone and will not return for hours — only becomes
obvious once everything is failing.

So rejections are counted, and when a provider tells us when it resets, that is
remembered. The counts are process-local and reset on restart, which is the right
scope: this answers "what is happening right now", not "what did we spend last
month".
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ProviderLimitState:
    provider: str
    hits: int = 0
    """How many requests this provider has rejected as rate-limited."""
    last_hit_at: float | None = None
    resets_at: float | None = None
    """Best known reset time, from a Retry-After or an X-RateLimit-Reset header."""
    last_message: str = ""

    @property
    def seconds_until_reset(self) -> int | None:
        if self.resets_at is None:
            return None
        return max(0, int(self.resets_at - time.time()))

    @property
    def is_limited(self) -> bool:
        """Still within a window the provider told us to wait out."""
        return (self.seconds_until_reset or 0) > 0

    def describe(self) -> str:
        if not self.hits:
            return "ok"
        if remaining := self.seconds_until_reset:
            # Round minutes up: a two-hour wait reported as "1h 59m" reads as a
            # bug rather than as rounding.
            hours, minutes = divmod(-(-remaining // 60), 60)
            when = f"{hours}h" if hours and not minutes else (
                f"{hours}h {minutes}m" if hours else f"{minutes}m"
            )
            return f"rate limited ({self.hits}x), resets in {when}"
        return f"rate limited {self.hits}x, may have recovered"


class RateLimitTracker:
    def __init__(self) -> None:
        self._states: dict[str, ProviderLimitState] = {}
        self._lock = threading.Lock()

    def record(
        self,
        provider: str,
        *,
        retry_after: float | None = None,
        reset_at: float | None = None,
        message: str = "",
    ) -> ProviderLimitState:
        """Note that `provider` refused a request as rate-limited.

        Args:
            retry_after: seconds to wait, from a Retry-After header.
            reset_at: absolute unix time, from an X-RateLimit-Reset header.
        """
        with self._lock:
            state = self._states.setdefault(provider, ProviderLimitState(provider=provider))
            state.hits += 1
            state.last_hit_at = time.time()
            state.last_message = message[:200]

            candidate = reset_at
            if candidate is None and retry_after is not None:
                candidate = time.time() + retry_after
            # Keep the furthest known reset: a daily allowance matters more than
            # a per-minute one that has already elapsed.
            if candidate is not None and (state.resets_at is None or candidate > state.resets_at):
                state.resets_at = candidate

        logger.warning(
            "provider rate limited",
            extra={
                "provider": provider,
                "hits": state.hits,
                "resets_in_seconds": state.seconds_until_reset,
                "detail": state.last_message,
            },
        )
        return state

    def state(self, provider: str) -> ProviderLimitState | None:
        return self._states.get(provider)

    def snapshot(self) -> dict[str, str]:
        """Human-readable status per provider, for the readiness endpoint."""
        with self._lock:
            return {name: state.describe() for name, state in self._states.items()}

    def reset(self) -> None:
        with self._lock:
            self._states.clear()


_tracker = RateLimitTracker()


def get_rate_limit_tracker() -> RateLimitTracker:
    return _tracker


def parse_reset_header(value: str | None) -> float | None:
    """Read an X-RateLimit-Reset header, which may be seconds or milliseconds.

    Providers disagree: OpenRouter sends unix milliseconds, others send seconds
    from now. Telling them apart by magnitude is crude but reliable — a value
    beyond the year 2100 in seconds is certainly milliseconds.
    """
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        return None

    if number > 4_102_444_800:  # year 2100 in seconds
        return number / 1000
    if number > time.time() - 86_400:  # plausibly already an absolute time
        return number
    return time.time() + number  # a relative duration
