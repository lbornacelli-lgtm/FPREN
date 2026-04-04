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


def run_agent(
    system_prompt: str,
    tools: list,
    tool_functions: dict,
    initial_message: str,
    model: str = "",
    max_iterations: int = 10,
    max_tokens: int = 1024,
) -> dict:
    """Run a tool-calling agent loop using UF LiteLLM.

    The agent sends the initial message, receives a response, executes any
    tool calls the LLM requests, feeds results back, and repeats until the
    LLM returns a plain text answer (no more tool calls) or max_iterations
    is reached.

    Args:
        system_prompt:   System instructions for the agent.
        tools:           List of tool schemas in OpenAI function-calling format.
        tool_functions:  Dict mapping function name → callable.
        initial_message: The user's request / task description.
        model:           Override model (defaults to UF_LITELLM_MODEL env var).
        max_iterations:  Safety cap on tool-calling rounds (default 10).
        max_tokens:      Max tokens per LLM response.

    Returns:
        {
            "response":   str,   # Final plain-text answer from the LLM
            "tool_calls": list,  # [{tool, args, result}, ...] audit log
            "iterations": int,   # Number of tool-calling rounds used
        }
    """
    import json as _json

    client = _get_client()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": initial_message},
    ]
    tool_calls_log = []
    iterations = 0

    while iterations < max_iterations:
        response = client.chat.completions.create(
            model      = model or _MODEL,
            messages   = messages,
            tools      = tools or None,
            tool_choice= "auto" if tools else "none",
            max_tokens = max_tokens,
        )
        msg = response.choices[0].message

        # Append assistant turn — works whether or not there are tool calls
        messages.append({
            "role":       "assistant",
            "content":    msg.content,
            "tool_calls": [
                {
                    "id":       tc.id,
                    "type":     "function",
                    "function": {"name": tc.function.name,
                                 "arguments": tc.function.arguments},
                }
                for tc in (msg.tool_calls or [])
            ] or None,
        })

        if not msg.tool_calls:
            # No more tools — return final answer
            return {
                "response":   (msg.content or "").strip(),
                "tool_calls": tool_calls_log,
                "iterations": iterations,
            }

        # Execute each requested tool
        for tc in msg.tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = _json.loads(tc.function.arguments)
            except Exception:
                fn_args = {}

            logger.debug("Agent tool call: %s(%s)", fn_name, fn_args)

            fn = tool_functions.get(fn_name)
            if fn is None:
                result = {"error": f"Unknown tool: {fn_name}"}
            else:
                try:
                    result = fn(**fn_args)
                except Exception as exc:
                    result = {"error": f"Tool error: {exc}"}

            tool_calls_log.append({"tool": fn_name, "args": fn_args, "result": result})

            messages.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      _json.dumps(result, default=str),
            })

        iterations += 1

    # Safety exit — ask for a final answer without allowing more tool calls
    final = client.chat.completions.create(
        model      = model or _MODEL,
        messages   = messages + [{"role": "user",
                                  "content": "Please provide your final answer now."}],
        max_tokens = max_tokens,
    )
    return {
        "response":   (final.choices[0].message.content or "").strip(),
        "tool_calls": tool_calls_log,
        "iterations": iterations,
    }
