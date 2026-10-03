"""Provider-neutral request and response shapes.

Agents and the client speak only these types. A provider translates them to its
own wire format, which is what lets a new vendor be added without touching any
agent (PROJECT_PLAN §17.1).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Strategy = Literal["tool_call", "json_schema", "prompt_json"]
"""How structured output is requested. In the order we prefer them: probing five
free models showed tool-call mode working on all of them, JSON-schema mode on
three, so tool-call is tried first."""


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class Usage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated: bool = False
    """True when the provider reported no usage and we guessed from text length."""

    @property
    def total(self) -> int:
        if self.total_tokens is not None:
            return self.total_tokens
        return (self.input_tokens or 0) + (self.output_tokens or 0)


class StructuredSpec(BaseModel):
    """What the provider should do to get machine-readable output."""

    strategy: Strategy
    name: str
    description: str = ""
    json_schema: dict[str, Any] = Field(default_factory=dict)


class LLMRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float = 0.0
    max_tokens: int = 2000
    timeout_seconds: float = 60.0
    structured: StructuredSpec | None = None


class LLMResponse(BaseModel):
    content: str | None
    """The model's payload: tool-call arguments for `tool_call`, message text
    otherwise. The client parses this; providers never interpret it."""
    usage: Usage = Field(default_factory=Usage)
    model: str
    provider: str
    finish_reason: str | None = None
    latency_ms: int = 0


class ChainEntry(BaseModel):
    """One `provider:model` step in the fallback chain."""

    provider: str
    model: str

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"

    @classmethod
    def parse(cls, value: str) -> ChainEntry:
        # Split on the first colon only: model ids such as
        # `nvidia/nemotron-3-super-120b-a12b:free` contain colons themselves.
        provider, sep, model = value.partition(":")
        if not sep or not provider or not model:
            raise ValueError(f"Expected 'provider:model', got {value!r}")
        return cls(provider=provider.strip(), model=model.strip())
