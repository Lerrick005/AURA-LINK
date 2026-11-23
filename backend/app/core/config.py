"""Centralized runtime configuration for Auralink.

All settings are loaded once at import and cached by ``get_settings()``.
No module should read ``os.environ`` directly — everything goes through here
so that validation, secret masking, and env-specific behavior stay consistent.

Design notes (see docs/adr/):
- Required secrets have no defaults: missing config fails fast at boot
  (ADR-0002 tenant isolation, ADR-0008 auth).
- DATABASE_URL scheme is validated to be the async driver. Mixing a sync URL
  into an async engine is a common prod incident; we reject it at startup.
- LLM_* fields are optional now and become required in Milestone 2 when the
  provider adapter lands (ADR-0003).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


Environment = Literal["dev", "staging", "prod"]


class Settings(BaseSettings):
    """Typed, validated application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- Application ------------------------------------------------------
    APP_NAME: str = "auralink"
    APP_ENV: Environment = "dev"
    APP_VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # --- Networking -------------------------------------------------------
    # Comma-separated list. Wildcard '*' is rejected outside dev.
    CORS_ORIGINS: str = "http://localhost:5173"
    TRUSTED_HOSTS: str = "localhost,127.0.0.1"

    # --- Database ---------------------------------------------------------
    # Must use the asyncpg driver; enforced below.
    DATABASE_URL: str = Field(..., min_length=1)
    DATABASE_POOL_SIZE: int = 10
    DATABASE_POOL_MAX_OVERFLOW: int = 5
    DATABASE_STATEMENT_TIMEOUT_MS: int = 15_000
    # Owner-role URL used by Alembic and admin_session(). Same scheme rules.
    ALEMBIC_DATABASE_URL: str = Field(..., min_length=1)

    # --- Auth / Sessions --------------------------------------------------
    # HS256 secrets must be at least 32 bytes of entropy (we hash a passphrase
    # upstream if the operator provides something shorter — see SECURITY.md).
    JWT_SECRET: SecretStr = Field(..., min_length=32)
    JWT_ALGORITHM: Literal["HS256", "HS384", "HS512"] = "HS256"
    ACCESS_TOKEN_TTL_SECONDS: int = 15 * 60
    REFRESH_TOKEN_TTL_SECONDS: int = 30 * 24 * 60 * 60
    ARGON2_TIME_COST: int = 3
    ARGON2_MEMORY_COST_KIB: int = 65_536  # 64 MiB
    ARGON2_PARALLELISM: int = 2

    # --- Rate limits / quotas (MVP defaults; per-org overrides later) -----
    RL_DEFAULT_PER_MINUTE: int = 120
    RL_AUTH_PER_MINUTE: int = 20
    RL_CHANNEL_PER_MINUTE: int = 600

    # --- LLM (optional at File 2; required from Milestone 2) --------------
    LLM_PROVIDER: Literal["openai", "azure-openai", "vllm"] | None = None
    LLM_API_KEY: SecretStr | None = None
    LLM_BASE_URL: str | None = None
    LLM_MODEL: str | None = None
    LLM_REQUEST_TIMEOUT_SECONDS: int = 60
    LLM_DEFAULT_MAX_OUTPUT_TOKENS: int = 1_024

    # --- Observability ----------------------------------------------------
    METRICS_ENABLED: bool = True
    LOG_JSON: bool = True

    # --- Validators -------------------------------------------------------

    @field_validator("DATABASE_URL")
    @classmethod
    def _require_async_postgres(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme != "postgresql+asyncpg":
            raise ValueError(
                "DATABASE_URL must use scheme 'postgresql+asyncpg://'. "
                "Got scheme '" + (parsed.scheme or "<empty>") + "'. "
                "Sync drivers belong in Alembic only; set ALEMBIC_DATABASE_URL separately."
            )
        if not parsed.hostname:
            raise ValueError("DATABASE_URL is missing a hostname.")
        return v

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _validate_cors(cls, v: str) -> str:
        parts = [p.strip() for p in v.split(",") if p.strip()]
        if not parts:
            raise ValueError("CORS_ORIGINS must contain at least one origin.")
        return ",".join(parts)

    @model_validator(mode="after")
    def _env_specific_rules(self) -> "Settings":
        origins = self.cors_origins_list
        if self.APP_ENV in ("staging", "prod"):
            if any(o == "*" for o in origins):
                raise ValueError(
                    "CORS_ORIGINS must not contain '*' outside dev. "
                    "Set explicit origins per environment."
                )
            if any(o.startswith("http://localhost") for o in origins):
                raise ValueError(
                    "CORS_ORIGINS must not include localhost outside dev."
                )
        return self

    # --- Derived helpers --------------------------------------------------

    @property
    def cors_origins_list(self) -> list[str]:
        return [p.strip() for p in self.CORS_ORIGINS.split(",") if p.strip()]

    @property
    def trusted_hosts_list(self) -> list[str]:
        return [p.strip() for p in self.TRUSTED_HOSTS.split(",") if p.strip()]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "prod"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so FastAPI dependencies and workers share one instance. Tests that
    need different config should set env vars before first call, or use
    ``get_settings.cache_clear()`` explicitly.
    """
    return Settings()  # type: ignore[call-arg]