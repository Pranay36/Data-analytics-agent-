"""Structured JSON logging with an ambient `analysis_id`.

Every log line emitted while an analysis is running carries that run's id, so a
single run can be traced across nodes without threading a logger through every
call. The id is stored in a `ContextVar`, which is both task-local and
inherited by `asyncio` child tasks.
"""

from __future__ import annotations

import json
import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_analysis_id: ContextVar[str | None] = ContextVar("analysis_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)

# Attributes present on every LogRecord; anything else was passed via `extra=`
# and is worth emitting as a structured field.
_STANDARD_ATTRS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "module", "msecs",
        "message", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    }
)

_REDACT_KEYS = ("password", "secret", "token", "api_key", "apikey", "authorization")


def set_analysis_id(analysis_id: str | None) -> None:
    _analysis_id.set(analysis_id)


def get_analysis_id() -> str | None:
    return _analysis_id.get()


def get_user_id() -> str | None:
    return _user_id.get()


@contextmanager
def analysis_context(analysis_id: str, user_id: str | None = None):
    """Bind `analysis_id` (and who asked) to every log line and LLM record in this block."""
    token = _analysis_id.set(analysis_id)
    user_token = _user_id.set(user_id)
    try:
        yield
    finally:
        _analysis_id.reset(token)
        _user_id.reset(user_token)


def _redact(key: str, value: Any) -> Any:
    return "***" if any(marker in key.lower() for marker in _REDACT_KEYS) else value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if (analysis_id := _analysis_id.get()) is not None:
            payload["analysis_id"] = analysis_id
        if (user_id := _user_id.get()) is not None:
            payload["user_id"] = user_id

        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = _redact(key, value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if json_output
        else logging.Formatter("%(levelname)-8s %(name)s: %(message)s")
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn ships its own handlers; let ours format everything instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
