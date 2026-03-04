#!/usr/bin/env python
"""Debug CLI for LangGraph agent (T053).

Why: Manual testing interface for agent logic without API layer.

What: Accepts user message, builds graph with MemorySaver, invokes agent,
prints final response with optional streaming.

Article I exemption: CLI tool, offline use only, no parser.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from langgraph.checkpoint.memory import MemorySaver

from core.agent.graph import astream_agent, build_graph
from core.agent.state import make_initial_state
from core.logging import setup_logging


async def main(message: str, stream: bool = False, session: str = "debug-session"):
    """Run agent with user message (T053, T082).

    Args:
        message: User query
        stream: Whether to stream per-node events (T082)
        session: Session ID for checkpointer
    """
    if stream:
        # T082: Streaming mode — print each NodeStreamEvent
        print(f"\n[STREAM] Processing: {message!r}")
        print("-" * 60)
        try:
            async for event in astream_agent(message, session, checkpointer=MemorySaver()):
                summary = json.dumps(event.state_snapshot, default=str)[:120]
                print(f"[{event.node_name}] {summary}")
        except Exception as e:
            print(f"\n[ERROR] Stream failed: {e}", file=sys.stderr)
            if "--debug" in sys.argv:
                import traceback

                traceback.print_exc()
            sys.exit(1)
        print("-" * 60)
        return

    # Non-streaming mode: invoke and print final result
    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": session}}
    initial_state = make_initial_state(message, session_id=session)

    try:
        final_state = await graph.ainvoke(initial_state, config=config)

        print("\n" + "=" * 60)
        print("AGENT OUTPUT")
        print("=" * 60)
        print(f"Intent: {final_state.get('intent')}")
        print(f"Confidence: {final_state.get('intent_confidence'):.2%}")
        print(f"Declined: {final_state.get('declined')}")
        print(f"Model Used: {final_state.get('model_used')}")
        print(f"Escalation Flag: {final_state.get('escalation_flag')}")
        print(f"\nResponse:\n{final_state.get('response')}")

        if final_state.get("citations"):
            print("\nCitations:")
            for i, citation in enumerate(final_state["citations"], 1):
                print(f"  {i}. {citation.name} ({citation.sku})")

        print("=" * 60)

    except Exception as e:
        print(f"\n[ERROR] Agent failed: {e}", file=sys.stderr)
        if "--debug" in sys.argv:
            import traceback

            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Debug CLI for LangGraph agent")
    parser.add_argument("message", help="User message to process")
    parser.add_argument("--stream", action="store_true", help="Stream per-node events")
    parser.add_argument("--session", default="debug-session", help="Session ID")
    parser.add_argument("--debug", action="store_true", help="Print full tracebacks")
    args = parser.parse_args()

    setup_logging()  # OTEL → Phoenix + instrumentors (same as API)
    asyncio.run(main(args.message, stream=args.stream, session=args.session))
