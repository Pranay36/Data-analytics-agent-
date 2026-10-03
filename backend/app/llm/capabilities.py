"""Which structured-output strategies a model supports.

Thin wrapper over the model registry, kept so callers do not need to know where
the data lives.
"""

from __future__ import annotations

from typing import cast

from app.core.model_registry import ModelRef, get_registry
from app.llm.types import ChainEntry, Strategy


def strategies_for(entry: ChainEntry) -> list[Strategy]:
    ref = ModelRef(provider=entry.provider, model=entry.model)
    return cast("list[Strategy]", get_registry().strategies_for(ref))
