"""Build providers from settings.

Only providers that appear in the fallback chain *and* have credentials are
constructed. A chain entry whose provider has no key is skipped at call time
with a clear reason, rather than failing the whole application at startup — the
usual state while Gemini has no key yet.
"""

from __future__ import annotations

from app.core.config import Settings
from app.llm.base import LLMProvider
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.types import ChainEntry


def build_providers(settings: Settings, chain: list[ChainEntry]) -> dict[str, LLMProvider]:
    wanted = {entry.provider for entry in chain}
    providers: dict[str, LLMProvider] = {}

    sources: dict[str, tuple[str, str, dict[str, str] | None]] = {
        "openrouter": (
            settings.openrouter_base_url,
            settings.openrouter_api_key.get_secret_value(),
            {"X-Title": "InsightFlow"},
        ),
        "groq": (settings.groq_base_url, settings.groq_api_key.get_secret_value(), None),
        "gemini": (settings.gemini_base_url, settings.gemini_api_key.get_secret_value(), None),
        # Local Ollama needs no key.
        "ollama": (settings.ollama_base_url, "ollama", None),
    }

    for name in wanted & sources.keys():
        base_url, api_key, headers = sources[name]
        if api_key:
            providers[name] = OpenAICompatibleProvider(
                name, base_url, api_key, default_headers=headers
            )
    return providers
