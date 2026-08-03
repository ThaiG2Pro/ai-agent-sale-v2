# Plan v2: 4.3 → agent ĐÚNG hơn · KHÔN hơn · RẺ hơn (3 trục giá trị, không đánh bóng ops)

## Context

Re-score 2026-07-16 (`docs/feature-scorecard.md`): dự án **~4.3/5**, cả 5 P0 đã đóng, unit suite 341/0.
Quyết định với user: **không đuổi 5/5 đồng đều** (phần còn lại chủ yếu là ops maturity, khách SME không
cảm nhận) — thay vào đó nâng theo **cả 3 trục giá trị**: (C) trả lời chính xác cao, (A) thông minh/handle
case khó, (B) tiết kiệm cho SME. Nền tảng tham chiếu: `docs/agent-orchestration-2026-research.md`
(mục §4 agentic RAG đã xong ở CR retry-loop; còn lại §5 episodic memory, §6 risk-score HITL,
§7 cascade verification).

**Nguyên tắc xuyên suốt:**
- **Đo trước, cải sau**: mọi WP accuracy phải so số eval trước/sau (WP-V2-0 là móng, làm ĐẦU TIÊN).
- Mọi hành vi mới có **kill-switch qua setting** (pattern `RAG_RETRY_MAX_ATTEMPTS=0` đã dùng tốt).
- WP đổi hành vi agent (V2-1, V2-3, V2-4) → chạy qua SDLC pipeline (`claude --agent sdlc-full cr <slug>`)
  vì cần spec/design gate; WP nhỏ (V2-0, V2-2, V2-5) có thể fast-track.
- Mỗi WP kết thúc bằng commit riêng theo `R-GIT-001`.

**Thứ tự & phụ thuộc:** WP-V2-0 trước tiên. Sau đó V2-1 ∥ V2-2 song song được. V2-3 và V2-4 **cùng chạm
`core/agent/graph.py` + state** → tuần tự hoặc worktree riêng. V2-5 độc lập hoàn toàn (chạy song song
với bất kỳ WP nào). Trục: V2-1/V2-2 = Chính xác · V2-3/V2-4 = Thông minh · V2-5 = Tiết kiệm.

**Baseline test:** unit 341 pass / 0 fail (main @ `4d85bb3`). Eval baseline: chưa có số — WP-V2-0 tạo.

---

## WP-V2-0 — Eval foundation: gold set ~40 case + eval gate PHÂN TẦNG THEO CHI PHÍ LLM (làm trước)

Hiện `tests/eval/gold_dataset.json` ~20 mục, `scripts/tier1_eval.py` chạy grading qua full RAG
pipeline. **Ràng buộc thiết kế (đã chốt với user):** chạy 100+ case qua full pipeline là bất khả thi
vận hành — local nghẹt VRAM, cloud đập rate limit (~300-400 chat-LLM call/lần chạy). Vì vậy gate
mặc định phải **KHÔNG gọi chat-LLM nào**; grading luôn deterministic (không LLM-as-judge trong gate).

1. **Gold set 20 → ~40 mục CHỌN LỌC** (không chạy đua số lượng), mỗi category 4-6 case, tag
   `category`: multi-intent, so sánh, mơ hồ-cần-clarify, ngoài-catalog (phải decline), bẫy
   hallucination (thuộc tính không tồn tại), tiếng Việt không dấu. Mỗi case ghi expectation
   deterministic: `expected_skus` (phải có trong top-k) / `must_contain` / `must_decline`.
2. **Tier-R — retrieval-only gate (MẶC ĐỊNH, rẻ):** mỗi case chỉ chạy embed (bge-m3) + hybrid RRF
   search trên **raw query, BỎ QUA bước LLM normalize** → chấm `recall@k`: expected_skus có trong
   top-k không. Chi phí: **1 embed call/case, 0 chat call** → 40 case chạy trong ~1 phút, không
   đụng VRAM chat model, không rate limit. Đây là gate chạy trước MỌI commit accuracy.
3. **Tier-F — full-pipeline smoke (10-12 case đại diện):** subset cố định (bắt buộc gồm các case
   hallucination-trap + must_decline — những thứ Tier-R không đo được vì cần generation) chạy qua
   `answer_with_rag`, grading vẫn deterministic (`must_contain`/`declined==True`). ~30-40 chat
   call/lần — chạy khi WP accuracy sắp commit, không phải mỗi vòng lặp dev.
4. **Script `scripts/eval_gate.sh`** bọc cả hai tier, thiết kế chịu-lỗi-LLM: `--tier r|f|all`,
   `--category`, `--limit N`; **checkpoint resume** (ghi JSONL append từng case — crash/rate-limit
   giữa chừng chạy lại không mất kết quả cũ); chạy tuần tự + backoff khi 429. Kết quả JSON có
   timestamp vào `tests/eval/baselines/`, so với baseline gần nhất, exit ≠ 0 nếu tụt >2 điểm %.
   `--tier all` (full 40 qua pipeline) chỉ dành cho chạy tay/qua đêm trước release — không phải gate.
5. **Chạy baseline:** Tier-R trên main (chỉ cần DB + embed model) → commit. Tier-F baseline chạy
   1 lần khi có Ollama/cloud key rảnh → commit riêng.

**Done:** gold ~40 mục có expectation deterministic; Tier-R chạy <2 phút không cần chat-LLM,
baseline committed; Tier-F resume được sau crash/rate-limit.
Commit `test(eval): <ticket> tiered eval gate — retrieval-only default + full smoke`.

---

## WP-V2-1 — Trục CHÍNH XÁC: groundedness self-check + cascade verification (SDLC full)

1. **Groundedness self-check trước khi gửi.** Sau khi answer được sinh trong answer path
   (`core/agent/nodes/answer.py` — đường accepted, quanh chỗ gọi AIGateway + `extract_llm_metrics`
   tại `:192-205`), thêm 1 call economy-chat chấm Pydantic `GroundednessVerdict {supported: bool,
   unsupported_claims: list[str]}`: "mọi claim về giá/tồn kho/thuộc tính trong answer có được
   citations chống lưng không?". Nếu fail → regenerate 1 lần với prompt siết "chỉ dùng thông tin trong
   context", vẫn fail → decline lịch sự (đường decline sẵn có). Setting `GROUNDEDNESS_CHECK_ENABLED: bool
   = True` + `GROUNDEDNESS_MAX_REGEN: int = 1` (0 = kill switch). Ghi verdict vào `model_traces.metadata`
   (bảng đã nhận metrics thật từ WP3 — `answer.py:309 _write_model_trace`).
2. **Cascade verification cho escalation** (research §7). Hiện COMPLAINT/NEGOTIATION nhảy thẳng
   premium qua `escalation_node`. Đổi thành cascade thật: trả lời bằng chat-tier trước; nếu
   groundedness fail HOẶC confidence thấp → retry với `PREMIUM_MODEL` + flag trong state
   (dùng lại `escalation_failure` pattern tại `answer.py:197-201` làm khung). Setting
   `CASCADE_VERIFY_ENABLED: bool = True`; tắt → hành vi cũ (premium thẳng).
   *Lưu ý cost:* cascade làm COMPLAINT rẻ đi (đa số dừng ở chat-tier) — trục B hưởng lợi cùng lúc.
3. **Đo:** `eval_gate.sh --tier f` trước/sau (nhóm hallucination-trap nằm trong Tier-F smoke) —
   pass-rate nhóm này phải tăng, Tier-R không tụt.
   Unit test: verdict fail → regen; regen fail → decline; kill-switch giữ hành vi cũ byte-identical.

**Done:** không còn answer chứa claim ngoài citations (đo bằng nhóm bẫy hallucination trong gold set);
cascade giảm số call premium (đếm qua `model_traces`). Commit
`feat(rag): <ticket> groundedness self-check + cascade verification`.

---

## WP-V2-2 — Trục CHÍNH XÁC: L1 cache key + fragment-level citations (fast-track)

1. **L1 cache key trên raw query.** `services/semantic_cache.py:33-47` hash SHA256 là deterministic,
   NHƯNG kiểm tra call-site trong `services/rag/pipeline.py`: nếu `get_l1_cache` đang được gọi với
   query **sau** bước LLM normalize (pipeline: classify → normalize → cache → …) thì key phụ thuộc
   output LLM non-deterministic → hit-rate thấp + rủi ro lệch key. **Fix:** key L1 trên raw user query
   (strip+lower sẵn có); L2 vector giữ nguyên trên normalized. Test: 2 lần cùng raw query → L1 hit
   dù normalize trả khác nhau (mock normalize trả 2 biến thể).
2. **Fragment-level citations (đóng FR-011).** Citation hiện trả chunk-level. Thêm field optional
   `fragment_text` vào citation payload: sentence trong chunk khớp answer nhất (SequenceMatcher —
   pattern đã dùng trong compression, không cần LLM call). Optional field → response schema không
   breaking (`QueryResponse.citations`); cập nhật spec `openspec/specs/rag-pipeline/spec.md` chính
   thức (spec-first, commit docs tách riêng).

**Done:** L1 hit với cùng raw query bất kể LLM normalize; citations có fragment_text; eval gate không tụt.
Commit `fix(rag): <ticket> deterministic L1 cache key + fragment citations`.

---

## WP-V2-3 — Trục THÔNG MINH: clarify-question loop + query decomposition (SDLC full)

1. **Clarify loop — hỏi lại thay vì từ chối.** Vùng borderline (qua L1 0.45 nhưng fused confidence
   thấp — logic 2 lớp trong `core/agent/nodes/confidence.py:43-61`): thay vì `declined=True`, sinh
   1 câu hỏi làm rõ bằng economy model ("Anh/chị đang hỏi về [X] hay [Y] ạ?" — Pydantic
   `ClarifyingQuestion`), trả về khách, set state `awaiting_clarification=True` + lưu query gốc.
   Turn kế tiếp: nếu cờ đang bật → merge câu trả lời khách vào query gốc rồi chạy lại retrieval.
   **Chống loop:** tối đa 1 clarify / query gốc (state counter) — clarify lần 2 → decline như cũ.
   Graph: cạnh mới `confidence → clarify_node → END`; state persist qua checkpointer sẵn có
   (thread_id = session_id, hoạt động xuyên turn trên cả Telegram). Setting
   `CLARIFY_ENABLED: bool = True` (kill switch → decline như cũ).
2. **Query decomposition tổng quát.** Thay mảnh vá regex COMPARISON (`và/vs/với`) trong
   `core/agent/nodes/retrieval.py` bằng LLM decomposition (economy, Pydantic `DecomposedQuery
   {sub_queries: list[str]}`, cap 3 sub-queries) → search từng sub-query rồi merge RRF (pattern
   split-merge sẵn có). Regex cũ giữ làm **fallback** khi LLM call lỗi/offline. Bắt được cả
   multi-intent ("giá X và còn hàng không") chứ không chỉ so sánh.
3. **Đo:** nhóm `multi-intent` đo được ngay ở Tier-R (cả 2 expected_skus phải vào top-k sau
   decomposition); luồng clarify đo bằng integration test qua graph compiled thật (2-turn, mock LLM)
   — không cần eval LLM thật (bài học "test xanh feature gãy").

**Done:** câu mơ hồ nhận được câu hỏi làm rõ (không phải lời từ chối), trả lời đúng ở turn 2;
multi-intent trả đủ ý; kill-switch giữ hành vi cũ. Commit
`feat(agent): <ticket> clarifying-question loop + LLM query decomposition`.

---

## WP-V2-4 — Trục THÔNG MINH: episodic memory + risk-score HITL (SDLC full)

1. **Episodic memory (research §5 — tầng còn thiếu).** Hiện chỉ có semantic (summary đã nén) —
   khách hỏi "cái máy hôm qua em tư vấn ấy" là chịu. Dữ liệu gốc đã nằm trong checkpoint messages;
   S3 design chọn 1 trong 2: (a) đọc trực tiếp từ bảng checkpoints theo thread_id của customer
   (không bảng mới, đọc-only) hoặc (b) bảng `episodic_events` append-only ghi từ answer_node
   (message + intent + sản phẩm nhắc tới, có timestamp; migration mới + downgrade — R-DB-001).
   Expose: `GET /memory/episodic/{customer_id}?limit=` (sau `verify_admin_key`) + wire vào
   `memory_retrieval_node`: câu hỏi có tham chiếu thời gian ("hôm qua", "lần trước") → kéo episodic
   gần nhất vào memory_context. Scope `customer_id` nghiêm ngặt + RTBF phải xoá cả tầng này
   (mở rộng cascade WP1 — `api/routes/memory.py:445-511`).
2. **Risk-score HITL (research §6 — chống approval fatigue).** `hitl_guard_node` hiện trigger bằng
   2 điều kiện rời (`confidence < threshold` HOẶC `intent == ORDER_PLACEMENT`). Thay bằng điểm
   tổng hợp: `risk = w_conf·(1-confidence) + w_val·order_value_norm + w_hist·history_factor`
   (history_factor từ `intent_tracking`: khách có lịch sử mua/đã escalate). 3 tier config:
   Tier1 tự động (risk < T1), Tier2 interrupt như hiện tại, Tier3 (risk cao) → escalate thẳng
   support queue. **Default conservative:** ORDER_PLACEMENT có giá trị đơn > ngưỡng luôn ≥ Tier2
   — không được nới an toàn khi thiếu dữ liệu (thiếu order_value → coi như cao). Weights + ngưỡng
   qua settings, spec phải ghi rõ bảng quyết định (đây là hành vi an toàn, S2 gate soi kỹ).
3. **Đo:** unit test ma trận risk (giá trị đơn × confidence × lịch sử); integration: đơn nhỏ khách
   quen → Tier1 tự chạy, đơn to khách mới → interrupt như cũ.

**Done:** agent nhớ và trả lời được tham chiếu hội thoại cũ; HITL volume giảm cho case rủi ro thấp
mà KHÔNG nới lỏng đơn giá trị cao; RTBF sạch cả episodic. Commit
`feat(agent): <ticket> episodic memory + risk-score HITL tiers`.

---

## WP-V2-5 — Trục TIẾT KIỆM: cost dashboard + budget guard + routing tune (fast-track, độc lập)

Số liệu đã THẬT sẵn: `model_traces` (bảng `models/schema.py:200`) nhận token/cost/latency thật từ
`extract_llm_metrics` (WP3). Chỉ còn aggregate + guard.

1. **Cost dashboard endpoint.** `GET /admin/costs?from=&to=&group_by=day|customer|model`
   (sau `verify_admin_key`, header-only): aggregate `model_traces` — tổng tokens in/out, cost USD,
   số call, latency p50/p95, cache hit-rate (join semantic_cache stats). Trả JSON thuần (đủ cho
   SME xem bằng curl/sheet, không cần UI).
2. **Budget guard.** Settings `DAILY_COST_LIMIT_USD` (0 = off, default 0 vì Ollama local cost=0)
   + `CUSTOMER_DAILY_MSG_CAP`. Check trong answer path: vượt ngân sách ngày → force xuống
   light-model + log warning + đánh dấu trong trace metadata; vượt cap per-customer → message
   lịch sự hẹn quay lại (chống 1 khách spam đốt ngân sách cloud của SME).
3. **Routing tune.** SMALLTALK hiện đi node nào / model nào — audit rồi ép các intent rẻ
   (SMALLTALK, AVAILABILITY đơn giản) xuống `LIGHT_CHAT_MODEL`; đo bằng eval gate (không tụt
   accuracy) + so cost report trước/sau trên cùng kịch bản demo.

**Done:** SME xem được chi phí theo ngày/khách/model bằng 1 lệnh curl; có trần chi phí cứng khi
dùng cloud key; cost/query giảm đo được, eval không tụt. Commit
`feat(ops): <ticket> cost dashboard, budget guard, cheap-intent routing`.

---

## Verification tổng (sau khi mọi WP xong)

1. `uv run pytest` 0 fail; `uv run pytest -m integration` (DB + Ollama/cloud key) 0 fail.
2. `scripts/eval_gate.sh` — pass-rate tổng ≥ baseline; nhóm hallucination-trap / ambiguous /
   multi-intent tăng rõ so với baseline WP-V2-0.
3. Demo 3 kịch bản mới trên Telegram: (a) hỏi mơ hồ → bot hỏi lại → trả lời đúng turn 2;
   (b) hỏi thuộc tính không tồn tại → bot từ chối thay vì bịa; (c) khách quen đặt đơn nhỏ →
   không cần admin approve; đơn to → vẫn pause chờ duyệt.
4. `curl /admin/costs` trả số thật khớp `model_traces`.
5. Re-score `docs/feature-scorecard.md`: kỳ vọng 002 ≈ 4.7 · 003 ≈ 4.7 · 004 ≈ 4.6 · 005 ≈ 4.6
   (điểm 5 tuyệt đối cần thêm ops: CI/CD + coverage gate + OTel per-node — ngoài scope plan này,
   ghi nhận là backlog riêng).

## Giao việc

WP-V2-0 giao trước cho 1 agent (fast-track). Sau đó: V2-1 ∥ V2-2 ∥ V2-5 song song (file không giao
nhau; V2-1 qua sdlc-full). V2-3 rồi V2-4 tuần tự (cùng chạm graph.py + state) hoặc worktree riêng,
cả hai qua sdlc-full vì đổi hành vi agent + hành vi an toàn HITL. Khi giao kèm: nội dung WP nguyên
văn + `docs/feature-scorecard.md` (re-score 07-16) + `docs/agent-orchestration-2026-research.md`
làm evidence nền + yêu cầu chạy `eval_gate.sh` và test liên quan trước khi commit.
