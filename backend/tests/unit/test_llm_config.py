import pytest

from app.core.config import DEFAULT_LLM_CHAIN, Settings
from app.llm.capabilities import strategies_for
from app.llm.types import ChainEntry


def test_chain_entries_split_on_the_first_colon_only() -> None:
    """Model ids such as `...:free` contain colons of their own."""
    entry = ChainEntry.parse("openrouter:nvidia/nemotron-3-super-120b-a12b:free")
    assert entry.provider == "openrouter"
    assert entry.model == "nvidia/nemotron-3-super-120b-a12b:free"


@pytest.mark.parametrize("bad", ["nocolon", ":model", "provider:", ""])
def test_malformed_chain_entries_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        ChainEntry.parse(bad)


def test_a_blank_chain_setting_means_use_the_default(monkeypatch) -> None:
    """`.env.example` used to ship `LLM_FALLBACK_CHAIN=`, which silently emptied the chain."""
    monkeypatch.setenv("LLM_FALLBACK_CHAIN", "")
    assert Settings(_env_file=None).llm_fallback_chain == DEFAULT_LLM_CHAIN


def test_every_default_model_is_declared_and_tries_tool_calls_first() -> None:
    """Tool-call mode worked on every model probed, so it leads. A default-chain model
    missing from models.yaml would silently get the conservative fallback instead."""
    from app.llm import capabilities

    _, declared = capabilities._load()
    for item in DEFAULT_LLM_CHAIN:
        assert item in declared, f"{item} is in the default chain but not in models.yaml"
        assert strategies_for(ChainEntry.parse(item))[0] == "tool_call"


def test_capabilities_reflect_measured_behaviour() -> None:
    # Measured: HTTP 400 in json_schema mode on this one.
    assert "json_schema" not in strategies_for(ChainEntry.parse("groq:qwen/qwen3.8-27b"))
    assert "json_schema" in strategies_for(ChainEntry.parse("groq:openai/gpt-oss-120b"))


def test_unknown_models_get_conservative_defaults() -> None:
    assert strategies_for(ChainEntry.parse("somewhere:new-model")) == ["tool_call", "prompt_json"]
