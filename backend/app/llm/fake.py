"""A scripted provider, for tests that must not touch a network or a quota.

Because every LLM call in the system goes through `LLMClient`, replacing the
provider here exercises the real retry, repair, fallback and telemetry logic
with fully deterministic model behaviour.
"""

from __future__ import annotations

from app.llm.base import LLMProvider
from app.llm.types import LLMRequest, LLMResponse, Usage


class FakeLLMProvider(LLMProvider):
    """Plays back a list of replies. Each item is a reply string, a ready-made
    `LLMResponse`, or an exception to raise."""

    def __init__(self, script: list[str | LLMResponse | Exception], name: str = "fake") -> None:
        self.name = name
        self._script = list(script)
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._script:
            raise AssertionError(
                f"FakeLLMProvider {self.name!r} was called more times than scripted "
                f"({len(self.requests)} calls)"
            )

        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, LLMResponse):
            return item
        return LLMResponse(
            content=item,
            usage=Usage(input_tokens=100, output_tokens=20, total_tokens=120),
            model=request.model,
            provider=self.name,
            finish_reason="stop",
            latency_ms=5,
        )
