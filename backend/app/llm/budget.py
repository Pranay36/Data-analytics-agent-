"""Per-analysis spending limits, enforced in code rather than requested in a prompt."""

from __future__ import annotations

from dataclasses import dataclass

from app.llm.types import Usage


class BudgetExceeded(RuntimeError):
    def __init__(self, limit: str) -> None:
        super().__init__(f"LLM budget exhausted: {limit}")
        self.limit = limit


@dataclass
class CallBudget:
    max_calls: int
    max_tokens: int
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def remaining_calls(self) -> int:
        return max(self.max_calls - self.calls, 0)

    def check(self) -> None:
        if self.calls >= self.max_calls:
            raise BudgetExceeded("calls")
        if self.total_tokens >= self.max_tokens:
            raise BudgetExceeded("tokens")

    def record(self, usage: Usage) -> None:
        self.calls += 1
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
