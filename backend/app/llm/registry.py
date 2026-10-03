"""Build LLM providers from the model registry.

Only providers actually referenced are constructed, and one without credentials
is skipped rather than fatal — the usual state while a key is still missing.
"""

from __future__ import annotations

import logging
import os

from app.core.config import Settings
from app.core.model_registry import get_registry
from app.llm.base import LLMProvider
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.types import ChainEntry

logger = logging.getLogger(__name__)

# Keys are read from settings where one exists, so a value in .env is picked up
# without the registry needing to know about pydantic.
_SETTINGS_FIELDS = {
    "OPENROUTER_API_KEY": "openrouter_api_key",
    "GROQ_API_KEY": "groq_api_key",
    "GEMINI_API_KEY": "gemini_api_key",
}


def _api_key(settings: Settings, env_name: str | None) -> str:
    """A local provider needs no key, so a missing `api_key_env` means 'none required'."""
    if env_name is None:
        return "not-required"
    field = _SETTINGS_FIELDS.get(env_name)
    if field is not None:
        return getattr(settings, field).get_secret_value()
    return os.environ.get(env_name, "")


def build_providers(settings: Settings, chain: list[ChainEntry]) -> dict[str, LLMProvider]:
    registry = get_registry()
    providers: dict[str, LLMProvider] = {}

    for name in {entry.provider for entry in chain}:
        spec = registry.provider(name)
        if spec is None:
            logger.warning("provider not defined in models.yaml", extra={"provider": name})
            continue

        key = _api_key(settings, spec.api_key_env)
        if not key:
            logger.info(
                "skipping provider with no credentials",
                extra={"provider": name, "expected_env": spec.api_key_env},
            )
            continue

        providers[name] = OpenAICompatibleProvider(
            name, spec.base_url, key, default_headers=spec.headers or None
        )
    return providers
