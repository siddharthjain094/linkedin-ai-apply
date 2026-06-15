from agent.config import Settings
from agent.llm.provider import llm_is_configured


def test_unconfigured_openai_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert llm_is_configured(Settings(llm_model="openai/gpt-4o-mini")) is False


def test_configured_with_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert llm_is_configured(Settings(llm_model="openai/gpt-4o-mini")) is True


def test_ollama_needs_no_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert llm_is_configured(Settings(llm_model="ollama/llama3.2")) is True


def test_custom_api_base_is_configured(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert llm_is_configured(
        Settings(llm_model="openai/x", llm_api_base="http://localhost:11434")
    ) is True
