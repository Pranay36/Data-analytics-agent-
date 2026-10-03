"""Reads `models.yaml`: which providers exist, and which models to use.

One place answers "what model is this, where does it live, and what can it do?",
so switching provider is an edit to a data file rather than a change to code.
Environment variables still win, because a deployment should be able to override
without editing a file baked into an image.

Resolution order, most specific first:

    LLM_FALLBACK_CHAIN / EMBEDDING_MODEL  (environment)
    models.yaml
    a built-in default, so the app runs with no configuration at all
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path(__file__).resolve().parents[2] / "models.yaml"

# Used only if models.yaml is missing or unreadable. Deliberately minimal: enough
# for the app to start and report a clear error, not a second source of truth.
# The providers are included because defaults that reference undefined providers
# would fail validation, leaving no working configuration at all.
_FALLBACK_PROVIDERS = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "api_key_env": "GROQ_API_KEY"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1",
                   "api_key_env": "OPENROUTER_API_KEY"},
}
_FALLBACK_CHAIN = ["groq:openai/gpt-oss-120b"]
_FALLBACK_EMBEDDING = "openrouter:liquid/lfm-2.5-embedding-350m:free"
_FALLBACK_DIMENSION = 1024

VALID_STRATEGIES = ("tool_call", "json_schema", "prompt_json")


class RegistryError(RuntimeError):
    """`models.yaml` is present but says something impossible."""


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    base_url: str
    api_key_env: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    daily_requests: int | None = None


@dataclass(frozen=True)
class ModelRef:
    """A `provider:model` pair."""

    provider: str
    model: str

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"

    @classmethod
    def parse(cls, value: str) -> ModelRef:
        # Split on the first colon only: ids such as `qwen/qwen3.8-27b:free`
        # contain colons of their own.
        provider, separator, model = value.partition(":")
        if not separator or not provider.strip() or not model.strip():
            raise ValueError(f"Expected 'provider:model', got {value!r}")
        return cls(provider=provider.strip(), model=model.strip())


@dataclass(frozen=True)
class EmbeddingSpec:
    ref: ModelRef
    dimension: int
    notes: str = ""
    request_dimensions: bool = False
    """Ask the API for `dimension` explicitly. Needed by models whose native
    width exceeds what pgvector can index, but which can return a shorter form."""


@dataclass
class ModelRegistry:
    providers: dict[str, ProviderSpec]
    chain: list[ModelRef]
    agent_overrides: dict[str, ModelRef]
    embedding: EmbeddingSpec
    capability_default: list[str]
    capability_models: dict[str, list[str]]
    embedding_models: dict[str, EmbeddingSpec]

    def provider(self, name: str) -> ProviderSpec | None:
        return self.providers.get(name)

    def strategies_for(self, ref: ModelRef) -> list[str]:
        from fnmatch import fnmatchcase

        for pattern, strategies in self.capability_models.items():
            if fnmatchcase(ref.key, pattern):
                return list(strategies)
        return list(self.capability_default)

    def embedding_for(self, key: str) -> EmbeddingSpec:
        """Look up an embedding model, inferring the dimension when it is unlisted."""
        if key in self.embedding_models:
            return self.embedding_models[key]
        logger.warning(
            "embedding model not listed in models.yaml; assuming the configured "
            "dimension. Add it there to make the dimension explicit.",
            extra={"model": key},
        )
        return EmbeddingSpec(ref=ModelRef.parse(key), dimension=self.embedding.dimension)


def _validate_strategies(name: str, strategies: list[str]) -> list[str]:
    unknown = [item for item in strategies if item not in VALID_STRATEGIES]
    if unknown:
        raise RegistryError(
            f"models.yaml: {name} lists unknown strategies {unknown}. "
            f"Valid values: {', '.join(VALID_STRATEGIES)}"
        )
    return list(strategies)


def _load_file(path: Path) -> dict[str, Any]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        logger.warning("models.yaml not found; using built-in defaults", extra={"path": str(path)})
        return {}
    except yaml.YAMLError as exc:
        raise RegistryError(f"models.yaml could not be parsed: {exc}") from exc


def build_registry(
    path: Path | None = None,
    *,
    chain_override: list[str] | None = None,
    embedding_override: str | None = None,
    dimension_override: int | None = None,
    agent_overrides: dict[str, str] | None = None,
) -> ModelRegistry:
    """Resolve the registry, with environment overrides applied."""
    data = _load_file(path or REGISTRY_PATH)

    raw_providers = data.get("providers") or _FALLBACK_PROVIDERS
    providers = {
        name: ProviderSpec(
            name=name,
            base_url=str(spec.get("base_url", "")).rstrip("/"),
            api_key_env=spec.get("api_key_env"),
            headers=spec.get("headers") or {},
            daily_requests=spec.get("daily_requests"),
        )
        for name, spec in raw_providers.items()
    }

    llm = data.get("llm") or {}
    raw_chain = chain_override or llm.get("chain") or _FALLBACK_CHAIN
    chain = [ModelRef.parse(item) for item in raw_chain]
    if not chain:
        raise RegistryError("No LLM models configured. Set `llm.chain` or LLM_FALLBACK_CHAIN.")

    overrides: dict[str, ModelRef] = {}
    for agent, value in {**(llm.get("agents") or {}), **(agent_overrides or {})}.items():
        if value:
            overrides[agent] = ModelRef.parse(value)

    capabilities = llm.get("capabilities") or {}
    capability_default = _validate_strategies(
        "capabilities.default", capabilities.get("default") or ["tool_call", "prompt_json"]
    )
    capability_models = {
        pattern: _validate_strategies(pattern, strategies)
        for pattern, strategies in (capabilities.get("models") or {}).items()
    }

    embeddings = data.get("embeddings") or {}
    embedding_models = {
        key: EmbeddingSpec(
            ref=ModelRef.parse(key),
            dimension=int(spec["dimension"]),
            notes=str(spec.get("notes", "")).strip(),
            request_dimensions=bool(spec.get("request_dimensions", False)),
        )
        for key, spec in (embeddings.get("models") or {}).items()
    }

    embedding_key = embedding_override or embeddings.get("default") or _FALLBACK_EMBEDDING
    if embedding_key in embedding_models:
        embedding = embedding_models[embedding_key]
    else:
        embedding = EmbeddingSpec(
            ref=ModelRef.parse(embedding_key),
            dimension=dimension_override or _FALLBACK_DIMENSION,
        )
    if dimension_override is not None and dimension_override != embedding.dimension:
        # Explicit beats declared, but say so: a silent mismatch means every
        # insert fails later with an unhelpful width error.
        logger.warning(
            "EMBEDDING_DIM differs from the dimension declared in models.yaml",
            extra={"configured": dimension_override, "declared": embedding.dimension},
        )
        embedding = EmbeddingSpec(
            ref=embedding.ref,
            dimension=dimension_override,
            notes=embedding.notes,
            request_dimensions=embedding.request_dimensions,
        )

    referenced = {ref.provider for ref in [*chain, *overrides.values(), embedding.ref]}
    unknown = referenced - set(providers)
    if unknown:
        raise RegistryError(
            f"models.yaml: provider(s) {sorted(unknown)} are used but not defined under "
            f"`providers`. Defined: {', '.join(sorted(providers)) or 'none'}"
        )

    return ModelRegistry(
        providers=providers,
        chain=chain,
        agent_overrides=overrides,
        embedding=embedding,
        capability_default=capability_default,
        capability_models=capability_models,
        embedding_models=embedding_models,
    )


@lru_cache
def get_registry() -> ModelRegistry:
    from app.core.config import get_settings

    settings = get_settings()
    return build_registry(
        chain_override=settings.llm_fallback_chain or None,
        embedding_override=settings.embedding_model or None,
        dimension_override=settings.embedding_dim,
        agent_overrides={
            "query": settings.llm_model_query,
            "analysis": settings.llm_model_analysis,
            "visualization": settings.llm_model_visualization,
        },
    )
