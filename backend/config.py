"""Application settings.

Single place env vars are read - with one deliberate exception: provider API keys
are NOT declared here. They are read exclusively by
`backend/providers/secret_broker.py` (block 2), so grepping for a key name
returns exactly one module.
"""

import os
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---------- application ----------
    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Render sets $PORT itself; 7860 is only the local-dev fallback.
    port: int = 7860

    # ---------- persistence ----------
    turso_database_url: str = ""
    turso_auth_token: str = ""

    @field_validator("turso_database_url", "turso_auth_token", mode="after")
    @classmethod
    def _strip_pasted_whitespace(cls, v: str) -> str:
        # A trailing newline from a pasted secret makes the Authorization
        # header invalid and turns every request into a 500.
        parts = v.split()  # also tolerates a secret pasted twice in one value
        return parts[0] if parts else ""

    # ---------- execution contract ----------
    max_repair_attempts: int = Field(default=2, ge=0, le=10)
    default_seed: int = 42
    default_temperature: float = Field(default=0.0, ge=0.0, le=2.0)

    # ---------- engine budgets ----------
    default_budget_tokens: int = Field(default=8000, gt=0)
    default_timeout_s: int = Field(default=60, gt=0)

    # Whole-run wall clock. On expiry the run is cancelled, ended with
    # RUN_END {"ok": false, "reason": "run_timeout"} and marked failed, so a run
    # can never sit in "running" forever (a serverless function is killed at its
    # maxDuration, 300s on Vercel, leaving nothing to mark it failed). 0 = off.
    # Default follows the platform: 270s on Vercel (killed at 300s), 30 min on
    # an always-on host, where runs have no function limit.
    run_timeout_s: int = Field(
        default_factory=lambda: 270 if os.environ.get("VERCEL") else 1800, ge=0
    )
    # One event-store round trip. The Turso driver is synchronous HTTP with no
    # timeout of its own; a hung request used to hold the store lock forever.
    # 0 = off.
    db_op_timeout_s: float = Field(default=30.0, ge=0)

    # ---------- security ----------
    # No default. An unset SECRET_KEY is blank in dev and fatal in prod (below),
    # so a shipped fallback secret can never silently become the live one.
    secret_key: str = ""
    cors_origins: str = "http://localhost:5173"
    # Single Netlify (or other) frontend origin, added to cors_origin_list.
    # Blank in dev on purpose - see _require_secrets_in_prod.
    frontend_origin: str = ""
    max_upload_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    # Guards the provider-management write endpoints (setting/clearing a key at
    # runtime). Falls back to SECRET_KEY; when both are blank the endpoints are
    # open in dev and refused in prod.
    admin_token: str = ""

    # ---------- knowledge workspace (RAG) ----------
    knowledge_max_docs: int = Field(default=50, gt=0)
    knowledge_max_chunks: int = Field(default=5000, gt=0)

    # ---------- LLM provider ----------
    # Which provider backend.providers._default_models() builds specs for.
    # Only "ollama" is implemented; the field is a plain str (not a Literal) so
    # a second provider is a registry entry away, not a schema change. Blank in
    # dev (falls back to "ollama" at the point of use); required in prod so a
    # forgotten env var fails at boot, not on the first chat request.
    llm_provider: str = ""
    # Ollama Cloud or a local/tunnelled instance - same adapter either way.
    # Blank default is deliberate: defaulting this to localhost in prod would
    # have the backend silently try to reach itself instead of failing fast.
    # The adapter falls back to localhost only outside prod (see secret_broker).
    ollama_base_url: str = ""
    # Comma-separated model tags, no "ollama:" prefix (e.g. "llama3.2:3b" for a
    # local/dev instance, "qwen3-coder:480b-cloud" for Ollama Cloud - a local
    # tag and a :cloud tag are interchangeable here, no code change needed).
    ollama_models: str = "llama3.2:3b,qwen2.5:7b"
    # Per-provider model lists (comma-separated tags, no "provider:" prefix).
    # Configuration rather than code, so switching a provider's models never
    # needs an edit to routes.py. OpenRouter is empty by default: its free tier
    # is unreliable enough that it only joins the mix when asked for.
    groq_models: str = "openai/gpt-oss-120b"
    gemini_models: str = "gemini-2.5-flash"
    openai_models: str = "gpt-4o-mini"
    openrouter_models: str = ""
    # Cap on simultaneous in-flight model calls across the whole council, so a
    # wide fanout cannot blow through the provider's rate limit at once.
    max_concurrent_model_calls: int = Field(default=4, gt=0)

    # ---------- rate limiting: thresholds are configuration, never constants ----------
    rate_limit_auth: str = "5/minute"
    rate_limit_public: str = "30/minute"
    rate_limit_authed: str = "120/minute"
    # Per-IP limits on the endpoints that spend the paid model key.
    rate_chat_per_min: int = Field(default=20, gt=0)
    rate_runs_per_min: int = Field(default=5, gt=0)  # POST /runs and /compile
    rate_upload_per_min: int = Field(default=10, gt=0)

    # ---------- spend cap ----------
    # Global tokens per UTC day across all model calls; 0 = unlimited.
    daily_token_cap: int = Field(default=2_000_000, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _blank_means_unset(cls, data: Any) -> Any:
        """`cp .env.example .env` leaves every value blank.

        pydantic reads a blank line as "" rather than unset, which fails eight
        typed fields before the app can start. Drop blanks so field defaults
        apply and a fresh clone boots.
        """
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v != ""}
        return data

    @field_validator("cors_origins")
    @classmethod
    def _reject_wildcard(cls, v: str) -> str:
        if v.strip() == "*":
            raise ValueError("CORS_ORIGINS must be an explicit allow-list, not '*'")
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    @model_validator(mode="after")
    def _require_secrets_in_prod(self) -> "Settings":
        """Only SECRET_KEY is truly required in prod.

        Turso is optional (EventStore falls back to a local sqlite file and
        logs a warning that it is lost on an ephemeral disk). Provider keys
        are read by secret_broker and may also be pasted at runtime, so they are
        not checked here. FRONTEND_ORIGIN is only needed when the frontend is
        hosted on a different origin than the API.
        """
        if self.is_prod:
            if not self.secret_key:
                raise ValueError("missing required prod settings: SECRET_KEY")
        return self


settings = Settings()
