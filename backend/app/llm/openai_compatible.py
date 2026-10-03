"""One provider class for every OpenAI-compatible endpoint.

OpenRouter, Groq, Gemini's compatibility layer and a local Ollama all speak the
same chat-completions protocol, so supporting another vendor is a configuration
entry rather than new code. This is the practical payoff of putting a provider
interface under the agents.
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx
import openai
from openai import AsyncOpenAI

from app.llm.base import LLMProvider
from app.llm.errors import (
    BadRequest,
    ModelUnavailable,
    ProviderAuthError,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimited,
)
from app.llm.types import LLMRequest, LLMResponse, Usage

# OpenRouter reports a withdrawn or misspelt model as HTTP 400 ("... is not a valid
# model ID") rather than 404. Read as a bad request it would be retried under every
# structured-output strategy, spending free quota on a model that cannot answer.
_DEAD_MODEL = re.compile(
    r"not a valid model|model.{0,80}(not found|does not exist|not available)"
    r"|no endpoints found|unknown model",
    re.IGNORECASE,
)


def _retry_after(exc: openai.APIStatusError) -> float | None:
    raw = exc.response.headers.get("retry-after")
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        *,
        default_headers: dict[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        # max_retries=0: retrying is the client's job, because it needs to
        # honour Retry-After and fall through to other models.
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "unused",
            default_headers=default_headers,
            max_retries=0,
            http_client=http_client,
        )

    @staticmethod
    def _body(request: LLMRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": [m.model_dump() for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }

        spec = request.structured
        if spec is None or spec.strategy == "prompt_json":
            return body

        if spec.strategy == "tool_call":
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description or "Submit the result.",
                        "parameters": spec.json_schema,
                    },
                }
            ]
            body["tool_choice"] = {"type": "function", "function": {"name": spec.name}}
        elif spec.strategy == "json_schema":
            # strict stays off: it requires every property to be required, which
            # rules out optional fields on our own models.
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": spec.name, "strict": False, "schema": spec.json_schema},
            }
        return body

    async def complete(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        try:
            completion = await self._client.chat.completions.create(
                **self._body(request), timeout=request.timeout_seconds
            )
        except openai.RateLimitError as exc:
            raise RateLimited(f"{self.name}: rate limited", retry_after=_retry_after(exc)) from exc
        except openai.APITimeoutError as exc:
            raise ProviderTimeout(f"{self.name}: timed out") from exc
        except openai.APIConnectionError as exc:
            raise ProviderUnavailable(f"{self.name}: connection failed") from exc
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise ProviderAuthError(f"{self.name}: credentials rejected") from exc
        except openai.NotFoundError as exc:
            raise ModelUnavailable(f"{self.name}: model {request.model!r} not found") from exc
        except openai.BadRequestError as exc:
            detail = str(getattr(exc, "message", exc))[:160]
            if _DEAD_MODEL.search(detail):
                raise ModelUnavailable(
                    f"{self.name}: model {request.model!r} is not available"
                ) from exc
            raise BadRequest(f"{self.name}: request rejected ({detail})") from exc
        except openai.APIStatusError as exc:
            raise ProviderUnavailable(f"{self.name}: HTTP {exc.status_code}") from exc

        # Some gateways answer 200 with an error body and no choices.
        if not completion.choices:
            raise ProviderUnavailable(f"{self.name}: empty response")

        choice = completion.choices[0]
        message = choice.message

        # tool_call mode: arguments. Otherwise (or if the model ignored the tool
        # and answered in text) the message content — the client parses either.
        content: str | None = message.content
        if message.tool_calls:
            content = message.tool_calls[0].function.arguments

        usage = completion.usage
        return LLMResponse(
            content=content,
            usage=Usage(
                input_tokens=getattr(usage, "prompt_tokens", None),
                output_tokens=getattr(usage, "completion_tokens", None),
                total_tokens=getattr(usage, "total_tokens", None),
            ),
            model=completion.model or request.model,
            provider=self.name,
            finish_reason=choice.finish_reason,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
