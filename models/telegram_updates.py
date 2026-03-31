"""SQLAlchemy model for telegram_updates table."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from models.schema import Base


class TelegramUpdate(Base):
    """Model for storing Telegram webhook updates."""

    __tablename__ = "telegram_updates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    update_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
        comment="Telegram update_id for deduplication",
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Type: message, callback_query, etc.",
    )
    processed_at: Mapped[Any] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the update was processed",
    )
    created_at: Mapped[Any] = mapped_column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
        nullable=False,
    )
    raw_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        comment="Full Telegram update JSON for audit",
    )

    __table_args__ = (
        Index("idx_telegram_updates_chat_id", "chat_id"),
        Index("idx_telegram_updates_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        """String representation of TelegramUpdate."""
        return (
            f"<TelegramUpdate(id={self.id}, update_id={self.update_id}, "
            f"chat_id={self.chat_id}, message_type={self.message_type})>"
        )
