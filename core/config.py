from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Why this exists: Centralized configuration management for the AI Sales Agent.
    What it does: Loads environment variables from .env or system environment.
    """

    # Deployment environment — gates fail-fast secret validation at startup.
    # "production" refuses to boot with default secrets; other envs only warn.
    ENV: Literal["dev", "staging", "production"] = "dev"

    # Database Configuration
    DB_USER: str = "user"
    DB_PASSWORD: str = "password"
    DB_NAME: str = "ai_agent"
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    # Pool sizing: tune for concurrent customer sessions.
    # Dev default: small (5+10). Prod: set DB_POOL_SIZE=20, DB_MAX_OVERFLOW=40 via env.
    DB_POOL_SIZE: int = Field(default=10, ge=1, le=100)
    DB_MAX_OVERFLOW: int = Field(default=20, ge=0, le=200)

    @property
    def database_url(self) -> str:
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @property
    def database_url_psycopg(self) -> str:
        """Why this exists: psycopg3 uses plain postgresql:// prefix (no driver name needed)."""
        return f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    # Admin Configuration
    X_ADMIN_KEY: str = "dev-secret-key"

    # AI Configuration
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    # Model tiers:
    #   LIGHT  — fast, cheap (qwen3:0.6b): normalization, keyword extraction
    #   CHAT   — general (qwen3-4b-q6):    metadata enrichment, RAG generation
    #   POWERFUL — deep reasoning (deepseek-r1): escalation, complex queries
    LIGHT_CHAT_MODEL: str = "ollama/qwen3:0.6b"
    CHAT_MODEL: str = "ollama/qwen3-1.7b"
    POWERFUL_CHAT_MODEL: str = "ollama/deepseek-r1:1.5b"
    EMBED_MODEL: str = "ollama/bge-m3"
    EMBED_DIMENSION: int = 1024  # Standard for bge-m3 / small

    # Logging & Observability
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    LOGFIRE_TOKEN: str | None = None
    OTLP_ENDPOINT: str = "http://localhost:4317"
    OTEL_SERVICE_NAME: str = "ai-sales-agent"

    # Week 3: Agent Configuration
    LAYER1_CONFIDENCE_THRESHOLD: float = 0.45  # RAG layer threshold (must be ≤ 0.6)
    RERANKER_ENABLED: bool = False
    AGENT_MAX_TURNS: int = 5
    AGENT_CONFIDENCE_THRESHOLD: float = 0.70  # Agent response threshold (must be > L1)
    AGENT_ALPHA: float = 0.7
    # Escalation model alias (used by escalation_node for COMPLAINT/NEGOTIATION)
    PREMIUM_MODEL: str = "premium-chat"
    # agentic-rag-retry-loop (ticket 2026): bounded self-evaluate -> rewrite -> retry
    # loop around retrieval. 0 = kill switch (exact static single-pass behavior).
    RAG_RETRY_MAX_ATTEMPTS: int = Field(default=1, ge=0, le=2)

    # Week 4: HITL Configuration
    HITL_TIMEOUT_WARN_MIN: int = Field(default=30, ge=1)
    HITL_TIMEOUT_ESCALATE_MIN: int = Field(default=60, ge=2)
    HITL_MAX_ESCALATION_COUNT: int = Field(default=2, ge=1, le=5)
    HITL_PRICE_DELTA_THRESHOLD: float = Field(default=0.05, gt=0.0, le=0.5)
    HITL_CLASSIFY_CONFIDENCE_THRESHOLD: float = Field(default=0.6, ge=0.0, le=1.0)
    HITL_COST_THRESHOLD_TOKENS: int = Field(default=8000, ge=100)
    SUPPORT_CONTACT_LINK: str = "https://t.me/support_bot"

    # Week 5: Memory & Persistence Configuration
    MEMORY_SUMMARY_THRESHOLD: int = Field(default=20, ge=1)
    MEMORY_RELEVANCE_THRESHOLD: float = Field(default=0.75, ge=0.0, le=1.0)
    MEMORY_TOP_K: int = Field(default=3, ge=1, le=100)
    CHECKPOINT_SIZE_WARN_BYTES: int = Field(default=1_048_576, ge=65536)  # 1MB default
    CHECKPOINT_RETENTION_DAYS: int = Field(default=90, ge=1, le=365)
    MEMORY_MERGE_PLATFORMS: bool = True  # One customer across Telegram + Web uses merged memory
    INTENT_LOCK_MAX_RETRIES: int = Field(default=3, ge=1, le=10)
    INTENT_LOCK_RETRY_BACKOFF_MS: list[int] = [50, 100, 200]  # Exponential backoff sequence

    # Week 6: Telegram Bot Configuration
    TELEGRAM_BOT_TOKEN: str = Field(
        default="your_bot_token_from_botfather",
        min_length=30,
        description="Telegram bot token from @BotFather",
    )
    TELEGRAM_WEBHOOK_SECRET: str = Field(
        default="",
        min_length=20,
        description="Secret token for webhook verification (min 20 chars)",
    )
    TELEGRAM_WEBHOOK_URL: str = Field(
        default="https://your-domain.com/webhooks/telegram",
        description="Public URL where Telegram sends webhook updates",
    )

    # Week 6: Tool Timeout Configuration
    TOOL_TIMEOUT_DEFAULT: int = Field(
        default=5,
        ge=1,
        le=60,
        description="Default timeout in seconds for tool calls",
    )
    TOOL_TIMEOUT_INVENTORY_CHECK: int = Field(
        default=5,
        ge=1,
        le=60,
        description="Timeout for inventory check tool",
    )
    TOOL_TIMEOUT_ORDER_PROCESSING: int = Field(
        default=10,
        ge=1,
        le=120,
        description="Timeout for order processing tool",
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# Insecure placeholder values shipped as dev defaults. Startup (api/main.py::lifespan)
# refuses to boot in ENV=production while any of these are still in effect.
INSECURE_DEFAULT_SECRETS: dict[str, str] = {
    "DB_PASSWORD": "password",
    "X_ADMIN_KEY": "dev-secret-key",
}


def find_insecure_default_secrets(s: Settings) -> list[str]:
    """Return names of secret settings still carrying their insecure default."""
    return [
        name for name, default in INSECURE_DEFAULT_SECRETS.items() if getattr(s, name) == default
    ]


settings = Settings()
