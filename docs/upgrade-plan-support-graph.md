# Plan: `support_graph` — graph CSKH cho Spacely (hướng 2)

Ngày: 2026-10-01. Trạng thái: bước 0–1 xong (commit), bước 2 trở đi chưa code.

## Mục tiêu

Widget chat trang chủ Spacely gõ tự do → AI thật. Dùng lại hạ tầng của repo
(RAG hybrid + pgvector, AIGateway, memory xuyên phiên, checkpointer, tracing
Phoenix, eval gate) nhưng **không** đi qua máy trạng thái bán hàng
(order / HITL / cancel / negotiation). Shop graph không đổi một dòng.

Ngoài phạm vi: gia sư trang learn (graph thứ 3, làm sau theo đúng khuôn này),
streaming token, UI admin cho queue hỗ trợ.

## Kiến trúc

```
Browser ──POST /api/v1/support/chat (Next.js, rate-limit, customer_id)──▶ FastAPI
                                                                          │
                                   POST /support/query (X-Agent-Key) ─────┘
                                                │
                     START → support_router → support_retrieval → confidence_node
                                                                         │
                                 ┌───────────────────────────────────────┤
                                 ▼                                       ▼
                        support_answer_node ◀── (clarify_node khi needs_clarification)
                                 │
                                END  →  post_turn_tasks (memory, nền)
```

Dùng lại nguyên: `retrieval_node` (hybrid search + cache + decomposition),
`memory_retrieval_node`, `confidence_node`, checkpointer,
`make_agent_config`, `_write_model_trace`, `post_turn_tasks`, `traced_node`.
Viết mới: `support_router_node`, `support_answer_node`, `support_clarify_node`
(logic của `clarify_node` nhưng prompt CSKH — prompt gốc nói "trợ lý bán hàng,
sản phẩm trong catalog"; dùng lại `_candidate_names`, `ClarifyingQuestion`,
`FALLBACK_CLARIFY_QUESTION`), `build_support_graph`, route `/support/*`, eval set.

## Bước 0 — Dọn thử nghiệm hướng 1 (30 phút)

Revert các vá vào graph shop, giữ phần dùng lại được:

| Giữ | Bỏ (git checkout) |
|---|---|
| `core/persona.py` (chỉ giữ SPACELY_SUPPORT prompt, bỏ ECOMMERCE + `intent_remap`/flags) | `core/agent/nodes/router.py`, `answer.py`, `confidence.py`, `clarify.py`, `customer_support.py`, `services/resilience.py` |
| `core/config.py`: `AGENT_API_KEY`; đổi `AGENT_PERSONA` → bỏ (không cần switch nữa) | `tests/unit/test_persona.py` (viết lại theo graph mới) |
| `api/dependencies.py::verify_agent_key` | |
| `services/ai.py` nhánh fastembed `local/` (sửa lỗi độc lập, tách commit riêng) | |
| `scripts/ingest_spacely_faq.py`, `scripts/run_spacely_local.sh` | |
| `.env.example`, `CHANGELOG.md` (sửa lại nội dung) | |

Lưu ý: repo đang có sửa đổi chưa commit của chủ repo (`api/routes/admin.py`,
`api/static/index.html`, `tests/unit/test_ui_routes.py`,
`tests/unit/test_admin_restock.py`) — không đụng, không gộp vào commit.

## Bước 1 — State và persona (1 giờ)

- Dùng lại `AgentState` (185 channel) để các node chia sẻ được dùng nguyên.
  Không tạo state mới: `retrieval_node`/`confidence_node` đọc-ghi các key
  `user_message`, `intent`, `retrieved_chunks`, `citations`,
  `similarity_score`, `similarity_gap`, `declined`, `needs_clarification`,
  `memory_context`, `clarify_*`.
- `core/support/persona.py`: `SPACELY_SUPPORT` với `router_system_prompt`,
  `answer_system_prompt`, `smalltalk_system_prompt`, `smalltalk_fastpath_reply`,
  `context_label = "Tài liệu Spacely"`, `decline_message`, `customer_cap_message`,
  `clarify_system_prompt`, `complaint_note`, `holding_message`. (Đã làm.)
- Đã kiểm tra: mọi channel graph support cần (`smalltalk_fastpath`,
  `cached_answer`, `memory_context`, `needs_clarification`, `clarify_*`,
  `risk_signals`, `turn_started_at`…) đều có sẵn trong `AgentState`.
- Intent cho support: `INFO_QUERY`, `PRICING`, `COMPLAINT`, `SMALLTALK`
  (dùng lại `IntentEnum`, không thêm giá trị mới để `intent_tracker`/memory
  không phải sửa).

## Bước 2 — `support_router_node` (2 giờ)

File `core/support/nodes/router.py`.

1. Fast-path: dùng lại `_smalltalk_fastpath()` của router shop (import hàm,
   không copy) → `goto=support_answer_node`, `smalltalk_fastpath=True`.
2. LLM classify: `AIGateway.complete_json(model="light-chat", schema=IntentClassification)`
   với prompt 4 intent. Có lịch sử 3 lượt gần nhất (dùng lại `_format_history`).
3. Chuẩn hoá: bất kỳ intent ngoài 4 cái → `INFO_QUERY`; COMPLAINT giữ nguyên
   (không escalation node, không HITL — xử lý bằng prompt ở answer).
4. Routing: `SMALLTALK → support_answer_node`; còn lại → `retrieval_node`.
5. Trả `Command(goto, update={intent, intent_confidence, secondary_intents=[]})`.

Không có: cancel keywords, hesitation flip, whitelist "còn hàng", sticky
transition table.

## Bước 3 — `support_answer_node` (3 giờ)

File `core/support/nodes/answer.py`. Các đường:

| Đường | Điều kiện | Hành vi |
|---|---|---|
| 0 | `response` đã có (clarify_node đặt) | trace rồi trả nguyên |
| 1 | `cached_answer` | trả cache, `model_used="cache"` |
| 2 | `smalltalk_fastpath` | template `smalltalk_fastpath_reply`, 0 LLM call |
| 3 | `declined` (confidence L1/L2) | `decline_message` + cờ `declined=True` |
| 4 | SMALLTALK | `economy-chat`, `smalltalk_system_prompt`, không context |
| 5 | INFO/PRICING/COMPLAINT | `economy-chat` với `"Tài liệu Spacely:\n{chunks}\n\n[Ngữ cảnh trước]\n{memory}\nCâu hỏi: …"`; COMPLAINT nối thêm `complaint_note` |

Sau khi sinh: groundedness self-check (dùng lại `_verify_grounded` nếu tách
được thành hàm public; nếu không, gọi `services.rag.groundedness` trực tiếp),
fail → `decline_message`. Ghi cache qua `services.semantic_cache` cho đường 5
không declined. Lỗi LLM → `holding_message`, `model_used=None`.

Không có: catalog fallback, follow-up order status, tool loop, cascade
premium, policy NEGOTIATION, CTA bán hàng.

## Bước 4 — `build_support_graph` + endpoint (2 giờ)

- `core/support/graph.py`: `StateGraph(AgentState)`, node đăng ký qua
  `traced_node` để Phoenix có span từng node. Edges: START→support_router;
  support_router→{retrieval_node | support_answer_node};
  retrieval_node→memory_retrieval_node→confidence_node;
  confidence_node→{support_clarify_node | support_answer_node} (hàm route riêng:
  `needs_clarification` → clarify, còn lại → answer; **không** hitl/escalation);
  support_clarify_node→support_answer_node; support_answer_node→END.
- `api/main.py` lifespan: `app.state.support_graph = build_support_graph(checkpointer)`
  (cùng checkpointer; `thread_id` khác prefix nên không đụng session shop).
- `api/routes/support.py`: `POST /support/query` (body/response giống
  `AgentQueryRequest/Response` rút gọn: `answer`, `declined`, `intent`,
  `session_id`, `elapsed_ms`, `citations=[{name}]`), dependency
  `verify_agent_key`, `using_attributes(session_id, user_id)` cho Phoenix,
  sau trả lời chạy `post_turn_tasks` nền (memory). Không có
  `check_paused_session`.
- Giữ `/agent/*` nguyên.

## Bước 5 — Dữ liệu và chạy local (1 giờ)

- DB riêng `spacely_agent` (đã tạo), `scripts/run_spacely_local.sh` giữ, bỏ
  `AGENT_PERSONA`, thêm `SUPPORT_GRAPH_ENABLED=true` (kill switch, default
  false để deploy shop không mở endpoint thừa).
- `scripts/ingest_spacely_faq.py` giữ nguyên (nguồn: Next.js
  `GET /api/v1/support/knowledge`).
- Model: `.env` đang trỏ `groq/llama-3.3-70b-versatile` + `llama-3.1-8b-instant`
  đã bị Groq gỡ. Đổi sang `groq/openai/gpt-oss-120b` (chat) và
  `groq/openai/gpt-oss-20b` (light). Việc này ảnh hưởng cả shop, cần chủ repo
  quyết.

## Bước 6 — Test và eval (3 giờ)

- Unit (mock LLM, Postgres test như hiện tại): router 4 intent + fast-path +
  fallback khi JSON hỏng; answer 6 đường; route sau confidence không bao giờ
  trả `hitl_guard_node`; `/support/query` 401 khi thiếu key, 200 happy path.
- Eval: `tests/eval/support_gold.json` ~20 câu tiếng Việt (mỗi FAQ 1 câu diễn
  đạt khác + 3 câu ngoài phạm vi phải decline + 2 khiếu nại không được hứa
  hoàn tiền). Chạy bằng `scripts/eval_gate.py --tier f --dataset …` (thêm
  tham số `--graph support`). Baseline commit lần đầu.
- Smoke thủ công 6 câu đã dùng hôm nay: chào / clone / giá credit / "mua 2
  iPhone" (phải từ chối ngoài phạm vi) / khiếu nại mất credit (phải xin lỗi +
  hướng sang người thật, không hứa hoàn) / giải toán (từ chối).

## Bước 7 — Phía elearning-platform (30 phút)

Đã có: `GET /support/knowledge`, `POST /support/chat`, widget 2 chế độ,
`lib/supportChat.ts`. Chỉ sửa: proxy gọi `/support/query` thay vì
`/agent/query`; map `citations[].name` nếu muốn hiện "Nguồn: …".

## Rủi ro

- `confidence_node` có nhánh riêng cho ORDER (extract order_info) — không
  kích hoạt vì intent không bao giờ là ORDER; vẫn thêm test khẳng định.
- `retrieval_node` prompt decomposition nói "product catalog" — chấp nhận,
  chỉ ảnh hưởng câu ghép; theo dõi qua eval.
- Rate-limit Groq free (30 RPM): mỗi lượt support tốn 2–3 call (router,
  answer, groundedness). Đã có `LLM_RPM_LIMIT=28`; ở widget thì 15 tin/phút/
  người là trần an toàn.

## Ước lượng

Khoảng 2 ngày làm việc: bước 0–4 ngày 1, bước 5–7 ngày 2.
