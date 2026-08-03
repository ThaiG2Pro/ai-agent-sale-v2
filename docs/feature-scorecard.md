# Feature Scorecard — ai-agent-sale-v2

> Đánh giá từng feature theo thang **1–5**: `1 = chỉ chạy được` · `2 = chạy đúng nghiệp vụ` · `3 = chạy thông minh/tối ưu` · `4 = chạy ổn định` · `5 = chuẩn production 2026`.
> Phương pháp: map spec (`specs/00X-*`) ↔ code thật, kiểm chứng bằng `file:line` (không tin lời `tasks.md`).
> **Ngày: 2026-08-03 (re-score sau plan V2, WP-V2-0 → V2-5 + Verification tổng). Nhánh: `main` @ `4772905`.**
> Bản re-score trước 2026-07-16 @ `4d85bb3` (điểm cũ ghi ở cột "Cũ"); audit gốc 2026-07-15.

## Tổng quan

**Điểm trung bình toàn dự án: ~4.6 / 5** *(cũ: ~4.3)* — *"Production-lean: agent ĐÚNG hơn (groundedness + cascade + fragment citations), KHÔN hơn (clarify loop, decomposition, episodic memory, risk-tier HITL), RẺ hơn (cost dashboard, budget guard, cheap-intent routing). Suite 549 pass / 5 skip / 0 fail; eval Tier-R 34/34; demo 3 kịch bản live PASS."*

| # | Feature | Điểm | Cũ | Mức đạt |
|---|---------|:----:|:--:|---------|
| 001 | Project Infra Setup | **4.3** | 4.3 | Giữ nguyên — ngoài scope plan V2 |
| 002 | Vietnamese RAG & Eval | **4.7** | 4.2 | V2-0/1/2 — eval gate phân tầng + groundedness/cascade + L1 key deterministic + fragment citations |
| 003 | Agentic Workflow | **4.7** | 4.3 | V2-3/5 — clarify loop, query decomposition, SMALLTALK light-routing, budget guard trong answer path |
| 006 | Telegram & Docker | **4.5** | 4.5 | Giữ nguyên — ngoài scope plan V2 |
| 004 | Human-in-the-Loop | **4.6** | 4.3 | V2-4 — risk-score 3 bậc chống approval-fatigue; đơn to (>5M) luôn pause (invariant) |
| 005 | Async Persistence & Memory | **4.6** | 4.4 | V2-4 — episodic memory cross-session; V2-5 metadata stamping customer/session/intent |

### Delta V2 (2026-08-03) — bằng chứng đo được

- **002 → 4.7**: WP-V2-0 gold set ~40 case + `scripts/eval_gate.sh` (Tier-R retrieval / Tier-F full-pipeline, baseline 2026-07-30, gate chặn -2pp); WP-V2-1 `_apply_groundedness` verify→regen→decline (`services/rag/pipeline.py:664`) + cascade verification (premium chỉ tiêu khi verdict fail, `core/agent/nodes/answer.py:232`); WP-V2-2 L1 cache key deterministic (keyed RAW query, `pipeline.py:612`) + fragment-level citations. **3 gap P2 cũ của 002 đóng 2** (citation fragment-level, L1 key determinism).
- **003 → 4.7**: WP-V2-3 `clarify_node` (1 câu hỏi lại, anti-loop `clarify_count`, `core/agent/nodes/clarify.py`) + LLM query decomposition cho multi-intent; WP-V2-5 SMALLTALK → light-chat + budget guard đầu Path 3 (`answer.py`); demo live: hỏi mơ hồ → bot hỏi lại → turn 2 trả đúng ASUS ROG RTX 4070.
- **004 → 4.6**: WP-V2-4 risk 3 bậc `risk = 0.4·(1-conf) + 0.4·value + 0.2·history` (`core/agent/nodes/hitl_guard.py:88`), tier 1 auto-approve / tier 3 luôn dừng; invariant an toàn: đơn > `HITL_HIGH_VALUE_ORDER_THRESHOLD` (5M) hoặc thiếu giá trị → luôn pause. Demo live: khách quen (CONVERTED) mua ốp 199k tự duyệt; MacBook 55tr pause chờ admin.
- **005 → 4.6**: WP-V2-4 episodic memory (nhớ khách quen cross-session, feed risk history term); WP-V2-5 `model_traces.metadata` stamp `customer_id/session_id/intent` (không cần migration).
- **Ops thêm (không tính điểm feature)**: WP-V2-5 `GET /admin/costs` (day/customer/model, p50/p95, cache hit-rate — đối chiếu khớp 100% raw SQL), `DAILY_COST_LIMIT_USD` (downgrade, không bao giờ chặn khách), `CUSTOMER_DAILY_MSG_CAP`.

### Verification tổng (2026-08-03)

1. ✅ `uv run pytest` (unit + integration): **549 pass / 5 skip / 0 fail** — 3 test lỗi thời cập nhật theo hành vi V2-1/V2-2/V2-4; `test_search_latency_10k` flaky khi máy tải nặng, pass khi chạy riêng.
2. ✅ Eval: Tier-R **34/34 (Δ0.0pp)**; Tier-F **11/12** — 1 case multi-intent dao động (grader groundedness borderline, xoay vòng mi_001/mi_002 giữa các run), cả 2 pass khi chạy riêng trên config production 70b; không có regression code (main không đổi từ lần 12/12).
3. ✅ Demo 3 kịch bản live qua `/agent/query` (server thật + Groq): (a) clarify 2 turn; (b) thuộc tính không tồn tại (HDMI/5G) → nói thẳng không có info, không bịa; (c) khách quen đơn nhỏ auto-approve / đơn to pause + pause_id.
4. ✅ `GET /admin/costs` khớp raw SQL `model_traces` chính xác (62 calls, 33.261 tokens, $0); auth 401/400/200 đúng.
5. Điểm 5 tuyệt đối cần: CI/CD + coverage gate + OTel per-node — backlog riêng (ngoài scope plan V2).

> **CR `agentic-rag-retry-loop`: ĐÃ MERGE** (S1→S6 DONE, archived; merge `dcd1470`). Retry loop chạy trong graph thật qua `retrieval_node` → `retrieve_with_retry` (`services/rag/pipeline.py:306`), kill-switch `RAG_RETRY_MAX_ATTEMPTS=0` (`core/config.py:69`, `.env.example:60`).

**Baseline test (main, 2026-07-16):** `uv run pytest tests/unit -q` → **341 passed, 0 failed** *(baseline trước WP: 268 pass / 1 fail)*.

---

## 001 — Project Infra Setup · **4.3 / 5** 🟢 *(cũ 3.8)*

| Sub-feature | Điểm | Cũ | Bằng chứng / Ghi chú |
|---|:--:|:--:|---|
| Docker / compose | **5** | 5 | Giữ nguyên: multi-stage, non-root, Docker secrets, HEALTHCHECK, PG tuning |
| Async DB engine | **4** | 4 | Giữ nguyên |
| Migrations base | **4** | 4 | Giữ nguyên |
| Health endpoint | **4** | 4 | `/readiness` 503 đúng; `/health` liveness-style (đã note chủ đích) |
| Config (pydantic-settings) | **4** | 3 | ✅ WP4: `ENV` Literal (`config.py:16`) + **fail-fast guard** — `ENV=production` với secret default → `RuntimeError` tại lifespan (`api/main.py:66-75`), dev chỉ warning; ✅ typo `deepseel-r1` → `deepseek-r1:1.5b` (`config.py:53`) |
| Logging / observability | **5** | 3 | ✅ WP4: stdout là **JSON structured** (`core/logging.py:174` `json.dumps(payload)`); ✅ PII masking `mask_email`/`mask_phone` + auto-mask trong message (`logging.py:69-98`) — hết vi phạm FR-008/security.md |

**Còn lại (minor):** default secrets vẫn tồn tại ở dev-mode (chấp nhận được vì có production guard).

---

## 002 — Vietnamese RAG & Eval · **4.2 / 5** 🟢 *(cũ 3.5)*

| Sub-feature | Điểm | Cũ | Bằng chứng / Ghi chú |
|---|:--:|:--:|---|
| Query normalization + ingest keyword | **5** | 5 | Giữ nguyên |
| **Agentic retry loop (mới)** | **4** | — | ✅ CR merged: self-eval → `rewrite_query` (economy-chat, ADR-002) → bounded retry ≤2 (`pipeline.py:306 retrieve_with_retry`), retry trace ghi lại; 37 unit test riêng |
| Hybrid RRF + Vietnamese FTS | **4** | 4 | Giữ nguyên; FTS except-block đã có `db.rollback()` (bug WP-005 fixed, integration 4/4) |
| Embedding + vector search | **4** | 4 | `model_version="v1.0"` vẫn hardcode (chưa trong scope WP) |
| Context compression | **4** | 4 | Threshold 0.25 giờ **khớp spec chính thức** (WP6 sync) |
| Citations grounding | **4** | 4 | Vẫn chunk-level (không phải fragment-level FR-011) — gap nhỏ còn lại |
| Evaluation CLI + gold dataset | **4** | 4 | Giữ nguyên |
| Semantic cache L1/L2 | **4** | 3 | ✅ WP5: **TTL freshness** `CACHE_TTL_SECONDS=3600` filter theo `created_at` (`semantic_cache.py:22-29`, 0 = tắt TTL) + `invalidate_cache()` gọi khi ingest (`semantic_cache.py:157`) — hết phục vụ giá cũ; ⚠️ L1 key qua canonical LLM vẫn non-deterministic |
| Confidence gating | **5** | 3 | ✅ WP6: **hết drift** — spec chính thức chấm 0.45 kèm rationale đầy đủ (bge-m3 band 0.35–0.70 + retry loop mitigation) tại `openspec/specs/rag-pipeline/spec.md:13-23`; spec-first repair đúng R-SDLC-001, commit `73ae6b8` tách riêng |
| Adaptive TopK + classification | **4** | 3 | ✅ WP6: bins `≤10/11-20/>20` giờ là spec chính thức — test khớp spec hợp lệ |

**Còn lại (minor):** `model_version` hardcode; citation fragment-level; L1 cache key determinism.

---

## 003 — Agentic Workflow · **4.3 / 5** 🟢 *(cũ 3.6)*

| Node / Sub-feature | Điểm | Cũ | Bằng chứng / Ghi chú |
|---|:--:|:--:|---|
| AgentState + contracts | **4** | 4 | Giữ nguyên |
| retrieval_node | **5** | 4 | ✅ Wired `retrieve_with_retry` với kill-switch; error handling giữ nguyên |
| memory_retrieval_node | **5** | 4 | ✅ WP1: signature `(state, config: RunnableConfig)` chuẩn LangGraph, db từ `configurable` (`memory_retrieval.py:23-29`) — recall hoạt động trong graph thật |
| confidence_node | **4** | 4 | Giữ nguyên |
| answer_node | **5** | 4 | ✅ WP3: **ModelTrace số thật** — `extract_llm_metrics` lấy `response.usage` + `completion_cost` + latency đo quanh call (`answer.py:192-205`, `services/ai.py:75-96 LLMUsageMetrics`) |
| Streaming (`astream_agent`) | **4** | 4 | Giữ nguyên |
| Checkpointer | **4** | 4 | Giữ nguyên |
| Tools + contract tests | **4** | 4 | Giữ nguyên |
| router_node | **5** | 3 | ✅ WP3: try/except quanh LLM call + `model_validate_json` → fallback `_FALLBACK_CLASSIFICATION` = INFO_QUERY + log warning (`router.py:62-79`) — không crash được vì LLM output |
| escalation_node | **4** | 3 | ✅ WP3: fallback thật tại point-of-use — premium fail → degrade economy-chat + `escalation_failure=True` trong state (`answer.py:197-201`); không health-probe riêng (đúng scope tối thiểu) |
| graph.py | **4** | 3 | ✅ WP3: recursion limit **enforced** — `AGENT_RECURSION_LIMIT = AGENT_MAX_TURNS * 4` truyền vào config (`graph.py:57,69`) |

**Còn lại (minor):** chưa có OTel span per-node (trace tổng vẫn qua logfire auto-instrument).

---

## 006 — Telegram & Docker · **4.5 / 5** 🟢 *(cũ 3.7)*

| Sub-feature | Điểm | Cũ | Bằng chứng / Ghi chú |
|---|:--:|:--:|---|
| Async webhook + agent wiring | **5** | 4 | ✅ WP5: hết GC footgun — module-level `_background_tasks: set` + `add_done_callback(discard)` (`api/webhooks/telegram.py:126-127`) |
| Webhook secret verification | **5** | 4 | ✅ WP5: compose **bắt buộc** secret từ env `${TELEGRAM_WEBHOOK_SECRET:?…}` — hết default yếu baked |
| Update dedup / idempotency | **4** | 4 | Giữ nguyên |
| Tool timeout guards | **4** | 4 | Giữ nguyên |
| Health checks | **4** | 4 | Giữ nguyên |
| Docker hardening / networking | **5** | 3 | ✅ WP5: **Ollama networking gap đã đóng** — `OLLAMA_BASE_URL` default `host.docker.internal:11434` + `extra_hosts: host-gateway` + full AI env passthrough (`CHAT_MODEL`…, `GEMINI_API_KEY` pass-through) trong `docker-compose.yml` → full-stack compose gọi được LLM (Ollama-trên-host HOẶC cloud fast-path chỉ cần API key, `.env.example:27-39`) |

**Còn lại (minor):** không có — feature này đạt mục tiêu demo E2E.

---

## 004 — Human-in-the-Loop · **4.3 / 5** 🟢 *(cũ 3.0)*

| Sub-feature | Điểm | Cũ | Bằng chứng / Ghi chú |
|---|:--:|:--:|---|
| queue_consumer_node | **4** | 4 | Giữ nguyên |
| order_execution + state_freshness | **4** | 4 | Giữ nguyên |
| API endpoints + auth | **4** | 4 | Giữ nguyên; ✅ WP6 admin key **header-only** `X-Admin-Key` (`api/routes/memory.py:135-144`, hết nhánh query-string dính access log) |
| Admin review flow (approve/reject/edit) | **5** | 3 | ✅ WP2: đủ 3 FR — (a) `_validate_state_edits` whitelist thật (`service.py:473-477`); (b) synthetic message **replace** theo id `admin_override:{field}:{pause_id}` qua add_messages reducer (`service.py:500-513`); (c) `acknowledged_message_ids` validate UUID + mark-ack để queue_consumer bỏ qua (`service.py:227,304,495`) |
| Paused session gateway | **5** | 3 | ✅ WP2: `BLOCKING_STATUSES = frozenset({paused, resuming, escalated})` (`api/dependencies.py:80,100`); `abandoned` cho qua như session mới |
| hitl_guard_node gating | **3** | 3 | Resume-vs-fresh detect bằng query DB — vẫn mong manh (ngoài scope WP) |
| Nightly archiving | **4** | 3 | ✅ WP2 (P2): neo giờ chạy `run_at_hour=2` UTC thay vì `sleep(24h)` trôi dạt (`archive_scheduler.py`) |
| Timeout & escalation scheduler | **5** | 2 | ✅ WP2 (P0-3): 30' **gửi Telegram thật** cho customer — parse `chat_id` từ `telegram_<chat_id>`, `send_telegram_message(chat_id, TIMEOUT_WARN_MESSAGE_VI)` (`timeout_scheduler.py:34-93`), không có kênh → warning rõ; ✅ `context_snapshot` escalation được điền thật trước khi flip status (`service.py:escalate_to_support`) |
| Cost guard | **5** | 2 | ✅ WP2: `litellm.token_counter(model=…, text=…)` + fallback heuristic chỉ khi lỗi (`cost_guard.py:47-58`) — đếm đúng tiếng Việt/Unicode |

**Còn lại (minor):** resume-vs-fresh detection của hitl_guard.

---

## 005 — Async Persistence & Memory · **4.4 / 5** 🟢 *(cũ 2.6)*

| Sub-feature | Điểm | Cũ | Bằng chứng / Ghi chú |
|---|:--:|:--:|---|
| Intent tracking optimistic-lock | **4** | 4 | Giữ nguyên |
| Intent extraction gating | **4** | 4 | Giữ nguyên; mock lỗi trong `test_intent_extractor.py` đã fix (suite xanh) |
| Checkpointer + schema-version guard | **4** | 3 | ✅ WP1: `check_checkpoint_size` **implement thật** — `SUM(octet_length(blob))` trên `checkpoint_blobs` theo thread_id, warn khi vượt `CHECKPOINT_SIZE_WARN_BYTES` (`background.py:29-75`, gọi tại `:412`) — hết no-op stub |
| Background dispatch / async | **4** | 3 | ✅ WP1: chain summarize→embed **đã nối** — embed chạy khi summary vừa persist trong turn (`background.py:206,294-296,365-373` `_summarize_and_embed`), hết cờ pre-turn luôn-False |
| Admin API (intent/semantic) | **4** | 3 | ✅ WP1: sort urgency đúng "hottest first" — `case()` HIGH=3>MEDIUM=2>LOW=1>UNKNOWN=0 desc (`api/routes/memory.py:246`) |
| Semantic memory retrieval | **5** | 2 | ✅ WP1 (P0-1): signature `(state, config)` + db từ `config["configurable"]["db"]` (`nodes/memory_retrieval.py:23-29`) — **recall hoạt động trong graph compiled thật**, có integration test qua graph |
| Conversation summarization | **5** | 2 | ✅ WP1 (P0-5): map đúng cột schema (`thread_id`) + `on_conflict_do_update(uq_summary_customer_thread)` upsert (`summarizer.py:186-194`) — save lần đầu VÀ re-summary đều persist; `budget_stated` persist thật (`:184`) |
| RTBF cascade delete | **5** | 1 | ✅ WP1 (P0-2): xoá **7 bảng** — semantic_memory, conversation_summaries, `sales_intent_logs`, intent_tracking + 3 bảng checkpoint LangGraph theo thread_id (`api/routes/memory.py:445-511`, to_regclass guard); bỏ gate `cust_` — nhận `tg:123`/chat_id số (`:359`) |

**Còn lại (minor):** không có gap chặn — feature từ "gãy E2E" lên ổn định.

---

## ✅ Trạng thái 5 bug chặn (P0) — TẤT CẢ ĐÃ ĐÓNG

| ID | Feature | Mô tả | Fix (bằng chứng) |
|---|---|---|---|
| P0-1 | 005 | `memory_retrieval_node` sai signature → recall rỗng | ✅ WP1 — `(state, config)` chuẩn LangGraph (`memory_retrieval.py:23`) |
| P0-2 | 005 | RTBF thiếu bảng, chặn ID Telegram | ✅ WP1 — 7 bảng + nhận mọi ID (`memory.py:445-511`) |
| P0-3 | 004 | Timeout 30' chỉ log | ✅ WP2 — gửi Telegram VI thật (`timeout_scheduler.py:88-93`) |
| P0-4 | 006 | Ollama networking gap trong compose | ✅ WP5 — `OLLAMA_BASE_URL` + `extra_hosts` + AI env passthrough (`docker-compose.yml`) |
| P0-5 | 005 | Summarization vi phạm UniqueConstraint | ✅ WP1 — upsert `on_conflict_do_update` (`summarizer.py:186`) |

## 🔥 Chủ đề xuyên suốt — tình trạng sau WP

1. **"Test xanh nhưng feature gãy"** → ✅ Đã có integration test chạy qua graph compiled thật (memory recall, retry loop pipeline); unit 341/0, integration retry-loop 4/4.
2. **Spec drift (R-SDLC-001)** → ✅ Đóng bằng spec-first repair chính thức (commit `73ae6b8`): confidence 0.45 + bins + compression 0.25 kèm rationale trong `openspec/specs/rag-pipeline/`.
3. **Observability "có mà rỗng"** → ✅ ModelTrace số thật (usage/cost/latency), cost guard đếm token thật, checkpoint-size đo bytes thật.
4. **Config/secret không fail-fast** → ✅ Production guard RuntimeError; compose bắt buộc webhook secret từ env.

## 📋 Gap còn lại (không chặn demo)

| Ưu tiên | Feature | Gap |
|---|---|---|
| P2 | 002 | `model_version="v1.0"` hardcode khi ingest *(citation fragment-level + L1 key determinism: ✅ đóng bởi WP-V2-2)* |
| P2 | 003 | Chưa có OTel span per-node; clarify_node chỉ kích hoạt với intent OTHER — câu mơ hồ bị router xếp INFO_QUERY đi đường escalation/answer (answer tự hỏi lại được, nhưng không qua merge-turn của clarify) |
| P2 | 004 | hitl_guard resume-vs-fresh detect bằng query DB — mong manh |
| P2 | WP6 | **`docs/demo-runbook.md` chưa được viết** (demo pack mới có `scripts/demo_seed.py`; 5 kịch bản demo chưa có runbook) |
| P2 | eval | 1 case multi-intent Tier-F dao động theo run (grader groundedness borderline với câu 2 ý) — cân nhắc nới verdict hoặc chạy decomposition trong Tier-F |
| P3 | — | CI/CD + coverage gate ≥80% chưa có (điều kiện lên điểm 5 tuyệt đối) |
