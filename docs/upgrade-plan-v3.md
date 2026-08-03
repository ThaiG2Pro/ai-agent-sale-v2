# Plan v3: 4.6 → dự án TỰ BẢO VỆ (CI/CD + coverage + observability) & vá 2 điểm yếu lộ ra ở Verification tổng

## Context

Re-score 2026-08-03 (`docs/feature-scorecard.md`): dự án **~4.6/5**, plan V2 (V2-0 → V2-5) xong
100%, Verification tổng 5/5 bước đạt (549 test / 0 fail, Tier-R 34/34, demo live 3 kịch bản PASS).
Khoảng cách còn lại tới "chuẩn production 2026" **không phải là feature** — mà là **khả năng tự bảo
vệ**: hiện lint/test/eval chỉ chạy tay ở local, không có gì chặn một commit hỏng lên `main`, không
có số đo coverage, không nhìn được node nào chậm trong graph. Kèm theo là 2 điểm yếu hành vi lộ ra
ngay trong Verification tổng (Tier-F multi-intent dao động; clarify_node gần như không kích hoạt
được với router thật).

**Nguyên tắc xuyên suốt (kế thừa V2):**
- Mọi hành vi mới có **kill-switch qua setting**; mọi WP accuracy so số eval trước/sau.
- WP đổi hành vi agent (V3-3, V3-4) → chạy qua SDLC pipeline (`claude --agent sdlc-full cr <slug>`);
  WP hạ tầng/ops (V3-0, V3-1, V3-2, V3-5) fast-track được.
- Mỗi WP kết thúc bằng commit riêng theo `R-GIT-001`.
- **Máy dev yếu**: các suite nặng chạy TUẦN TỰ, không song song (bài học Verification tổng —
  `test_search_latency_10k` flaky khi máy tải nặng).

**Thứ tự & phụ thuộc:** V3-0 (CI) làm ĐẦU TIÊN — là móng cho mọi WP sau (coverage gate V3-1 cắm vào
CI; eval ổn định V3-3 mới cắm Tier-F vào CI được). Sau đó V3-1 ∥ V3-2 song song. V3-3 và V3-4 cùng
chạm answer path / confidence → tuần tự. V3-5 độc lập, nhét xen kẽ lúc rảnh.

**Baseline (main @ `d7c2a46`):** 549 pass / 5 skip / 0 fail · Tier-R 34/34 (Δ0.0pp) · Tier-F 11/12
(1 case multi-intent dao động giữa run — chính là đối tượng của V3-3) · coverage: **chưa đo được**
(chưa có tooling — V3-1 tạo số đầu tiên).

---

## WP-V3-0 — CI pipeline trên GitHub Actions (làm trước)

Hiện `.github/workflows/` không tồn tại; lint chỉ enforce qua pre-commit hook tự nguyện. Repo:
`github.com/ThaiG2Pro/ai-agent-sale-v2`.

1. **Workflow `ci.yml`** chạy trên push/PR vào `main`, các job TUẦN TỰ theo tầng chi phí
   (fail nhanh, rẻ trước):
   - **lint**: `uv sync` (cache uv) → `ruff check .` + `ruff format --check .`.
   - **unit**: Postgres 16 + pgvector qua `services:` container → `uv run alembic upgrade head` →
     `uv run pytest -q -m "not integration"` (unit thuần, không cần LLM).
   - **eval-tier-r**: chạy `./scripts/eval_gate.sh --tier r` — chỉ cần DB + fastembed local
     (ONNX CPU, không cần Ollama/cloud key) → gate chặn tụt >2pp vs baseline committed.
2. **KHÔNG đưa vào CI** (chạy tay/nightly): suite `-m integration` (cần chat-LLM key), Tier-F
   (cần Groq key + V3-3 ổn định trước — ghi rõ TODO trỏ sang V3-3), performance tests
   (`test_search_latency_10k` flaky trên runner yếu — mark `@pytest.mark.performance` và exclude).
3. **Branch protection `main`**: require CI xanh trước khi merge (bật trên GitHub settings —
   ghi hướng dẫn 1 đoạn trong README hoặc docs vì cần quyền owner).
4. Secrets CI: KHÔNG cần LLM key ở tầng này (unit mock hết, Tier-R dùng fastembed) — đúng triết lý
   Zero-Cost-First. `DB_*` trỏ vào service container.

**Done:** push một commit cố ý hỏng (lint lỗi / test fail / eval tụt) → CI đỏ, chặn merge; commit
sạch → CI xanh 3 job, tổng thời gian < ~10 phút.
Commit `ci(project): <ticket> GitHub Actions — lint + unit + Tier-R eval gate`.

---

## WP-V3-1 — Coverage gate ≥ 80% (R-COV-001, đang là rule "trên giấy")

`pyproject.toml` chưa có `pytest-cov`; R-COV-001 tuyên bố ≥80% nhưng chưa từng đo.

1. Thêm `pytest-cov` vào dev deps; cấu hình `[tool.coverage]` trong `pyproject.toml`:
   source = `api, core, services, models`, omit migrations/cli/tests.
2. **Đo số thật trước** (`uv run pytest -m "not integration" --cov --cov-report=term-missing`)
   → ghi số baseline vào WP report. Nếu < 80%: liệt kê 5 module thủng nhất, viết test bù CÓ GIÁ
   TRỊ (test hành vi, không test tautology — bài học đợt dọn test 5371207) tới khi qua 80%.
   Nếu bù quá 1 ngày công → hạ `--cov-fail-under` xuống số thật + 2pp, ghi backlog nâng dần.
3. Cắm `--cov-fail-under=80` vào job **unit** của CI (V3-0) — CI hard fail dưới ngưỡng.
4. Badge coverage trong README (tùy chọn, 15 phút).

**Done:** CI đỏ khi coverage tụt dưới ngưỡng; số coverage baseline ghi trong commit message.
Commit `test(coverage): <ticket> pytest-cov + fail-under gate — baseline NN%`.

---

## WP-V3-2 — OTel span per-node (gap P2 của 003 từ audit 07-15, 2 lần re-score chưa đóng)

Hiện trace tổng qua logfire auto-instrument; không nhìn được node nào (router / retrieval /
confidence / answer / hitl_guard) ăn bao nhiêu ms trong một turn.

1. Decorator/wrapper `traced_node` trong `core/agent/graph.py` (hoặc `core/logging.py`): bọc mỗi
   node function bằng `tracer.start_as_current_span(node_name)` với attributes: `session_id`,
   `intent`, `model_used`, `declined`. KHÔNG log PII/nội dung tin nhắn vào span (R-SEC-002).
2. Áp cho mọi node khi build graph (một chỗ duy nhất — vòng `for` lúc `add_node`, không sửa
   từng file node).
3. Span latency per-node đã có sẵn trong Phoenix/Logfire UI — không xây dashboard mới. Nếu rẻ
   (< ~20 dòng): thêm `latency_by_node` vào metadata `model_traces` để `/admin/costs` sau này
   group được (không bắt buộc trong WP này).
4. Kill-switch: `OTEL_NODE_SPANS_ENABLED=true` default (tắt được nếu overhead đo được > ~5ms/turn).

**Done:** một turn `/agent/query` hiện cây span cha-con đủ các node đã chạy trong Phoenix;
overhead đo được ghi trong report. Unit test: graph build với wrapper vẫn pass toàn suite.
Commit `feat(obs): <ticket> per-node OTel spans with kill-switch`.

---

## WP-V3-3 — Trục ĐÚNG hơn: ổn định Tier-F multi-intent (case dao động lộ ra ở Verification tổng)

Hiện tượng đo được (2026-08-03): Tier-F 11/12, case fail XOAY VÒNG giữa `mi_001`/`mi_002` tùy run;
cả hai PASS khi chạy riêng trên config production. Chẩn đoán: (a) Tier-F gọi thẳng
`answer_with_rag` nên **decomposition của WP-V2-3 không chạy** (nó nằm ở graph path) — câu 2 ý đi
nguyên con vào generation; (b) grader groundedness chấm câu trả lời 2 ý là "chưa đủ căn cứ" ở mức
borderline → decline. Đây là mismatch giữa eval harness và production path, KHÔNG phải bug code.

1. **Sửa harness trước (rẻ, đúng bản chất):** Tier-F cho category `multi_intent` chạy qua
   **graph path** (hoặc gọi decomposition trước `answer_with_rag`) để đo đúng những gì production
   làm. Các category khác giữ nguyên đường cũ.
2. **Đo lại 3 run liên tiếp** Tier-F: yêu cầu 12/12 cả 3 run (chứng minh hết dao động), cập nhật
   baseline nếu dataset hash đổi.
3. Nếu sau (1) vẫn dao động → mới đụng tới verdict: nới `check_groundedness` cho câu trả lời
   multi-part (chấm supported theo TỪNG claim khớp citation thay vì all-or-nothing), sau kill-switch
   `GROUNDEDNESS_PER_CLAIM=false` default off, và PHẢI qua sdlc-full vì đổi hành vi decline.
4. Khi ổn định: cắm Tier-F vào CI dạng **nightly workflow** (cần `GROQ_API_KEY` secret) — đóng
   TODO của V3-0.

**Done:** Tier-F 12/12 × 3 run liên tiếp; nightly CI eval chạy được.
Commit `fix(eval): <ticket> Tier-F multi-intent runs production decomposition path`.

---

## WP-V3-4 — Trục KHÔN hơn: clarify_node kích hoạt được với router thật (SDLC full)

Hiện tượng đo được (demo Verification tổng): điều kiện clarify yêu cầu intent **OTHER** + qua L1 +
fused < 0.70 — nhưng router thật xếp gần như mọi câu mơ hồ về sản phẩm vào INFO_QUERY/AVAILABILITY/
COMPARISON (nhóm FR-007 "borderline vẫn trả lời") → clarify_node gần như không bao giờ chạy live;
câu mơ hồ hoặc bị decline bởi groundedness, hoặc answer node tự hỏi lại NGẪU NHIÊN (không có
merge-turn có kiểm soát của WP-V2-3).

1. **Mở rộng điều kiện clarify** trong `confidence_node`: intent ∈ {INFO_QUERY, AVAILABILITY,
   COMPARISON} + fused < 0.70 + **similarity_gap nhỏ** (nhiều ứng viên sát nhau = mơ hồ thật) →
   `needs_clarification=True` thay vì đẩy sang escalation. PRICING giữ đường cũ (câu giá thường
   cụ thể). Anti-loop `clarify_count < 1` giữ nguyên.
2. Ngưỡng gap qua setting `CLARIFY_SIMILARITY_GAP_MAX` (đề xuất 0.05 — theo số đo demo:
   "Điện thoại Samsung ấy" có gap 0.0019, "shop ơi giúp em chọn" gap tương tự) + kill-switch
   chung `CLARIFY_ENABLED` đã có.
3. **Đo trước/sau bắt buộc** (đây là WP đổi hành vi decline → sdlc-full): nhóm `ambiguous` trong
   gold set phải giữ/tăng (Tier-R 5/5 hiện tại); thêm 2-3 case gold mới dạng "mơ hồ nhưng
   INFO_QUERY" có expectation `must_clarify` (cần extend eval harness chấm được response là câu
   hỏi lại — deterministic: `awaiting_clarification == True`).
4. Demo lại kịch bản (a) của Verification tổng: kỳ vọng lần này đi qua clarify_node thật
   (turn 2 merge `clarify_original_query`), không phải answer node tự hỏi.

**Done:** câu "Điện thoại Samsung ấy còn hàng không?" → bot hỏi lại "S24 Ultra hay A55?" →
turn 2 trả đúng; eval ambiguous không tụt; kill-switch hoạt động.
Commit `feat(agent): <ticket> clarify loop reaches borderline INFO_QUERY/COMPARISON`.

---

## WP-V3-5 — Dọn nợ nhỏ (fast-track, nhét xen kẽ)

Ba mục P2/P3 tồn đọng từ scorecard, không chặn gì, mỗi mục ≤ nửa ngày:

1. **`model_version` hardcode "v1.0"** khi ingest (`services/rag/ingest`): lấy từ
   `settings.EMBED_MODEL` (+ dimension) để đổi model embed là phân biệt được embedding cũ/mới
   trong DB. Migration KHÔNG cần — cột đã có.
2. **hitl_guard resume-vs-fresh** detect bằng query DB status "paused/resuming" — mong manh khi
   race. Tối thiểu: thêm unit test chốt hành vi hiện tại + comment WHY; nếu rẻ, chuyển cờ resume
   vào state do `service.py` set lúc resume (nguồn sự thật 1 chiều) thay vì đoán từ DB.
3. **`docs/demo-runbook.md`**: viết runbook 5 kịch bản demo (3 kịch bản Verification tổng +
   RTBF + cost dashboard) — lệnh curl/Telegram từng bước, seed data cần gì (`scripts/demo_seed.py`),
   reset thế nào. Mục duy nhất của upgrade-plan cũ (pre-V2) chưa hoàn tất.

**Done:** 3 commit riêng lẻ (`fix(rag)`, `test(hitl)`/`fix(hitl)`, `docs(demo)`).

---

## Verification tổng V3 (sau khi mọi WP xong)

1. CI xanh trên PR thật (lint + unit + coverage ≥80% + Tier-R); commit cố ý hỏng bị chặn.
2. Tier-F **12/12 × 3 run** liên tiếp (V3-3); nightly eval workflow chạy được với Groq key.
3. Demo clarify thật: câu mơ hồ INFO_QUERY → hỏi lại → merge turn 2 (V3-4), qua clarify_node
   (kiểm tra `awaiting_clarification` trong checkpoint).
4. Một turn `/agent/query` hiện đủ cây span per-node trong Phoenix (V3-2).
5. Re-score `docs/feature-scorecard.md`: kỳ vọng 002 ≈ 4.8 · 003 ≈ 4.8-4.9 (đóng nốt OTel per-node
   + clarify) · toàn dự án ≈ **4.7-4.8/5**. Phần còn thiếu tới 5.0 tuyệt đối: deploy pipeline
   thật (CD lên môi trường staging/prod) — ngoài scope V3, cần quyết định hạ tầng từ user.

## Giao việc

V3-0 giao trước (fast-track, 1 agent). Sau đó V3-1 ∥ V3-2 song song (file không giao nhau —
LƯU Ý máy yếu: chạy suite đo lường thì tuần tự). V3-3 rồi V3-4 tuần tự (cùng chạm answer path /
confidence + eval harness), V3-4 qua sdlc-full. V3-5 nhét xen kẽ lúc chờ. Khi giao kèm: nội dung
WP nguyên văn + `docs/feature-scorecard.md` (re-score 08-03) + kết quả Verification tổng V2
(trong scorecard §Verification tổng).
