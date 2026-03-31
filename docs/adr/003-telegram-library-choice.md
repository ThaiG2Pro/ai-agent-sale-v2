# ADR 003: Telegram Library Choice (httpx Direct API vs python-telegram-bot)

**Status**: Accepted  
**Date**: 2026-03-30  
**Context**: Feature 006 - Telegram Integration & Production Docker  
**Constitution Article**: Article XI (Engineering Maturity)

---

## Context and Problem Statement

For implementing the Telegram bot webhook integration in our AI Sales Agent, we need to decide between:
1. Using the popular `python-telegram-bot` library (wrapper with high-level abstractions)
2. Using direct HTTP calls via `httpx` to the Telegram Bot API

This decision impacts Docker image size, type safety, control over async operations, and constitutional compliance.

---

## Decision Drivers

1. **Constitutional Article II (Simplicity)**: "Avoid unnecessary abstractions that obscure core logic"
2. **Constitutional Article X (Cost Management)**: Minimize dependencies and Docker image size (<300MB target)
3. **Type Safety**: Maintain Pydantic-based type checking throughout the stack
4. **Async Control**: Full control over async I/O without framework-imposed event loop handling
5. **Learning Curve**: Balance simplicity vs developer familiarity with Telegram concepts
6. **Maintenance**: Long-term dependency management and update burden

---

## Considered Options

### Option 1: python-telegram-bot Library

**Pros**:
- High-level API with built-in webhook handling
- Extensive documentation and community examples
- Built-in rate limiting and retry logic
- Message builders and helper functions
- Type hints included

**Cons**:
- Adds 15-20MB to Docker image size (base library + dependencies)
- Introduces framework-specific async patterns (PTB's `Application` and context managers)
- Wrapper layer obscures actual HTTP calls (harder to debug/trace)
- Additional dependency to maintain and update
- May conflict with our existing async architecture patterns
- **Violates Article II**: Adds abstraction layer over simple HTTP POST/GET

**Impact on Success Criteria**:
- SC-006: Increases Docker image size by ~15-20MB (still under 300MB, but uses 5-7% of budget)
- SC-002: Webhook acknowledgment controlled by framework (less visibility)

---

### Option 2: Direct httpx API Calls (CHOSEN)

**Pros**:
- **Zero new dependencies** (httpx already in stack for other API calls)
- **Maintains type safety** with Pydantic models for Telegram payloads
- **Full async control** - no framework-imposed event loop management
- **Transparent HTTP operations** - easier to trace, debug, and monitor
- **Smaller image size** - saves 15-20MB (6% of 300MB budget)
- **Aligns with Article II** - no unnecessary abstraction
- **Consistent with existing patterns** - LiteLLM and other integrations use direct HTTP

**Cons**:
- Must implement webhook payload parsing manually (mitigated by Pydantic)
- Must handle retry logic manually (mitigated by httpx built-in retry + LiteLLM patterns)
- Need to reference Telegram Bot API docs directly (acceptable learning curve)
- No built-in message builders (simple string formatting sufficient for MVP)

**Implementation Approach**:
```python
# Pydantic models for type safety
class TelegramMessage(BaseModel):
    chat_id: int
    text: str
    reply_markup: Optional[InlineKeyboardMarkup] = None

# Direct httpx call
async def send_telegram_message(bot_token: str, message: TelegramMessage):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json=message.model_dump(exclude_none=True),
            timeout=10.0
        )
        response.raise_for_status()
        return response.json()
```

**Impact on Success Criteria**:
- SC-006: ✅ Saves 15-20MB Docker image size
- SC-002: ✅ Full control over webhook acknowledgment timing
- FR-002: ✅ Direct access to headers for signature verification

---

## Decision

**CHOSEN: Option 2 - Direct httpx API Calls**

### Rationale

1. **Constitutional Alignment**:
   - Article II (Simplicity): Direct HTTP calls are simpler than learning framework abstractions
   - Article X (Cost Management): Zero incremental cost for new dependency
   - Article VI (Type Safety): Pydantic models provide same type safety as library

2. **Technical Merit**:
   - httpx is already in our dependency tree (zero marginal cost)
   - Telegram Bot API is RESTful and well-documented (low learning curve)
   - Direct HTTP gives us full observability and tracing
   - Async control remains with FastAPI (no competing event loops)

3. **Practical Impact**:
   - Saves 15-20MB in Docker image (6% of 300MB budget freed for future features)
   - Reduces dependency update surface area (one less library to monitor for CVEs)
   - Consistent with our existing HTTP integration patterns (LiteLLM, external tools)

4. **Risk Mitigation**:
   - **Manual parsing**: Pydantic models provide same safety as library classes
   - **Retry logic**: httpx supports retries; we can add exponential backoff if needed
   - **API changes**: Telegram Bot API is stable (v6.0+ has been consistent for years)
   - **Developer familiarity**: Team already uses httpx; Telegram API docs are comprehensive

---

## Consequences

### Positive

- ✅ **15-20MB Docker image size savings** (toward SC-006: <300MB)
- ✅ **Zero new dependencies** to audit/update
- ✅ **Full async control** - no framework event loop conflicts
- ✅ **Transparent operations** - every HTTP call visible in traces
- ✅ **Constitutional compliance** - Article II (simplicity) and Article X (cost) satisfied

### Negative

- ❌ **Manual implementation** of:
  - Webhook payload parsing (mitigated: Pydantic handles this in ~20 lines)
  - Message sending helper (mitigated: simple httpx POST wrapper, ~30 lines)
  - Inline keyboard builders (mitigated: dict literals for MVP, can extract to utils later)
  
- ❌ **No built-in rate limiting** (acceptable: FastAPI handles concurrency; Telegram's 30 msg/sec limit unlikely to hit in SME context)

### Neutral

- 📘 **Documentation responsibility**: Must document Telegram API endpoints we use
- 📘 **Learning curve**: Developers reference Telegram Bot API docs instead of library docs

---

## Implementation Notes

### Core Components Required

1. **Pydantic Models** (`core/telegram/models.py`):
   - `TelegramUpdate`, `TelegramMessage`, `TelegramChat`, `TelegramUser`
   - `InlineKeyboardButton`, `InlineKeyboardMarkup` (for retry UI)

2. **HTTP Service** (`services/telegram_service.py`):
   - `send_message()` - POST to `/sendMessage`
   - `answer_callback_query()` - POST to `/answerCallbackQuery` (for retry buttons)
   - Error handling: httpx.HTTPStatusError → FastAPI HTTPException

3. **Webhook Handler** (`api/webhooks/telegram.py`):
   - Parse incoming JSON → `TelegramUpdate` Pydantic model
   - Validate signature (dependency injection)
   - Return 200 OK immediately (FastAPI BackgroundTasks for processing)

### Testing Strategy

- **Contract Tests**: Mock httpx responses for Telegram API
- **Integration Tests**: Use Telegram Bot API test environment
- **Type Safety**: mypy validates Pydantic models end-to-end

---

## Alternatives Considered Details

### Why not aiohttp?
- httpx is already in stack; aiohttp would add another async HTTP client
- httpx has better type hints and modern async API

### Why not requests?
- Synchronous only - violates Article V (async I/O)
- Would block FastAPI event loop on Telegram API calls

### Why not telepot/aiogram?
- Same abstraction concerns as python-telegram-bot
- Smaller communities (higher maintenance risk)

---

## References

- [Telegram Bot API Documentation](https://core.telegram.org/bots/api)
- [httpx Async Client](https://www.python-httpx.org/async/)
- Constitution Article II: Simplicity and Anti-Abstraction
- Constitution Article X: Cost Management and Model Economics
- Feature 006 Research: `specs/006-telegram-docker/research.md` (Decision 1)

---

## Review and Approval

- **Proposed by**: AI Agent (speckit.plan workflow)
- **Reviewed by**: (To be filled during implementation)
- **Approved by**: (To be filled during implementation)
- **Implementation Date**: 2026-03-30 (Week 6)

---

## Amendments

None yet. This ADR may be amended if:
1. Telegram API significantly changes (breaking changes)
2. Image size pressure requires removing httpx entirely (extremely unlikely)
3. Constitutional articles change to favor higher-level abstractions (unlikely)
