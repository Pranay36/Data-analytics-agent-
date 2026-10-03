"""Chain parsing and capability lookup."""

import pytest

from app.core.model_registry import REGISTRY_PATH, build_registry
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


def test_every_model_in_the_shipped_chain_tries_tool_calls_first() -> None:
    """Tool-call mode worked on every model probed; JSON-schema mode did not."""
    for ref in build_registry(REGISTRY_PATH).chain:
        entry = ChainEntry(provider=ref.provider, model=ref.model)
        assert strategies_for(entry)[0] == "tool_call"


def test_capabilities_reflect_measured_behaviour() -> None:
    # Measured: HTTP 400 in json_schema mode on this one.
    assert "json_schema" not in strategies_for(ChainEntry.parse("groq:qwen/qwen3.8-27b"))
    assert "json_schema" in strategies_for(ChainEntry.parse("groq:openai/gpt-oss-120b"))


def test_unknown_models_get_conservative_defaults() -> None:
    assert strategies_for(ChainEntry.parse("groq:brand-new-model")) == [
        "tool_call", "prompt_json"
    ]
