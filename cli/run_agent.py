#!/usr/bin/env python
"""Debug CLI for LangGraph agent (T053).

Why: Manual testing interface for agent logic without API layer.

What: Accepts user message, builds graph with MemorySaver, invokes agent,
prints final response with optional streaming.

Article I exemption: CLI tool, offline use only, no parser.
"""

from __future__ import annotations

import asyncio
import sys

from langgraph.checkpoint.memory import MemorySaver

from core.agent.graph import build_graph
from core.agent.state import make_initial_state


async def main(message: str, stream: bool = False, session: str = "debug-session"):
    """Run agent with user message (T053).

    Args:
        message: User query
        stream: Whether to stream node outputs (future)
        session: Session ID for checkpointer
    """
    # Build graph with in-memory checkpointer
    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": session}}

    # Create initial state
    initial_state = make_initial_state(message, session_id=session)

    # Invoke graph
    try:
        final_state = await graph.ainvoke(initial_state, config=config)

        # Print results
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
    if len(sys.argv) < 2:
        print(
            "Usage: python cli/run_agent.py <message> [--stream] [--session SESSION]",
            file=sys.stderr,
        )
        sys.exit(1)

    message = sys.argv[1]
    stream = "--stream" in sys.argv
    session = "debug-session"

    # Parse --session argument
    for i, arg in enumerate(sys.argv):
        if arg == "--session" and i + 1 < len(sys.argv):
            session = sys.argv[i + 1]
            break

    asyncio.run(main(message, stream=stream, session=session))
