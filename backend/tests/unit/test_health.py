from fastapi.testclient import TestClient

from app.main import create_app


def test_health_is_dependency_free() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_settings_accept_comma_separated_strings(monkeypatch) -> None:
    from app.core.config import Settings

    monkeypatch.setenv("LLM_FALLBACK_CHAIN", "gemini:a, groq:b ,openrouter:c:free")
    settings = Settings(_env_file=None)
    assert settings.llm_fallback_chain == ["gemini:a", "groq:b", "openrouter:c:free"]
