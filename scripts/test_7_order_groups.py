"""Full 7-Group Order Scenario Test Runner for AI Sales Agent."""

import asyncio
import uuid

import httpx

BASE_URL = "http://localhost:8000"
ADMIN_KEY = "dev-key"


async def run_7_group_tests():
    print("=" * 80)
    print("🚀 BẮT ĐẦU CHẠY THỬ NGHIỆM ĐẦY ĐỦ 7 GROUP KỊCH BẢN ORDER LIVE")
    print("=" * 80)

    async with httpx.AsyncClient(timeout=40.0) as client:
        # -------------------------------------------------------------
        # GROUP 1: Luồng Chốt Đơn Tự Động (Auto-Approval / Tier 1 Risk)
        # -------------------------------------------------------------
        print("\n" + "─" * 70)
        print("🔹 GROUP 1: LUỒNG CHỐT ĐƠN TỰ ĐỘNG (Tier 1 Risk)")
        print("─" * 70)

        cust_g1 = f"cust_g1_{uuid.uuid4().hex[:6]}"
        sess_g1 = f"sess_g1_{uuid.uuid4().hex[:6]}"

        req_1_1 = {
            "message": "Tôi quyết định chốt đặt mua 1 chiếc điện thoại Samsung Galaxy A55 128GB giá 6.990.000đ, SĐT 0988111222, địa chỉ 123 Nguyễn Trãi Hà Nội",
            "session_id": sess_g1,
            "customer_id": cust_g1,
        }
        print("\n[Test 1.1] Single product order (Samsung Galaxy A55 - 6.99M VND)")
        resp = await client.post(f"{BASE_URL}/agent/query", json=req_1_1)
        d1 = resp.json()
        print(
            f"  • Primary Intent: {d1['intent']['primary_intent']} (Conf: {d1['intent']['confidence']:.2f})"
        )
        print(f"  • HITL Paused: {d1.get('hitl_paused', False)}")
        print(f"  • Answer: {d1['answer'][:180]}...")

        req_1_2 = {
            "message": "Tôi muốn chốt đặt mua 1 chiếc Anker 737 Power Bank 140W giá 3490000đ, SĐT 0912345678, địa chỉ 456 Cầu Giấy Hà Nội",
            "session_id": f"sess_g1_b_{uuid.uuid4().hex[:6]}",
            "customer_id": cust_g1,
        }
        print("\n[Test 1.2] Single product order (Anker Power Bank - 3.49M VND)")
        resp = await client.post(f"{BASE_URL}/agent/query", json=req_1_2)
        d1_2 = resp.json()
        print(f"  • Primary Intent: {d1_2['intent']['primary_intent']}")
        print(f"  • HITL Paused: {d1_2.get('hitl_paused', False)}")
        print(f"  • Answer: {d1_2['answer'][:180]}...")

        # -------------------------------------------------------------
        # GROUP 2: Phê Duyệt Con Người (HITL Interrupt / Tier 2 Risk)
        # -------------------------------------------------------------
        print("\n" + "─" * 70)
        print("🔹 GROUP 2: PHÊ DUYỆT CON NGƯỜI (HITL Interrupt - Tier 2 Risk)")
        print("─" * 70)

        cust_g2 = f"cust_g2_{uuid.uuid4().hex[:6]}"
        sess_g2 = f"sess_g2_{uuid.uuid4().hex[:6]}"

        req_2_1 = {
            "message": "Tôi chốt đặt mua 1 chiếc LG 32UP550 32-inch 4K Professional Monitor giá 16990000đ, SĐT 0909999111, địa chỉ 88 Trần Hưng Đạo HCM",
            "session_id": sess_g2,
            "customer_id": cust_g2,
        }
        print("\n[Test 2.1] Order high-medium value (16.99M VND -> Trigger HITL Interrupt)")
        resp = await client.post(f"{BASE_URL}/agent/query", json=req_2_1)
        d2 = resp.json()
        pause_id = d2.get("hitl_pause_id")
        hitl_paused = d2.get("hitl_paused", False)
        print(f"  • Primary Intent: {d2['intent']['primary_intent']}")
        print(f"  • HITL Paused: {hitl_paused}")
        print(f"  • Pause ID: {pause_id}")
        print(f"  • Answer: {d2['answer'][:180]}...")

        if hitl_paused and pause_id:
            print("\n[Test 2.2] Staff Review Approve (POST /hitl/review)")
            rev_resp = await client.post(
                f"{BASE_URL}/hitl/review",
                headers={
                    "X-Admin-Key": ADMIN_KEY,
                    "X-Idempotency-Key": f"idem-{uuid.uuid4().hex[:8]}",
                },
                json={
                    "session_id": sess_g2,
                    "approved": True,
                    "reason": "Đã xác minh khách hàng qua điện thoại",
                    "admin_id": "manager_01",
                },
            )
            rev_data = rev_resp.json()
            print(f"  • Status Code: {rev_resp.status_code}")
            print(f"  • Review Result: {rev_data.get('status') or rev_data}")

        # -------------------------------------------------------------
        # GROUP 3: Rủi Ro Cao & Chuyển Trực Tiếp CSKH (Tier 3 Risk & Direct Escalation)
        # -------------------------------------------------------------
        print("\n" + "─" * 70)
        print("🔹 GROUP 3: RỦI RO CAO & CHUYỂN TRỰC TIẾP CSKH (Tier 3 Risk)")
        print("─" * 70)

        cust_g3 = f"cust_g3_{uuid.uuid4().hex[:6]}"
        sess_g3 = f"sess_g3_{uuid.uuid4().hex[:6]}"

        req_3_1 = {
            "message": "Tôi muốn đặt mua 5 chiếc Dell XPS 15 Plus giá 39990000đ/cái, tổng tiền khoảng 200 triệu VNĐ, SĐT 0905555666, giao về Tòa nhà Landmark 81",
            "session_id": sess_g3,
            "customer_id": cust_g3,
        }
        print("\n[Test 3.1] Ultra High Value Order (200M VND)")
        resp = await client.post(f"{BASE_URL}/agent/query", json=req_3_1)
        d3 = resp.json()
        print(f"  • Primary Intent: {d3['intent']['primary_intent']}")
        print(f"  • Escalation Flag: {d3['model_trace']['escalation_flag']}")
        print(f"  • Answer: {d3['answer'][:180]}...")

        req_3_2 = {
            "message": "Tôi rất không hài lòng với giá bán bên bạn, tôi muốn khiếu nại và đàm phán giảm giá chiếc iPhone 15 Pro Max này 40% rồi mới đặt!",
            "session_id": f"sess_g3_b_{uuid.uuid4().hex[:6]}",
            "customer_id": cust_g3,
        }
        print("\n[Test 3.2] Order combined with Complaint & Negotiation")
        resp = await client.post(f"{BASE_URL}/agent/query", json=req_3_2)
        d3_2 = resp.json()
        print(f"  • Primary Intent: {d3_2['intent']['primary_intent']}")
        print(f"  • Escalation Reason: {d3_2['model_trace'].get('escalation_reason')}")
        print(f"  • Answer: {d3_2['answer'][:180]}...")

        # -------------------------------------------------------------
        # GROUP 4: Xử Lý Thông Tin Thiếu & Làm Rõ (Clarification)
        # -------------------------------------------------------------
        print("\n" + "─" * 70)
        print("🔹 GROUP 4: XỬ LÝ THÔNG TIN THIẾU & LÀM RÕ (Clarification)")
        print("─" * 70)

        cust_g4 = f"cust_g4_{uuid.uuid4().hex[:6]}"
        sess_g4 = f"sess_g4_{uuid.uuid4().hex[:6]}"

        req_4_1 = {
            "message": "Tôi muốn đặt mua 1 cái điện thoại Samsung",
            "session_id": sess_g4,
            "customer_id": cust_g4,
        }
        print("\n[Test 4.1] Ambiguous product variant request")
        resp = await client.post(f"{BASE_URL}/agent/query", json=req_4_1)
        d4_1 = resp.json()
        print(f"  • Primary Intent: {d4_1['intent']['primary_intent']}")
        print(f"  • Answer: {d4_1['answer'][:180]}...")

        req_4_2 = {
            "message": "Tôi chốt đặt mua 1 chiếc Bàn phím cơ Keychron K3 Pro RGB giá 3.290.000đ",
            "session_id": f"sess_g4_b_{uuid.uuid4().hex[:6]}",
            "customer_id": cust_g4,
        }
        print("\n[Test 4.2] Missing phone & delivery address")
        resp = await client.post(f"{BASE_URL}/agent/query", json=req_4_2)
        d4_2 = resp.json()
        print(f"  • Primary Intent: {d4_2['intent']['primary_intent']}")
        print(f"  • Answer: {d4_2['answer'][:180]}...")

        # -------------------------------------------------------------
        # GROUP 5: Hủy Đơn & Đổi Trả (Order Cancellation & Refund)
        # -------------------------------------------------------------
        print("\n" + "─" * 70)
        print("🔹 GROUP 5: HỦY ĐƠN & ĐỔI TRẢ (Cancellation & Refund)")
        print("─" * 70)

        cust_g5 = f"cust_g5_{uuid.uuid4().hex[:6]}"
        sess_g5 = f"sess_g5_{uuid.uuid4().hex[:6]}"

        req_5_1 = {
            "message": "Hủy đơn hàng giúp tôi, tôi không muốn mua nữa",
            "session_id": sess_g5,
            "customer_id": cust_g5,
        }
        print("\n[Test 5.1] Explicit Order Cancellation")
        resp = await client.post(f"{BASE_URL}/agent/query", json=req_5_1)
        d5_1 = resp.json()
        print(f"  • Primary Intent: {d5_1['intent']['primary_intent']}")
        print(f"  • Answer: {d5_1['answer'][:180]}...")

        req_5_2 = {
            "message": "Sản phẩm bị hỏng màn hình, tôi muốn yêu cầu trả hàng và hoàn tiền",
            "session_id": f"sess_g5_b_{uuid.uuid4().hex[:6]}",
            "customer_id": cust_g5,
        }
        print("\n[Test 5.2] Refund & Defective Product Return")
        resp = await client.post(f"{BASE_URL}/agent/query", json=req_5_2)
        d5_2 = resp.json()
        print(f"  • Primary Intent: {d5_2['intent']['primary_intent']}")
        print(f"  • Answer: {d5_2['answer'][:180]}...")

        # -------------------------------------------------------------
        # GROUP 6: Kiểm Tra Tồn Kho & Chống Gian Lận (Inventory & Anti-Fraud)
        # -------------------------------------------------------------
        print("\n" + "─" * 70)
        print("🔹 GROUP 6: KIỂM TRA TỒN KHO & CHỐNG GIAN LẬN (Inventory & Anti-Fraud)")
        print("─" * 70)

        cust_g6 = f"cust_g6_{uuid.uuid4().hex[:6]}"
        sess_g6 = f"sess_g6_{uuid.uuid4().hex[:6]}"

        req_6_1 = {
            "message": "Tôi muốn chốt đặt mua 500 chiếc Tai nghe Sony WH-1000XM5, SĐT 0987111222, ĐC: Hà Nội",
            "session_id": sess_g6,
            "customer_id": cust_g6,
        }
        print("\n[Test 6.1] Out of stock / Exceeding inventory limit (500 units)")
        resp = await client.post(f"{BASE_URL}/agent/query", json=req_6_1)
        d6_1 = resp.json()
        print(f"  • Primary Intent: {d6_1['intent']['primary_intent']}")
        print(f"  • Answer: {d6_1['answer'][:180]}...")

        req_6_2 = {
            "message": "Tôi muốn chốt đặt mua 1 chiếc iPhone 15 Pro Max 512GB với giá 1000 đồng, SĐT 0988888888",
            "session_id": f"sess_g6_b_{uuid.uuid4().hex[:6]}",
            "customer_id": cust_g6,
        }
        print("\n[Test 6.2] Price Manipulation Attack (iPhone 15 Pro Max @ 1,000 VND)")
        resp = await client.post(f"{BASE_URL}/agent/query", json=req_6_2)
        d6_2 = resp.json()
        print(f"  • Primary Intent: {d6_2['intent']['primary_intent']}")
        print(f"  • Answer: {d6_2['answer'][:180]}...")

        # -------------------------------------------------------------
        # GROUP 7: Kịch Bản Biên & Đa Đơn Hàng (Edge Cases & Multi-item)
        # -------------------------------------------------------------
        print("\n" + "─" * 70)
        print("🔹 GROUP 7: KỊCH BẢN BIÊN & ĐA ĐƠN HÀNG (Edge Cases & Multi-Item)")
        print("─" * 70)

        cust_g7 = f"cust_g7_{uuid.uuid4().hex[:6]}"
        sess_g7 = f"sess_g7_{uuid.uuid4().hex[:6]}"

        req_7_1 = {
            "message": "Tôi muốn chốt đặt mua 1 Bàn phím Keychron K3 Pro và 1 Chuột Logitech MX Master 3S, SĐT 0912345678, địa chỉ 789 Hoàng Hoa Thám Hà Nội",
            "session_id": sess_g7,
            "customer_id": cust_g7,
        }
        print("\n[Test 7.1] Multi-item Combo Order")
        resp = await client.post(f"{BASE_URL}/agent/query", json=req_7_1)
        d7_1 = resp.json()
        print(f"  • Primary Intent: {d7_1['intent']['primary_intent']}")
        print(f"  • Answer: {d7_1['answer'][:180]}...")

        print("\n[Test 7.2] Multi-turn intent shift within session")
        # Turn 1
        resp_t1 = await client.post(
            f"{BASE_URL}/agent/query",
            json={
                "message": "Tôi đang tham khảo điện thoại Samsung Galaxy A55",
                "session_id": f"sess_g7_b_{uuid.uuid4().hex[:6]}",
                "customer_id": cust_g7,
            },
        )
        print(f"  • Turn 1 Intent: {resp_t1.json()['intent']['primary_intent']}")

        # Turn 2 (Order placement shift)
        resp_t2 = await client.post(
            f"{BASE_URL}/agent/query",
            json={
                "message": "À thôi chốt đặt mua luôn con POCO F6 256GB giá 8.490.000đ nhé, SĐT 0977888999, ĐC Cầu Giấy",
                "session_id": f"sess_g7_b_{uuid.uuid4().hex[:6]}",
                "customer_id": cust_g7,
            },
        )
        d7_2 = resp_t2.json()
        print(f"  • Turn 2 Intent: {d7_2['intent']['primary_intent']}")
        print(f"  • Answer: {d7_2['answer'][:180]}...")

    print("\n" + "=" * 80)
    print("✅ ĐÃ HOÀN THÀNH TẤT CẢ 7 GROUP KỊCH BẢN TEST ORDER LIVE!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_7_group_tests())
