"""The real provider against a mock HTTP server: what it sends, and how it reads errors.

No network is involved, but the whole openai SDK path is exercised, so a mismatch
between what we send and what an OpenAI-compatible endpoint expects shows up here.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.llm.errors import (
    BadRequest,
    ModelUnavailable,
    ProviderAuthError,
    ProviderUnavailable,
    RateLimited,
)
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.types import ChatMessage, LLMRequest, StructuredSpec

SCHEMA = {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}


def completion(message: dict, *, usage: dict | None = None) -> dict:
    return {
        "id": "x", "object": "chat.completion", "created": 0, "model": "served-model",
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", **message}}
        ],
        "usage": usage or {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def provider_for(handler) -> tuple[OpenAICompatibleProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    provider = OpenAICompatibleProvider(
        "test", "https://example.test/v1", "key", http_client=client
    )
    return provider, seen


def request(strategy: str | None) -> LLMRequest:
    spec = None if strategy is None else StructuredSpec(
        strategy=strategy, name="answer", description="An answer.", json_schema=SCHEMA
    )
    return LLMRequest(model="some/model:free", messages=[ChatMessage(role="user", content="hi")],
                      structured=spec)


def body_of(req: httpx.Request) -> dict:
    return json.loads(req.content)


# ── What we send ─────────────────────────────────────────────────────────────
async def test_tool_call_mode_forces_the_tool() -> None:
    provider, seen = provider_for(lambda r: httpx.Response(200, json=completion(
        {"content": None, "tool_calls": [{"id": "1", "type": "function",
         "function": {"name": "answer", "arguments": '{"sql": "SELECT 1"}'}}]})))
    response = await provider.complete(request("tool_call"))

    body = body_of(seen[0])
    assert body["tools"][0]["function"]["name"] == "answer"
    assert body["tools"][0]["function"]["parameters"] == SCHEMA
    assert body["tool_choice"] == {"type": "function", "function": {"name": "answer"}}
    assert "response_format" not in body
    assert response.content == '{"sql": "SELECT 1"}', "tool arguments are the payload"


async def test_json_schema_mode_sends_response_format() -> None:
    provider, seen = provider_for(lambda r: httpx.Response(200, json=completion(
        {"content": '{"sql": "SELECT 1"}'})))
    await provider.complete(request("json_schema"))

    body = body_of(seen[0])
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["response_format"]["json_schema"]["strict"] is False
    assert "tools" not in body


async def test_prompt_json_mode_sends_no_structured_fields() -> None:
    provider, seen = provider_for(lambda r: httpx.Response(200, json=completion({"content": "{}"})))
    await provider.complete(request("prompt_json"))

    body = body_of(seen[0])
    assert not {"tools", "tool_choice", "response_format"} & body.keys()


async def test_plain_request_without_structure() -> None:
    provider, seen = provider_for(lambda r: httpx.Response(200, json=completion({"content": "hi"})))
    await provider.complete(request(None))
    assert "tools" not in body_of(seen[0])


async def test_sends_the_api_key_and_endpoint() -> None:
    provider, seen = provider_for(lambda r: httpx.Response(200, json=completion({"content": "x"})))
    await provider.complete(request(None))

    assert str(seen[0].url) == "https://example.test/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer key"


# ── What we read ─────────────────────────────────────────────────────────────
async def test_reads_usage_and_the_serving_model() -> None:
    provider, _ = provider_for(lambda r: httpx.Response(200, json=completion({"content": "x"})))
    response = await provider.complete(request(None))

    usage = response.usage
    assert (usage.input_tokens, usage.output_tokens, usage.total) == (11, 7, 18)
    assert response.model == "served-model"
    assert response.provider == "test"


async def test_a_model_that_ignores_the_tool_and_answers_in_text_is_still_read() -> None:
    """Free models do this. The client's parser deals with the content."""
    provider, _ = provider_for(lambda r: httpx.Response(200, json=completion(
        {"content": '```json\n{"sql": "SELECT 1"}\n```'})))
    response = await provider.complete(request("tool_call"))
    assert "SELECT 1" in response.content


# ── Error classification ─────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("status", "headers", "expected"),
    [
        (429, {"retry-after": "7"}, RateLimited),
        (404, {}, ModelUnavailable),
        (400, {}, BadRequest),
        (401, {}, ProviderAuthError),
        (403, {}, ProviderAuthError),
        (500, {}, ProviderUnavailable),
        (503, {}, ProviderUnavailable),
    ],
)
async def test_http_errors_are_classified_by_what_to_do_about_them(
    status, headers, expected
) -> None:
    provider, _ = provider_for(lambda r: httpx.Response(
        status, headers=headers, json={"error": {"message": "boom"}}))

    with pytest.raises(expected):
        await provider.complete(request("tool_call"))


async def test_retry_after_is_carried_on_the_error() -> None:
    provider, _ = provider_for(lambda r: httpx.Response(
        429, headers={"retry-after": "7"}, json={"error": {"message": "slow down"}}))

    with pytest.raises(RateLimited) as exc_info:
        await provider.complete(request("tool_call"))
    assert exc_info.value.retry_after == 7.0


async def test_a_200_with_no_choices_is_an_error_not_a_crash() -> None:
    """Some gateways report failure inside a 200 response."""
    provider, _ = provider_for(lambda r: httpx.Response(200, json={
        "id": "x", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [], "error": {"message": "upstream failed"}}))

    with pytest.raises(ProviderUnavailable, match="empty"):
        await provider.complete(request(None))


async def test_connection_failure_is_reported_as_unavailable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    provider, _ = provider_for(refuse)
    with pytest.raises(ProviderUnavailable):
        await provider.complete(request(None))


async def test_the_api_key_never_appears_in_an_error_message() -> None:
    provider, _ = provider_for(
        lambda r: httpx.Response(401, json={"error": {"message": "bad key"}})
    )

    with pytest.raises(ProviderAuthError) as exc_info:
        await provider.complete(request(None))
    assert "key" not in exc_info.value.message.replace("credentials", "")


@pytest.mark.parametrize(
    "message",
    [
        "nvidia/this-model-does-not-exist:free is not a valid model ID",
        "No endpoints found for some/model:free",
        "The model `x` does not exist or you do not have access to it.",
    ],
)
async def test_a_dead_model_reported_as_a_400_is_not_retried_as_a_bad_strategy(message) -> None:
    """OpenRouter answers a withdrawn model with 400, not 404. Treating it as a bad
    request would retry it under every strategy and burn free-tier quota."""
    provider, seen = provider_for(
        lambda r: httpx.Response(400, json={"error": {"message": message, "code": 400}})
    )
    with pytest.raises(ModelUnavailable):
        await provider.complete(request("tool_call"))
    assert len(seen) == 1


async def test_a_genuine_unsupported_parameter_is_still_a_bad_request() -> None:
    """The distinction that matters: a different strategy may work for this one."""
    provider, _ = provider_for(lambda r: httpx.Response(
        400, json={"error": {"message": "unknown variant `json_schema`", "code": 400}}))
    with pytest.raises(BadRequest):
        await provider.complete(request("json_schema"))
