"""Parsing must be tolerant of packaging and strict about content."""

import pytest
from pydantic import BaseModel

from app.llm.errors import OutputInvalid
from app.llm.structured import (
    build_schema,
    extract_json,
    parse_structured,
    with_schema_instructions,
)
from app.llm.types import ChatMessage


class Inner(BaseModel):
    metric: str


class Answer(BaseModel):
    can_answer: bool
    sql: str
    inner: Inner | None = None


GOOD = '{"can_answer": true, "sql": "SELECT 1"}'


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(GOOD, id="plain"),
        pytest.param(f"```json\n{GOOD}\n```", id="fenced"),
        pytest.param(f"```\n{GOOD}\n```", id="fenced-no-language"),
        pytest.param(f"Here you go:\n{GOOD}\nHope that helps!", id="prose-around"),
        pytest.param(f"<think>The user wants a query...</think>\n{GOOD}", id="reasoning-prefix"),
        pytest.param(f"<think>use {{braces}} here</think>{GOOD}", id="braces-in-reasoning"),
    ],
)
def test_extracts_json_however_it_is_packaged(reply: str) -> None:
    assert parse_structured(reply, Answer).sql == "SELECT 1"


def test_braces_inside_strings_do_not_confuse_extraction() -> None:
    reply = 'Result: {"can_answer": true, "sql": "SELECT \'{x}\' AS s"} done'
    assert parse_structured(reply, Answer).sql == "SELECT '{x}' AS s"


def test_validates_nested_models() -> None:
    reply = '{"can_answer": true, "sql": "x", "inner": {"metric": "revenue"}}'
    assert parse_structured(reply, Answer).inner.metric == "revenue"


@pytest.mark.parametrize("reply", [None, "", "   "])
def test_empty_reply_is_invalid(reply) -> None:
    with pytest.raises(OutputInvalid, match="empty"):
        parse_structured(reply, Answer)


def test_prose_without_json_is_invalid() -> None:
    with pytest.raises(OutputInvalid, match="not valid JSON"):
        parse_structured("I cannot help with that.", Answer)


def test_validation_errors_name_the_field() -> None:
    """The field name is what lets a repair prompt fix the right thing."""
    with pytest.raises(OutputInvalid) as exc_info:
        parse_structured('{"can_answer": "maybe"}', Answer)
    message = exc_info.value.message
    assert "can_answer" in message
    assert "sql" in message


def test_extract_json_raises_when_nothing_found() -> None:
    with pytest.raises(ValueError):
        extract_json("no braces here")


def test_schema_has_no_unresolved_references() -> None:
    """Nested models become $ref in pydantic; several providers choke on those."""
    raw = Answer.model_json_schema()
    assert "$defs" in raw  # confirms the premise

    schema = build_schema(Answer)
    assert "$ref" not in str(schema)
    assert "$defs" not in schema


def test_schema_instructions_extend_an_existing_system_message() -> None:
    messages = [ChatMessage(role="system", content="You write SQL."),
                ChatMessage(role="user", content="q")]
    out = with_schema_instructions(messages, {"type": "object"})
    assert out[0].content.startswith("You write SQL.")
    assert "JSON Schema" in out[0].content
    assert messages[0].content == "You write SQL.", "must not mutate the caller's messages"


def test_schema_instructions_add_a_system_message_when_absent() -> None:
    out = with_schema_instructions([ChatMessage(role="user", content="q")], {"type": "object"})
    assert out[0].role == "system"
    assert len(out) == 2
