"""Rate-limit tracking.

A spent free-tier allowance is otherwise invisible: requests simply start
failing, the client falls through to another model, and nothing says a daily
quota is gone for hours.
"""

import time

import pytest

from app.observability.rate_limits import RateLimitTracker, parse_reset_header


@pytest.fixture
def tracker() -> RateLimitTracker:
    return RateLimitTracker()


def test_counts_rejections_per_provider(tracker) -> None:
    tracker.record("openrouter")
    tracker.record("openrouter")
    tracker.record("groq")

    assert tracker.state("openrouter").hits == 2
    assert tracker.state("groq").hits == 1
    assert tracker.state("gemini") is None


def test_retry_after_becomes_a_reset_time(tracker) -> None:
    state = tracker.record("groq", retry_after=90)
    assert 85 <= state.seconds_until_reset <= 90
    assert state.is_limited


def test_the_longest_known_wait_wins(tracker) -> None:
    """A per-minute limit must not overwrite a daily one that lasts hours."""
    tracker.record("openrouter", reset_at=time.time() + 14_400)
    state = tracker.record("openrouter", retry_after=30)

    assert state.seconds_until_reset > 14_000


def test_describe_is_readable(tracker) -> None:
    tracker.record("openrouter", retry_after=7200)
    assert "resets in 2h" in tracker.state("openrouter").describe()

    tracker.record("groq")
    assert "may have recovered" in tracker.state("groq").describe()


def test_snapshot_reports_every_provider(tracker) -> None:
    tracker.record("openrouter", retry_after=60)
    assert set(tracker.snapshot()) == {"openrouter"}


def test_a_past_reset_is_no_longer_limiting(tracker) -> None:
    state = tracker.record("groq", reset_at=time.time() - 10)
    assert not state.is_limited
    assert state.seconds_until_reset == 0


@pytest.mark.parametrize(
    ("header", "description"),
    [
        ("1791072000000", "unix milliseconds, as OpenRouter sends"),
        (str(int(time.time()) + 3600), "unix seconds"),
        ("3600", "seconds from now"),
    ],
)
def test_reset_headers_are_understood_in_every_form(header: str, description: str) -> None:
    """Providers disagree on the unit, and guessing wrong misreports by hours."""
    parsed = parse_reset_header(header)
    assert parsed is not None, description
    assert parsed > time.time() - 86_400


def test_a_malformed_reset_header_is_ignored() -> None:
    assert parse_reset_header("not-a-number") is None
    assert parse_reset_header(None) is None
