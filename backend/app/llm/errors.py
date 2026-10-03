"""LLM failures, classified by what the caller should do about them.

The classes exist to drive one decision: retry the same model, try a different
strategy, move to the next model, or give up. Collapsing them into a single
exception would make the client guess.
"""

from __future__ import annotations


class LLMError(Exception):
    """Base class for everything in this package."""


class ProviderError(LLMError):
    """A provider call failed."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


# Transient: the same request may well succeed shortly. Retry, then move on.
class RateLimited(ProviderError):
    pass


class ProviderTimeout(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    """5xx, dropped connection, or an empty reply."""


# Permanent for this model: retrying cannot help. Move to the next one.
class ModelUnavailable(ProviderError):
    """The model id no longer exists. Free models are withdrawn without notice."""


class ProviderAuthError(ProviderError):
    """Missing, invalid or unauthorised API key."""


# Permanent for this *strategy*: a different one may work on the same model.
class BadRequest(ProviderError):
    """The provider rejected the request, typically an unsupported feature.

    Observed: one free model returns HTTP 400 for JSON-schema mode while
    accepting tool calls on the same endpoint.
    """


TRANSIENT = (RateLimited, ProviderTimeout, ProviderUnavailable)


class OutputInvalid(LLMError):
    """The model replied, but not with something matching the schema."""

    def __init__(self, message: str, *, raw: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.raw = raw


class LLMUnavailable(LLMError):
    """Every model in the chain failed. `failures` says why, in order."""

    def __init__(self, failures: list[str]) -> None:
        self.failures = failures
        super().__init__("All models failed: " + "; ".join(failures))
