#!/usr/bin/env python
"""Test Order Workflow Scenarios (Complex & Sensitive Sales Operations)."""

from __future__ import annotations

import asyncio
import json
import os

from langgraph.checkpoint.memory import MemorySaver

from core.agent.graph import build_graph, make_agent_config
from core.agent.state import make_initial_state
from services.database import AsyncSessionLocal

ORDER_SCENARIOS = [
    {
        "id": "ORDER_1_HAPPY_PATH",
        "name": "Đặt hàng đầy đủ thông tin (Full Product & Shipping Details)",
        "query": "Tôi muốn đặt mua 1 chiếc iPhone 15 Pro Max 512GB giá 28.9tr, sđt 0901234567, giao về 123 Nguyễn Huệ Quận 1",
        "expected_intent": "ORDER_PLACEMENT",
    },
    {
        "id": "ORDER_2_CUSTOM_PRICE_DISCOUNT",
        "name": "Đặt hàng kèm yêu cầu chiết khấu riêng (Custom Price Negotiation)",
        "query": "Cho tôi đặt 1 chiếc iPhone 15 Pro Max 512GB với giá 27.5 triệu được không?",
        "expected_intent": "ORDER_PLACEMENT",
    },
    {
        "id": "ORDER_3_OUT_OF_STOCK_VARIANT",
        "name": "Đặt hàng cấu hình không có sẵn (Out-of-stock / Invalid Variant)",
        "query": "Tôi muốn đặt mua ngay chiếc ASUS VivoBook Pro 15 bản RAM 64GB",
        "expected_intent": "ORDER_PLACEMENT",
    },
    {
        "id": "ORDER_4_STATUS_FOLLOW_UP",
        "name": "Kiểm tra tiến độ đơn hàng (Order Status Inquiry)",
        "query": "Shop kiểm tra giúp tôi đơn hàng đã được đặt thành công chưa?",
        "expected_intent": "FOLLOW_UP",
    },
    {
        "id": "ORDER_5_CANCEL_REQUEST",
        "name": "Hủy đơn hàng vừa đặt (Order Cancellation)",
        "query": "Tôi muốn hủy đơn hàng vừa đặt, shop dừng giao giúp tôi nhé",
        "expected_intent": "CANCEL",
    },
]


async def run_order_scenario_tests():
    print("=" * 75)
    print("BAT DAU KIEM THU CHUYEN SAU QUY TRINH ORDER & THANH TOAN (SALE AGENT)")
    print("=" * 75)

    graph = build_graph(checkpointer=MemorySaver())
    results = []

    async with AsyncSessionLocal() as db:
        for idx, scen in enumerate(ORDER_SCENARIOS, 1):
            session_id = f"test-order-{idx}"
            customer_id = f"cust-order-{idx}"
            query = scen["query"]

            print(f"\n[{scen['id']}] {scen['name']}")
            print(f'Cau lenh khach: "{query}"')
            print("-" * 55)

            config = make_agent_config(session_id, db=db)
            initial_state = make_initial_state(
                query, session_id=session_id, customer_id=customer_id
            )

            try:
                final_state = await graph.ainvoke(initial_state, config=config)

                intent = final_state.get("intent")
                confidence = final_state.get("intent_confidence", 0.0)
                model_used = final_state.get("model_used")
                response = final_state.get("response", "")
                escalation = final_state.get("escalation_flag", False)
                declined = final_state.get("declined", False)
                order_info = final_state.get("order_info")

                print(f"  -> Intent:          {intent} ({confidence:.1%})")
                print(f"  -> Model Used:      {model_used}")
                print(f"  -> Escalated:       {escalation} | Declined: {declined}")
                if order_info:
                    print(
                        f"  -> Order Info:      Product ID: {order_info.get('product_id')}, Status: {order_info.get('status')}"
                    )
                print(f"  -> Phan hoi AI:\n{response}\n")

                results.append(
                    {
                        "scenario": scen,
                        "intent": str(intent),
                        "confidence": confidence,
                        "model_used": model_used,
                        "escalation": escalation,
                        "declined": declined,
                        "order_info": order_info,
                        "response": response,
                    }
                )
            except Exception as e:
                print(f"  [INTERRUPT / RESULT] Node flow paused or note: {e}")
                results.append({"scenario": scen, "status_note": str(e)})

    report_path = "/home/thai/.gemini/antigravity-cli/brain/a10f7156-7a45-4516-8392-2beee5499db7/order_scenarios_test_results.json"
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("=" * 75)
    print(f"HOAN THANH KIEM THU ORDER. Ket qua duoc luu tai: {report_path}")
    print("=" * 75)


if __name__ == "__main__":
    asyncio.run(run_order_scenario_tests())
