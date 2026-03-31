"""Pydantic models for Telegram Bot API payloads.

Why this exists: Type-safe parsing of Telegram webhook updates.
What it does: Validates incoming webhook payloads, provides structured access to Telegram entities.
Constitutional compliance: Article VI (Structured Determinism) - no regex parsing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TelegramUser(BaseModel):
    """Represents a Telegram user."""

    id: int = Field(..., description="Unique identifier for this user")
    is_bot: bool = Field(..., description="True if this user is a bot")
    first_name: str = Field(..., description="User's first name")
    last_name: str | None = Field(None, description="User's last name (optional)")
    username: str | None = Field(None, description="User's username (optional)")


class TelegramChat(BaseModel):
    """Represents a Telegram chat."""

    id: int = Field(..., description="Unique identifier for this chat")
    type: str = Field(..., description="Type of chat: private, group, supergroup, or channel")


class TelegramMessage(BaseModel):
    """Represents a Telegram message."""

    message_id: int = Field(..., description="Unique message identifier inside this chat")
    from_: TelegramUser | None = Field(None, alias="from", description="Sender of the message")
    chat: TelegramChat = Field(..., description="Conversation the message belongs to")
    date: int = Field(..., description="Date the message was sent in Unix time")
    text: str | None = Field(None, description="Text content of the message")


class TelegramCallbackQuery(BaseModel):
    """Represents an incoming callback query from an inline keyboard button."""

    id: str = Field(..., description="Unique identifier for this query")
    from_: TelegramUser = Field(..., alias="from", description="Sender")
    message: TelegramMessage | None = Field(None, description="Message with the callback button")
    data: str | None = Field(None, description="Data associated with the callback button")


class TelegramUpdate(BaseModel):
    """Represents an incoming update from Telegram.

    Why this exists: Root webhook payload container.
    What it does: Parses the Telegram update, extracts message or callback_query.
    """

    update_id: int = Field(..., description="Unique identifier for this update")
    message: TelegramMessage | None = Field(None, description="New incoming message")
    callback_query: TelegramCallbackQuery | None = Field(
        None, description="Callback query from an inline keyboard button"
    )

    def get_chat_id(self) -> int | None:
        """Extract chat_id from either message or callback_query."""
        if self.message:
            return self.message.chat.id
        if self.callback_query and self.callback_query.message:
            return self.callback_query.message.chat.id
        return None

    def get_message_id(self) -> int | None:
        """Extract message_id from either message or callback_query."""
        if self.message:
            return self.message.message_id
        if self.callback_query and self.callback_query.message:
            return self.callback_query.message.message_id
        return None

    def get_text(self) -> str | None:
        """Extract text from message."""
        if self.message:
            return self.message.text
        return None

    def to_json_dict(self) -> dict[str, Any]:
        """Convert to dict for raw_payload storage."""
        return self.model_dump(mode="json", by_alias=True)
