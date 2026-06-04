"""Application configuration and LLM provider resolution.

All settings are environment-driven (prefix ``FINOPTIC_``) with safe offline defaults,
so the service boots and runs end-to-end with zero configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["none", "anthropic", "openai", "azure-openai"]

_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o-mini",
    "azure-openai": "gpt-4o-mini",
}


@dataclass(frozen=True)
class ResolvedLLM:
    """Concrete LLM target after auto-detection. ``provider == 'none'`` means offline."""

    provider: LLMProvider
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_version: str = "2024-06-01"
    max_tokens: int = 900
    temperature: float = 0.2
    timeout_seconds: float = 30.0

    @property
    def enabled(self) -> bool:
        return self.provider != "none" and bool(self.api_key)


class Settings(BaseSettings):
    """Runtime settings. List-like values are CSV strings to avoid env JSON-parsing pitfalls."""

    model_config = SettingsConfigDict(
        env_prefix="FINOPTIC_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "FinOptic"
    version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False

    database_url: str = "sqlite:///./finoptic.db"

    # --- Auth ---
    require_auth: bool = False
    api_keys_raw: str = ""  # CSV
    rate_limit_per_minute: int = 120

    # --- Limits ---
    max_upload_bytes: int = 25 * 1024 * 1024  # 25 MB cap on /ingest uploads

    # --- CORS ---
    cors_origins_raw: str = "*"  # CSV

    # --- LLM / GenAI ---
    llm_provider: Literal["none", "anthropic", "openai", "azure-openai", "auto"] = "auto"
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_version: str = "2024-06-01"
    llm_max_tokens: int = 900
    llm_temperature: float = 0.2
    llm_timeout_seconds: float = 30.0

    # --- Derived helpers ---
    @property
    def api_keys(self) -> list[str]:
        return [k.strip() for k in self.api_keys_raw.split(",") if k.strip()]

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()] or ["*"]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def resolved_llm(self) -> ResolvedLLM:
        """Resolve the effective LLM target, auto-detecting from standard env vars.

        Resolution never raises: if no usable key is found it returns a disabled
        ``ResolvedLLM`` so callers transparently fall back to deterministic output.
        """
        provider: str = self.llm_provider
        api_key = self.llm_api_key
        base_url = self.llm_base_url

        if provider == "auto":
            if api_key:
                provider = "openai"  # explicit key with no provider -> OpenAI-compatible
            elif os.getenv("ANTHROPIC_API_KEY"):
                provider, api_key = "anthropic", os.getenv("ANTHROPIC_API_KEY")
            elif os.getenv("AZURE_OPENAI_API_KEY"):
                provider, api_key = "azure-openai", os.getenv("AZURE_OPENAI_API_KEY")
                base_url = base_url or os.getenv("AZURE_OPENAI_ENDPOINT")
            elif os.getenv("OPENAI_API_KEY"):
                provider, api_key = "openai", os.getenv("OPENAI_API_KEY")
            else:
                provider = "none"

        if provider == "none" or not api_key:
            return ResolvedLLM(provider="none")

        return ResolvedLLM(
            provider=provider,  # type: ignore[arg-type]
            api_key=api_key,
            model=self.llm_model or _DEFAULT_MODELS.get(provider),
            base_url=base_url,
            api_version=self.llm_api_version,
            max_tokens=self.llm_max_tokens,
            temperature=self.llm_temperature,
            timeout_seconds=self.llm_timeout_seconds,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
