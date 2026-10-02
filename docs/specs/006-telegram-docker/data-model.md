# Data Model: Telegram Integration & Production Docker

**Branch**: `006-telegram-docker` | **Date**: 2026-03-30  
**Purpose**: Define data entities and relationships for Telegram webhook integration

## Overview

This feature introduces minimal new database entities focused on deduplication and audit logging. The primary design principle is to reuse existing LangGraph state persistence infrastructure and avoid duplication.

---

## New Entities

### 1. TelegramUpdate

**Purpose**: Track incoming Telegram webhook updates to ensure exactly-once processing during retry storms.

**Table Name**: `telegram_updates`

**Schema**:
```sql
CREATE TABLE telegram_updates (
    id                BIGSERIAL PRIMARY KEY,
    update_id         BIGINT NOT NULL UNIQUE,  -- Telegram's unique update identifier
    chat_id           BIGINT NOT NULL,          -- Telegram chat/user ID
    message_id        BIGINT,                   -- Telegram message ID (null for non-message updates)
    message_type      VARCHAR(50),              -- e.g., 'text', 'callback_query', 'inline_query'
    processed_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    created_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    raw_payload       JSONB,                    -- Full Telegram update payload (for debugging)
    
    INDEX idx_telegram_updates_chat_id (chat_id),
    INDEX idx_telegram_updates_created_at (created_at)
);
```

**Fields Explained**:
- `update_id`: Telegram's monotonically increasing unique ID (UNIQUE constraint = idempotency)
- `chat_id`: Links to conversation (can join with LangGraph conversation state if needed)
- `message_type`: Quick filter for analytics (what types of messages are we receiving?)
- `raw_payload`: Full JSON for debugging/audit (helps diagnose webhook issues)

**Validation Rules**:
- `update_id` MUST be unique (database enforces via UNIQUE constraint)
- `chat_id` MUST be positive integer (Telegram's format)
- `processed_at` auto-set on insert (never updated)
- `created_at` immutable (audit trail)

**Relationships**:
- Loosely couples with LangGraph conversation state via `chat_id`
- No foreign keys (Telegram data is ephemeral, LangGraph state is persistent)

**Retention Policy**:
- Keep records for 7 days (sufficient for Telegram's retry window ~24h)
- Periodic cleanup job: `DELETE FROM telegram_updates WHERE created_at < NOW() - INTERVAL '7 days'`

---

### 2. ToolExecutionLog (Optional Enhancement)

**Purpose**: Track tool timeout events for monitoring and optimization.

**Table Name**: `tool_execution_logs`

**Schema**:
```sql
CREATE TABLE tool_execution_logs (
    id                BIGSERIAL PRIMARY KEY,
    tool_name         VARCHAR(100) NOT NULL,
    execution_id      UUID NOT NULL,            -- Links to LangGraph execution
    started_at        TIMESTAMP WITH TIME ZONE NOT NULL,
    completed_at      TIMESTAMP WITH TIME ZONE,
    timeout_seconds   REAL,
    timed_out         BOOLEAN DEFAULT FALSE,
    error_message     TEXT,
    metadata          JSONB,                    -- Tool-specific context
    
    INDEX idx_tool_execution_logs_tool_name (tool_name),
    INDEX idx_tool_execution_logs_started_at (started_at),
    INDEX idx_tool_execution_logs_timed_out (timed_out)
);
```

**Note**: This table is **optional** and should only be added if monitoring dashboard (Week 7) requires it. For Phase 1, structured logging to stdout may be sufficient.

---

## Existing Entities (Modified)

### 3. Environment Configuration (No DB Changes)

**Purpose**: Manage Telegram bot credentials and timeout settings.

**Storage**: `.env` file (not database)

**Required Variables**:
```bash
# Telegram Configuration
TELEGRAM_BOT_TOKEN=<bot_token_from_botfather>
TELEGRAM_WEBHOOK_SECRET=<random_secret_for_signature_verification>
TELEGRAM_WEBHOOK_URL=https://your-domain.com/webhooks/telegram

# Tool Timeout Configuration
TOOL_TIMEOUT_DEFAULT=5.0
TOOL_TIMEOUT_INVENTORY_CHECK=5.0
TOOL_TIMEOUT_ORDER_PROCESSING=10.0

# Docker/Database Configuration (existing)
DATABASE_URL=postgresql+asyncpg://user:password@db:5432/ai_agent
DATABASE_POOL_SIZE=20
DATABASE_MAX_OVERFLOW=0
```

**Validation** (at application startup):
```python
from pydantic_settings import BaseSettings
from pydantic import Field, validator

class Settings(BaseSettings):
    telegram_bot_token: str = Field(..., min_length=30)
    telegram_webhook_secret: str = Field(..., min_length=20)
    telegram_webhook_url: str = Field(..., regex=r'^https://.*')
    
    tool_timeout_default: float = Field(default=5.0, ge=1.0, le=30.0)
    tool_timeout_inventory_check: float = Field(default=5.0, ge=1.0, le=30.0)
    tool_timeout_order_processing: float = Field(default=10.0, ge=1.0, le=30.0)
    
    @validator('telegram_webhook_secret')
    def secret_strength(cls, v):
        if len(v) < 20:
            raise ValueError('Webhook secret too short (min 20 chars)')
        return v
    
    class Config:
        env_file = '.env'
```

---

## State Management (Reusing LangGraph)

### 4. Conversation State (No Changes)

**Storage**: LangGraph's `AsyncPostgresSaver` (existing from Week 5)

**What's Stored**:
- Conversation history (messages back and forth)
- Agent state (current step in graph)
- Tool results (inventory check results, etc.)
- Interrupt state (for HITL approval)

**Telegram Integration Points**:
```python
# Link Telegram chat_id to LangGraph thread_id
thread_id = f"telegram_{chat_id}"

# Resume conversation from checkpoint
config = {"configurable": {"thread_id": thread_id}}
state = await graph.aget_state(config)
```

**Why No Changes Needed**:
- LangGraph already persists conversation state
- `chat_id` serves as unique identifier (maps to thread_id)
- No need to duplicate conversation data in separate table

---

## Data Flow Diagram

```
Telegram Update (webhook)
    ↓
telegram_updates table (deduplication check)
    ↓ [if new]
Process Message
    ↓
LangGraph Conversation State (existing)
    ↓
Tool Calls (with timeout)
    ↓ [optional]
tool_execution_logs table (monitoring)
    ↓
Response to Telegram
```

---

## Migration Strategy

### Alembic Migration Script

```python
"""Add Telegram webhook deduplication table

Revision ID: 006_telegram_webhook
Revises: 005_async_persistence_memory
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '006_telegram_webhook'
down_revision = '005_async_persistence_memory'
branch_labels = None
depends_on = None

def upgrade():
    # Create telegram_updates table
    op.create_table(
        'telegram_updates',
        sa.Column('id', sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column('update_id', sa.BigInteger(), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('message_id', sa.BigInteger(), nullable=True),
        sa.Column('message_type', sa.String(50), nullable=True),
        sa.Column('processed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('raw_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('update_id', name='uq_telegram_updates_update_id')
    )
    
    # Create indexes
    op.create_index('idx_telegram_updates_chat_id', 'telegram_updates', ['chat_id'])
    op.create_index('idx_telegram_updates_created_at', 'telegram_updates', ['created_at'])

def downgrade():
    op.drop_index('idx_telegram_updates_created_at')
    op.drop_index('idx_telegram_updates_chat_id')
    op.drop_table('telegram_updates')
```

### SQLAlchemy Model

```python
# models/telegram_updates.py
from datetime import datetime
from sqlalchemy import BigInteger, String, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from core.database import Base

class TelegramUpdate(Base):
    """
    Tracks incoming Telegram webhook updates for idempotent processing.
    
    The unique constraint on update_id ensures duplicate webhooks
    (from Telegram retry storms) are rejected at the database level.
    """
    __tablename__ = "telegram_updates"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    update_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        nullable=False
    )
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    
    __table_args__ = (
        Index("idx_telegram_updates_chat_id", "chat_id"),
        Index("idx_telegram_updates_created_at", "created_at"),
    )
    
    def __repr__(self) -> str:
        return f"<TelegramUpdate(update_id={self.update_id}, chat_id={self.chat_id})>"
```

---

## Query Patterns

### Check for Duplicate Update

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from models.telegram_updates import TelegramUpdate

async def is_duplicate_update(
    db: AsyncSession,
    update_id: int
) -> bool:
    """
    Check if this update_id has already been processed.
    
    Returns True if duplicate (already exists), False if new.
    """
    stmt = select(TelegramUpdate.id).where(TelegramUpdate.update_id == update_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None
```

### Record New Update

```python
async def record_telegram_update(
    db: AsyncSession,
    update_id: int,
    chat_id: int,
    message_id: int | None,
    message_type: str,
    raw_payload: dict
) -> TelegramUpdate:
    """
    Record a new Telegram update in the database.
    
    Raises IntegrityError if update_id already exists (duplicate).
    """
    update = TelegramUpdate(
        update_id=update_id,
        chat_id=chat_id,
        message_id=message_id,
        message_type=message_type,
        raw_payload=raw_payload
    )
    db.add(update)
    await db.commit()
    await db.refresh(update)
    return update
```

### Cleanup Old Updates

```python
from datetime import datetime, timedelta
from sqlalchemy import delete

async def cleanup_old_telegram_updates(
    db: AsyncSession,
    retention_days: int = 7
) -> int:
    """
    Delete Telegram updates older than retention_days.
    
    Returns number of rows deleted.
    """
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    stmt = delete(TelegramUpdate).where(TelegramUpdate.created_at < cutoff)
    result = await db.execute(stmt)
    await db.commit()
    return result.rowcount
```

---

## Monitoring Queries

### Telegram Activity Stats

```sql
-- Messages received per hour (last 24 hours)
SELECT 
    date_trunc('hour', created_at) AS hour,
    COUNT(*) AS message_count,
    COUNT(DISTINCT chat_id) AS unique_users
FROM telegram_updates
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY hour
ORDER BY hour DESC;

-- Most active users
SELECT 
    chat_id,
    COUNT(*) AS message_count,
    MAX(created_at) AS last_message_at
FROM telegram_updates
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY chat_id
ORDER BY message_count DESC
LIMIT 10;

-- Message types breakdown
SELECT 
    message_type,
    COUNT(*) AS count
FROM telegram_updates
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY message_type
ORDER BY count DESC;
```

---

## Security Considerations

1. **PII in raw_payload**: Contains user messages and metadata
   - Consider encryption at rest for GDPR compliance
   - Implement data retention policy (7-day default)
   - Add admin endpoint for GDPR deletion requests

2. **Chat ID as identifier**: 
   - Not directly linkable to user identity (good for privacy)
   - Can be pseudonymized if needed

3. **Database access control**:
   - Application user has INSERT/SELECT/DELETE on telegram_updates
   - No UPDATE needed (immutable audit log)

---

## Testing Strategy

### Contract Tests (Before Implementation)

```python
# tests/contract/test_telegram_update_model.py
import pytest
from models.telegram_updates import TelegramUpdate

def test_telegram_update_schema_has_required_fields():
    """Verify TelegramUpdate model has all required fields."""
    required_fields = {'update_id', 'chat_id', 'processed_at', 'created_at'}
    model_fields = set(TelegramUpdate.__table__.columns.keys())
    assert required_fields.issubset(model_fields)

def test_update_id_has_unique_constraint():
    """Verify update_id has UNIQUE constraint."""
    constraints = {c.name for c in TelegramUpdate.__table__.constraints}
    assert any('update_id' in str(c) for c in TelegramUpdate.__table__.constraints)

async def test_duplicate_update_id_raises_integrity_error(async_db_session):
    """Verify database rejects duplicate update_id."""
    from sqlalchemy.exc import IntegrityError
    
    # Insert first update
    update1 = TelegramUpdate(update_id=12345, chat_id=67890)
    async_db_session.add(update1)
    await async_db_session.commit()
    
    # Attempt duplicate insert
    update2 = TelegramUpdate(update_id=12345, chat_id=99999)
    async_db_session.add(update2)
    
    with pytest.raises(IntegrityError):
        await async_db_session.commit()
```

### Integration Tests

```python
# tests/integration/test_telegram_deduplication.py
import pytest
from models.telegram_updates import TelegramUpdate

@pytest.mark.asyncio
async def test_concurrent_duplicate_updates_handled(async_db_session):
    """
    Verify concurrent webhook deliveries with same update_id
    result in only one record (database enforces idempotency).
    """
    import asyncio
    from sqlalchemy.exc import IntegrityError
    
    async def insert_update(update_id: int):
        try:
            update = TelegramUpdate(update_id=update_id, chat_id=12345)
            async_db_session.add(update)
            await async_db_session.commit()
            return True
        except IntegrityError:
            await async_db_session.rollback()
            return False
    
    # Simulate two concurrent inserts with same update_id
    results = await asyncio.gather(
        insert_update(99999),
        insert_update(99999),
        return_exceptions=True
    )
    
    # Exactly one should succeed, one should fail
    assert results.count(True) == 1
    assert results.count(False) == 1
```

---

**Status**: Phase 1 data model complete.  
**Next**: API contracts (OpenAPI schemas) and quickstart documentation.
