"""_maybe_extract_intent read msg.get() on LangChain messages — regression."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from services.memory.background import _msg_content, _msg_role


def test_langchain_messages():
    assert _msg_role(HumanMessage(content="hi")) == "user"
    assert _msg_role(AIMessage(content="hello")) == "assistant"
    assert _msg_content(HumanMessage(content="hi")) == "hi"


def test_dict_messages_still_work():
    assert _msg_role({"role": "user", "content": "x"}) == "user"
    assert _msg_content({"content": "x"}) == "x"
    assert _msg_role({}) == "unknown" and _msg_content({}) == ""
