"""The transport interface. One method, deliberately.

A provider's only job is to send a request and return what came back. Retries,
fallback, structured-output parsing, caching and telemetry all live in
`LLMClient`, so each provider stays small and a new one is cheap to add.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.llm.types import LLMRequest, LLMResponse


class LLMProvider(ABC):
    name: str

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Send one request.

        Raises a subclass of `app.llm.errors.ProviderError` for any failure, so
        the client never has to know a vendor's exception types.
        """
