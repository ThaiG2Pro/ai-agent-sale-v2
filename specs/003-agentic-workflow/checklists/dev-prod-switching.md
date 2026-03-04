# Dev→Prod Switching Checklist: Agentic Workflow & Safe Logic

**Purpose**: Validate that spec/plan/data-model requirements adequately specify every dev→prod switching boundary — specifically: are stubs, type contracts, env-var gates, and deferred interfaces stable enough that switching requires **zero code refactoring**, only config/env changes? Each item tests whether the *requirements are written well enough*, not whether the implementation works.

**Created**: 2026-03-03  
**Feature**: [spec.md](../spec.md) | [plan.md](../plan.md) | [research.md](../research.md) | [data-model.md](../data-model.md)  
**Audience**: Reviewer (PR gate)  
**Scope**: Code-refactoring-risk only — stubs, type contracts, interface stability  
**Risk Focus**: Stub interface contracts (Week 3→6) AND model alias / env-var config layer

---

## 1. Checkpointer Switching (`MemorySaver` → `AsyncPostgresSaver`, Week 5)

> Research Decision 5 gates `AsyncPostgresSaver` behind `ENVIRONMENT=prod`. The graph is designed to "accept a checkpointer so Week 5 can swap it in without code changes." These items test whether the requirements make that claim verifiable.

- [X] CHK001 — Is the `build_graph(checkpointer=...)` interface explicitly documented as the **only** injection point for the checkpointer, ensuring a single-line swap from `MemorySaver()` to `AsyncPostgresSaver()` without touching node code? [Clarity, Research Decision 5]

why : tài liệu hóa rõ ràng trong Task T050 (core/agent/graph.py).

- [ ] CHK002 — Does the spec define what PostgreSQL schema `AsyncPostgresSaver` requires (table names, columns, migration file location)? Without this, Week 5 may need schema discovery work that is currently uncosted. [Gap, Completeness]

answer : Tài liệu techniques-overview.md (Mục 4) nhắc đến bảng agent_checkpoints. Task T049 xác định các trace sẽ được ghi vào schema agent_v1. Lưu ý: Vì dự án sử dụng AsyncPostgresSaver tiêu chuẩn của LangGraph, các bảng này thường được tự động khởi tạo hoặc đi kèm schema mặc định của thư viện. Tuy nhiên, vị trí tệp migration  (alembic/) chưa được chỉ định cụ thể trong các task tuần 3 (thường nằm ở tuần 5 theo lộ trình).

- [X] CHK003 — Is `AgentState` TypedDict documented as **serialization-stable for JSONB** — specifically, are there requirements that prohibit adding non-JSON-serializable types (e.g., Python objects, datetime without ISO format) to the state? [Completeness, data-model.md §1]

why :  tài liệu hóa rất kỹ trong techniques-overview.md (Mục 1).

- [X] CHK004 — Does the spec define the mapping between `session_id` (UUID7 string in `AgentState`) and `thread_id` (LangGraph checkpointer key)? If they are different values, Week 5 state retrieval logic will require code changes. [Clarity, Gap]

why : Task T053 (cli/run_agent.py) định nghĩa rõ việc ánh xạ: config = {"configurable": {"thread_id": session}}. thread_id là khóa để truy xuất lịch sử cuộc hội thoại.

- [X] CHK005 — Are the requirements for handling **pre-existing MemorySaver state** (e.g., active dev sessions) documented for the Week 5 cut-over? Is data migration in or out of scope? [Edge Case, Gap]

why : techniques-overview.md nêu rõ MemorySaver chỉ dùng cho dev local và sẽ "Mất dữ liệu khi restart".  Vì dữ liệu trong MemorySaver là transient (tạm thời), việc di cư dữ liệu sang Postgres ở tuần 5 được hiểu là không cần thiết (không có dữ liệu cũ để mang theo). Tài liệu tập trung vào "Schema Evolution" (nâng cấp cấu trúc state) hơn là migration data từ memory.

- [X] CHK006 — Is the `ENVIRONMENT=prod` env-var gate documented with its **full scope of effects** — does setting it switch only the checkpointer, or also model aliases, observability backends, and other prod behaviors? [Ambiguity, Clarity]

why : Observability: Mục 10.3 trong techniques-overview.md hướng dẫn dùng ENV để toggle giữa Phoenix (Dev) và Logfire/LangSmith (Prod). Reranker: Mục 2 quy định: Dev/Local dùng Local Reranker, nhưng Prod/Staging PHẢI dùng Async Rerank API. Checkpointer: Được cấu hình thông qua việc tiêm AsyncPostgresSaver thay vì MemorySaver dựa trên config hệ thống.

---

## 2. Reranker Switching (`RERANKER_ENABLED=false` → CrossEncoder → API Reranker, Week 7)

> Research Decision 8 makes the reranker opt-in. These items test whether the requirements protect the `confidence_node` interface from requiring code changes when the reranker is eventually enabled.

- [X] CHK007 — Is `rerank_score: float | None` in `AgentState` documented as **permanently optional** (never becoming `float` required) so enabling the reranker at any point does not require updating callers that currently pass `None`? [Clarity, data-model.md §1, Gap]

why : Task T017 định nghĩa trong AgentState: rerank_score: float | None. Task T047 và T059 chỉ định rõ logic xử lý: "nếu rerank_score là None (trường hợp Dev hoặc tắt reranker), điểm tin cậy confidence_score sẽ lấy bằng similarity_score".

- [X] CHK008 — Does the spec define the **min-max scaling contract** for rerank scores before fusion? Specifically: are the expected input ranges of CrossEncoder (local, Week 7) vs. Cohere/Jina API (prod) scores documented so the fusion formula `(1-α)·similarity + α·rerank` remains valid across both? [Clarity, Research Decision 4]

why : Tài liệu techniques-overview.md (Mục 7) quy định rõ: "Min-Max Scaling trước khi fusion: $X_{norm} = \frac{X - X_{min}}{X_{max} - X_{min}}$".

- [X] CHK009 — Is the `anyio.to_thread.run_sync` pattern **required** for CrossEncoder in dev, and is the **prod equivalent** (async API call) documented as a drop-in replacement with the same `rerank_score: float | None` output type? [Consistency, plan.md Blocking Policy]

why :  techniques-overview.md (Mục 2) phân biệt rõ: Dev sử dụng CrossEncoder cục bộ (CPU-bound), Prod sử dụng Async API (Cohere/Jina). Tài liệu cũng nhắc đến việc chuẩn hóa Logits của CrossEncoder về dải [0, 1] bằng hàm Sigmoid: $\sigma(x) = \frac{1}{1 + e^{-x}}$.

- [X] CHK010 — Does the spec define what happens to the **`AGENT_ALPHA=0.7` parameter** when switching from dev (no reranker, α falls back to 0) to prod (reranker enabled, α=0.7)? Is a config-only change sufficient, or does the fusion formula need code modification? [Clarity, Research Decision 4, Gap]

why : Tài liệu techniques-overview.md (Mục 2) ghi rõ: "Dev/Local: Local Reranker với anyio.to_thread.run_sync. Prod/Staging: PHẢI dùng Async Rerank API".

- [ ] CHK011 — Is `RERANKER_ENABLED` documented as a **per-environment flag** (`.env` or `ENVIRONMENT`-based), or can it be toggled mid-session? Requirements should define whether enabling it mid-deployment requires a restart. [Completeness, Gap]

why : Task T059 định nghĩa "α=0 fallback" khi không có reranker (rerank_score=None).Công thức thực tế trong mã nguồn (T047) được thiết lập để tự động bỏ qua $\alpha$ nếu không có điểm rerank: if rerank_score is not None then ... else confidence = similarity.

---

## 3. Model Alias Contract Stability (Ollama Local → Staging/Prod API)

> Spec Assumption: "economy-chat" and "premium-local-chat" refer to LiteLLM aliases. These items test whether the alias contract is specified as stable across environments.

- [X] CHK012 — Are the string values `"economy-chat"` and `"premium-local-chat"` documented as **environment-stable alias names** — i.e., the same string works in dev (Ollama) and prod (API), only the underlying model mapping changes in `LITELLM_CONFIG`? [Clarity, Spec Assumptions]

why : Task T018 & T044: Sử dụng trực tiếp chuỗi "economy-chat" làm giá trị mặc định cho đầu vào RAG và node định tuyến. Contract `rag_tool.md`: Liệt kê rõ các giá trị ví dụ cho model_used là "economy-chat", "premium-local-chat", hoặc "cache". Task T063: Sử dụng settings.POWERFUL_CHAT_MODEL (một alias cấu hình) thay vì hardcode tên mô hình cụ thể của nhà cung cấp.

- [X] CHK013 — Does the spec define **who owns and how `LITELLM_CONFIG` is updated** when moving from dev to prod? Is this documented as a config-file change, env-var override, or requires code change to `core/ai_config.py`? [Completeness, Gap]

why : Task T009: Định nghĩa việc thêm các trường cấu hình vào core/config.py.Techniques-overview.md (Mục 5): Cung cấp ví dụ về cách cấu hình litellm.Router với model_list ánh xạ từ model_name (alias) sang litellm_params (mô hình thật như ollama/qwen3 hoặc       gpt-4o)

- [X] CHK014 — Is the **Ollama G1 constraint** (one model in VRAM at a time — Research Decision 10) documented as **dev-only** or also applicable in prod? If it applies in staging/prod (e.g., resource-constrained VPS), does the `economy-chat`-only router requirement carry over, and is this specified? [Ambiguity, Research Decision 10]

why : Task T044: Ghi rõ: "Critical: dùng economy-chat KHÔNG dùng light-chat (Ràng buộc Ollama G1 — Research Decision 10)". Staging/Prod: Tài liệu techniques-overview.md (Mục 5) chỉ ra rằng Staging/Prod ưu tiên sử dụng Cloud API (Groq/OpenRouter). Trong trường hợp này, ràng    buộc G1 không còn hiệu lực kỹ thuật (vì Cloud API xử lý song song nhiều model).Yêu cầu dùng economy-chat cho Router vẫn giữ nguyên ở mọi môi trường để đảm bảo tính nhất quán của bí danh (alias) model. Việc bí danh đó trỏ đến model nào (Ollama localhay Groq Cloud) sẽ do tệp .env quyết định.
Triết lý Zero-Cost-First yêu cầu hệ thống phải chạy được 100% offline (0 VND). Do đó, ràng buộc G1 được coi là mặc định cho bản cài đặt cơ bản nhất. Nếu Staging/Prod được triển khai theo cấu hình "Zero-Cost" trên một VPS yếu, yêu cầu economy-chat-only cho Router sẽ tự động giúp hệ thống sống sót mà không cần cấu hình thêm mã nguồn.

- [X] CHK015 — Does the spec clarify whether `model_used` stored in `AgentState` and `model_traces` contains the **LiteLLM alias** (e.g., `"economy-chat"`) or the **resolved model name** (e.g., `"ollama/qwen3-1.7b"`)? This affects log analysis and cost attribution across environments. [Ambiguity, Spec §FR-008]

why : Task US1 (Independent Test): Yêu cầu kiểm tra kết quả đầu ra phải có model_used == "economy-chat". Task US2 (Independent Test): Yêu cầu xác minh model_used là "premium model alias" .Techniques-overview.md (Mục 7): Xác định model_used là một trong "4 chỉ số vàng" bắt buộc lưu trong State để phân tích hiệu quả của từng tầng mô hình.

- [ ] CHK016 — Are the **premium model fallback requirements** (FR-007: `escalation_failure` flag) specified for staging (where a real premium API may be rate-limited) vs. dev (where "premium" is just a larger Ollama model)? The behavior spec should distinguish these environments. [Completeness, Spec §FR-007]

answer : Task T064: Quy định logic chung: "Nếu POWERFUL_CHAT_MODEL không khả dụng (kiểm tra qua LiteLLM router hoặc config), hạ cấp xuống economy-chat và ghi nhận escalation_failure=True." Task T070: Yêu cầu viết unit test cho trường hợp "unavailable", bất kể nguyên nhân là gì. 
Tuy nhiên, tài liệu chưa có chỉ dẫn riêng biệt cho việc cấu hình retry_policy khác nhau cho
từng môi trường (ví dụ: Staging cần backoff lâu hơn API thật).

---

## 4. Inventory Stub → Real ERP Interface Stability (Week 3 → Week 6)

> `inventory_lookup` is a stub in Week 3. These items test whether the stub's Pydantic contract is specified as frozen enough that Week 6 integration requires zero `AgentState` or node code changes.

- [X] CHK017 — Is `InventoryLookupOutput` explicitly documented as **schema-frozen** between Week 3 and Week 6? Without this, the Week 6 ERP integration team may add/rename fields that break agent state reducers. [Gap, contracts/inventory_tool.md]

- [X] CHK018 — Does the spec define the **minimum interface the stub must maintain** for a zero-refactor Week 6 drop-in? Specifically: is it documented that Week 6 only replaces the tool's internal implementation (not its Pydantic I/O schemas)? [Clarity, contracts/inventory_tool.md, Gap]

why : Trong contracts/inventory_tool.md ghi rõ: "The contract defines the interface boundary so that when the real integration is built (Week 6+), zero refactoring of the agent state
or nodes is required.". Đánh giá: Mặc dù không dùng từ "schema-frozen", nhưng cam kết "zero refactoring" (không chỉnh sửa mã nguồn) buộc Schema Pydantic (InventoryLookupInput/Output) phải được giữ nguyên. Tuần 6 chỉ được phép thay thế logic bên trong (internal implementation) chứ không được chạm vào hợp đồng I/O.

- [X] CHK019 — Are the **contract tests** (T035–T040) documented as the **primary gate** that will catch Week 6 breaking changes before they reach the agent? Is this requirement traceable in spec (FR-009)? [Traceability, Spec §FR-009]

why : contracts/inventory_tool.md dẫn chiếu: "Article IV + FR-009 mandate contract tests before tool logic. Task T040 và T041 định nghĩa việc chạy test hợp đồng để phát hiện sai lệch (Schema Drift) ngay từ giai đoạn Red (thất bại) đến Green (vượt qua).

- [ ] CHK020 — Does the spec define whether `inventory_lookup` uses the **same `make_xxx_tool(db)` factory closure pattern** as `make_rag_tool(db)`, ensuring consistent DB-session injection when the real tool requires database access in Week 6? [Consistency, data-model.md §7, Gap]

CÓ KHOẢNG TRỐNG (Gap) giữa hướng dẫn module tổng quát và task thực thi cụ thể cho inventory.: Task T042 quy định rõ: make_rag_tool(db: AsyncSession). Task T043 chỉ ghi: inventory_lookup stub tool ... Always returns .... Không nhắc đến việc bọc trong factory make_inventory_tool(db). Tuy nhiên, docstring module tại T022 lại ghi: "DB session injected via factory closure pattern".

- [ ] CHK021 — Are there requirements for **Week 6 error scenarios** that don't exist in the Week 3 stub? For example: partial inventory data, multi-warehouse aggregation failures, or ERP timeout behavior. Is `InventoryLookupOutput.error` field designed to handle all anticipated Week 6 failure modes? [Completeness, Edge Case]

ĐÃ BAO PHỦ CƠ BẢN, nhưng thiếu các trường hợp đặc thù của ERP. Chi tiết: contracts/inventory_tool.md đã có Scenario 3 (429), 4 (500), 5 (Timeout). Trường InventoryLookupOutput.error được thiết kế để chứa thông báo lỗi thay vì ném exception (graceful failure).
Khoảng trống: Chưa có yêu cầu cho lỗi "dữ liệu tồn kho không đầy đủ" (partial data) hoặc "lỗi tổng hợp kho" (aggregation failure). Tuy nhiên, thiết kế available: bool và error: str | None đủ linh hoạt để xử lý các mode thất bại này bằng cách đặt available=False và ghi chi tiết vào error.

---

## 5. Streaming Interface Stability (In-Process CLI → Telegram Webhook, Week 6)

> Spec defers Telegram webhook streaming to Week 6. These items test whether `NodeStreamEvent` and `astream_agent()` are specified as stable enough that Week 6 requires zero refactoring.

- [ ] CHK022 — Is `NodeStreamEvent` (fields: `node_name`, `state_snapshot`, `timestamp`) documented as **schema-frozen** until at least Week 6, so the Telegram webhook integration team has a stable contract to build against? [Gap, Spec §FR-006]

KHOẢNG TRỐNG (Gap) : Task T080 định nghĩa cấu trúc NodeStreamEvent (gồm node_name, state_snapshot, timestamp) và Task T084 yêu cầu kiểm tra tính hợp lệ của các trường này, nhưng không có yêu cầu rõ ràng rằng cấu trúc này sẽ không thay đổi cho đến khi Telegram Webhook được triển khai. Việc thiếu cam kết này tạo ra một khoảng trống (Gap) trong tài liệu, vì nó không đảm bảo rằng đội ngũ phát triển Telegram Webhook sẽ có một hợp đồng ổn định để làm việc, dẫn đến nguy cơ phải sửa đổi mã nguồn nếu cấu trúc NodeStreamEvent thay đổi trước Tuần 6.

- [ ] CHK023 — Does the spec define whether `state_snapshot` in `NodeStreamEvent` is a **full AgentState snapshot** or a **delta** (only changed fields)? This significantly affects Week 6 webhook payload size and bandwidth cost. [Ambiguity, Spec §FR-006]

MƠ HỒ (Ambiguity) : Trong techniques-overview.md (Mục 6), có phân biệt các chế độ: values (Full Snapshot) và updates (Delta). Tuy nhiên, trong Task T081, hàm astream_agent sử dụng astream_events phiên bản v2 và bắt sự kiện on_chain_end. Trong LangGraph, dữ liệu trả về từ on_chain_end của một node thường là giá trị trả về của node đó (tương đương với một Delta/Update), chứ không phải toàn bộ AgentState. Việc thiếu rõ ràng về việc state_snapshot có phải là một bản sao đầy đủ của AgentState hay chỉ là một delta (các trường đã thay đổi) tạo ra sự mơ hồ, vì nó ảnh hưởng đến cách đội ngũ Telegram Webhook sẽ xử lý dữ liệu và tối ưu hóa payload của họ.

- [ ] CHK024 — Is `astream_agent()` function signature documented with explicit parameter stability commitments? Specifically: will `(message, session_id, db, checkpointer)` parameters remain stable, or might Week 5 persistent memory add new required parameters? [Completeness, Gap]

KHOẢNG TRỐNG (Gap) : Task T081 cung cấp boilerplate: async def astream_agent(message, session_id, db, checkpointer). Không có cam kết về tính ổn định của tham số. Khi Tuần 5 bổ sung bộ nhớ dài hạn (Persistent Memory) hoặc Tuần 6 bổ sung tích hợp ERP, chữ ký hàm này có nguy cơ bị thay đổi, gâyphá vỡ các nơi gọi hiện tại (như CLI ở T082). Việc thiếu cam kết rõ ràng về tính ổn định của tham số tạo ra một khoảng trống trong tài liệu, vì nó không đảm bảo rằng các nhà phát triển sẽ có một hợp đồng ổn định để làm việc, dẫn đến nguy cơ phải sửa đổi mã nguồn nếu chữ ký hàm astream_agent thay đổi trước Tuần 6.

- [ ] CHK025 — Does the spec define **how streaming events will be delivered in Week 6** (Server-Sent Events, WebSocket, Telegram polling)? Without this, the `NodeStreamEvent` schema may need breaking changes to add transport-specific metadata. [Gap, Completeness]

ĐÃ CÓ ĐỊNH HƯỚNG KỸ THUẬT nhưng chưa có đặc tả cho Telegram. techniques-overview.md (Mục 6) mô tả rất kỹ về Server-Sent Events (SSE) kết hợp với FastAPI như một tiêu chuẩn cho backend. Tài liệu đang tập trung vào SSE cho giao diện Web. Đối với Telegram (Tuần 6), vốn hoạt động qua Webhook hoặc Polling, SSE không phải là phương thức truyền tải phù hợp. Schema NodeStreamEvent hiện tại chưa tính đến các metadata đặc thù của Telegram (ví dụ: chat_id, message_id để cập nhật tin nhắn đang gõ). Việc thiếu định nghĩa rõ ràng về cách sự kiện sẽ được truyền tải trong Tuần 6 tạo ra một khoảng trống trong tài liệu, vì nó không đảm bảo rằng đội ngũ phát triển Telegram Webhook sẽ có một hợp đồng ổn định để làm việc, dẫn đến nguy cơ phải sửa đổi mã nguồn nếu cách thức truyền tải sự kiện thay đổi trước Tuần 6.

---

## 6. HITL Interrupt Readiness (Week 3 Escalation Node → Week 4 `interrupt_before`)

> Plan Article VIII: "HITL deferred to Week 4. Week 3 adds the escalation node that Week 4 will interrupt." These items test whether the requirements make that claim specific enough to execute.

- [ ] CHK026 — Does the spec identify **which specific node(s)** Week 4 will `interrupt_before`? "The escalation node" is mentioned in Article VIII but FR-002/FR-005 do not explicitly document the interrupt target, creating ambiguity for Week 4 planning. [Ambiguity, plan.md Article VIII, Gap]

ĐÃ CÓ ĐỊNH HƯỚNG nhưng còn mâu thuẫn nhẹ. Tài liệu techniques-overview.md (Mục 9.1 và 10.1) xác định các node đích là order_node và checkout_node. Tuy nhiên, dự án (theo project-log.md) lại nhắc đến việc dừng trước "escalation node" để kiểm soát rủi ro. Có sự mơ hồ (Ambiguity) giữa việc dừng ở node nghiệp vụ (order_node) hay dừng ở node kỹ thuật (escalation_node). Tuần 3 đã chuẩn bị sẵn hạ tầng checkpointer (T050), nhưng danh sách node interrupt_before chính thức thường sẽ được chốt vào đầu Tuần 4. Việc thiếu xác nhận rõ ràng về node nào sẽ là mục tiêu của `interrupt_before` tạo ra một khoảng trống (Gap) trong tài liệu, vì nó không đảm bảo rằng đội ngũ phát triển Tuần 4 sẽ có một hợp đồng ổn định để làm việc, dẫn đến nguy cơ phải sửa đổi mã nguồn nếu node interrupt target thay đổi sau khi Tuần 3 đã hoàn thành. 

- [X] CHK027 — Is the **`escalation_node` state output** documented as stable for Week 4 HITL? Specifically: does the spec guarantee that `EscalationDecision` fields will not change between Week 3 and Week 4, so Week 4 can build the `interrupt_before` logic without revisiting Week 3 code? [Completeness, data-model.md §2]

why : Task T016 định nghĩa mô hình EscalationDecision với các trường: escalate, reason, selected_model. Task T063 yêu cầu node này trả về đúng cấu trúc delta state dựa trên model đó.
Đánh giá: Việc sử dụng Pydantic làm ranh giới (boundary model) đảm bảo Tuần 4 có thể tin tưởng vào cấu trúc dữ liệu này để xây dựng logic phê duyệt mà không cần quay lại sửa code Tuần 3. Tuy nhiên, tài liệu có thể được cải thiện bằng cách thêm một tuyên bố rõ ràng rằng cấu trúc của EscalationDecision sẽ không thay đổi trong Tuần 4, đảm bảo rằng đội ngũ phát triển Tuần 4 có một hợp đồng ổn định để làm việc, dẫn đến việc xây dựng logic `interrupt_before` mà không cần phải sửa đổi mã nguồn của Tuần 3. 

- [X] CHK028 — Are requirements defined for how **resumed state** (post-HITL human approval in Week 4) re-enters the graph — specifically, which node does execution resume at after `interrupt_before=["escalation_node"]`? Week 3 should leave a comment or stub hook; is this documented? [Gap, Edge Case]

why : Mục 10.1 của techniques-overview.md mô tả chi tiết quy trình Resume : Lưu HITLCheckpoint bao gồm interrupted_node. Khi resume: gọi graph.invoke(input=None, config=...). Cơ chế mặc định của LangGraph là sẽ thực thi đúng node đã bị interrupt trước đó. Đánh giá: Mặc dù Tuần 3 chưa cài đặt logic này, nhưng tài liệu kỹ thuật đã định nghĩa rõ ràng "hợp đồng resume": Agent sẽ tiếp tục tại chính node bị dừng sau khi con người cập nhật trạng thái. 

---

## 7. AgentState Schema Evolution & Compatibility

> `AgentState` TypedDict is persisted by `AsyncPostgresSaver` (Week 5+). These items test whether requirements protect against breaking schema changes that corrupt persisted state.

- [X] CHK029 — Is `AgentState` documented as **append-only** (new fields may be added, existing fields may not be removed or renamed) once `AsyncPostgresSaver` begins persisting it in Week 5? Requirements should specify this constraint to prevent future data corruption. [Gap, Completeness]

ĐÃ ĐƯỢC TÀI LIỆU HÓA. Chi tiết: Tài liệu techniques-overview.md (Mục 1, phần "Schema Evolution") quy định rõ các thay đổi an toàn và không an toàn: An toàn: thêm trường có default, xóa trường không dùng. Không an toàn: đổi tên trường, thay đổi kiểu dữ liệu, thêm trường bắt buộc không có default. Đánh giá: Đây chính là định nghĩa kỹ thuật của ràng buộc "append-only" (hoặc tương thích ngược) để bảo vệ dữ liệu JSONB trong Postgres không bị hỏng khi đọc lại các phiên cũ. Tuy nhiên, tài liệu có thể được cải thiện bằng cách thêm một tuyên bố rõ ràng rằng một khi `AgentState` bắt đầu được `AsyncPostgresSaver` sử dụng để lưu trữ, thì cấu trúc của `AgentState` sẽ phải tuân thủ nguyên tắc "append-only" để đảm bảo rằng các thay đổi trong tương lai không làm hỏng dữ liệu đã lưu.

- [X] CHK030 — Does spec/data-model define how `AgentState` handles **new optional fields** added in Week 4/5 when reading state records created in Week 3? Are `None` defaults sufficient, or does a migration script need to be specified? [Edge Case, Gap]

why : ĐÃ CÓ GIẢI PHÁP MẪU. Chi tiết: techniques-overview.md cung cấp một "State Schema Versioning Pattern" hoàn chỉnh. Nó bao gồm: Sử dụng state.get("field", default) (lập trình phòng vệ).Hàm migrate_state_to_v2 để nạp các giá trị mặc định cho các tương tác cũ khi hệ thống nâng cấp. Đánh giá: Đặc tả đã tính đến kịch bản đọc state cũ ở Tuần 5 và cung cấp cơ chế xử lý bằng code (migration helper) thay vì chỉ dựa vào None mặc định. Tuy nhiên, tài liệu có thể được cải thiện bằng cách thêm một tuyên bố rõ ràng rằng khi các trường mới được thêm vào `AgentState`, thì logic đọc state phải bao gồm cơ chế để xử lý các bản ghi cũ không có trường đó (ví dụ: sử dụng `state.get("new_field", default_value)` hoặc gọi hàm migration) để đảm bảo rằng hệ thống vẫn hoạt động bình thường khi đọc dữ liệu đã lưu trước đó.

- [ ] CHK031 — There is a **discrepancy between FR-001 and data-model.md §1**: FR-001 lists `conversation_history (List of Message models)` as a required `AgentState` field, but data-model.md §1 defines only `messages: Annotated[list, add_messages]` with no `conversation_history`. Is this intentional (rename) or a requirements conflict requiring reconciliation? [Conflict, Spec §FR-001 vs. data-model.md §1]

CÓ XUNG ĐỘT (Conflict) giữa FR-001 và logic thực thi. Chi tiết: FR-001 (và Task T055): Yêu cầu trường conversation_history. Data-model §1 (và Task T017): Chỉ định nghĩa trường messages: Annotated[list, add_messages]. Đánh giá: Đây là lỗi không nhất quán về thuật ngữ. Trong LangGraph, trường messages chính là lịch sử cuộc hội thoại. Việc FR-001 yêu cầu cả hai hoặc yêu cầu một tên khác với tên chuẩn của LangGraph (messages) sẽ gây nhầm lẫn khi triển khai reducers. Tài liệu có thể được cải thiện bằng cách sửa FR-001 để chỉ yêu cầu `messages: Annotated[list, add_messages]` và loại bỏ tham chiếu đến `conversation_history`, đảm bảo rằng tất cả tài liệu đều sử dụng cùng một thuật ngữ cho trường lưu trữ lịch sử cuộc hội thoại trong `AgentState`.

- [ ] CHK032 — Is the `escalation_failure` flag (mentioned in spec edge cases and FR-007) documented as a field in `AgentState`? It does not appear in data-model.md §1's TypedDict definition, creating an incomplete requirements specification. [Gap, Spec edge cases vs. data-model.md §1]

KHOẢNG TRỐNG (Gap). Chi tiết:  Task T064 yêu cầu: "set escalation_failure=True in state". Tuy nhiên, định nghĩa AgentState trong Task T017 lại không có trường này. Danh sách chỉ dừng lại ở escalation_flag và escalation_reason. Đánh giá: Đặc tả chưa hoàn thiện. Nếu không có trường này trong TypedDict, node escalation_node sẽ gây lỗi kiểu dữ liệu (static type error) khi cố gắng cập nhật state. Tài liệu có thể được cải thiện bằng cách thêm trường `escalation_failure: bool` vào định nghĩa `AgentState` trong data-model.md §1, đảm bảo rằng tất cả tài liệu đều nhất quán về việc có trường này trong state và tránh lỗi kiểu dữ liệu khi cập nhật state trong node escalation_node.

---

## 8. Confidence Tuning Parameters as Config-Only Changes

> `AGENT_ALPHA=0.7` and `AGENT_CONFIDENCE_THRESHOLD=0.70` are baked into the spec. These items test whether requirements guarantee these are config-only tunable without code changes.

- [X] CHK033 — Are `AGENT_ALPHA` and `AGENT_CONFIDENCE_THRESHOLD` explicitly documented as **settable via environment variables with no code changes**, so A/B testing confidence thresholds in staging does not require a redeploy? [Clarity, Completeness]

Chi tiết: Task T008 yêu cầu thêm AGENT_ALPHA và AGENT_CONFIDENCE_THRESHOLD vào .env.example. Task T009 yêu cầu thêm các trường này vào core/config.py sử dụng pydantic-settings. Đánh giá: Thiết kế này cho phép thay đổi ngưỡng tin cậy để A/B testing trên môi trường Staging chỉ bằng cách cập nhật file .env mà không cần build lại mã nguồn hoặc deploy lại code. Tuy nhiên, tài liệu có thể được cải thiện bằng cách thêm một tuyên bố rõ ràng rằng `AGENT_ALPHA` và `AGENT_CONFIDENCE_THRESHOLD` có thể được điều chỉnh thông qua biến môi trường mà không cần thay đổi mã nguồn, đảm bảo rằng các nhà phát triển và kỹ sư vận hành hiểu rằng họ có thể thực hiện các điều chỉnh này một cách linh hoạt trong các môi trường khác nhau mà không phải lo lắng về việc sửa đổi mã hoặc redeploy.

- [ ] CHK034 — Does the spec define acceptable **ranges and validation rules** for `AGENT_ALPHA` (e.g., 0.0–1.0) and `AGENT_CONFIDENCE_THRESHOLD` (e.g., > Layer 1 threshold of 0.45)? Without this, a misconfigured prod value could break the dual-layer guard architecture. [Completeness, data-model.md §4]

KHOẢNG TRỐNG (Gap). Chi tiết: Task T009 chỉ yêu cầu thêm trường vào Settings class nhưng không chỉ định các ràng buộc Pydantic như Field(ge=0.0, le=1.0) cho AGENT_ALPHA. Tài liệu chưa quy định việc kiểm tra xem AGENT_CONFIDENCE_THRESHOLD (L2) có vô tình bị cấu hình thấp hơn ngưỡng L1 (0.45) hay không. Rủi ro: Một cấu hình sai (ví dụ: Alpha > 1.0) có thể làm hỏng công thức fusion hoặc làm mất hiệu lực của lớp bảo vệ thứ hai. Tài liệu có thể được cải thiện bằng cách thêm các ràng buộc rõ ràng cho các tham số này trong core/config.py (ví dụ: sử dụng Pydantic Field để giới hạn phạm vi giá trị) và thêm một tuyên bố rõ ràng trong tài liệu rằng `AGENT_CONFIDENCE_THRESHOLD` phải luôn cao hơn `CONFIDENCE_THRESHOLD` của Layer 1 để đảm bảo kiến trúc bảo vệ hai lớp hoạt động đúng cách.

- [ ] CHK035 — Is the **interaction between `AGENT_CONFIDENCE_THRESHOLD` (0.70) and `CONFIDENCE_THRESHOLD` (0.45, Layer 1, in `services/rag/constants.py`)** documented as a constraint: specifically, that L2 threshold must always be > L1 threshold? [Consistency, data-model.md §4, Research Decision 11]

ĐÃ ĐỊNH NGHĨA RIÊNG BIỆT NHƯNG THIẾU RÀNG BUỘC PHỤ THUỘC. Chi tiết: contracts/rag_tool.md xác định ngưỡng L1 (Layer 1 Guard) là 0.45. Task T047 và techniques-overview.md xác định ngưỡng L2 (Agent Confidence) mặc định là 0.70. Đánh giá: Mặc dù hai con số này được ghi nhận, nhưng tài liệu chưa nêu rõ một ràng buộc hệ thống (Constraint) yêu cầu L2 luôn phải lớn hơn L1. Trong kiến trúc dual-layer, nếu L2 ≤ L1, lớp bảo vệ L2 sẽ trở nên vô nghĩa vì mọi kết quả vượt qua được L1 đều sẽ tự động vượt qua L2. Tài liệu có thể được cải thiện bằng cách thêm một tuyên bố rõ ràng rằng `AGENT_CONFIDENCE_THRESHOLD` (L2) phải luôn được cấu hình cao hơn `CONFIDENCE_THRESHOLD` (L1) để đảm bảo rằng lớp bảo vệ thứ hai thực sự cung cấp một mức độ kiểm tra bổ sung thay vì trở nên vô dụng khi L2 ≤ L1.

---

## 9. `make_rag_tool` Factory and DB Session Lifetime (Dev → Prod FastAPI)

> data-model.md §7 documents the factory closure pattern. These items test whether the requirements specify how this pattern behaves under prod concurrency.

- [ ] CHK036 — Does the spec define the **DB session lifetime** for the `make_rag_tool(db)` factory closure in a prod FastAPI request context? Specifically: is it documented that `db` is a per-request `AsyncSession` (from `Depends(get_db)`), ensuring no session reuse across concurrent requests? [Completeness, data-model.md §7, Gap]

- [ ] CHK037 — Is the factory closure pattern (`make_rag_tool(db)`) documented as the **canonical injection pattern for all future tools** (including real `inventory_lookup` in Week 6), so Week 6 implementers don't introduce an alternative pattern that creates inconsistency? [Consistency, data-model.md §7]

CÓ KHOẢNG TRỐNG (Gap) và THIẾU NHẤT QUÁN ở CK036 và CK037. Chi tiết: Task T022 và T042 chỉ định mẫu factory closure make_rag_tool(db: AsyncSession).Tài liệu techniques-overview.md (Mục 2 - Dependency Injection Pattern) lại đưa ra ví dụ tiêm Connection Pool (db_pool: asyncpg.Pool) thay vì AsyncSession. Rủi ro: Nếu tiêm AsyncSession (phiên làm việc đơn lẻ), công cụ chỉ có thể được sử dụng trong một request duy nhất. Đặc tả chưa làm rõ cam kết rằng db là một AsyncSession được tạo mới mỗi  request (từ Depends(get_db)) hay là một Pool để Agent tự quản lý session. Việc Task T043 (inventory stub) không sử dụng factory cũng tạo ra sự không nhất quán cho người triển khai ở Tuần 6. Tài liệu có thể được cải thiện bằng cách xác định rõ ràng rằng `make_rag_tool(db)` sẽ nhận một `AsyncSession` mới cho mỗi request trong môi trường FastAPI, đảm bảo rằng không có phiên nào bị chia sẻ giữa các request đồng thời, và khuyến khích sử dụng `Depends(get_db)` để quản lý vòng đời của session một cách an toàn.

- [ ] CHK038 — Does the spec define **tool registration** — specifically, how tools built via factory closures are passed to `graph.compile(tools=tools)`? Is this documented as requiring graph re-compilation per request, or are compiled graphs cached? If cached, is session isolation guaranteed? [Ambiguity, Completeness, data-model.md §7]

MƠ HỒ (Ambiguity).Chi tiết: Task T050 định nghĩa hàm build_graph() nhưng không nêu rõ hàm này sẽ được gọi ở đâu trong FastAPI. Vấn đề: Nếu các công cụ được tạo bằng AsyncSession (per-request), thì toàn bộ đồ thị (Graph) bắt buộc phải được biên dịch lại (`.compile()`) cho mỗi request để tiêm session mới. Nếu đồ thị được biên dịch một lần ở startup (singleton), các công cụ sẽ bị kẹt với session cũ đã đóng. Đặc tả chưa làm rõ việc Graph là Singleton hay Per-request. Nếu Graph là Singleton, thì việc sử dụng AsyncSession sẽ không khả thi trừ khi có cơ chế đặc biệt để tái tạo session cho mỗi request, điều này sẽ làm tăng đáng kể độ phức tạp. Tài liệu có thể được cải thiện bằng cách xác định rõ ràng rằng khi sử dụng `make_rag_tool(db)` với một `AsyncSession` được tạo mới cho mỗi request, thì toàn bộ Graph sẽ cần phải được biên dịch lại cho mỗi request để đảm bảo rằng các công cụ nhận được session mới, hoặc nếu Graph được thiết kế để được biên dịch một lần và tái sử dụng, thì phải có một cơ chế rõ ràng để đảm bảo rằng các công cụ bên trong Graph có thể nhận được session mới cho mỗi request mà không cần phải biên dịch lại toàn bộ Graph.

---

## 10. Observability Backend Switching (Local Arize Phoenix → Logfire/LangSmith)

- [X] CHK039 — Is the **OTLP gateway** documented as the stable abstraction layer that allows switching from local Arize Phoenix (dev) to Logfire/LangSmith (prod) via **config-only changes** (endpoint URL + API key)? Or does switching require code changes to instrumentation calls? [Clarity, plan.md Observability]

ĐÃ ĐƯỢC TÀI LIỆU HÓA RÕ RÀNG. Chi tiết: techniques-overview.md (Mục 10.3) định nghĩa cơ chế Observability Stack Toggle dựa trên biến môi trường OBSERVABILITY_BACKEND. Đánh giá: Tài liệu khẳng định sử dụng giao thức OTLP làm lớp trừu tượng. Việc chuyển đổi từ Arize Phoenix sang Logfire/LangSmith được thiết kế là "config-only" thông qua các tham số như  otlp_endpoint và API key, không yêu cầu sửa đổi mã nguồn đo đạc (instrumentation calls). Tuy nhiên, tài liệu có thể được cải thiện bằng cách thêm một tuyên bố rõ ràng rằng việc chuyển đổi giữa các backend quan sát (observability backends) như Arize Phoenix và Logfire/LangSmith có thể được thực hiện hoàn toàn thông qua việc thay đổi cấu hình (ví dụ: cập nhật biến môi trường hoặc tệp cấu hình) mà không cần phải sửa đổi mã nguồn của các cuộc gọi instrumentation, đảm bảo rằng các nhà phát triển và kỹ sư vận hành hiểu rằng họ có thể linh hoạt chuyển đổi backend quan sát mà không phải lo lắng về việc thay đổi mã hoặc redeploy.

- [X] CHK040 — Are the **OpenTelemetry span attributes** emitted by agent nodes documented as stable across environments? If Logfire requires different attribute names or formats than Arize Phoenix, it constitutes a code change cost not currently reflected in the spec. [Gap, Completeness]

ĐÃ ĐƯỢC TÀI LIỆU HÓA. Chi tiết: Mục 10.3 cũng liệt kê danh sách các thuộc tính bắt buộc (trace_attributes) như: service.name, agent.version, environment, thread_id, user_id. Đánh giá: Việc định nghĩa một bộ thuộc tính chuẩn (canonical attributes) đảm bảo dữ liệu sẽ nhất quán khi gửi đến bất kỳ backend nào (Phoenix hay Logfire), loại bỏ chi phí sửa đổi mã nguồn khi thay đổi công cụ quan sát. Tuy nhiên, tài liệu có thể được cải thiện bằng cách thêm một tuyên bố rõ ràng rằng các thuộc tính OpenTelemetry được định nghĩa trong tài liệu sẽ được giữ nguyên và không thay đổi khi chuyển đổi giữa các backend quan sát khác nhau, đảm bảo rằng các nhà phát triển hiểu rằng họ có thể dựa vào sự ổn định của các thuộc tính này cho việc phân tích và giám sát mà không phải lo lắng về việc sửa đổi mã nguồn khi thay đổi backend quan sát.

---

## Summary of Critical Gaps Found

> Items below are the highest-priority requirement deficiencies identified. Address before implementation begins.

| ID | Gap | Risk Level | Spec Location |
|----|-----|-----------|---------------|
| CHK031 | `conversation_history` vs `messages` field conflict between FR-001 and data-model.md | 🔴 HIGH — breaks FR-001 compliance check | Spec §FR-001 vs. data-model.md §1 |
| CHK032 | `escalation_failure` missing from `AgentState` TypedDict | 🔴 HIGH — spec mentions it but state schema omits it | Spec edge cases, FR-007 |
| CHK004 | `session_id` → `thread_id` mapping undefined | 🟠 MEDIUM — Week 5 refactor risk | Research Decision 5 |
| CHK002 | `AsyncPostgresSaver` DB schema not specified | 🟠 MEDIUM — Week 5 discovery cost | Research Decision 5 |
| CHK017 | `InventoryLookupOutput` not declared schema-frozen | 🟠 MEDIUM — Week 6 breaking change risk | contracts/inventory_tool.md |
| CHK026 | Week 4 `interrupt_before` target node unspecified | 🟠 MEDIUM — Week 4 planning ambiguity | plan.md Article VIII |
| CHK023 | `state_snapshot` in `NodeStreamEvent` is full vs. delta unspecified | 🟡 LOW — Week 6 bandwidth cost unknown | Spec §FR-006 |
| CHK038 | Graph re-compile vs. cache under concurrency unspecified | 🟡 LOW — prod session isolation risk | data-model.md §7 |
