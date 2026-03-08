"""Why this exists: Performs persistent data structures for the AI agent.
What it does: Defines SQLAlchemy models for products, embeddings, and chat history.
"""

from __future__ import annotations

import uuid  # noqa: TC003
from datetime import UTC, datetime
from decimal import Decimal  # noqa: TC003
from typing import Any, ClassVar

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from uuid_utils import uuid7

from core.config import settings


# Article II: Simplicity - Declarative Models
class Base(DeclarativeBase):
    pass


SCHEMA = "agent_v1"


class Product(Base):
    __tablename__ = "products"
    __table_args__: ClassVar[dict[str, Any]] = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    sku: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0.00)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    embeddings: Mapped[list[TextEmbedding]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class TextEmbedding(Base):
    __tablename__ = "text_embeddings"
    __table_args__: ClassVar[tuple[Any, dict[str, Any]]] = (
        Index(
            "idx_text_embeddings_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.products.id"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # e.g., 'product_description'
    embedding: Mapped[Vector] = mapped_column(Vector(settings.EMBED_DIMENSION))
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[str] = mapped_column(String(50), nullable=True)
    keywords: Mapped[list[str]] = mapped_column(JSONB, default=list)  # FR-003
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    product: Mapped[Product] = relationship(back_populates="embeddings")


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"
    __table_args__: ClassVar[dict[str, Any]] = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    external_id: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    messages: Mapped[list[ConversationMessage]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    signals: Mapped[list[SalesSignal]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__: ClassVar[dict[str, Any]] = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.conversation_sessions.id"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user, assistant, system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(nullable=True)
    model_name: Mapped[str] = mapped_column(String(100), nullable=True)
    source_chunk_ids: Mapped[dict] = mapped_column(
        JSONB, nullable=True
    )  # Article IX: Citation Requirement
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    session: Mapped[ConversationSession] = relationship(back_populates="messages")
    traces: Mapped[list[ModelTrace]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class SemanticCache(Base):
    __tablename__ = "semantic_cache"
    __table_args__: ClassVar[tuple[Any, dict[str, Any]]] = (
        Index(
            "idx_semantic_cache_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        {"schema": SCHEMA},
    )

    query_hash: Mapped[str] = mapped_column(String(64), primary_key=True)  # SHA256
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Vector] = mapped_column(Vector(settings.EMBED_DIMENSION))
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    citations: Mapped[dict] = mapped_column(JSONB, nullable=True)  # Article IX
    similarity_score: Mapped[float] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class SalesSignal(Base):
    """Why this exists: Tracks business-critical events and customer intent (Week 5)."""

    __tablename__ = "sales_signals"
    __table_args__: ClassVar[dict[str, Any]] = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.conversation_sessions.id"),
        nullable=False,
    )
    signal_type: Mapped[str] = mapped_column(String(50), index=True)  # e.g., 'budget'
    signal_value: Mapped[dict] = mapped_column(JSONB)
    confidence: Mapped[float] = mapped_column(default=1.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    session: Mapped[ConversationSession] = relationship(back_populates="signals")


class ModelTrace(Base):
    """Why this exists: Tracks detailed LLM performance, tokens, and cost (Week 7)."""

    __tablename__ = "model_traces"
    __table_args__: ClassVar[dict[str, Any]] = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.conversation_messages.id"),
        nullable=True,
    )
    model_name: Mapped[str] = mapped_column(String(100))
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    total_tokens: Mapped[int] = mapped_column(default=0)
    latency_ms: Mapped[float] = mapped_column(nullable=True)
    cost: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=0.00)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    message: Mapped[ConversationMessage] = relationship(back_populates="traces")


# Week 4: HITL & Order Models


class HITLMetadata(Base):
    """Why this exists: Authoritative source for session pause status (Week 4)."""

    __tablename__ = "hitl_metadata"
    __table_args__: ClassVar[dict[str, Any]] = {"schema": SCHEMA}

    pause_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    session_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    pause_reason: Mapped[str] = mapped_column(String(50), nullable=False)
    paused_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    timeout_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated_to_support_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="paused", nullable=False)
    admin_id: Mapped[str | None] = mapped_column(String(100))
    escalation_count: Mapped[int] = mapped_column(default=0, nullable=False)


class ReviewAction(Base):
    """Why this exists: Immutable audit log of every admin decision (Week 4)."""

    __tablename__ = "review_actions"
    __table_args__: ClassVar[tuple[Any, dict[str, Any]]] = (
        UniqueConstraint("idempotency_key", name="uq_review_actions_idempotency"),
        {"schema": SCHEMA},
    )

    action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    session_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    pause_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    state_edits: Mapped[dict | None] = mapped_column(JSONB)
    reason_or_comment: Mapped[str | None] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    admin_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    expected_version: Mapped[int] = mapped_column(nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)


class QueuedMessage(Base):
    """Why this exists: Customer messages received while a session is not 'active' (Week 4)."""

    __tablename__ = "queued_messages"
    __table_args__: ClassVar[tuple[Any, dict[str, Any]]] = (
        Index("ix_queued_messages_session_proc_time", "session_id", "processed", "received_at"),
        {"schema": SCHEMA},
    )

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    session_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    processed: Mapped[bool] = mapped_column(default=False, nullable=False)
    archived: Mapped[bool] = mapped_column(default=False, nullable=False)


class SupportQueue(Base):
    """Why this exists: Escalated sessions awaiting human support agent (Week 4)."""

    __tablename__ = "support_queue"
    __table_args__: ClassVar[tuple[Any, dict[str, Any]]] = (
        UniqueConstraint("session_id", name="uq_support_queue_session"),
        {"schema": SCHEMA},
    )

    queue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    session_id: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    assigned_to: Mapped[str | None] = mapped_column(String(100))
    context_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)


class InterruptedSession(Base):
    """Why this exists: Tracks escalation state and optimistic locking per session (Week 4)."""

    __tablename__ = "interrupted_sessions"
    __table_args__: ClassVar[dict[str, Any]] = {"schema": SCHEMA}

    session_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    next_node: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str] = mapped_column(String(50), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    admin_id: Mapped[str | None] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(default=0, nullable=False)
    escalation_count: Mapped[int] = mapped_column(default=0, nullable=False)


class Order(Base):
    """Why this exists: Business entity for confirmed orders (Week 4)."""

    __tablename__ = "orders"
    __table_args__: ClassVar[dict[str, Any]] = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    session_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    customer_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    order_info: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
