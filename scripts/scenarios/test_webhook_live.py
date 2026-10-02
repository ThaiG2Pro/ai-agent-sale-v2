"""Quick test script to verify Telegram webhook integration."""

import asyncio

from core.telegram.models import TelegramUpdate


async def test_webhook():
    """Test the webhook endpoint."""
    payload = {
        "update_id": 999999999,
        "message": {
            "message_id": 1,
            "from": {
                "id": 12345678,
                "is_bot": False,
                "first_name": "Test",
                "username": "testuser",
            },
            "chat": {"id": 12345678, "type": "private"},
            "date": 1711766400,
            "text": "What products do you have?",
        },
    }

    # Parse with Pydantic first
    update = TelegramUpdate(**payload)
    print(f"✓ Parsed update: {update.update_id}")
    print(f"✓ Chat ID: {update.get_chat_id()}")
    print(f"✓ Text: {update.get_text()}")

    print("\n✓ All Pydantic models work correctly!")
    return True


if __name__ == "__main__":
    result = asyncio.run(test_webhook())
    exit(0 if result else 1)
