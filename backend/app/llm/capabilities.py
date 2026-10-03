"""Look up which structured-output strategies a model supports."""

from __future__ import annotations

from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path
from typing import cast

import yaml

from app.llm.types import ChainEntry, Strategy

_VALID: tuple[Strategy, ...] = ("tool_call", "json_schema", "prompt_json")
_FILE = Path(__file__).with_name("models.yaml")


@lru_cache
def _load() -> tuple[list[Strategy], dict[str, list[Strategy]]]:
    data = yaml.safe_load(_FILE.read_text(encoding="utf-8")) or {}

    def clean(strategies: list[str]) -> list[Strategy]:
        unknown = [s for s in strategies if s not in _VALID]
        if unknown:
            raise ValueError(f"models.yaml: unknown strategy {unknown}; valid: {_VALID}")
        return cast("list[Strategy]", list(strategies))

    return (
        clean(data.get("default", ["tool_call", "prompt_json"])),
        {pattern: clean(s) for pattern, s in (data.get("models") or {}).items()},
    )


def strategies_for(entry: ChainEntry) -> list[Strategy]:
    default, models = _load()
    for pattern, strategies in models.items():
        if fnmatchcase(entry.key, pattern):
            return list(strategies)
    return list(default)
