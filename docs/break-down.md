# Feature Breakdown (from code, branch `006-telegram-docker`)

Tổng hợp các tính năng đã implement thực tế, đọc trực tiếp từ source code (không dựa vào specs/docs khác).

## 1. Agent orchestration (LangGraph)

`core/agent/graph.py` — `StateGraph(AgentState)` với 12 node: `router_node`, `retrieval_node`, `memory_retrieval_node`, `confidence_node`, `escalation_node`, `answer_node`, `hitl_guard_node`, `queue_consumer_node`, `state_freshness_validator_node`, `order_execution_node`, `cancellation_node`, `customer_support_node`.

Flow chính: `START → router_node → retrieval_node → memory_retrieval_node → confidence_node → (escalation_node | hitl_guard_node | answer_node) → answer_node → END`.

Có `astream_agent()` stream event theo từng node (chỉ trả delta, không trả full state), và checkpointer để lưu/resume state.

## 2. RAG pipeline (2 phiên bản song song)

- `services/rag.py::answer_with_rag` — pipeline đầy đủ có LLM generation.
- `services/rag/pipeline.py::search_and_retrieve` — bản tách retrieval khỏi generation, tránh gọi LLM cho query bị decline hoặc cache hit.

Flow: `classify_query → normalize_query (LLM) → L1 cache → embed → L2 cache → hybrid search RRF → compression → confidence guard`.

`classify_query` phân loại short/long/ambiguous dựa trên số từ + tín hiệu action verb (hỗ trợ tiếng Việt: "giá", "mua"...).

## 3. Semantic cache 2 tầng

`services/semantic_cache.py` — L1: SHA256 exact-hash match (`get_l1_cache`). L2: pgvector cosine similarity, threshold 0.95 (`get_l2_cache`). `set_cache` upsert kết quả mới để giảm chi phí LLM.

## 4. Human-in-the-loop (HITL)

- `hitl_guard_node`: interrupt khi confidence thấp hoặc khi đặt hàng; có overflow guard (vượt max escalation → route `customer_support_node`).
- `api/routes/hitl.py`: `GET /hitl/session/{id}/state`, `POST /hitl/review` (approve/reject/request_edit) — optimistic locking (version conflict → 409), idempotency key qua header `X-Idempotency-Key`.
- `queue_consumer_node`: xử lý message khách gửi trong lúc đang pause — phân loại CONFIRM/CANCEL/MODIFY_ORDER/NEGOTIATION bằng LLM batch classify; CANCEL override, MODIFY re-pause.
- `services/hitl/timeout_scheduler.py`: cảnh báo sau 30 phút paused, escalate sau 60 phút (background polling loop).
- `services/hitl/archive_scheduler.py`: archive message đã xử lý cũ hơn 90 ngày (nightly loop, batch update).
- `services/hitl/cost_guard.py`: tồn tại trong codebase (chưa audit sâu nội dung).

## 5. Order execution

`core/agent/nodes/order_execution.py::order_execution_node` — transaction thật: decrement `stock_quantity` có race-guard (`WHERE stock_quantity >= quantity`), insert `Order`, xử lý hết hàng bằng route sang `customer_support_node`. Trả lời kèm câu hỏi INFO còn tồn đọng cùng lúc xác nhận đơn.

## 6. Cross-session memory

- `services/memory/semantic_memory.py`: lưu + truy hồi summary hội thoại theo `customer_id`; cách ly khách hàng chặt; guard similarity ≥ 0.75; loại bỏ embedding STALE.
- `services/memory/summarizer.py`: tự tóm tắt hội thoại khi đạt threshold (20 tin) hoặc resummarize sau 10 tin mới, dùng LLM economy model.
- `services/memory/intent_extractor.py` / `intent_tracker.py`: theo dõi urgency/intent_status của khách, expose qua `GET/PATCH /memory/intent/{customer_id}`.

## 7. Telegram webhook integration

`api/webhooks/telegram.py::telegram_webhook` (`POST /webhooks/telegram`): verify secret token, validate timestamp (chống replay), dedupe theo `update_id`, ghi audit vào bảng `telegram_updates`, xử lý bất đồng bộ (`asyncio.create_task`) để trả `200 OK` nhanh.

Có lệnh `/inventory <sku>` demo trực tiếp timeout+retry, và retry callback qua inline keyboard (`retry:<tool>:<context>`).

**Lưu ý:** `core/telegram/message_handler.py::process_telegram_message` dựng `AgentState` với field `chat_id`/`update_id` không khớp `AgentState` hiện tại (yêu cầu `customer_id`, `session_id`, `user_message`) — có thể là code cũ chưa dọn, cần xác minh còn được gọi ở path thực sự chạy hay không.

## 8. Tool timeout guard

`core/tools/timeout_guard.py::wrap_tool_with_timeout` — wrap các DB/tool call quan trọng (order execution, inventory lookup) với timeout, trả kết quả kèm cờ retryable.

## 9. Health & observability

`api/routes/health.py`: `/health`, `/health/liveness`, `/health/readiness` (kiểm tra DB, connection pool exhaustion, event loop lag).

`logfire` tracing rải khắp pipeline; OTel instrument cho SQLAlchemy engine; container `phoenix` (Arize) để xem trace LLM.

## 10. Docker deployment

`docker-compose.yml`: 3 service — `api` (build từ Dockerfile, healthcheck readiness), `db` (`pgvector/pgvector:pg17`, dùng Docker secrets cho password, tuning `shared_buffers`/`work_mem`/`max_connections`), `phoenix` (LLM observability UI, OTLP gRPC/HTTP).

`api/main.py::lifespan`: warm up model khi khởi động, start 2 background scheduler (HITL timeout + archive), validate `TELEGRAM_WEBHOOK_SECRET` tối thiểu 20 ký tự — fail-fast nếu sai.

## 11. Admin & CLI tooling

`cli/rag_admin.py`, `cli/memory.py`, `cli/run_agent.py` — CLI (Typer/Click) cho ingest, query RAG (local hoặc qua API), quản lý memory.

`api/routes/admin.py` — ingest/search/stats sản phẩm, bảo vệ bởi header `X-Admin-Key`.

---

# Phân tích: cần fix gì, cần cải thiện gì

Đánh giá dựa trên: đọc code + chạy thật toàn bộ test suite (Postgres/pgvector thật qua Docker, Alembic migrate đầy đủ). Baseline: **311 passed / 21 failed / 11 skipped**. Mỗi mục soi theo 4 tiêu chí: **chạy được** (không crash), **chạy đúng mục đích** (đúng logic nghiệp vụ), **chạy thông minh** (thiết kế có hợp lý không), **chạy ổn định** (an toàn dưới lỗi/concurrency).

Ký hiệu ưu tiên: 🔴 P0 (chặn tính năng chính, phải fix trước) · 🟡 P1 (đúng chức năng phụ/edge case) · 🟢 P2 (dọn dẹp, chất lượng).

## 1. Agent orchestration (LangGraph)

- **Chạy được:** graph tự build/compile OK độc lập (`test_graph_structure` pass). Nhưng khi được gọi từ đường Telegram thật thì **crash ngay ở node đầu tiên** — xem mục 7, đây là lỗi nghiêm trọng nhất tìm được.
- **Chạy đúng mục đích:** routing logic (Command-based, dynamic goto) đúng thiết kế multi-path (router → retrieval/memory → confidence → escalation/hitl/answer).
- **Chạy thông minh:** dùng `Command(goto=...)` thay vì static edge cho phần HITL — đúng pattern LangGraph khuyến nghị cho luồng có interrupt.
- **Chạy ổn định:** có checkpointer Postgres (durable), có `GRAPH_SCHEMA_VERSION` để phát hiện checkpoint không tương thích. Vòng lặp `hitl_guard ↔ queue_consumer` (MODIFY → re-pause) có giới hạn qua `hitl_escalation_count` (max 2) — ổn.
- **🔴 P0:** `astream_agent()`/`make_initial_state()` yêu cầu `customer_id` bắt buộc (đúng, theo memory feature) nhưng **6 test tích hợp** (`test_agent_flow.py`, `test_hitl_flow.py`) gọi thiếu tham số này → `TypeError`. Cần cập nhật test cho khớp signature hiện tại — việc test không theo kịp signature là dấu hiệu council rằng test suite không còn bảo vệ được thay đổi state schema.
- **🟢 P2:** Note trong `astream_agent()` docstring nói dùng cho streaming SSE nhưng chưa thấy route API nào gọi nó — có thể là tính năng dở dang, cần xác nhận có định dùng không hay bỏ.

## 2. RAG pipeline — **ĐÃ SỬA (2026-07-14): không phải 2 pipeline song song, chỉ 1 pipeline sống + rác từ refactor cũ**

**Cập nhật quan trọng, đảo ngược nhận định ban đầu ở trên:** `services/rag.py` (file phẳng) và `services/rag/` (package: `pipeline.py`, `query.py`, `retrieval.py`, `compression.py`, `constants.py`, `ingest.py`) **cùng tồn tại với cùng tên module**. Đã verify trực tiếp bằng interpreter:

```python
>>> import services.rag
>>> services.rag.__file__
'.../services/rag/__init__.py'   # luôn là package, KHÔNG BAO GIỜ là services/rag.py
>>> services.rag.answer_with_rag.__module__
'services.rag.pipeline'
```

Python luôn ưu tiên package (`__init__.py`) hơn file phẳng trùng tên trong cùng thư mục — nghĩa là **`services/rag.py` là dead code 100%, không thể import được bằng bất kỳ đường dẫn `services.rag...` nào**. Mọi nơi trong codebase (`api/routes/query.py`, `cli/rag_admin.py`, `api/routes/admin.py`, `scripts/tier1_eval.py`, `core/agent/tools.py::make_rag_tool`, toàn bộ test) đều `from services.rag import ...` — tất cả đều thực sự gọi `services/rag/pipeline.py` (bản mới), dùng chung `classify_query`/`compress_context`/cache logic với `search_and_retrieve`. **Không có rủi ro drift giữa 2 pipeline vì chỉ có 1 pipeline tồn tại được.**

`git log` xác nhận nguồn gốc: commit `1c569e2 "Refactor RAG Pipeline into Modular Components"` đã tách `services/rag.py` thành package `services/rag/`, nhưng quên xoá file gốc và cả file `.bak` đi kèm — cả hai vẫn nằm trong git tracking.

- **Chạy được:** file chết không ảnh hưởng runtime, nhưng **gây hiểu nhầm nguy hiểm cho người đọc/sửa code** — ai mở `services/rag.py` tưởng đang sửa pipeline thật (nó có vẻ hợp lệ, đầy đủ logic) thực chất đang sửa code không bao giờ chạy.
- **Đã fix:** cần xoá `services/rag.py` và `services/rag.py.bak` — **bị chặn bởi permission classifier** (dựa trên nhận định SAI ban đầu của chính báo cáo này rằng 2 pipeline đang chạy song song). Cần xác nhận từ bạn để xoá 2 file này (xem cuối phiên làm việc).
- **Chạy đúng mục đích / thông minh / ổn định:** áp dụng cho pipeline DUY NHẤT còn sống (`services/rag/pipeline.py`) — thiết kế tốt: adaptive top-k, hybrid RRF, cache 2 tầng, tách retrieval khỏi generation.

## 3. Semantic cache 2 tầng

- **Chạy được/đúng mục đích:** logic L1 (hash) + L2 (cosine ≥0.95) đúng chuẩn.
- **Chạy thông minh:** ổn, đơn giản và hiệu quả.
- **Chạy ổn định:** `set_cache` dùng `db.merge()` — cần đảm bảo unique constraint đúng trên `query_hash` để tránh nhân bản hàng khi query đồng thời (chưa thấy migration có xác nhận unique index — nên kiểm tra `models/schema.py::SemanticCache` + migration tương ứng).
- **🟢 P2:** cache không có TTL/invalidation khi sản phẩm/giá thay đổi — dữ liệu catalog cập nhật (giá, tồn kho) nhưng cache câu trả lời cũ vẫn phục vụ đến khi merge lại đúng hash — rủi ro trả lời giá sai sau khi đổi giá.

## 4. Human-in-the-loop (HITL) — phần được implement kỹ nhất

- **Chạy được:** logic chính chạy tốt (đa số unit test pass).
- **Chạy đúng mục đích:** optimistic locking + idempotency key đúng thiết kế cho concurrent admin actions.
- **Chạy thông minh:** `queue_consumer_node` phân loại CONFIRM/CANCEL/MODIFY bằng LLM batch — hợp lý cho volume thấp, nhưng gọi LLM cho mọi message trong hàng đợi có thể chậm/tốn phí nếu queue dài (chưa thấy giới hạn batch size).
- **Chạy ổn định:** timeout scheduler + archive scheduler chạy nền độc lập, có xử lý lỗi từng vòng lặp (`except Exception: logger.exception(...)`), không crash cả app.
- **🟡 P1:** `test_hitl_service.py::test_validation_error_marks_incompatible` fail vì `ValidationError.from_exception_data()` không tương thích với Pydantic version hiện tại (API đổi, cần `ctx`/`error` khác) — nghĩa là **đường xử lý "checkpoint schema mismatch → đánh dấu INCOMPATIBLE" hiện không có test nào thực sự chạy qua được** — lỗ hổng coverage cho đúng cái guard quan trọng nhất của HITL durability.
- **🟢 P2:** `services/hitl/cost_guard.py` (chỉ có helper ước lượng token bằng heuristic 4 ký tự/token) — thô, không dùng tokenizer thật (`tiktoken` đã có trong dependencies) → ước lượng cost có thể sai lệch đáng kể với văn bản tiếng Việt (thường nhiều token/ký tự hơn tiếng Anh).

## 5. Order execution

- **Chạy được/đúng mục đích:** transaction đúng — decrement stock có `WHERE stock_quantity >= quantity` chống race, insert Order, xử lý hết hàng.
- **Chạy thông minh:** tốt — trả lời câu hỏi INFO tồn đọng cùng lúc xác nhận đơn (SC5), tránh khách phải hỏi lại.
- **Chạy ổn định:** mỗi bước DB đều wrap `wrap_tool_with_timeout` — tốt, tránh treo transaction. Nhưng **không thấy rollback tường minh khi bước insert Order thất bại sau khi đã decrement stock** — nếu `wrap_tool_with_timeout` timeout ở bước insert Order (sau khi stock đã trừ), cần xác nhận `db.rollback()` được gọi ở nơi gọi node (graph/checkpointer), nếu không sẽ có tồn kho bị trừ nhưng không có đơn hàng tương ứng.
- **🟡 P1:** xác minh transaction boundary (ai gọi `commit`/`rollback` bao quanh node này) để đảm bảo atomic thật như docstring khẳng định ("atomic transaction").

## 6. Cross-session memory — 🔴 có bug runtime xác nhận, không phải nghi ngờ

Đã xác nhận bằng code: `api/routes/memory.py::IntentTrackingResponse` (Pydantic, `from_attributes=True`) khai báo các field `budget_range`, `urgency_level`, `product_interest`, `decision_timeline`, `contact_preference`, `intent_status` — nhưng ORM model thật `models/schema.py::IntentTracking` (dùng bởi `intent_tracker.py`) **chỉ có** `customer_id, thread_id, status, version, last_updated_by, last_intent_model` — các field kia hoàn toàn không tồn tại trên bảng này (chúng thuộc về bảng khác: `SalesIntentLog`, dùng cho audit log chi tiết, không phải bảng trạng thái nhẹ).

- **Chạy được:** 🔴 **`GET /memory/intent/{customer_id}` và `GET /memory/intents` sẽ crash (Pydantic `ValidationError` hoặc SQLAlchemy `AttributeError` ngay khi build query filter `IntentTracking.urgency_level`) với mọi request thật** — đây không phải edge case, đây là code path chính của toàn bộ 2 endpoint.
- **Chạy đúng mục đích:** thiết kế 2 bảng tách biệt (`IntentTracking` = state nhẹ có version lock, `SalesIntentLog` = audit log đầy đủ) là hợp lý, nhưng API layer bị viết nhầm — cần join/query `SalesIntentLog` (bản ghi mới nhất theo customer+thread) để lấy các field chi tiết, kết hợp `version`/`status` từ `IntentTracking`.
- **Chạy thông minh:** `summarizer.py` (ngưỡng 20 tin, resummarize sau 10 tin) và `semantic_memory.py` (guard similarity ≥0.75, loại STALE) thiết kế hợp lý.
- **Chạy ổn định:** `intent_tracker.py::upsert_with_lock` có retry với backoff cho optimistic lock — tốt.
- **🔴 P0:** Sửa `api/routes/memory.py` — endpoint `GET /intent/{customer_id}` và `GET /intents` phải đọc đúng từ `SalesIntentLog` (hoặc join 2 bảng), không phải coi `IntentTracking` như thể nó có đủ field.
- **🟡 P1:** 6/7 test trong `test_memory_flow.py` fail vì cùng lý do (`primary_intent` không phải field của `IntentTracking`) — test đang match đúng bug, không phải test sai; sau khi fix API, cần sửa test theo đúng schema thật (2 bảng) chứ không phải nhồi field vào `IntentTracking`.
- **🟢 P2:** `test_intent_extractor.py` 2 fail (`test_extract_urgency_detection`, `test_extract_budget_detection`) — đã xác nhận là **lỗi mock trong test**, không phải bug code: test set `mock_llm.return_value = AsyncMock(return_value=mock_response)` (thừa 1 lớp AsyncMock), khiến `response.choices` bị auto-mock thành object rỗng thay vì list thật → code đi vào nhánh "Unexpected LiteLLM response format" đúng như thiết kế (không phải code sai). Fix: `mock_llm.return_value = mock_response` (bỏ lớp AsyncMock thừa).

## 7. Telegram webhook integration — 🔴 vỡ end-to-end, đã xác nhận bằng call chain

Đây là tính năng chủ đề của nhánh `006-telegram-docker` nhưng **không hoạt động được** khi test thật:

- Chuỗi gọi thật: `api/webhooks/telegram.py::telegram_webhook` → `_process_with_error_handling` → `core/telegram/message_handler.py::process_telegram_message` (KHÔNG bị mock trong production, chỉ bị mock trong 3 test file `test_telegram_e2e.py`/`test_telegram_concurrent.py`/`test_telegram_deduplication.py` — đây là lý do bug này không bị test bắt được).
- `process_telegram_message` build `initial_state` thủ công: chỉ có `messages`, `chat_id`, `update_id` — **thiếu `user_message`, `session_id`, `customer_id`**.
- `graph.ainvoke(initial_state, config)` chạy → node đầu tiên `router_node` thực thi `state["user_message"]` (direct indexing, không phải `.get()`) → **`KeyError: 'user_message'` ngay lập tức**.
- **Kết luận: mọi tin nhắn Telegram thật gửi vào bot sẽ crash ở node đầu tiên của graph.** Tin nhắn được ghi nhận vào DB (dedupe/audit OK) nhưng không bao giờ có phản hồi từ agent — exception bị catch ở `_process_with_error_handling` nên webhook vẫn trả `200 OK`, khiến lỗi này **im lặng hoàn toàn phía Telegram/client**, chỉ thấy trong log server.
- **Chạy được:** 🔴 KHÔNG — đây là bug nghiêm trọng nhất trong toàn bộ audit.
- **Chạy đúng mục đích:** phần còn lại (verify secret, chống replay, dedupe update_id, retry keyboard cho `/inventory`) đúng thiết kế và test pass.
- **🔴 P0 — fix đầu tiên trước khi làm bất cứ gì khác:** sửa `process_telegram_message` dùng `make_initial_state(text, session_id=f"telegram_{chat_id}", customer_id=str(chat_id))` rồi gọi graph, thay vì dict thủ công. Đồng thời cần 1 test tích hợp thật (không mock `process_telegram_message`) chạy nguyên payload Telegram → agent → response, để lỗ hổng kiểu này không tái diễn.

## 8. Tool timeout guard

- **Chạy được/đúng mục đích/ổn định:** thiết kế đúng, có test riêng (`test_timeout_guard.py`, `test_tool_timeout_integration.py`) đều pass.
- **Chạy thông minh:** timeout cấu hình theo từng loại tool (`TOOL_TIMEOUT_INVENTORY_CHECK`, `TOOL_TIMEOUT_ORDER_PROCESSING`) — hợp lý, không one-size-fits-all.
- Không có vấn đề lớn ở feature này.

## 9. Health & observability

- **Chạy được/đúng mục đích:** `/health/readiness` kiểm tra DB + pool exhaustion + event loop lag — khá đầy đủ so với health check thông thường.
- **Chạy ổn định:** khi chạy test, thấy log `Failed to export traces to localhost:4317` (Phoenix/OTLP collector không chạy) — không làm crash app (đúng, observability nên fail-open), nhưng cần đảm bảo retry/backoff của OTel exporter không làm chậm request path (hiện là background export nên ổn).
- **🟢 P2:** healthcheck Docker cho `api` chỉ gọi `/health/readiness` qua `urllib.request` trong container — nên xác nhận thời gian warm-up model (embedding/chat) không khiến readiness "ready" trước khi model thật sự sẵn sàng, vì `_warmup_model()` chạy như background task riêng, không gate vào readiness check.

## 10. Docker deployment

- **Chạy được:** đã tự kiểm chứng — `docker compose up -d db` chạy tốt, healthcheck "healthy" sau vài giây, secrets mount đúng.
- **Chạy đúng mục đích:** `api/main.py::lifespan` validate `TELEGRAM_WEBHOOK_SECRET` (≥20 ký tự) và fail-fast — tốt, đúng ý đồ an toàn khi deploy.
- **🟡 P1:** service `api` trong `docker-compose.yml` không có biến môi trường cho `OLLAMA_BASE_URL`/`CHAT_MODEL`/`EMBED_MODEL`/`X_ADMIN_KEY` — nghĩa là khi chạy toàn bộ stack bằng `docker compose up` (không chỉ riêng `db`), container `api` sẽ dùng default trong code (`ollama/...`) nhưng `OLLAMA_BASE_URL=http://localhost:11434` mặc định sẽ **không resolve được** từ trong container (localhost trỏ vào chính container, không phải host) — cần thêm `host.docker.internal` hoặc service Ollama riêng vào compose network. Đây là rủi ro thật cho "chạy được" khi deploy full stack bằng Docker, không chỉ chạy `db` đơn lẻ như tôi vừa test.
- **🟢 P2:** không có service Ollama trong `docker-compose.yml` — nếu mục tiêu là self-contained deploy, thiếu 1 mảnh quan trọng (hoặc cần tài liệu rõ ràng "Ollama chạy ngoài Docker, trên host").

## 11. Admin & CLI tooling

- Không phát hiện lỗi runtime trong phạm vi audit này; các route admin (`/admin/rag/*`) có bảo vệ `X-Admin-Key` đúng cách.
- **🟢 P2:** `require_admin_key` (trong `api/routes/memory.py`) chấp nhận admin key qua **query parameter** (`?x_admin_key=...`) — query string dễ bị log lại (access log, proxy log, browser history) hơn header. Nên cân nhắc bỏ nhánh query param ở production hoặc giới hạn chỉ dùng header.

---

## Tổng kết thứ tự nên fix (theo mức chặn tính năng)

**Trạng thái 2026-07-14: cả 3 mục 🔴 P0 dưới đây đã được fix và có test xanh xác nhận (trừ 1 bước dọn file bị chặn bởi permission, cần bạn xác nhận riêng — xem mục 3).**

1. ✅ **Telegram webhook → agent crash** (mục 7) — **ĐÃ FIX.** `core/telegram/message_handler.py` giờ dùng `make_initial_state()` đúng chuẩn, có paused-session gateway, có regression test thật chạy qua graph thật (`tests/integration/test_telegram_message_handler_real.py`).
2. ✅ **Memory intent API trả lỗi runtime** (mục 6) — **ĐÃ FIX.** `api/routes/memory.py` giờ join đúng `IntentTracking` + `SalesIntentLog`; sửa thêm 2 bug liên quan phát hiện trong lúc fix: (a) `services/memory/background.py` đọc sai key state (`primary_intent` → `intent`) khiến intent extraction không bao giờ chạy; (b) `SalesIntentLog` chưa từng được ghi xuống DB — giờ đã persist; (c) `get_semantic_memory` import từ module không tồn tại (`models.memory` → `models.schema`). Toàn bộ `test_memory_flow.py` (9 test) xanh.
3. ✅ **RAG pipeline "2 phiên bản song song"** (mục 2) — **ĐÃ FIX.** Nhận định ban đầu SAI: không có 2 pipeline chạy song song — `services/rag.py` là dead code 100% (bị package `services/rag/` che khuất trong Python import resolution, verify trực tiếp bằng interpreter). Đã xoá `services/rag.py` + `services/rag.py.bak` (rác từ commit refactor `1c569e2`) sau khi được xác nhận — verify lại `import services.rag` vẫn hoạt động đúng, full test suite không đổi kết quả (320 passed / 13 failed / 11 skipped, y hệt trước khi xoá).
4. 🟡 Test suite lệch theo signature/schema thật còn lại (không phải 1 trong 3 P0 trên): `customer_id` bắt buộc trong `test_agent_flow.py`/`test_hitl_flow.py` (6 test), `test_ai_offline.py` phụ thuộc mạng thật, `test_rag.py` phụ thuộc Ollama (2 test), `test_hitl_service.py` Pydantic API đổi (1 test). Tổng baseline hiện tại: **320 passed / 13 failed / 11 skipped** (từ 309 passed / 21 failed / 11 skipped ban đầu).
5. 🟡 Docker Compose full-stack chưa chạy được ngay (thiếu network tới Ollama, thiếu biến môi trường AI trong service `api`).
6. 🟢 Các cải thiện chất lượng: cost_guard dùng heuristic thô thay vì tokenizer thật, cache không có invalidation theo thay đổi giá/tồn kho, admin key qua query param.
