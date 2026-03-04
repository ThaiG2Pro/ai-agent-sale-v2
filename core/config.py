from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Why this exists: Centralized configuration management for the AI Sales Agent.
    What it does: Loads environment variables from .env or system environment.
    """

    # Database Configuration
    DB_USER: str = "user"
    DB_PASSWORD: str = "password"
    DB_NAME: str = "ai_agent"
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432

    @property
    def database_url(self) -> str:
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

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
    POWERFUL_CHAT_MODEL: str = "ollama/deepseel-r1:1.5b"
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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
