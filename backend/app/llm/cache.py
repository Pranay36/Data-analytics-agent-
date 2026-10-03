"""On-disk response cache, so iterating on code downstream of the LLM is free.

Most development work — the profiler, the drill-down guard, the dashboard
validator, the whole frontend — happens *after* the model has answered. Without a
cache, every re-run spends free-tier quota to re-ask the same question. With one,
only a change to the prompt or the schema (which changes the key) costs a call.

Only replies that parsed and validated are stored, so a malformed reply can never
be replayed as though it were good.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from app.llm.types import LLMRequest, LLMResponse

logger = logging.getLogger(__name__)


def cache_key(provider: str, request: LLMRequest) -> str:
    payload = json.dumps(
        {"provider": provider, "request": request.model_dump(exclude={"timeout_seconds"})},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class ResponseCache:
    def __init__(self, directory: Path, *, enabled: bool = True) -> None:
        self.directory = directory
        self.enabled = enabled

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> LLMResponse | None:
        if not self.enabled:
            return None
        try:
            return LLMResponse.model_validate_json(self._path(key).read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.warning("unreadable cache entry", extra={"key": key, "error": str(exc)})
            return None

    def put(self, key: str, response: LLMResponse) -> None:
        if not self.enabled:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._path(key).write_text(response.model_dump_json())
        except OSError as exc:  # a cache failure must never fail a request
            logger.warning("could not write cache entry", extra={"error": str(exc)})
