# Codebase map

Bản đồ để trả lời "chỗ X xảy ra ở đâu" trong 5 giây: mỗi dòng là một fact kiểm chứng được, có đường dẫn
file và dòng. Cập nhật lần cuối 2026-10-03. Bản tiếng Anh tổng quan: [README.md](../README.md).

## 1. Kích thước

| | |
|---|---|
| App code (`api/ core/ services/ models/`) | ~17.9k dòng Python |
| Tests (`tests/`) | ~16.7k dòng, 96 file: unit 72 · integration 16 · contract 5 · performance 3 |
| Migrations | 14 revision Alembic (`migrations/versions/`) |
| Bảng DB | 20 bảng trong schema `agent_v1` |
| Graph | 13 node (`core/agent/graph.py`, dict `_NODE_FUNCS`) |
| Routes | 7 router prefix, ~22 endpoint |
| ADR | 5 (`docs/adr/`) |
| Commit | 196, tháng 2 → tháng 10/2026 |

## 2. Một request đi qua đâu (kể từ trên xuống)

```
Telegram → api/webhooks/telegram.py  (verify secret token, dedup update_id, ack nhanh)
        → core/telegram/message_handler.py
        → core/agent/graph.py: astream_agent()  thread_id = session_id
             router_node          core/agent/nodes/router.py:209     intent enum 11 giá trị
             retrieval_node       core/agent/nodes/retrieval.py:174  → services/rag/pipeline.py
             memory_retrieval_node                                    → services/memory/semantic_memory.py
             confidence_node      core/agent/nodes/confidence.py:50  fuse score, quyết answer/clarify/escalate/decline
             clarify_node | escalation_node | answer_node (answer.py:49)
             hitl_guard_node      core/agent/nodes/hitl_guard.py:161 risk score → interrupt()
             order_execution_node | cancellation_node | customer_support_node
             queue_consumer_node  xử lý tin nhắn đến trong lúc pause
             state_freshness_validator_node  kiểm tra giá/tồn kho trước khi chốt
        → reply + citations
```

HTTP trực tiếp: `POST /query` (`api/routes/query.py`, RAG thuần không graph) và `POST /agent/query`
(`api/routes/agent.py`, chạy graph đầy đủ). Demo GIF dùng `/query`.

## 3. Các ngưỡng và công thức (số trong `core/config.py`)

| Tên | Giá trị mặc định | Dòng | Ý nghĩa |
|---|---|---|---|
| `LAYER1_CONFIDENCE_THRESHOLD` | 0.45 | 121 | ngưỡng retrieval; dưới là decline sớm |
| `AGENT_CONFIDENCE_THRESHOLD` | 0.70 | 124 | ngưỡng fused ở agent |
| `AGENT_ALPHA` | 0.7 | 125 | trọng số fuse: `alpha·retrieval + (1−alpha)·llm` |
| `CLARIFY_SIMILARITY_GAP_MAX` | 0.05 | 149 | top-1 và top-2 sát nhau quá → hỏi lại |
| `HITL_RISK_W_CONF / W_VALUE / W_HISTORY` | 0.4 / 0.4 / 0.2 | ~270 | risk = Σ w·x |
| `HITL_HIGH_VALUE_ORDER_THRESHOLD` | 5.000.000 VND | 274 | trên mức này luôn pause, bất kể risk |
| `LLM_RPM_LIMIT` | 0 (dev .env: 28) | 216 | throttle client-side cho Groq free 30 RPM |
| `RAG_RETRY_MAX_ATTEMPTS` | kill-switch = 0 | | retry loop retrieval (CR agentic-rag-retry-loop) |
| `OTEL_NODE_SPANS_ENABLED` | true | | kill-switch span từng node |
| `EMBED_DIMENSION` | 1024 | | khớp cột `Vector(1024)` |

Intent enum (`core/agent/state.py:20`): INFO_QUERY, PRICING, COMPARISON, COMPLAINT, NEGOTIATION,
SMALLTALK, AVAILABILITY, ORDER_PLACEMENT, FOLLOW_UP, CANCEL, OTHER.

## 4. Dữ liệu — 20 bảng, nhóm theo việc

| Nhóm | Bảng | Ghi chú |
|---|---|---|
| Catalog + RAG | `products`, `text_embeddings`, `semantic_cache` | embedding có `model_version`; cache có TTL, invalidate khi ingest/restock |
| Hội thoại | `conversation_sessions`, `conversation_messages`, `conversation_summaries`, `telegram_updates` | `telegram_updates` dedup theo `update_id` |
| Memory | `semantic_memory`, `episodic_events`, `intent_tracking`, `sales_intent_logs`, `sales_signals` | `intent_tracking.version` = optimistic lock; `semantic_memory.status` ACTIVE/STALE |
| HITL + order | `hitl_metadata`, `interrupted_sessions`, `queued_messages`, `review_actions`, `support_queue`, `orders` | `orders` có draft lifecycle (v3-0 P2) |
| Ops | `model_traces`, `llm_token_budget` | `model_traces` nuôi `GET /admin/costs` |
| LangGraph | `checkpoints*` (do LangGraph tự tạo) | psycopg3, `JsonPlusSerializer(pickle_fallback=False)` |

Schema ORM: `models/schema.py`. UUIDv7 cho PK.

## 5. API

| Prefix | Endpoint | Việc |
|---|---|---|
| `/query` | `POST ""` | RAG trả lời + citations (QueryResponse) |
| `/agent` | `POST /query`, `POST /stream`, `GET /session/{id}/history`, `GET /session/{id}/state` | graph đầy đủ |
| `/hitl` | `GET /pending`, `POST /review`, `GET /session/{id}/state` | hàng đợi duyệt; 409 khi duyệt trùng |
| `/memory` | `GET /intents`, `GET|PATCH /intent/{cid}`, `GET /semantic/{cid}`, `GET /episodic/{cid}`, `DELETE /customer/{cid}?confirm=true` | RTBF ở DELETE |
| `/admin` | `GET /costs`, `POST /restock`, `/rag/ingest`, `/rag/search`, `/rag/stats`, `GET /ui` | cần `X-Admin-Key` |
| `/support` | `POST /query` | support graph (Spacely), tắt mặc định |
| `/webhooks` | `POST /telegram` | secret token ≥ 20 ký tự |
| `/health` | `/health`, `/liveness`, `/readiness` | readiness check DB + pool + event loop |

## 6. Eval và test — chạy gì, ở đâu

| Lệnh | Việc | Thời gian |
|---|---|---|
| `uv run pytest tests/unit -q` | 700 test, LLM mock, cần Postgres | ~3 phút |
| `uv run pytest -m integration` | graph thật, cần chat LLM | tuỳ model |
| `./scripts/eval_gate.sh --tier r` | recall retrieval 34 case vs baseline `tests/eval/baselines/tier-r-*.json` | ~1 phút |
| `./scripts/eval_gate.sh --tier f --rerun` | 12 case qua graph thật, grader rule-based | ~5 phút trên Groq |
| `uv run python scripts/eval_conversations.py --yes` | 14 hội thoại nhiều lượt, 3 case đang fail từ 22/8 | 15–20 phút |
| `uv run python scripts/eval_support.py` | support graph, 25 case | |

Gold set: `tests/eval/gold_dataset.json` (42 case). Baseline re-commit khi cố ý đổi hành vi, kèm lý do
trong commit message.

## 7. Resilience và cost (v3-0 P3)

- `services/resilience.py`: rate limiter theo RPM, cooldown deployment khi 429, timeout local vs cloud
  (`LLM_TIMEOUT_LOCAL_S`, `LLM_TIMEOUT_CLOUD_S`), `TURN_BUDGET_S`.
- `services/costs.py` + `GET /admin/costs`: p50/p95, cost theo ngày/khách/model, cache hit rate.
- `DAILY_COST_LIMIT_USD`: vượt thì hạ tier model, không chặn khách. `CUSTOMER_DAILY_MSG_CAP`.
- `llm_token_budget` + `TOKEN_BUDGET_DEGRADE_RATIO`: premium model có ngân sách ngày.

## 8. Observability

- `core/logging.py`: JSON log, PII mask (`mask_email`, `mask_phone`), OTel instrumentors (FastAPI,
  SQLAlchemy, HTTPX, Logging, LangChain/OpenInference).
- `core/agent/graph.py` `traced_node()`: mỗi node một span `node.<name>`; `GRAPH_NODES` derive từ
  `_NODE_FUNCS` nên không thêm node nào mà quên tracing được.
- Phoenix tại `localhost:6006`, OTLP gRPC `localhost:4317`, project `ai-sales-agent`.

## 9. Bảo mật

- Telegram: `X-Telegram-Bot-Api-Secret-Token` (`core/telegram/security.py`), replay/timestamp check,
  dedup `update_id`.
- Admin: `X-Admin-Key`. Support: `X-Agent-Key` tuỳ chọn.
- `ENV=production` + secret default → RuntimeError lúc boot (`api/main.py` lifespan).
- Checkpointer không pickle (CVE-2026-27794 note trong `core/agent/checkpointer.py`).
- DB password: env → `/run/secrets/db_password` → default dev.
- Chưa có: rate limit ở webhook (ghi trong `docs/deployment.md`).

## 10. Known gaps

| Gap | Bằng chứng | Kế hoạch |
|---|---|---|
| Coverage 76.8%, gate 75 | `ci.yml` comment | 4 module hụt nhiều nhất, 3–4 giờ |
| Intent-flip P1 | eval conv case #5 | `openspec/changes/v3-0-.../proposal.md` P1 |
| 3 case conv-eval fail | `reports/eval_runs/conv-20260822-*` | re-run trước |
| `confidence.py` còn 2 regex đơn vị số lượng | dòng ~321/329 | dọn theo báo cáo 22/8 |
| Docker image 1.5 GB | CHANGELOG | upgrade-plan-v4 |
| Chưa có hallucination rate riêng | | thêm grader vào Tier-F |
| Không có CD lên prod thật | | upgrade-plan-v4 |

## 11. Lịch sử theo giai đoạn

| Giai đoạn | Việc | Dấu vết |
|---|---|---|
| Tuần 1 (02/2026) | infra: uv, Postgres+pgvector, Alembic, config, logging JSON | `docs/specs/001-*`, ADR-001 |
| Tuần 2 | RAG tiếng Việt, eval Tier-1, compression | `docs/specs/002-*`, `docs/week2/` |
| Tuần 3 | LangGraph: router → retrieval → confidence → answer, escalation | ADR-002, `docs/specs/003-*` |
| Tuần 4 | HITL interrupt, review queue | `docs/specs/004-*` |
| Tuần 5 | memory: checkpointer, intent tracking, semantic memory, HNSW | ADR-005, `docs/specs/005-*` |
| Tuần 6 | Telegram webhook, Docker prod | ADR-003, `docs/specs/006-*`, CHANGELOG |
| 07/2026 | plan V2: eval gate, groundedness, clarify, risk HITL, cost dashboard | `docs/upgrade-plan-v2.md`, scorecard |
| 08/2026 | plan V3: CI, coverage, OTel per-node, nightly Tier-F; ADR-006; v3-0 P2–P4; báo cáo 22/8 | `docs/upgrade-plan-v3.md`, `docs/wayfinder/` |
| 10/2026 | support graph (Spacely), polish, sửa model Groq | CHANGELOG 2026-10 |
