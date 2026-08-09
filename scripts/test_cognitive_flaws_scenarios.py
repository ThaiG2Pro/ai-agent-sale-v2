#!/usr/bin/env python
"""Test Agent against Sales Cognitive Flaws test scenarios."""

from __future__ import annotations

import asyncio
import json
import os

from langgraph.checkpoint.memory import MemorySaver

from core.agent.graph import build_graph, make_agent_config
from core.agent.state import make_initial_state
from services.database import AsyncSessionLocal

SCENARIOS = [
    {
        "id": "SCEN_1_NEGOTIATION",
        "name": "Thương lượng giá (Literal Reading vs Negotiation)",
        "query": "Giá iPhone 15 Pro Max 28.9tr hơi đắt nhỉ, shop có giảm giá hay khuyến mãi gì không?",
        "expected_traits": [
            "Nhận diện đàm phán/giá",
            "Giải thích ưu đãi quà tặng/freeship",
            "Đề xuất xin ý kiến Admin nếu muốn giảm thêm",
            "Có Sales CTA",
        ],
    },
    {
        "id": "SCEN_2_VALUE_SELLING",
        "name": "Bán giá trị giải pháp (Feature Dumper vs Value Selling)",
        "query": "Màn hình OLED và chip Apple A17 Pro có lợi ích gì khi dùng hàng ngày?",
        "expected_traits": [
            "Tập trung lợi ích thực tế (chơi game mượt, xem phim chân thực)",
            "Không chỉ xả thông số kỹ thuật",
            "Có Sales CTA",
        ],
    },
    {
        "id": "SCEN_3_PROACTIVE_CTA",
        "name": "Chủ động định hướng (Reactive Respondent vs Proactive Sales CTA)",
        "query": "Shop có bán laptop ASUS Vivobook không?",
        "expected_traits": [
            "Báo thông tin/giá",
            "BẮT BUỘC kết thúc bằng Sales CTA gợi ý đặt hàng/giữ hàng",
        ],
    },
    {
        "id": "SCEN_4_COMPLAINT_SUPPORT",
        "name": "Xử lý khiếu nại (Tone-Deaf vs Empathetic Support Handling)",
        "query": "Sản phẩm tôi vừa nhận bị hỏng màn hình rồi, shop giải quyết ngay cho tôi!",
        "expected_traits": [
            "Nhận diện COMPLAINT",
            "Giọng điệu đồng cảm tiếng Việt",
            "Chuyển Support/Human Escalation",
        ],
    },
    {
        "id": "SCEN_5_CATALOG_BROWSING",
        "name": "Duyệt danh mục & đa ý định (Catalog Fallback & Multi-intent)",
        "query": "Shop đang bán những loại điện thoại và laptop nào, giá từ bao nhiêu?",
        "expected_traits": ["Báo danh mục/khoảng giá", "Kết thúc bằng lời chào mua hàng"],
    },
]


async def run_cognitive_flaw_tests():
    print("=" * 70)
    print("BAT DAU KIEM THU BO KICH BAN LOI TU DUY CUA SALE AGENT")
    print("=" * 70)

    graph = build_graph(checkpointer=MemorySaver())
    results = []

    async with AsyncSessionLocal() as db:
        for idx, scen in enumerate(SCENARIOS, 1):
            session_id = f"test-cognitive-{idx}"
            customer_id = f"cust-cognitive-{idx}"
            query = scen["query"]

            print(f"\n[{scen['id']}] {scen['name']}")
            print(f'Khach hoi: "{query}"')
            print("-" * 50)

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

                print(f"  -> Intent:       {intent} ({confidence:.1%})")
                print(f"  -> Model Used:   {model_used}")
                print(f"  -> Escalated:    {escalation} | Declined: {declined}")
                print(f"  -> Phan hoi AI:\n{response}\n")

                results.append(
                    {
                        "scenario": scen,
                        "intent": str(intent),
                        "confidence": confidence,
                        "model_used": model_used,
                        "escalation": escalation,
                        "declined": declined,
                        "response": response,
                    }
                )
            except Exception as e:
                print(f"  [ERROR] Cross-check failed: {e}")
                results.append({"scenario": scen, "error": str(e)})

    report_path = "/home/thai/.gemini/antigravity-cli/brain/a10f7156-7a45-4516-8392-2beee5499db7/cognitive_flaws_test_results.json"
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("=" * 70)
    print(f"HOAN THANH KIEM THU. Ket qua duoc luu tai: {report_path}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_cognitive_flaw_tests())
