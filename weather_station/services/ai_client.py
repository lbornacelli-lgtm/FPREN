"""Shared LiteLLM client for the FPREN weather station.

All AI calls in this project go through this module so the endpoint and
key only need to be set in one place (.env or environment variables).

Environment variables:
    UF_LITELLM_BASE_URL  — LiteLLM proxy base URL  (e.g. https://api.ai.it.ufl.edu)
    UF_LITELLM_API_KEY   — LiteLLM virtual key      (must start with sk-)
    UF_LITELLM_MODEL     — default model to use     (default: gpt-4o-mini)
"""

import logging
import os

from openai import OpenAI

logger = logging.getLogger("ai_client")

_BASE_URL = os.getenv("UF_LITELLM_BASE_URL", "https://api.ai.it.ufl.edu")
_API_KEY  = os.getenv("UF_LITELLM_API_KEY", "")
_MODEL    = os.getenv("UF_LITELLM_MODEL", "gpt-4o-mini")

# Single shared client — instantiated once at import time.
_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not _API_KEY:
            raise RuntimeError(
                "UF_LITELLM_API_KEY is not set. "
                "Add it to weather_station/.env to enable AI features."
            )
        _client = OpenAI(base_url=_BASE_URL, api_key=_API_KEY)
        logger.info("LiteLLM client ready → %s (model: %s)", _BASE_URL, _MODEL)
    return _client


def chat(prompt: str, system: str = "", model: str = "", max_tokens: int = 512) -> str:
    """Send a chat completion request and return the response text.

    Raises RuntimeError if the key is not configured.
    Raises any openai.*Error on API failure — callers should handle these.
    """
    client = _get_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = client.chat.completions.create(
        model=model or _MODEL,
        messages=messages,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content.strip()


def is_configured() -> bool:
    """Return True if the API key env var is present."""
    return bool(_API_KEY)
