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


async def main(
    message: str,
    stream: bool = False,
    session: str = "debug-session",
    customer_id: str = "cli-debug-user",
    api: bool = False,
):
    """Run agent with user message (T053, T082).

    Args:
        message: User query
        stream: Whether to stream per-node events (T082)
        session: Session ID for checkpointer
        customer_id: Customer ID for memory scoping (defaults to cli-debug-user for testing)
        api: Whether to call via HTTP API instead of direct invocation
    """
    # API mode: call via HTTP
    if api:
        await _call_api(message, stream, session, customer_id)
        return

    # Direct mode: invoke graph in-process
    from services.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        if stream:
            # T082: Streaming mode — print each NodeStreamEvent
            print(f"\n[STREAM] Processing: {message!r}")
            print("-" * 60)
            try:
                async for event in astream_agent(
                    message,
                    session_id=session,
                    customer_id=customer_id,
                    db=db,
                    checkpointer=MemorySaver(),
                ):
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
        # Pass db via configurable for retrieval_node and answer_node injection
        config = {"configurable": {"thread_id": session, "db": db}}
        initial_state = make_initial_state(message, session_id=session, customer_id=customer_id)

        try:
            final_state = await graph.ainvoke(initial_state, config=config)

            print("\n" + "=" * 60)
            print("AGENT OUTPUT")
            print("=" * 60)
            print(f"Intent:         {final_state.get('intent')}")
            print(f"Confidence:     {final_state.get('intent_confidence'):.2%}")
            print(f"Declined:       {final_state.get('declined')}")
            print(f"Model Used:     {final_state.get('model_used')}")
            print(f"Escalation:     {final_state.get('escalation_flag')}")
            print(f"Similarity:     {final_state.get('similarity_score', 0):.3f}")
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


async def _call_api(
    message: str,
    stream: bool = False,
    session: str = "debug-session",
    customer_id: str = "cli-debug-user",
):
    """Call the agent via HTTP API (new Week 3 endpoint).

    Args:
        message: User query
        stream: Whether to use streaming SSE endpoint
        session: Session ID
        customer_id: Customer ID for memory scoping
    """
    import httpx

    api_base = "http://localhost:8000"

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            if stream:
                # Stream mode: GET /agent/stream
                print(f"\n[STREAM via API] Processing: {message!r}")
                print("-" * 60)
                async with client.stream(
                    "POST",
                    f"{api_base}/agent/stream",
                    json={"message": message, "session_id": session, "customer_id": customer_id},
                ) as response:
                    if response.status_code != 200:
                        print(f"[ERROR] API returned {response.status_code}")
                        sys.exit(1)
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            event_json = line[6:]  # Strip "data: " prefix
                            event = json.loads(event_json)
                            summary = json.dumps(event.get("state_snapshot", {}), default=str)[
                                :120
                            ]
                            print(f"[{event.get('node_name')}] {summary}")
                print("-" * 60)
            else:
                # Non-stream mode: POST /agent/query
                response = await client.post(
                    f"{api_base}/agent/query",
                    json={"message": message, "session_id": session, "customer_id": customer_id},
                )
                if response.status_code != 200:
                    print(f"[ERROR] API returned {response.status_code}: {response.text}")
                    sys.exit(1)

                result = response.json()
                print("\n" + "=" * 60)
                print("AGENT OUTPUT (via API)")
                print("=" * 60)
                intent_info = result["intent"]
                model_trace = result["model_trace"]
                print(f"Intent:         {intent_info['primary_intent']}")
                print(f"Confidence:     {intent_info['confidence']:.2%}")
                print(f"Declined:       {result['declined']}")
                print(f"Model Used:     {model_trace['selected_model']}")
                print(f"Escalation:     {model_trace['escalation_flag']}")
                print(f"Similarity:     {model_trace['similarity_score']:.3f}")
                print(f"Execution Path: {result['execution_path']}")
                print(f"Latency:        {result['elapsed_ms']:.1f}ms")
                print(f"\nResponse:\n{result['answer']}")

                if result.get("citations"):
                    print("\nCitations:")
                    for i, citation in enumerate(result["citations"], 1):
                        print(f"  {i}. {citation.get('name')} ({citation.get('sku')})")

                print("=" * 60)

        except httpx.ConnectError:
            print(
                "[ERROR] Could not connect to API at http://localhost:8000",
                file=sys.stderr,
            )
            print(
                "       Start the API with: uv run python -m uvicorn api.main:app --reload",
                file=sys.stderr,
            )
            sys.exit(1)
        except Exception as e:
            print(f"\n[ERROR] API call failed: {e}", file=sys.stderr)
            if "--debug" in sys.argv:
                import traceback

                traceback.print_exc()
            sys.exit(1)


if __name__ == "__main__":
    import uuid

    parser = argparse.ArgumentParser(description="Debug CLI for LangGraph agent")
    parser.add_argument("message", help="User message to process")
    parser.add_argument("--stream", action="store_true", help="Stream per-node events")
    parser.add_argument(
        "--session",
        default=str(uuid.uuid4()),
        help="Session ID (UUID). Defaults to a new UUID.",
    )
    parser.add_argument(
        "--customer-id",
        default="cli-debug-user",
        help="Customer ID for memory scoping. Defaults to 'cli-debug-user'.",
    )
    parser.add_argument(
        "--api",
        action="store_true",
        help="Call via HTTP API instead of direct invocation",
    )
    parser.add_argument("--debug", action="store_true", help="Print full tracebacks")
    args = parser.parse_args()

    setup_logging()  # OTEL → Phoenix + instrumentors (same as API)
    asyncio.run(
        main(
            args.message,
            stream=args.stream,
            session=args.session,
            customer_id=args.customer_id,
            api=args.api,
        )
    )
