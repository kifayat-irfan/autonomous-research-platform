"""Provider-agnostic LLM client (OpenAI-compatible chat completions).

Configuration via environment variables:
  LLM_API_KEY   — API key. When unset/empty, the LLM is DISABLED and all
                  callers must fall back to deterministic templates.
  LLM_BASE_URL  — default https://token.sensenova.ai/v1
  LLM_MODEL     — default sensenova-6.8-flash-lite
  LLM_TIMEOUT   — request timeout in seconds (default 90)
  LLM_MAX_TOKENS — default 1500

Design rules:
  - The platform NEVER fails just because the LLM is unavailable.
    is_configured() gates every call; complete() raises LLMError on
    failure so callers can fall back to templates.
  - The API key is never logged, never persisted, never returned.
"""
import logging
import os

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://token.sensenova.ai/v1"
DEFAULT_MODEL = "sensenova-6.8-flash-lite"


class LLMError(Exception):
    """LLM call failed — caller should fall back to templates."""


def is_configured() -> bool:
    return bool(os.environ.get("LLM_API_KEY", "").strip())


def _config() -> dict:
    return {
        "api_key": os.environ.get("LLM_API_KEY", "").strip(),
        "base_url": os.environ.get("LLM_BASE_URL",
                                   DEFAULT_BASE_URL).rstrip("/"),
        "model": os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        "timeout": float(os.environ.get("LLM_TIMEOUT", "90")),
        "max_tokens": int(os.environ.get("LLM_MAX_TOKENS", "1500")),
    }


def _extract_text(data) -> str:
    """Pull the assistant text out of a chat-completions payload.

    Defensive: providers sometimes return a message without 'content'
    (reasoning-only / empty message). Never raise KeyError/IndexError —
    return "" so the caller can retry or fall back.
    """
    try:
        choices = data.get("choices") or []
        message = choices[0].get("message") or {}
        text = message.get("content") or ""
    except (AttributeError, IndexError, TypeError):
        return ""
    return text.strip() if isinstance(text, str) else ""


def complete(prompt: str, system: str | None = None,
             max_tokens: int | None = None,
             temperature: float = 0.4) -> str:
    """Single chat completion. Raises LLMError on any failure."""
    cfg = _config()
    if not cfg["api_key"]:
        raise LLMError("LLM_API_KEY not set — LLM disabled.")
    try:
        import requests
    except ImportError as exc:
        raise LLMError("requests library not available.") from exc

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    url = cfg["base_url"] + "/chat/completions"
    # The provider sometimes returns HTTP 200 with a message that carries no
    # 'content' (observed live 2026-11-15: SenseNova answered v1 fine, then
    # v2 with a content-less message). Treat a missing/empty content as a
    # transient failure and retry ONCE before giving up — every caller
    # already falls back to templates on LLMError.
    for attempt in range(2):
        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {cfg['api_key']}",
                         "Content-Type": "application/json"},
                json={"model": cfg["model"],
                      "messages": messages,
                      "temperature": temperature,
                      "max_tokens": max_tokens or cfg["max_tokens"]},
                timeout=cfg["timeout"])
        except Exception as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

        if resp.status_code != 200:
            raise LLMError(f"LLM HTTP {resp.status_code}: "
                           f"{resp.text[:300]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMError(f"LLM response is not JSON: {exc}") from exc
        text = _extract_text(data)
        if text:
            return text
        if attempt == 0:
            log.warning("LLM returned no content (attempt 1/2); retrying once.")
    raise LLMError("LLM returned empty text.")


def status() -> dict:
    """Safe status for UI display — never includes the key."""
    cfg = _config()
    return {"configured": is_configured(),
            "base_url": cfg["base_url"],
            "model": cfg["model"]}
