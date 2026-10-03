"""Getting validated Python objects out of free-tier models.

Free models are inconsistent in ways that matter here: some wrap JSON in
markdown fences, some prefix it with `<think>` reasoning, some answer in prose
when asked for a tool call. So the parser is tolerant about *packaging* but
strict about *content* — the object must still validate against the schema.
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.llm.errors import OutputInvalid
from app.llm.types import ChatMessage

T = TypeVar("T", bound=BaseModel)

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


# ── Schema preparation ───────────────────────────────────────────────────────
def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            target = defs[node["$ref"].rsplit("/", 1)[-1]]
            overrides = {k: v for k, v in node.items() if k != "$ref"}
            return {**_inline_refs(target, defs), **overrides}
        return {k: _inline_refs(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    return node


def build_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema for `model`, with references inlined.

    Pydantic emits `$ref`/`$defs` for nested models. Several providers reject or
    mis-handle those, so everything is expanded into one self-contained schema.
    """
    raw = model.model_json_schema()
    return _inline_refs(raw, raw.get("$defs", {}))


# ── Parsing ──────────────────────────────────────────────────────────────────
def extract_json(text: str) -> Any:
    """Find the JSON value in a model's reply, however it was packaged.

    Raises:
        ValueError: nothing parseable was found.
    """
    cleaned = _THINK_BLOCK.sub("", text).strip()

    candidates = [cleaned]
    candidates += [match.strip() for match in _FENCE.findall(cleaned)]

    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        # Prose around the object: scan for a `{` that starts a valid value.
        # raw_decode copes with braces inside strings, which a bracket counter
        # would not.
        for start in (i for i, ch in enumerate(candidate) if ch == "{"):
            try:
                value, _ = decoder.raw_decode(candidate[start:])
                return value
            except json.JSONDecodeError:
                continue

    raise ValueError("no JSON object found in the reply")


def format_validation_error(exc: ValidationError) -> str:
    """One line per problem, naming the field — written to be fed back to a model."""
    parts = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


def parse_structured[T: BaseModel](raw: str | None, model: type[T]) -> T:
    """Turn a model's reply into a validated `model` instance.

    Raises:
        OutputInvalid: the reply was empty, not JSON, or did not match the schema.
            The message names the problem so it can be used as repair feedback.
    """
    if not raw or not raw.strip():
        raise OutputInvalid("The reply was empty.", raw=raw)

    try:
        data = extract_json(raw)
    except ValueError as exc:
        raise OutputInvalid(f"The reply was not valid JSON ({exc}).", raw=raw) from exc

    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise OutputInvalid(format_validation_error(exc), raw=raw) from exc


# ── Prompting ────────────────────────────────────────────────────────────────
def repair_messages(
    messages: list[ChatMessage], bad_reply: str | None, problem: str
) -> list[ChatMessage]:
    """Extend a conversation with the failed reply and what was wrong with it.

    Naming the specific field is what makes the retry likely to succeed; a bare
    "try again" tends to reproduce the same mistake.
    """
    return [
        *messages,
        ChatMessage(role="assistant", content=(bad_reply or "")[:4000]),
        ChatMessage(
            role="user",
            content=(
                f"That reply could not be used: {problem}\n"
                "Reply again with ONLY the corrected JSON object — no commentary, "
                "no markdown fences."
            ),
        ),
    ]


def with_schema_instructions(
    messages: list[ChatMessage], schema: dict[str, Any]
) -> list[ChatMessage]:
    """For `prompt_json`: put the schema in the prompt, since the API cannot carry it."""
    instruction = (
        "Respond with ONLY a single JSON object that matches this JSON Schema. "
        "No prose, no markdown fences.\n" + json.dumps(schema, separators=(",", ":"))
    )
    out = [m.model_copy() for m in messages]
    for message in out:
        if message.role == "system":
            message.content = f"{message.content}\n\n{instruction}"
            return out
    return [ChatMessage(role="system", content=instruction), *out]
