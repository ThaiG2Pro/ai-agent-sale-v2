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
    LLAMA_SERVER_BASE_URL: str = "http://llama-server:8080/v1"
    OLLAMA_BASE_URL: str = "http://host.docker.internal:8080"
    OLLAMA_EMBED_BASE_URL: str = "http://ai-agent-ollama:11434"
    # Ollama defaults num_ctx to ~2k-4k and SILENTLY truncates anything beyond
    # it — for a RAG prompt (system + chunks + history) that means retrieval
    # succeeds but the model never sees the chunks (ADR-006 mandatory
    # mitigation). Applied to ollama/* models only; cloud providers manage
    # their own context windows. 8192 covers the assembled RAG prompt for the
    # default top-k; raise alongside RAG_TOP_K/history growth.
    OLLAMA_NUM_CTX: int = 8192
    # Model tiers — any LiteLLM model string works (cloud fast-path):
    #   e.g. CHAT_MODEL=gemini/gemini-2.5-flash (needs GEMINI_API_KEY in env)
    #        CHAT_MODEL=gpt-4o-mini             (needs OPENAI_API_KEY in env)
    # LiteLLM reads provider API keys directly from the environment.
    # OLLAMA_BASE_URL is only attached to models with the "ollama/" prefix.
    #   LIGHT  — fast, cheap (qwen3:0.6b): normalization, keyword extraction
    #   CHAT   — general (qwen3-4b-q6):    metadata enrichment, RAG generation
    #   POWERFUL — deep reasoning (deepseek-r1): escalation, complex queries
    LIGHT_CHAT_MODEL: str = "hosted_vllm/economy-chat"
    CHAT_MODEL: str = "hosted_vllm/economy-chat"
    POWERFUL_CHAT_MODEL: str = "hosted_vllm/economy-chat"
    # ⚠️ Embedding dimension constraint: pgvector columns are Vector(1024) (bge-m3).
    # If you switch EMBED_MODEL, the new model MUST produce 1024-dim vectors
    # (e.g. OpenAI text-embedding-3-large with dimensions=1024). Recommended
    # default: chat = cloud, embed = local Ollama bge-m3.
    EMBED_MODEL: str = "ollama/bge-m3"
    EMBED_DIMENSION: int = 1024  # Standard for bge-m3 / small
    # Cloud provider keys — declared here so a plain `.env` works OUTSIDE Docker
    # too (pydantic-settings only populates its own fields; LiteLLM reads keys
    # from os.environ, so core/ai_config.py exports these at router init).
    GROQ_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = "sk-no-key-required"
    ANTHROPIC_API_KEY: str | None = None
    OPENROUTER_API_KEY: str | None = None
    DEEPSEEK_API_KEY: str | None = None
    COHERE_API_KEY: str | None = None
    MISTRAL_API_KEY: str | None = None

    # Semantic cache TTL (seconds). Entries older than this are ignored by
    # L1/L2 lookups so price/stock changes stop serving stale answers.
    # 0 disables the TTL filter (entries never expire).
    CACHE_TTL_SECONDS: int = Field(default=3600, ge=0)

    # Logging & Observability
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    LOGFIRE_TOKEN: str | None = None
    OTLP_ENDPOINT: str = "http://localhost:4317"
    OTEL_SERVICE_NAME: str = "ai-sales-agent"
    # Phoenix groups traces by the `openinference.project.name` resource
    # attribute — without it everything lands in the "default" project.
    PHOENIX_PROJECT_NAME: str = "ai-sales-agent"
    # WP-V3-2: per-node OTel tracing in LangGraph graph execution
    OTEL_NODE_SPANS_ENABLED: bool = True

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
    # WP-V2-1: groundedness self-check on generated answers. False = kill switch
    # (byte-identical pre-check behavior). On verdict fail the answer is
    # regenerated with a stricter prompt up to GROUNDEDNESS_MAX_REGEN times,
    # then declined politely. answerable=False declines immediately (regen
    # cannot conjure an out-of-catalog product).
    GROUNDEDNESS_CHECK_ENABLED: bool = True
    GROUNDEDNESS_MAX_REGEN: int = Field(default=1, ge=0, le=2)
    # WP-V2-1: cascade verification for intent escalations (COMPLAINT/
    # NEGOTIATION). True = answer on economy tier first, escalate to
    # PREMIUM_MODEL only when the groundedness verdict fails. False = old
    # behavior (premium directly).
    CASCADE_VERIFY_ENABLED: bool = True
    # WP-V2-3: borderline queries (passed L1 but fused < AGENT_CONFIDENCE_THRESHOLD)
    # get ONE clarifying question instead of a decline. False = kill switch
    # (old decline behavior). Max 1 clarify per original query is hardcoded —
    # the second borderline pass declines as before.
    CLARIFY_ENABLED: bool = True
    # WP-V3-4: similarity gap threshold for clarify node eligibility
    CLARIFY_SIMILARITY_GAP_MAX: float = Field(default=0.05, ge=0.0, le=1.0)
    # v3-0 P1 (T03/T06/T08): multi-turn intent tracking combo. Router reads the
    # last 3 turns + previous_intent from checkpointed state, applies a pure-
    # Python transition table (sticky intent, hysteresis <0.5, strange jumps
    # need >=0.7), make_initial_state stops wiping intent/secondary_intents,
    # hesitation keywords ("thôi", "khoan đã"...) flag a probable 180° flip,
    # multi-intent priority CANCEL > COMPLAINT > NEGOTIATION > ORDER > INFO,
    # ambiguous ORDER_PLACEMENT clarifies instead of auto-selecting the top
    # citation, and queue_consumer post-validates LLM batch labels (F2 guard).
    # False = kill switch (stateless per-turn router, intent wiped every
    # invoke — exact pre-v3-0 behavior).
    INTENT_TRACKING_V3_ENABLED: bool = True
    # v3-0 P2 (T05/T06/T07/T13): order/HITL axis. Draft orders live in the
    # orders table (status lifecycle draft → pending_review → confirmed /
    # cancelled / superseded / expired, supersedes_id chain); Tier 1 orders
    # auto-proceed when ALL conditions hold (value < TIER1_MAX, unique product,
    # phone+address present, stock sufficient); NEGOTIATION creates a draft at
    # the ORIGINAL price + structured note and pauses for the human to decide
    # the price (agent never counter-offers); COMPLAINT collects 3 facts in
    # <=2 turns then always hands off; in-catalog ambiguity clarifies twice
    # then hands off, out-of-catalog declines politely without handoff;
    # HITL pauses build a 4-part handoff package and notify the Telegram
    # admin chat with inline review buttons; admin reject/counter reasons are
    # ALWAYS pushed to the customer (O27). False = kill switch: every order
    # pauses at Tier 2, no draft rows, no admin Telegram notify — exact
    # pre-v3-0-P2 behavior.
    ORDER_HITL_V3_ENABLED: bool = True
    # VND. Tier 1 auto-proceed ceiling (T07: default 10 triệu). Orders at or
    # above this value — or with unknown value — never auto-proceed.
    HITL_TIER1_MAX_ORDER_VALUE: float = Field(default=10_000_000.0, gt=0.0)
    # T05: soft cap on active drafts per customer (creating one more
    # supersedes the oldest active draft) and draft TTL (lazy expiry on read).
    DRAFT_ORDER_CAP_PER_CUSTOMER: int = Field(default=3, ge=1, le=20)
    DRAFT_ORDER_TTL_HOURS: int = Field(default=24, ge=1)
    # T06/T07: clarify quota for in-catalog ambiguity before handing off to a
    # human (out-of-catalog queries decline instead — they never hand off).
    CLARIFY_MAX_ROUNDS: int = Field(default=2, ge=1, le=5)
    # T06: complaint fact-collection turn quota before the mandatory handoff.
    COMPLAINT_MAX_COLLECT_TURNS: int = Field(default=2, ge=1, le=5)
    # T13/T12: minutes promised in the customer-facing holding message
    # ("phản hồi trong ~X phút") — matches the wait-too-long alert threshold.
    HITL_WAIT_ALERT_MINUTES: int = Field(default=10, ge=1)
    # T13: Telegram chat that receives the handoff package + review buttons.
    # Empty = admin notify disabled (REST /hitl/review remains the only path).
    TELEGRAM_ADMIN_CHAT_ID: str = ""

    # ── v3-0 P3 (T09/T12): resilience + observability ──────────────────────
    # Master kill switch for the whole P3 group: fallback ladder, timeout
    # budget, 429 cooldown, backpressure semaphore, token-budget gate and
    # degrade-driven holding. False = pre-P3 behavior (single manual fallback
    # to economy-chat inside AIGateway.complete).
    RESILIENCE_V3_ENABLED: bool = True
    # T09 timeout budget: per-call caps (cloud vs local) and the whole-turn
    # v3-0 P3 (T09): per-call timeout budget. Local models get 90s (CPU inference under load),
    # cloud models get 15s (network hop only).
    LLM_TIMEOUT_LOCAL_S: float = Field(default=90.0, gt=0.0)
    LLM_TIMEOUT_CLOUD_S: float = Field(default=15.0, gt=0.0)
    TURN_BUDGET_S: float = Field(default=30.0, gt=0.0)
    # T09: free-tier 429s last hours — never retry them; cool the rung down
    # and jump to the next one immediately.
    LLM_429_COOLDOWN_S: float = Field(default=600.0, gt=0.0)
    # T09 backpressure: in-process cap on concurrent LLM turns. Overflow goes
    # to queued_messages + holding instead of piling onto the providers.
    LLM_MAX_CONCURRENT_TURNS: int = Field(default=8, ge=1, le=64)
    # T09 token-budget gate (app-side — Groq exposes no daily counter): one
    # Postgres row per (day UTC, model); at DEGRADE_RATIO of the daily cap the
    # ladder proactively skips the premium rung.
    TOKEN_BUDGET_DAILY_PREMIUM: int = Field(default=100_000, ge=0)
    TOKEN_BUDGET_DEGRADE_RATIO: float = Field(default=0.9, gt=0.0, le=1.0)
    # T12 alerts on the timeout_scheduler loop (Telegram admin chat):
    # queue depth > N, oldest pending case waiting > HITL_WAIT_ALERT_MINUTES,
    # and degraded turns since the last tick.
    ALERT_QUEUE_DEPTH: int = Field(default=5, ge=1)

    # ── v3-0 P4 (T08/T11): efficiency cuts + tool loop ──────────────────────
    # 4.2: in-graph SMALLTALK fast-path (keyword branch in router_node +
    # template branch in answer_node). Conservative gate: full-match, <=4
    # words, no product/price/order token. False = router LLM classifies
    # greetings like any other turn (pre-P4 behavior).
    SMALLTALK_FASTPATH_ENABLED: bool = True
    # 4.3: keyword pre-classify whitelist for INFO_QUERY/PRICING/AVAILABILITY
    # skips the router LLM call (cache stays in-graph). Requires the semantic
    # cache invalidation hook on price/stock updates (services/rag).
    PRECLASSIFY_WHITELIST_ENABLED: bool = True
    # 4.4: skip memory_retrieval_node ONLY on cache-hit or similarity >=
    # MEMORY_SKIP_SIMILARITY; never for FOLLOW_UP or time-referenced queries.
    MEMORY_SKIP_ENABLED: bool = True
    MEMORY_SKIP_SIMILARITY: float = Field(default=0.85, ge=0.0, le=1.0)
    # 4.1: tool-calling loop on the premium tier for ambiguous advisory turns
    # only (~20%), guardrails G1-G8 (read-only tools, <=2 hops, <=3 cloud
    # calls/turn, Pydantic-validated args + 1 retry, 429 -> local single-shot).
    # This is the FIRST feature the degrade ladder turns off (3.1).
    TOOL_LOOP_ENABLED: bool = True
    TOOL_LOOP_MAX_HOPS: int = Field(default=2, ge=1, le=4)
    TOOL_LOOP_MAX_CALLS: int = Field(default=3, ge=1, le=6)
    # WP-V2-3: LLM query decomposition for declined multi-intent/comparison
    # queries in the graph retrieval node. False = regex COMPARISON split only
    # (pre-V2-3 behavior). On LLM error the regex split remains the fallback.
    QUERY_DECOMPOSITION_ENABLED: bool = True
    # WP-V2-4: episodic memory — per-turn event log so time-referenced queries
    # ("hôm qua", "lần trước") recall past consultations. False = kill switch
    # (no writes, no retrieval — semantic memory only, pre-V2-4 behavior).
    EPISODIC_MEMORY_ENABLED: bool = True
    EPISODIC_RECENT_LIMIT: int = Field(default=5, ge=1, le=50)
    # WP-V2-4: risk-score HITL tiers (anti approval-fatigue). False = kill
    # switch (pre-V2-4 binary triggers: ORDER_PLACEMENT OR low confidence).
    # risk = W_CONF*(1-confidence) + W_VALUE*order_value_norm + W_HISTORY*history
    # Tier1 (risk < TIER1): auto-proceed. Tier2: interrupt (current behavior).
    # Tier3 (risk >= TIER3): straight to support queue.
    # SAFETY INVARIANT (hardcoded in hitl_guard): ORDER_PLACEMENT with order
    # value > HIGH_VALUE threshold — or with MISSING order value — is always
    # >= Tier2. Tuning these weights can never auto-approve a high-value order.
    RISK_HITL_ENABLED: bool = True
    HITL_RISK_W_CONF: float = Field(default=0.4, ge=0.0, le=1.0)
    HITL_RISK_W_VALUE: float = Field(default=0.4, ge=0.0, le=1.0)
    HITL_RISK_W_HISTORY: float = Field(default=0.2, ge=0.0, le=1.0)
    HITL_RISK_TIER1_THRESHOLD: float = Field(default=0.35, gt=0.0, lt=1.0)
    HITL_RISK_TIER3_THRESHOLD: float = Field(default=0.75, gt=0.0, le=1.0)
    # VND. Order value / NORM_CAP (clamped to 1.0) is the order_value_norm term.
    HITL_ORDER_VALUE_NORM_CAP: float = Field(default=20_000_000.0, gt=0.0)
    # VND. Orders above this NEVER auto-approve regardless of risk score.
    HITL_HIGH_VALUE_ORDER_THRESHOLD: float = Field(default=5_000_000.0, gt=0.0)
    # WP-V2-5: budget guard. DAILY_COST_LIMIT_USD > 0 → when today's summed
    # model_traces cost reaches the limit, LLM calls are force-downgraded to
    # light-chat (never blocked — availability first). 0 = off (default:
    # local Ollama cost is 0, a limit only matters with cloud keys).
    DAILY_COST_LIMIT_USD: float = Field(default=0.0, ge=0.0)
    # WP-V2-5: per-customer daily LLM-call cap (anti single-customer spam
    # burning the SME's cloud budget). Over cap → polite come-back-later
    # message, no LLM call. 0 = off (default).
    CUSTOMER_DAILY_MSG_CAP: int = Field(default=0, ge=0)
    # WP-V2-5: route cheap intents (SMALLTALK) to LIGHT_CHAT_MODEL instead of
    # economy-chat. False = kill switch (pre-V2-5 routing).
    CHEAP_INTENT_LIGHT_ROUTING: bool = True

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
    MEMORY_RELEVANCE_THRESHOLD: float = Field(default=0.55, ge=0.0, le=1.0)
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

    # env_ignore_empty: docker-compose pass-through vars (`${VAR:-}`) arrive as
    # empty strings — treat them as unset so code defaults still apply.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)


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
