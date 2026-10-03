"""The registry resolves models.yaml, with environment variables taking priority."""

from pathlib import Path

import pytest
import yaml

from app.core.model_registry import (
    REGISTRY_PATH,
    ModelRef,
    RegistryError,
    build_registry,
)

SAMPLE = {
    "providers": {
        "groq": {"base_url": "https://groq.test/v1", "api_key_env": "GROQ_API_KEY",
                 "daily_requests": 1000},
        "openrouter": {"base_url": "https://or.test/v1", "api_key_env": "OPENROUTER_API_KEY",
                       "headers": {"X-Title": "InsightFlow"}},
        "ollama": {"base_url": "http://localhost:11434/v1", "api_key_env": None},
        "gemini": {"base_url": "https://gemini.test/v1", "api_key_env": "GEMINI_API_KEY"},
    },
    "llm": {
        "chain": ["groq:openai/gpt-oss-120b", "openrouter:some/model:free"],
        "agents": {"query": None, "analysis": None},
        "capabilities": {
            "default": ["tool_call", "prompt_json"],
            "models": {"groq:openai/gpt-oss-120b": ["tool_call", "json_schema"]},
        },
    },
    "embeddings": {
        "default": "openrouter:small:free",
        "models": {
            "openrouter:small:free": {"dimension": 1024},
            "gemini:text-embedding-004": {"dimension": 768},
        },
    },
}


@pytest.fixture
def registry_file(tmp_path: Path) -> Path:
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(SAMPLE))
    return path


def test_reads_providers_chain_and_embedding(registry_file) -> None:
    registry = build_registry(registry_file)

    assert [ref.key for ref in registry.chain] == [
        "groq:openai/gpt-oss-120b", "openrouter:some/model:free"
    ]
    assert registry.embedding.ref.key == "openrouter:small:free"
    assert registry.embedding.dimension == 1024
    assert registry.provider("openrouter").headers == {"X-Title": "InsightFlow"}


def test_model_ids_containing_colons_are_parsed_correctly() -> None:
    ref = ModelRef.parse("openrouter:nvidia/nemotron-3-super-120b-a12b:free")
    assert ref.provider == "openrouter"
    assert ref.model == "nvidia/nemotron-3-super-120b-a12b:free"


def test_environment_overrides_the_file(registry_file) -> None:
    registry = build_registry(
        registry_file,
        chain_override=["ollama:llama3"],
        embedding_override="gemini:text-embedding-004",
    )

    assert [ref.key for ref in registry.chain] == ["ollama:llama3"]
    assert registry.embedding.dimension == 768, "dimension follows the chosen model"


def test_per_agent_override_is_recorded(registry_file) -> None:
    registry = build_registry(registry_file, agent_overrides={"query": "groq:openai/gpt-oss-20b"})
    assert registry.agent_overrides["query"].model == "openai/gpt-oss-20b"
    assert "analysis" not in registry.agent_overrides, "a null override is not an override"


def test_capabilities_fall_back_to_the_default(registry_file) -> None:
    registry = build_registry(registry_file)

    assert registry.strategies_for(ModelRef.parse("groq:openai/gpt-oss-120b")) == [
        "tool_call", "json_schema"
    ]
    assert registry.strategies_for(ModelRef.parse("groq:something-new")) == [
        "tool_call", "prompt_json"
    ]


def test_an_undefined_provider_is_rejected_with_a_useful_message(tmp_path) -> None:
    broken = {**SAMPLE, "llm": {**SAMPLE["llm"], "chain": ["mystery:model"]}}
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(broken))

    with pytest.raises(RegistryError, match="mystery"):
        build_registry(path)


def test_an_unknown_strategy_is_rejected(tmp_path) -> None:
    broken = {**SAMPLE}
    broken["llm"] = {**SAMPLE["llm"], "capabilities": {"default": ["telepathy"]}}
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(broken))

    with pytest.raises(RegistryError, match="telepathy"):
        build_registry(path)


def test_a_missing_file_still_yields_a_usable_registry(tmp_path) -> None:
    """The app should start and explain itself, not fail to import.

    The built-in defaults have to define their own providers too, or validation
    rejects them and there is no working configuration at all.
    """
    registry = build_registry(tmp_path / "absent.yaml")

    assert registry.chain
    assert registry.embedding.dimension
    for ref in registry.chain:
        assert registry.provider(ref.provider) is not None
    assert registry.provider(registry.embedding.ref.provider) is not None


def test_an_unlisted_embedding_model_keeps_the_configured_dimension(registry_file) -> None:
    registry = build_registry(
        registry_file, embedding_override="openrouter:brand-new", dimension_override=1024
    )
    assert registry.embedding.dimension == 1024


# ── The real file ────────────────────────────────────────────────────────────
def test_the_shipped_registry_is_valid() -> None:
    registry = build_registry(REGISTRY_PATH)

    assert registry.chain, "a default chain must exist so the app runs unconfigured"
    for ref in registry.chain:
        assert registry.provider(ref.provider) is not None
        assert registry.strategies_for(ref)[0] == "tool_call"


def test_groq_leads_the_chain_to_protect_the_scarcer_quota() -> None:
    """OpenRouter's free tier allows 50 requests a day, shared with embeddings;
    Groq allows 1,000. Putting OpenRouter first drained the shared allowance on
    chat alone and left nothing for indexing."""
    registry = build_registry(REGISTRY_PATH)

    assert registry.chain[0].provider == "groq"
    assert registry.embedding.ref.provider == "openrouter"


def test_the_selected_embedding_model_fits_the_database_column() -> None:
    from app.db.models.knowledge import EMBEDDING_DIM

    assert build_registry(REGISTRY_PATH).embedding.dimension == EMBEDDING_DIM
