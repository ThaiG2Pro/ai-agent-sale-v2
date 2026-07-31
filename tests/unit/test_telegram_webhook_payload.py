"""Contract test: Valid Telegram webhook payload structure (T021).

Purpose: Verify TelegramUpdate Pydantic model correctly parses valid webhook payloads.
This test MUST FAIL initially (TDD) - will pass once implementation is complete.
"""

from core.telegram.models import TelegramUpdate


def test_valid_text_message_payload():
    """Verify TelegramUpdate parses valid text message webhook."""
    payload = {
        "update_id": 123456789,
        "message": {
            "message_id": 1,
            "from": {
                "id": 987654321,
                "is_bot": False,
                "first_name": "John",
                "last_name": "Doe",
                "username": "johndoe",
            },
            "chat": {"id": 987654321, "type": "private"},
            "date": 1711766400,
            "text": "What products do you have?",
        },
    }

    update = TelegramUpdate(**payload)

    assert update.update_id == 123456789
    assert update.message is not None
    assert update.message.text == "What products do you have?"
    assert update.message.chat.id == 987654321


def test_callback_query_payload():
    """Verify TelegramUpdate parses callback query (button click)."""
    payload = {
        "update_id": 123456790,
        "callback_query": {
            "id": "callback123",
            "from": {
                "id": 987654321,
                "is_bot": False,
                "first_name": "John",
            },
            "message": {
                "message_id": 2,
                "chat": {"id": 987654321, "type": "private"},
                "date": 1711766410,
                "text": "Original message",
            },
            "data": "retry_inventory_check",
        },
    }

    update = TelegramUpdate(**payload)

    assert update.update_id == 123456790
    assert update.callback_query is not None
    assert update.callback_query.data == "retry_inventory_check"


def test_get_chat_id_from_message():
    """Verify get_chat_id() helper extracts chat_id from message."""
    payload = {
        "update_id": 123456791,
        "message": {
            "message_id": 3,
            "from": {"id": 111222333, "is_bot": False, "first_name": "Alice"},
            "chat": {"id": 111222333, "type": "private"},
            "date": 1711766420,
            "text": "Test",
        },
    }

    update = TelegramUpdate(**payload)
    chat_id = update.get_chat_id()

    assert chat_id == 111222333


def test_get_chat_id_from_callback_query():
    """Verify get_chat_id() helper extracts chat_id from callback_query."""
    payload = {
        "update_id": 123456792,
        "callback_query": {
            "id": "callback456",
            "from": {"id": 444555666, "is_bot": False, "first_name": "Bob"},
            "message": {
                "message_id": 4,
                "chat": {"id": 444555666, "type": "private"},
                "date": 1711766430,
                "text": "Button message",
            },
            "data": "some_action",
        },
    }

    update = TelegramUpdate(**payload)
    chat_id = update.get_chat_id()

    assert chat_id == 444555666


def test_get_text_from_message():
    """Verify get_text() helper extracts message text."""
    payload = {
        "update_id": 123456793,
        "message": {
            "message_id": 5,
            "from": {"id": 777888999, "is_bot": False, "first_name": "Charlie"},
            "chat": {"id": 777888999, "type": "private"},
            "date": 1711766440,
            "text": "Hello, bot!",
        },
    }

    update = TelegramUpdate(**payload)
    text = update.get_text()

    assert text == "Hello, bot!"


def test_get_text_returns_none_for_callback_query():
    """Verify get_text() returns None for callback_query updates."""
    payload = {
        "update_id": 123456794,
        "callback_query": {
            "id": "callback789",
            "from": {"id": 123123123, "is_bot": False, "first_name": "Diana"},
            "message": {
                "message_id": 6,
                "chat": {"id": 123123123, "type": "private"},
                "date": 1711766450,
                "text": "Button text",
            },
            "data": "retry_action",
        },
    }

    update = TelegramUpdate(**payload)
    text = update.get_text()

    assert text is None


def test_to_json_dict_serialization():
    """Verify to_json_dict() returns serializable dict."""
    payload = {
        "update_id": 123456795,
        "message": {
            "message_id": 7,
            "from": {"id": 456456456, "is_bot": False, "first_name": "Eve"},
            "chat": {"id": 456456456, "type": "private"},
            "date": 1711766460,
            "text": "Serialize me",
        },
    }

    update = TelegramUpdate(**payload)
    json_dict = update.to_json_dict()

    assert isinstance(json_dict, dict)
    assert json_dict["update_id"] == 123456795
    assert "message" in json_dict


def test_missing_optional_fields():
    """Verify TelegramUpdate handles missing optional fields."""
    payload = {
        "update_id": 123456796,
        "message": {
            "message_id": 8,
            "from": {"id": 789789789, "is_bot": False, "first_name": "Frank"},
            "chat": {"id": 789789789, "type": "private"},
            "date": 1711766470,
            # No "text" field - optional
        },
    }

    update = TelegramUpdate(**payload)

    assert update.update_id == 123456796
    assert update.message is not None
    assert update.message.text is None
