"""Provider-agnostic LLM client built directly on ``httpx`` (no vendor SDKs).

Supports Anthropic Messages, OpenAI Chat Completions, and Azure OpenAI Chat Completions
behind a single ``complete(system, user)`` call. The client is intentionally total:
it returns ``None`` on any error (transport, timeout, non-2xx, malformed body) and never
raises, so callers can transparently fall back to deterministic output.
"""

from __future__ import annotations

from typing import Any

import httpx

from finoptic.config import ResolvedLLM, get_settings
from finoptic.logging_config import get_logger

logger = get_logger(__name__)

_ANTHROPIC_DEFAULT_BASE = "https://api.anthropic.com"
_OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"
_ANTHROPIC_VERSION = "2023-06-01"


class LLMClient:
    """Thin, defensive HTTP client over multiple chat/completions providers."""

    def __init__(self, resolved: ResolvedLLM) -> None:
        self._resolved = resolved

    @classmethod
    def from_settings(cls) -> LLMClient:
        """Build a client from the resolved LLM target in application settings."""
        return cls(get_settings().resolved_llm())

    @property
    def enabled(self) -> bool:
        """True when a provider and API key are configured (i.e. calls may succeed)."""
        return self._resolved.enabled

    @property
    def provider(self) -> str:
        """Configured provider string (``"none"`` when offline)."""
        return self._resolved.provider

    # -- public API ---------------------------------------------------------

    def complete(self, system: str, user: str) -> str | None:
        """Return the model's text completion, or ``None`` on any failure.

        Dispatches on the resolved provider. All exceptions and non-2xx responses
        are swallowed (logged at warning level) so the caller can fall back.
        """
        r = self._resolved
        if not r.enabled or not r.model:
            return None
        try:
            if r.provider == "anthropic":
                return self._complete_anthropic(system, user)
            if r.provider == "openai":
                return self._complete_openai(system, user)
            if r.provider == "azure-openai":
                return self._complete_azure(system, user)
            logger.warning("llm.unsupported_provider", extra={"provider": r.provider})
            return None
        except Exception as exc:  # noqa: BLE001 - never propagate to the pipeline
            logger.warning(
                "llm.complete_failed",
                extra={"provider": r.provider, "error": type(exc).__name__},
            )
            return None

    # -- provider implementations ------------------------------------------

    def _complete_anthropic(self, system: str, user: str) -> str | None:
        r = self._resolved
        base = (r.base_url or _ANTHROPIC_DEFAULT_BASE).rstrip("/")
        url = f"{base}/v1/messages"
        headers = {
            "x-api-key": r.api_key or "",
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": r.model,
            "max_tokens": r.max_tokens,
            "temperature": r.temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        data = self._post(url, headers, body)
        if data is None:
            return None
        # Expected: {"content": [{"type": "text", "text": "..."}], ...}
        content = data.get("content")
        if isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type", "text") == "text"
            ]
            text = "".join(p for p in parts if isinstance(p, str)).strip()
            return text or None
        return None

    def _complete_openai(self, system: str, user: str) -> str | None:
        r = self._resolved
        base = (r.base_url or _OPENAI_DEFAULT_BASE).rstrip("/")
        url = f"{base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {r.api_key or ''}",
            "content-type": "application/json",
        }
        body = self._openai_body()
        body["messages"] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        data = self._post(url, headers, body)
        return self._extract_openai_text(data)

    def _complete_azure(self, system: str, user: str) -> str | None:
        r = self._resolved
        if not r.base_url:
            logger.warning("llm.azure_missing_base_url")
            return None
        base = r.base_url.rstrip("/")
        url = f"{base}/openai/deployments/{r.model}/chat/completions?api-version={r.api_version}"
        headers = {"api-key": r.api_key or "", "content-type": "application/json"}
        body = self._openai_body()
        body["messages"] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        data = self._post(url, headers, body)
        return self._extract_openai_text(data)

    # -- helpers ------------------------------------------------------------

    def _openai_body(self) -> dict[str, Any]:
        r = self._resolved
        return {
            "model": r.model,
            "max_tokens": r.max_tokens,
            "temperature": r.temperature,
        }

    @staticmethod
    def _extract_openai_text(data: dict[str, Any] | None) -> str | None:
        if data is None:
            return None
        # Expected: {"choices": [{"message": {"content": "..."}}], ...}
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    text = message.get("content")
                    if isinstance(text, str) and text.strip():
                        return text.strip()
        return None

    def _post(
        self, url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> dict[str, Any] | None:
        """POST JSON and return the parsed object, or ``None`` on non-2xx/error."""
        timeout = self._resolved.timeout_seconds
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json=body)
        if resp.status_code < 200 or resp.status_code >= 300:
            logger.warning(
                "llm.http_error",
                extra={
                    "provider": self._resolved.provider,
                    "status_code": resp.status_code,
                    "body": resp.text[:500],
                },
            )
            return None
        parsed = resp.json()
        return parsed if isinstance(parsed, dict) else None
