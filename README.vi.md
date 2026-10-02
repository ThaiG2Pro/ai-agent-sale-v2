# AI Sales Agent cho SME thương mại điện tử

> Bản tóm tắt tiếng Việt 1 trang. Tài liệu đầy đủ (kiến trúc, eval, quickstart) ở [README.md](README.md).

Agent bán hàng hội thoại cho cửa hàng nhỏ: nạp catalog sản phẩm, trả lời khách qua **Telegram**
bằng **RAG có citation**, theo dõi **ý định mua** (ngân sách, độ gấp, sản phẩm quan tâm) xuyên
phiên, và **dừng chờ người duyệt** trước mọi hành động nhạy cảm (chốt đơn, mặc cả, khiếu nại).
Chạy được hoàn toàn local (Ollama + fastembed) hoặc đổi sang cloud bằng một biến env.

**Stack**: Python 3.13 · FastAPI · LangGraph · LiteLLM · PostgreSQL + pgvector · SQLAlchemy 2.0
async · OpenTelemetry → Arize Phoenix · Docker

![Demo](docs/img/demo.gif)

## Điểm khác biệt: được đo, không chỉ chạy được

| | |
|---|---|
| 🧪 **782 test** | unit / integration / contract / eval / performance; integration chạy LangGraph thật trên Postgres thật |
| 📊 **Eval gate có baseline commit** | **Tier-R** (recall retrieval, 34/34) chạy mỗi PR, không tốn LLM; **Tier-F** (nguyên graph, 12/12 × 3 run) chạy nightly, tụt >2 điểm là build đỏ |
| 🛡️ **CI chặn commit hỏng** | lint → unit (pgvector thật, LLM mock) → eval, coverage gate 75% |
| 🔍 **Tracing từng node** | mỗi node graph là một span OpenTelemetry vào Phoenix, biết lượt nào chậm ở node nào |
| 📜 **Quyết định có ghi lại** | [ADR](docs/adr/) cho chọn model/provider, orchestration, governance embedding, gồm 2 incident thật |
| 🔒 **Privacy** | cách ly dữ liệu theo khách, PII không vào log/span, xoá theo yêu cầu (RTBF) |

## Kiến trúc trong một câu

Monolith async: FastAPI giao mỗi lượt chat cho một `StateGraph` LangGraph 13 node, state lưu trong
Postgres checkpointer nên hội thoại nối được qua nhiều lượt và nhiều kênh.

- **Confidence gating 2 tầng**: ngưỡng ở retrieval và ngưỡng fused ở agent → trả lời / hỏi lại /
  escalate / từ chối. Bot từ chối thay vì bịa.
- **Model tiering qua LiteLLM**: alias `light / chat / powerful / embed`; backend là config.
- **HITL risk score**: `0.4·(1−confidence) + 0.4·giá trị đơn + 0.2·lịch sử`, 3 bậc. Đơn > 5 triệu
  luôn dừng chờ người.
- **Semantic memory có governance**: vector tóm tắt hội thoại theo `customer_id`, lưu kèm model
  version, STALE flag khi đổi model.

## Hai incident đáng đọc ([ADR-006](docs/adr/006-model-provider-and-embedding-runtime.md))

1. **Thư viện embedding đổi vector space mà không đổi tên model.** fastembed đổi pooling CLS → mean
   giữa hai bản minor; mọi vector trong DB lệch im lặng. Fix: pin exact version, coi đổi embedding
   là migration event với runbook 7 bước.
2. **Ollama cắt ngầm prompt RAG.** Context mặc định ~2–4k token làm rơi chunk mà không báo lỗi.
   Fix: inject `num_ctx` tại gateway LiteLLM, có unit test chặn.

## Chạy thử

```bash
uv sync
cp .env.example .env            # điền TELEGRAM_* và GROQ_API_KEY
echo "change-me" > secrets/db_password.txt
echo "DB_PASSWORD=change-me" >> .env
docker compose up -d --build    # Postgres + pgvector, Phoenix, API :8000
uv run alembic upgrade head
uv run python scripts/demo_seed.py
python3 scripts/demo_ask.py "Điện thoại nào pin tốt dưới 10 triệu?"
```

Trace xem tại `http://localhost:6006`. Kịch bản demo 5 bước: [docs/demo-runbook.md](docs/demo-runbook.md).

## Cách dự án được xây (và AI dùng ở đâu)

Dự án cá nhân, tháng 2 → tháng 10/2026, 196 commit, **70 commit có Claude đồng tác giả**. Quy
trình spec-driven: proposal → spec → design → tasks → build → QA, có người duyệt giữa các pha
(artifact trong `openspec/`). AI viết code theo design đã duyệt và bản nháp test. Người quyết định
kiến trúc (ADR), eval gold set, công thức risk HITL, và debug hai incident trên. Chi tiết ở
[README.md](README.md#how-this-was-built-and-how-ai-was-used).

---

*Dự án portfolio. Catalog demo là dữ liệu giả, không liên quan đến merchant nào.*
