# Plan: Nâng toàn bộ feature lên 4–5/5 — demo-ready cho khách SME

## Context

Audit `docs/feature-scorecard.md` (2026-07-15) chấm dự án **~3.4/5**: 001 Infra=3.8, 002 RAG=3.5, 003 Agentic=3.6, 006 Telegram/Docker=3.7, 004 HITL=3.0, 005 Memory=2.6. Đã re-verify hôm nay bằng code: **cả 5 P0 bug còn nguyên**, 13 gap P1/P2 còn nguyên, CR `agentic-rag-retry-loop` đã code ~90% nhưng chưa reconcile. Mục tiêu: mọi feature ≥4/5, chạy E2E full-stack, demo được cho khách SME (Telegram bot: hỏi giá → trả lời có citation, đặt hàng → HITL approve, nhớ khách cũ cross-session).

**Quyết định đã chốt với user:**
- CR retry-loop: gộp phần còn thiếu vào plan (WP0).
- Spec drift 002: **giữ CONFIDENCE_THRESHOLD=0.45**, update spec chính thức (không kéo code về 0.7).
- LLM backend: **thêm đường cloud provider qua API key (LiteLLM)** làm fast-path cho test/demo; Ollama giữ làm option zero-cost. Không thêm service ollama vào compose.
- Thực thi: **chia work package theo feature**, mỗi WP giao cho 1 agent độc lập. Mỗi WP dưới đây tự chứa đủ context (file:line + cách fix + test + done-criteria).

**Thứ tự & phụ thuộc:** WP0 trước (nền sạch, commit). Sau đó WP1 và WP2 chạy song song được. WP3, WP4, WP5 độc lập nhau (sau WP0). WP6 cuối cùng (spec sync + suite xanh + demo pack, phụ thuộc mọi WP trước). Mỗi WP kết thúc bằng commit riêng theo `R-GIT-001`.

**Baseline test:** unit `tests/unit`: 268 pass / 1 fail (`test_hitl_service.py::test_validation_error_marks_incompatible` — Pydantic fixture, pre-existing). Full suite trước đó: 320 pass / 13 fail / 11 skip.

---

## WP0 — Hoàn thiện CR `agentic-rag-retry-loop` (nhánh hiện tại, working tree đã có code)

Code retry-loop đã nằm uncommitted trong `services/rag/pipeline.py` (+258: `retrieve_with_retry`, `_write_retry_trace`, refactor `answer_with_rag`), `services/ai.py` (+90: `RewrittenQuery`, `AIGateway.rewrite_query`), `core/agent/tools.py`, `core/agent/nodes/queue_consumer.py`, `core/config.py` (`RAG_RETRY_MAX_ATTEMPTS`). 37 unit test mới xanh (`tests/unit/test_retrieve_with_retry.py`, `test_rewrite_query.py`).

Việc còn thiếu:
1. **Task 4.1**: wire `core/agent/nodes/retrieval.py::retrieval_node` gọi `retrieve_with_retry` thay vì `search_and_retrieve` (file này chưa có trong diff — đối chiếu `openspec/changes/agentic-rag-retry-loop/tasks.md` + `design.md` ADR-001 để wiring đúng, có kill-switch `RAG_RETRY_MAX_ATTEMPTS=0`).
2. **Task 1.2**: thêm `RAG_RETRY_MAX_ATTEMPTS` vào `.env.example` (chèn quanh dòng 36, cạnh nhóm RAG config).
3. **Integration test** `tests/integration/test_retry_loop_pipeline.py` đang fail vì embedding không mock (Ollama offline) — mock `AIGateway.embed`/`normalize_query` theo pattern các integration test hiện có, hoặc gắn skip-marker Ollama-down như `test_rag.py`.
4. Sync `openspec/changes/agentic-rag-retry-loop/tasks.md` (tick [x] các task đã xong) + `_state.json` (đang ghi sai "no source files touched").
5. Chạy `uv run pytest tests/unit -q` xanh → commit `feat(rag): 2026 agentic retry loop — bounded rewrite-retry with kill switch`.

**Done:** retry loop chạy trong graph thật qua retrieval_node; kill switch =0 giữ hành vi cũ byte-identical; unit xanh.

---

## WP1 — Feature 005 Memory: 2.6 → 4.0 (nhiều bug chặn demo nhất)

1. **P0-1 — semantic recall luôn rỗng trong graph.** `core/agent/nodes/memory_retrieval.py:18` có signature `(state, db)` nhưng `core/agent/graph.py:82` đăng ký raw — LangGraph truyền `config` vào chỗ `db` → AttributeError bị nuốt (except tại :100-111) → recall rỗng vĩnh viễn. **Fix:** đổi signature thành `(state, config: RunnableConfig)` và lấy `db = (config.get("configurable") or {}).get("db")` — đúng pattern `core/agent/nodes/retrieval.py:97-112`. Thêm integration test chạy qua **graph compiled thật** assert memory_context không rỗng khi có semantic memory seeded (bài học "test xanh nhưng feature gãy").
2. **P0-5 + tệ hơn audit — summarizer không lưu được cả lần đầu.** `services/memory/summarizer.py:169-180` insert cột `session_id`/`turn_count_at_summary` **không tồn tại** trên model `ConversationSummary` (`models/schema.py:235-253` — cột thật là `thread_id`, không có turn_count). **Fix:** map đúng cột theo schema thật + dùng `sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_update(constraint="uq_summary_customer_thread")` để re-summary upsert được. Test: save lần 1 + resummary lần 2 cùng (customer, thread) đều persist.
3. **P0-2 — RTBF xoá thiếu dữ liệu khách.** `api/routes/memory.py:475-515` chỉ xoá 3 bảng (intent_tracking, conversation_summaries, semantic_memory). **Fix:** xoá thêm `sales_intent_logs` (`models/schema.py:292`, có customer_id) + các bảng checkpoint LangGraph (checkpoints/checkpoint_writes/checkpoint_blobs theo thread_id của customer). Bỏ gate `customer_id.startswith("cust_")` tại :470 — chấp nhận mọi ID không rỗng (Telegram dùng `tg:123` hoặc chat_id số). Cập nhật response đếm đủ số bảng.
4. **Sort urgency sai.** `api/routes/memory.py:249-252` sort alphabetical trên String. **Fix:** `case()` mapping HIGH=3 > MEDIUM=2 > LOW=1 > UNKNOWN=0, sort desc.
5. **Chain summarize→embed đứt + stub.** `services/memory/background.py`: cờ gate pre-turn luôn False làm embed sau summarize không chạy — sửa cho embed chạy sau khi summary mới được lưu; `check_checkpoint_size` (`background.py:29-55`) là no-op stub — implement đo size thật (len serialized state) hoặc xoá hẳn + xoá call sites, không để stub giả.
6. **Test:** sửa `tests/integration/test_memory_flow.py` nếu còn lệch; fix 2 mock lỗi trong `test_intent_extractor.py` (`mock_llm.return_value = mock_response`, bỏ lớp AsyncMock thừa — chi tiết ở `docs/break-down.md` §6 P2).

**Done:** recall hoạt động trong graph thật (integration test), summary/re-summary persist, RTBF xoá sạch 5+ bảng và nhận ID Telegram, suite memory xanh. Commit `fix(memory): <ticket> semantic recall, summarizer upsert, full RTBF cascade`.

---

## WP2 — Feature 004 HITL: 3.0 → 4.0

1. **P0-3 — timeout 30' không báo khách.** `services/hitl/timeout_scheduler.py:58-72` chỉ log. **Fix:** gửi Telegram thật cho customer (reuse hàm send trong `services/telegram_service.py` / pattern gửi ở `core/telegram/`), message tiếng Việt "đơn của bạn đang chờ duyệt…"; giữ log; điền `context_snapshot` khi escalate (đang rỗng). Chỉ gửi khi session đến từ Telegram (có chat_id) — nếu không có kênh gửi thì log warning rõ ràng.
2. **Gateway chặn thiếu trạng thái.** `api/dependencies.py:88` chỉ chặn `status=="paused"`. **Fix:** chặn thêm `escalated`, `resuming` (frozenset BLOCKING_STATUSES); `abandoned` cho qua như session mới.
3. **`request_edit` thiếu 3 FR.** `services/hitl/service.py:366-435`: (a) validate field/value thật thay vì presence-check; (b) replace synthetic message theo key `(field, pause_id)` thay vì insert chồng tại `insertion_idx`; (c) xử lý `acknowledged_message_ids` (đã có ở `services/hitl/schemas.py:51` nhưng chưa dùng) — đánh dấu message đã ack để queue_consumer bỏ qua.
4. **Cost guard heuristic thô.** `services/hitl/cost_guard.py:31-35` dùng `len(text)//4`. **Fix:** dùng `litellm.token_counter(model=..., text=...)`, fallback heuristic khi lỗi (offline). Test với chuỗi tiếng Việt.
5. **Test:** fix `tests/unit/test_hitl_service.py::test_validation_error_marks_incompatible` (Pydantic v2 hiện tại yêu cầu ctx `{"error": ValueError(...)}` trong `from_exception_data`); fix `test_hitl_flow.py` gọi `make_initial_state` thiếu `customer_id` (thêm param).
6. (P2, làm nếu rẻ) `archive_scheduler` neo giờ chạy đêm thay vì `sleep(24h)` trôi dạt.

**Done:** khách nhận thông báo timeout thật qua Telegram, gateway chặn đủ trạng thái, request_edit đủ 3 FR, cost guard đếm token thật, HITL tests xanh. Commit `fix(hitl): <ticket> timeout notify, gateway states, request_edit FRs, real token count`.

---

## WP3 — Feature 003 Agentic workflow: 3.6 → 4.2

1. **router_node crash khi LLM trả JSON xấu.** `core/agent/nodes/router.py:57` — `model_validate_json` không try/except. **Fix:** wrap try/except → fallback `INFO_QUERY` + log warning. Unit test JSON rác/thiếu field.
2. **Recursion limit chỉ là comment.** `core/agent/graph.py:142-143`. **Fix:** enforce thật — truyền `recursion_limit` trong config invoke (`graph.py:198` chỗ build `config`) hoặc `.with_config(recursion_limit=...)`; giá trị theo Article X (5 turns ~ 15-20 steps, đọc constitution/design để chốt số).
3. **escalation_node fallback stub.** `core/agent/nodes/escalation.py:82-90` chỉ check config rỗng, không probe availability. **Fix tối thiểu:** khi gọi model premium fail ở answer path thì degrade về chat-model + flag `escalation_failure` thật trong state (fix tại điểm dùng model, không cần health-probe riêng).
4. **ModelTrace toàn số 0.** `core/agent/nodes/answer.py:300-307` hardcode tokens/cost=0, latency=None. **Fix:** lấy `response.usage` (prompt/completion tokens) + đo latency quanh call LLM trong `services/ai.py` AIGateway → trả kèm để answer_node ghi trace thật; cost tính qua `litellm.completion_cost` (=0 với Ollama, đúng; >0 với cloud key — khớp WP5).
5. **Test:** fix `tests/integration/test_agent_flow.py` thiếu `customer_id` (phần còn lại của 6 test lệch signature).

**Done:** router không crash được vì LLM output, recursion limit enforced, trace có số liệu thật, agent flow tests xanh. Commit `fix(agent): <ticket> router guard, recursion limit, real model trace`.

---

## WP4 — Feature 001 Infra/Config/Logging: 3.8 → 4.5

1. **Fail-fast secrets mặc định.** `core/config.py:16` `DB_PASSWORD="password"`, `:35` `X_ADMIN_KEY="dev-secret-key"` không có guard. **Fix:** thêm check trong `api/main.py::lifespan` (đã có pattern validate `TELEGRAM_WEBHOOK_SECRET` ≥20 ký tự ở đó): khi `ENV=production` (thêm setting `ENV`, default `dev`) mà secret còn default → raise RuntimeError fail-fast; dev thì warning.
2. **Typo model.** `core/config.py:45` `deepseel-r1` → `deepseek-r1`.
3. **JSON structured logging + PII masking.** `core/logging.py:~60-65` đang plaintext `basicConfig`. **Fix:** JSON formatter cho stdout (fields: timestamp, level, message, logger, request_id nếu có — theo `steering/security.md`); thêm helper `mask_email`/`mask_phone` + dùng ở các chỗ log identity (grep `logger.*email\|phone\|customer`). Không log token/secret.
4. (P2) `/health` trả 200 khi DB chết — để nguyên `/health` là liveness-style nhưng note rõ docstring; `/health/readiness` đã đúng 503.

**Done:** app từ chối start ở production với secret default, log ra JSON, không PII trần. Commit `fix(infra): <ticket> fail-fast secrets, JSON logging, PII masking`.

---

## WP5 — Feature 006 + LLM provider linh hoạt (đường demo nhanh bằng cloud API key)

1. **Cloud provider fast-path.** LiteLLM đã là gateway (`services/ai.py`, model list trong `core/config.py`) nên chỉ cần config: các setting `LIGHT_CHAT_MODEL`/`CHAT_MODEL`/`POWERFUL_CHAT_MODEL`/`EMBED_MODEL` nhận model string bất kỳ của LiteLLM (vd `gemini/gemini-2.5-flash`, `gpt-4o-mini`) + đọc API key từ env (`GEMINI_API_KEY`/`OPENAI_API_KEY`… — LiteLLM tự đọc). Việc cần làm: (a) kiểm tra `ai_router`/`AIGateway` không hardcode prefix `ollama/` chỗ nào (grep `"ollama` ngoài config); (b) **embedding dimension constraint**: pgvector cột `Vector(1024)` (bge-m3) — nếu đổi embed provider phải dùng model hỗ trợ `dimensions=1024` (OpenAI text-embedding-3-*) hoặc GIỮ embed qua Ollama/bge-m3 và chỉ đổi chat models (khuyến nghị mặc định: **chat=cloud, embed=local bge-m3** — ghi rõ trong docs); (c) `.env.example` thêm block "Cloud provider option" có placeholder key.
2. **docker-compose env passthrough.** Service `api` trong `docker-compose.yml` thiếu toàn bộ AI env. **Fix:** thêm `OLLAMA_BASE_URL`, `CHAT_MODEL`/`EMBED_MODEL`/…, `GEMINI_API_KEY`/`OPENAI_API_KEY` (pass-through `${VAR:-}`), `X_ADMIN_KEY`; thêm `extra_hosts: ["host.docker.internal:host-gateway"]` để option Ollama-trên-host vẫn chạy.
3. **create_task GC footgun.** `api/webhooks/telegram.py` tạo task nền không giữ reference (`create_task` + `del task`). **Fix:** module-level `set` giữ task + `add_done_callback(discard)` — pattern chuẩn asyncio.
4. **Semantic cache TTL/invalidation** (002 nhưng demo-critical: đổi giá xong cache vẫn trả giá cũ). `models/schema.py:151-172` + `services/semantic_cache.py` không có expiry. **Fix:** thêm cột `expires_at` (Alembic migration mới, có downgrade — R-DB-001) hoặc filter theo `created_at > now() - CACHE_TTL` (setting mới, không cần migration — chọn cách này nếu muốn nhẹ); thêm hàm `invalidate_cache()` gọi khi ingest/re-ingest sản phẩm (`services/rag/ingest.py`, `api/routes/admin.py` ingest endpoints).
5. **Default secret yếu trong compose** — dùng `${TELEGRAM_WEBHOOK_SECRET:?err}` bắt buộc set từ env thay vì bake giá trị.

**Done:** `docker compose up` full-stack + set API key cloud là bot trả lời được (không cần Ollama); cache không phục vụ giá cũ quá TTL; không còn task GC footgun. Commit `feat(deploy): <ticket> cloud LLM fast-path, compose AI env, cache TTL`.

---

## WP6 — Feature 002 spec sync + suite xanh + Demo pack (chạy cuối)

1. **Hết spec drift (R-SDLC-001).** Update spec chính thức trong `openspec/specs/` (capability rag-pipeline): CONFIDENCE_THRESHOLD 0.7→0.45 (kèm rationale: retry loop giảm rủi ro vùng 0.45–0.7 + UX SME), top-k bins `≤10/11-20/>20`, compression threshold 0.25 — spec khớp code đang chạy. Đây là spec-first repair: sửa spec có chủ đích, không phải "update spec theo code" âm thầm.
2. **Admin key header-only.** `api/routes/memory.py:135-146` bỏ nhánh `Query()`, chỉ nhận header `X-Admin-Key` (query string dính access log).
3. **Toàn bộ suite xanh:** xử lý các fail còn lại — `test_ai_offline.py` (phụ thuộc mạng → mock/respx), `test_rag.py` 2 test phụ thuộc Ollama (→ marker integration/skip-if-Ollama-down như pattern sẵn có). Mục tiêu: `uv run pytest` 0 fail (skip được phép); chạy cả `-m integration` với DB docker.
4. **Demo pack cho khách SME:**
   - `scripts/demo_seed.py` (hoặc dùng CLI ingest sẵn `cli/rag_admin.py`): seed catalog mẫu ~20 sản phẩm tiếng Việt (điện thoại/phụ kiện, có giá + tồn kho).
   - `docs/demo-runbook.md`: các bước dựng (compose up, set API key, ngrok webhook Telegram) + 5 kịch bản demo: (1) hỏi giá → trả lời + citation; (2) hỏi mơ hồ → retry loop rewrite rồi trả lời; (3) đặt hàng → HITL pause → admin approve → trừ kho; (4) khách quay lại session mới → agent nhớ context (semantic memory); (5) `/inventory <sku>` + retry keyboard.
   - Smoke E2E: script curl `POST /query` + giả lập payload Telegram webhook, assert answer không rỗng.

**Done:** spec khớp code, suite xanh toàn bộ, demo-runbook chạy được từ đầu tới cuối trên máy sạch. Commit `docs(spec): <ticket> sync rag spec + demo pack` (spec commit tách riêng theo quy tắc docs-sync).

---

## Verification tổng (sau khi mọi WP xong)

1. `uv run pytest` → 0 fail; `uv run pytest -m integration` (DB qua `docker compose up -d db`) → 0 fail.
2. `./scripts/lint.sh check` sạch.
3. Full-stack: `docker compose up -d` + cloud API key → `curl POST /query` trả answer + citations; giả lập Telegram webhook payload → bot phản hồi (kiểm tra qua log/DB `telegram_updates`).
4. Demo scenarios trong `docs/demo-runbook.md` chạy tay đủ 5 kịch bản.
5. Re-score nhanh `docs/feature-scorecard.md`: mỗi feature đối chiếu top-gap đã đóng → cập nhật điểm, mục tiêu mọi feature ≥4.0.

## Giao việc cho agent

Mỗi WP là 1 prompt độc lập cho 1 agent. Khi giao, kèm: (1) nội dung WP đó nguyên văn, (2) đường dẫn `docs/feature-scorecard.md` + `docs/break-down.md` làm evidence nền, (3) yêu cầu chạy test liên quan trước khi commit. WP0 làm trước tiên; WP1+WP2 song song được (file không giao nhau ngoài tests/conftest); WP6 sau cùng.
