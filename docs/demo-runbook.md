# Demo Runbook — 5 kịch bản demo cho khách SME

> **Why this exists:** đóng nốt mục P2/WP6 của scorecard — demo pack đã có `scripts/demo_seed.py`
> nhưng chưa có runbook. Tài liệu này là kịch bản từng bước (lệnh curl + Telegram) để bất kỳ ai
> trong team chạy lại đúng 5 demo đã PASS ở Verification tổng V2 (2026-08-03), không cần nhớ gì.

## 0. Chuẩn bị (một lần trước buổi demo)

### 0.1 Hạ tầng

```bash
docker compose up -d                      # Postgres (pgvector) + Phoenix
uv run alembic upgrade head               # schema mới nhất
```

LLM backend — chọn MỘT trong hai:

| Backend | Cấu hình `.env` | Ghi chú |
|---|---|---|
| **Ollama local** (Zero-Cost) | mặc định trong `.env.example` (`CHAT_MODEL=ollama/qwen3-4b-q6`) | Cần `ollama serve` + đã pull `qwen3`, `bge-m3`. Máy yếu: chậm nhưng chạy được |
| **Groq** (demo mượt, đã dùng ở Verification tổng) | `GROQ_API_KEY=...` + trỏ `CHAT_MODEL`/`POWERFUL_CHAT_MODEL` sang `groq/llama-3.3-70b-versatile` (xem comment sẵn trong `.env.example`) | Embedding VẪN cần Ollama local (`bge-m3`) |

### 0.2 Seed dữ liệu demo

```bash
uv run python scripts/demo_seed.py        # ~20 sản phẩm VN (điện thoại/laptop/phụ kiện) + tồn kho 8-32
```

Idempotent — chạy lại bao nhiêu lần cũng được (SKU đã có thì skip, tồn kho được set lại).

### 0.3 Chạy server + biến môi trường cho lệnh curl

```bash
uvicorn api.main:app --port 8000
# Terminal thứ hai:
export API=http://localhost:8000
export ADMIN_KEY="<giá trị X_ADMIN_KEY trong .env>"   # KHÔNG hardcode vào lệnh/log
```

### 0.4 Reset giữa các lần demo

```bash
# Xoá sạch data (giữ schema), rồi seed lại:
uv run python scripts/cleanup_db.py --confirm
uv run python scripts/demo_seed.py
```

Reset nhẹ (không xoá catalog): chỉ cần đổi `session_id`/`customer_id` mới cho mỗi lượt demo.

---

## Kịch bản 1 — Clarify 2 lượt (câu mơ hồ → bot hỏi lại → trả lời đúng)

Chứng minh: bot không đoán bừa khi câu hỏi thiếu thông tin; hỏi lại rồi merge ngữ cảnh ở lượt 2.
(Kill-switch: `CLARIFY_ENABLED=true` — mặc định bật.)

```bash
# Lượt 1 — câu mơ hồ, không nói rõ model nào
curl -s $API/agent/query -X POST -H 'Content-Type: application/json' -d '{
  "message": "Điện thoại Samsung ấy còn hàng không shop?",
  "session_id": "demo-clarify-01",
  "customer_id": "demo-cust-01"
}' | jq '.answer'
# Kỳ vọng: bot hỏi lại để làm rõ (vd "Anh/chị đang hỏi Galaxy S24 Ultra hay Buds2 Pro ạ?")

# Lượt 2 — trả lời câu hỏi của bot, GIỮ NGUYÊN session_id
curl -s $API/agent/query -X POST -H 'Content-Type: application/json' -d '{
  "message": "S24 Ultra",
  "session_id": "demo-clarify-01",
  "customer_id": "demo-cust-01"
}' | jq '.answer'
# Kỳ vọng: trả lời đúng về Samsung Galaxy S24 Ultra 256GB (giá ~24.990.000đ, còn hàng)
```

> Lưu ý (đã ghi nhận ở WP-V3-4): với router thật, clarify_node chỉ kích hoạt trong điều kiện hẹp —
> nếu lượt 1 bot tự hỏi lại từ answer node thì demo vẫn đạt về mặt UX, nhưng đó chưa phải
> clarify_node; V3-4 sẽ mở rộng điều kiện này.

## Kịch bản 2 — Không bịa thuộc tính không tồn tại (groundedness)

Chứng minh: hỏi về thuộc tính KHÔNG có trong catalog (HDMI trên điện thoại, 5G trên chuột) →
bot nói thẳng không có thông tin, kèm citation, không hallucinate.

```bash
curl -s $API/agent/query -X POST -H 'Content-Type: application/json' -d '{
  "message": "iPhone 15 Pro Max có cổng HDMI không?",
  "session_id": "demo-ground-01",
  "customer_id": "demo-cust-02"
}' | jq '{answer, citations: .citations}'
# Kỳ vọng: trả lời dạng "trong thông tin sản phẩm không đề cập cổng HDMI" — KHÔNG bịa "có/không có"
# như một spec chắc chắn nếu nguồn không nói.
```

Biến thể: `"Chuột Logitech MX Master 3S có hỗ trợ 5G không?"` — kỳ vọng tương tự.

## Kịch bản 3 — Đặt hàng + HITL (đơn nhỏ tự duyệt / đơn to chờ người duyệt)

Chứng minh risk-tier HITL (WP-V2-4): đơn giá trị nhỏ của khách quen đi thẳng
(Tier 1 auto-approve); đơn > ngưỡng `HITL_HIGH_VALUE_ORDER_THRESHOLD` (mặc định **5.000.000đ**)
LUÔN pause chờ admin — bất biến an toàn, không tune được.

### 3a. Đơn nhỏ — auto-approve

```bash
# Khách "quen": dùng customer_id đã từng chat vài lượt (hoặc chạy kịch bản 1-2 trước bằng cùng ID)
curl -s $API/agent/query -X POST -H 'Content-Type: application/json' -d '{
  "message": "Cho mình đặt 1 ốp lưng silicone",
  "session_id": "demo-order-small-01",
  "customer_id": "demo-cust-01"
}' | jq '.answer'
# Kỳ vọng: đơn 199.000đ được xác nhận luôn (không có pause_id) — tồn kho CASE-PHONE-001 giảm 1.
```

### 3b. Đơn to — pause + admin duyệt

```bash
# Bước 1: đặt đơn vượt ngưỡng 5 triệu
curl -s $API/agent/query -X POST -H 'Content-Type: application/json' -d '{
  "message": "Mình muốn đặt 1 chiếc Samsung Galaxy S24 Ultra 256GB",
  "session_id": "demo-order-big-01",
  "customer_id": "demo-cust-01"
}' | jq '.answer'
# Kỳ vọng: bot báo đơn cần người duyệt — session tạm dừng.

# Bước 2 (vai admin): xem trạng thái pause, lấy pause_id + version
curl -s -H "X-Admin-Key: $ADMIN_KEY" \
  $API/hitl/session/demo-order-big-01/state | jq '{pause_id, status, version, order_info: .state_snapshot.order_info}'

# Bước 3 (vai admin): duyệt đơn (có thể kèm giảm giá qua approved_price)
curl -s $API/hitl/review -X POST \
  -H 'Content-Type: application/json' \
  -H "X-Admin-Key: $ADMIN_KEY" \
  -H "X-Idempotency-Key: demo-approve-$(date +%s)" \
  -d '{
    "session_id": "demo-order-big-01",
    "pause_id": "<pause_id từ bước 2>",
    "action": "approve",
    "expected_version": <version từ bước 2>,
    "admin_user_id": "demo-admin"
  }' | jq
# Kỳ vọng: resume → đơn thực thi, tồn kho PHONE-SM-001 giảm 1; gọi lại /hitl/review với CÙNG
# X-Idempotency-Key trả {"status":"hit"} (chống double-approve).
# Nhánh phụ: "action":"reject" + "reason_or_comment" → khách được chuyển hướng hỗ trợ.
```

## Kịch bản 4 — RTBF (Right To Be Forgotten)

Chứng minh tuân thủ FR-019: xoá cascade TOÀN BỘ dữ liệu của một khách hàng
(semantic memory, summaries, intent, episodic events, LangGraph checkpoints).

```bash
# Trước khi xoá — cho khán giả thấy bot CÓ nhớ (memory tồn tại):
curl -s -H "X-Admin-Key: $ADMIN_KEY" $API/memory/semantic/demo-cust-01 | jq '.total // .items'

# Xoá — bắt buộc confirm=true (chặn xoá nhầm), yêu cầu admin key:
curl -s -X DELETE -H "X-Admin-Key: $ADMIN_KEY" \
  "$API/memory/customer/demo-cust-01?confirm=true" | jq
# Kỳ vọng: JSON đếm số bản ghi đã xoá theo từng bảng; audit trail ghi vào structured log.

# Sau khi xoá — memory rỗng, và thiếu confirm bị chặn 400:
curl -s -H "X-Admin-Key: $ADMIN_KEY" $API/memory/semantic/demo-cust-01 | jq
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE -H "X-Admin-Key: $ADMIN_KEY" \
  "$API/memory/customer/demo-cust-01"      # → 400
```

## Kịch bản 5 — Cost dashboard (chi phí thật bằng một lệnh curl)

Chứng minh WP-V2-5: SME đọc được chi phí model (tokens, USD, latency p50/p95, cache hit-rate)
tổng hợp từ `model_traces` — chạy SAU các kịch bản 1-4 để có số liệu.

```bash
# Theo ngày (mặc định 7 ngày gần nhất):
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$API/admin/costs" | jq

# Theo khách hàng / theo model:
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$API/admin/costs?group_by=customer" | jq
curl -s -H "X-Admin-Key: $ADMIN_KEY" "$API/admin/costs?group_by=model" | jq

# Auth đúng chuẩn: thiếu key → 401; group_by lạ → 400
curl -s -o /dev/null -w '%{http_code}\n' "$API/admin/costs"                       # 401
curl -s -o /dev/null -w '%{http_code}\n' -H "X-Admin-Key: $ADMIN_KEY" \
  "$API/admin/costs?group_by=nope"                                               # 400
```

Điểm nhấn khi demo với Ollama: cột USD = **$0** — đúng triết lý Zero-Cost-First.

---

## Demo qua Telegram (tuỳ chọn, thay cho curl kịch bản 1-3)

Nếu đã cấu hình `TELEGRAM_BOT_TOKEN` + webhook (xem `POST /webhooks/telegram`,
secret ≥ 20 ký tự): nhắn trực tiếp các câu ở kịch bản 1-3 cho bot — `customer_id` là
chat_id Telegram, không cần tự đặt. Duyệt HITL (3b) vẫn qua curl `/hitl/review`.

## Sự cố thường gặp

| Triệu chứng | Nguyên nhân / cách xử lý |
|---|---|
| `/agent/query` treo lâu lần đầu | Ollama đang load model vào RAM — gọi warm-up 1 câu trước buổi demo |
| Bot decline liên tục | Chưa seed catalog, hoặc embedding backend chết — chạy lại §0.2, check `ollama ps` |
| `/hitl/review` trả 409 | Pause đã được xử lý rồi (double-click) — xem `status` trong detail; demo idempotency luôn |
| `/hitl/review` trả 404 | Sai `pause_id` — lấy lại từ `/hitl/session/{session_id}/state` |
| Máy yếu bị nghẽn | Đừng chạy pytest/eval song song với server demo; tắt Phoenix nếu không cần trace |
