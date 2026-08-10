"""Live scenario test runner for AI Sales Agent order workflows."""

import asyncio
import uuid

import httpx

BASE_URL = "http://localhost:8000"
ADMIN_KEY = "dev-key"


async def run_scenario_tests():
    print("=" * 70)
    print("🚀 BẮT ĐẦU CHẠY THỬ NGHIỆM CÁC KỊCH BẢN ORDER LIVE")
    print("=" * 70)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # -------------------------------------------------------------
        # Test 1: Tư vấn sản phẩm (Info Query RAG)
        # -------------------------------------------------------------
        print("\n[Test 1] Tư vấn sản phẩm & Tồn kho")
        cust_1 = f"cust_test_{uuid.uuid4().hex[:6]}"
        sess_1 = f"sess_test_{uuid.uuid4().hex[:6]}"

        resp = await client.post(
            f"{BASE_URL}/agent/query",
            json={
                "message": "Chuột Logitech MX Master 3S giá bao nhiêu và còn hàng không?",
                "session_id": sess_1,
                "customer_id": cust_1,
            },
        )
        data = resp.json()
        print(f"  • Session ID: {sess_1}")
        print(
            f"  • Primary Intent: {data['intent']['primary_intent']} (Conf: {data['intent']['confidence']:.2f})"
        )
        print(f"  • Node Executed: {data['execution_path']}")
        print(f"  • Answer: {data['answer'][:150]}...")

        # -------------------------------------------------------------
        # Test 2: Đặt hàng thông thường (Order Placement - Logitech MX Master 3S)
        # -------------------------------------------------------------
        print("\n[Test 2] Đặt hàng sản phẩm Logitech MX Master 3S (2.990.000đ)")
        resp = await client.post(
            f"{BASE_URL}/agent/query",
            json={
                "message": "Tôi muốn đặt 1 con Chuột Logitech MX Master 3S Wireless Mouse giá 2990000đ, SĐT 0988111222, địa chỉ 123 Nguyễn Trãi Hà Nội",
                "session_id": sess_1,
                "customer_id": cust_1,
            },
        )
        data = resp.json()
        print(f"  • Primary Intent: {data['intent']['primary_intent']}")
        print(f"  • HITL Paused: {data.get('hitl_paused', False)}")
        print(f"  • Execution Path: {data['execution_path']}")
        print(f"  • Answer: {data['answer'][:200]}...")

        # -------------------------------------------------------------
        # Test 3: Đặt đơn giá trị cao (High Value > 10M -> Reaches HITL Tier 3 / Interrupt)
        # -------------------------------------------------------------
        print("\n[Test 3] Đặt đơn hàng giá trị cao (MacBook Pro 54.990.000đ -> Trigger HITL)")
        cust_2 = f"cust_test_{uuid.uuid4().hex[:6]}"
        sess_2 = f"sess_test_{uuid.uuid4().hex[:6]}"

        resp = await client.post(
            f"{BASE_URL}/agent/query",
            json={
                "message": "Tôi muốn mua 1 chiếc MacBook Pro 16 inch M3 Pro 18GB giá 54990000đ, SĐT 0901234567, địa chỉ 456 Lê Lợi TP HCM",
                "session_id": sess_2,
                "customer_id": cust_2,
            },
        )
        data = resp.json()
        pause_id = data.get("hitl_pause_id")
        hitl_paused = data.get("hitl_paused", False)
        print(f"  • Primary Intent: {data['intent']['primary_intent']}")
        print(f"  • HITL Paused: {hitl_paused}")
        print(f"  • Pause ID: {pause_id}")
        print(f"  • Answer: {data['answer']}")

        # -------------------------------------------------------------
        # Test 4: Phê duyệt đơn hàng bị tạm dừng (Admin Submit Review)
        # -------------------------------------------------------------
        if hitl_paused and pause_id:
            print("\n[Test 4] Admin phê duyệt đơn hàng bị dừng (HITL Review Endpoint)")
            review_resp = await client.post(
                f"{BASE_URL}/hitl/review",
                headers={
                    "X-Admin-Key": ADMIN_KEY,
                    "X-Idempotency-Key": f"idem-{uuid.uuid4().hex[:8]}",
                },
                json={
                    "session_id": sess_2,
                    "approved": True,
                    "reason": "Khách VIP, đã gọi điện xác nhận địa chỉ",
                    "admin_id": "admin_test",
                },
            )
            review_data = review_resp.json()
            print(f"  • Admin Review Status: {review_resp.status_code}")
            print(f"  • HITL Review Output: {review_data.get('status') or review_data}")

        # -------------------------------------------------------------
        # Test 5: Đặt hàng thiếu biến thể (Clarification Request)
        # -------------------------------------------------------------
        print("\n[Test 5] Đặt hàng thiếu biến thể sản phẩm (Yêu cầu làm rõ)")
        cust_3 = f"cust_test_{uuid.uuid4().hex[:6]}"
        sess_3 = f"sess_test_{uuid.uuid4().hex[:6]}"
        resp = await client.post(
            f"{BASE_URL}/agent/query",
            json={
                "message": "Bán cho tôi 1 chiếc điện thoại iPhone",
                "session_id": sess_3,
                "customer_id": cust_3,
            },
        )
        data = resp.json()
        print(f"  • Primary Intent: {data['intent']['primary_intent']}")
        print(f"  • Execution Path: {data['execution_path']}")
        print(f"  • Answer: {data['answer'][:200]}...")

        # -------------------------------------------------------------
        # Test 6: Yêu cầu hủy đơn hàng (Order Cancellation)
        # -------------------------------------------------------------
        print("\n[Test 6] Yêu cầu hủy đơn hàng")
        resp = await client.post(
            f"{BASE_URL}/agent/query",
            json={
                "message": "Cho tôi hủy đơn hàng vừa mới đặt",
                "session_id": sess_1,
                "customer_id": cust_1,
            },
        )
        data = resp.json()
        print(f"  • Primary Intent: {data['intent']['primary_intent']}")
        print(f"  • Execution Path: {data['execution_path']}")
        print(f"  • Answer: {data['answer'][:200]}...")

    print("\n" + "=" * 70)
    print("✅ ĐÃ HOÀN THÀNH TẤT CẢ CÁC KỊCH BẢN TEST ORDER LIVE!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_scenario_tests())
