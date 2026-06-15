"""Pluggable LLM wrapper over litellm.

A single `LLMClient` works with any provider supported by litellm
(openai/*, anthropic/*, gemini/*, ollama/*, ...) selected purely by the
`LLM_MODEL` string, so swapping providers is a config change, not a code change.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

from agent.config import Settings


class LLMUnavailableError(RuntimeError):
    """Raised when the LLM fails repeatedly, signalling a systemic problem
    (bad/missing API key, wrong model, provider outage) rather than a one-off."""


# Which env var holds the API key for each provider prefix (ollama needs none).
_PROVIDER_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "azure": "AZURE_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def resolve_api_key(settings: Settings) -> str | None:
    """The key to use for every LLM call.

    Precedence: the universal LLM_API_KEY, then the provider-specific env var
    implied by the model prefix."""
    if settings.llm_api_key:
        return settings.llm_api_key
    model = settings.llm_model or ""
    provider = model.split("/", 1)[0].lower() if "/" in model else "openai"
    env = _PROVIDER_KEY_ENV.get(provider)
    return os.getenv(env) if env else None


def llm_is_configured(settings: Settings) -> bool:
    """True if the configured model can plausibly be called.

    Lets callers skip the LLM (and its retries/error banners) when no key is set,
    e.g. during `intake` when the user hasn't configured a provider yet."""
    if settings.llm_api_key or settings.llm_api_base:
        return True  # universal key or custom/self-hosted endpoint
    model = settings.llm_model or ""
    provider = model.split("/", 1)[0].lower() if "/" in model else ""
    if provider == "ollama":
        return True
    env = _PROVIDER_KEY_ENV.get(provider)
    if env is None:
        return True  # unknown provider: let litellm decide
    return bool(os.getenv(env))


class LLMClient:
    def __init__(self, settings: Settings):
        self.model = settings.llm_model
        self.temperature = settings.llm_temperature
        self.api_base = settings.llm_api_base or None
        self.api_key = resolve_api_key(settings)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=20))
    def _complete(self, messages: list[dict[str, str]], temperature: float | None) -> str:
        # Imported lazily so the package imports without the heavy dep present.
        import litellm

        # Keep litellm's "Give Feedback / Get Help" banner out of our output.
        litellm.suppress_debug_info = True

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key
        resp = litellm.completion(**kwargs)
        return resp["choices"][0]["message"]["content"] or ""

    def chat(
        self,
        system: str,
        user: str,
        temperature: float | None = None,
    ) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return self._complete(messages, temperature).strip()

    def chat_json(self, system: str, user: str, temperature: float | None = 0.0) -> dict:
        """Chat and parse a JSON object out of the response (robust to fencing)."""
        raw = self.chat(system + "\n\nRespond ONLY with valid JSON.", user, temperature)
        return _extract_json(raw)


def _extract_json(text: str) -> dict:
    text = text.strip()
    # Strip ```json ... ``` fences if present.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            text = brace.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}
