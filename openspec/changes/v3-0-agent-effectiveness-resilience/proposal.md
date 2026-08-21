# Proposal: v3-0-agent-effectiveness-resilience — Locked Upgrade Spec

**Type**: upgrade-plan (LOCKED 2026-08-12 — tổng hợp từ wayfinder map
"Agent Effectiveness & Resilience Upgrade Spec", `wayfinder/map.md`)
**Status**: planning-only. Spec này KHÔNG kèm implementation; đưa vào SDLC pipeline
theo từng nhóm ưu tiên P1→P4 (mỗi nhóm có thể bổ thành change con khi build).
**Nguồn quyết định**: 12 ticket đã đóng trong `wayfinder/tickets/` — spec này lắp ráp
và KHÓA, chi tiết đầy đủ nằm ở ticket + `wayfinder/research/`.

## Problem

1. **Intent-flip 180° giữa hai câu liền nhau làm gãy hội thoại** — router stateless,
   `make_initial_state` wipe intent mỗi turn; flip "đổi ý" khi session paused bị gán
   FOLLOW_UP (FAIL mở trong hard-scenario inventory).
2. **8 FAIL mở / 55 hard case** (T01): ADD-ON làm mất đơn gốc (O14), reject reason
   không đến tay khách (O27), đa ý định xử sai nhánh (H6)…; 17 case chưa test;
   zero test cho 429/degradation.
3. **Tier 1 là dead code** — mọi order interrupt như Tier 2, human duyệt cả đơn lặt vặt;
   handoff package chỉ là `reason` 50 ký tự + JSONB tự do.
4. **Zero resilience** — LiteLLM Router không retry/timeout/runtime-fallback
   (`cooldown_time: 0`); Groq free tier 70b trần 100K TPD (~25-50 turn RAG nặng/ngày);
   một call treo là treo cả turn.
5. **Không đo được 80/20** — không có metric deflection, không alert khi khách chờ quá
   lâu trong queue (gap O28).

## Destination (tiêu chí nghiệm thu spec)

Agent tự xử lý ~80% case phổ thông (deflection rate theo session ≥ 80%), bàn giao mượt
~20% case khó cho human, xử lý được intent-flip 180°, bền ở demo scale
(**SLO thiết kế: ~10 user đồng thời, ~30 message/phút** — T09), tất cả trong
**zero-cost stack** (Groq free tier / Ollama local + fastembed, ADR-001/ADR-006).

---

## Locked decisions — 4 nhóm ưu tiên

> Thứ tự P1→P4 là thứ tự build đã chốt: **nền chống-gãy trước, hiệu quả sau** — lỗi
> đang xảy ra sửa trước, tính năng mới (tool-loop) và tối ưu chi phí sau cùng.

### P1 — Nền chống-gãy: intent-flip + FAIL fixes (T03 · T06 · T08)

| # | Quyết định | Nguồn | Chi phí LLM |
|---|---|---|---|
| 1.1 | **Full combo intent-tracking**: router đọc 3 turn cuối + `previous_intent` từ checkpointed state; `IntentClassification` thêm cờ `intent_shift`; transition table thuần Python (ORDER→CANCEL là chuyển tiếp mong đợi, nhảy lạ cần confidence ≥0.7, hysteresis <0.5); `make_initial_state` NGỪNG wipe `intent`/`secondary_intents`; keyword fast-path mở rộng tín hiệu do dự ("thôi", "để xem thêm", "khoan đã"…). Query rewriting: DEFER. | T03, T08 | **0 call thêm/turn** |
| 1.2 | **Đa ý định**: ưu tiên cứng CANCEL > COMPLAINT > NEGOTIATION > ORDER > INFO/PRICING; xử nhánh cao nhất, acknowledge phần còn lại, nhánh chưa xử giữ `secondary_intents` cho turn sau (fix H6). | T06 | 0 |
| 1.3 | **Confidence gating giữ embedding-similarity threshold + tinh chỉnh**: `intent_shift`/`previous_intent` tham gia route; borderline ưu tiên clarify hơn escalate. Không LLM-judge. | T08 | 0 |
| 1.4 | **Sửa các FAIL mở thuộc flow hội thoại** (flip "đổi ý" khi paused bị gán FOLLOW_UP; các FAIL router/queue_consumer trong T01) — riêng ADD-ON mất đơn gốc giải ở P2 (items[]), reject reason giải ở P2 (O27). | T01 | 0 |

### P2 — Trục order/HITL hoàn chỉnh (T05 · T06 · T07 · T13)

| # | Quyết định | Nguồn | Chi phí |
|---|---|---|---|
| 2.1 | **Draft-order model**: KHÔNG bảng mới — draft là row `orders`, `status ∈ {draft, pending_review, confirmed, cancelled, superseded, expired}` + cột `supersedes_id` (self-FK); `order_info.items[]` multi-item (giải gốc O14 ADD-ON, mở đường combo O12); soft cap 3 active/khách; TTL 24h; supersede inline cùng transaction; expiry lazy lúc đọc; không xóa row, không background job. Agent không edit draft — chỉ tạo mới. | T05 | 1 migration, 0 LLM |
| 2.2 | **Tier 1 auto-proceed có điều kiện**: bỏ hardcode `ORDER_PLACEMENT → Tier 2` trong `_resolve_risk_tier`; tự tạo đơn khi: giá trị < ngưỡng Tier 1 (default **10 triệu VND**, config), SP xác định duy nhất, đủ SĐT + địa chỉ, stock đủ, khách không flag. **Safety invariant giữ nguyên**: giá trị unknown/cao → luôn ≥ Tier 2. | T07 | 0 |
| 2.3 | **Tiêu chí 20% — 4 tín hiệu OR**: risk score composite ∨ intent NEGOTIATION/COMPLAINT ∨ clarify-loop ≥ 2 ∨ hạ tầng degraded (429/offline). **Metric: deflection rate theo session ≥ 80%**, đo từ `support_queue` + `interrupted_sessions`. | T07 | 0 |
| 2.4 | **Handoff package 4 phần bắt buộc**: tóm tắt hội thoại + lý do escalate structured (tín hiệu nào bắn); draft order snapshot; intent log + trạng thái khách; gợi ý hành động (+1 LLM call lúc escalate — chấp nhận). | T07 | +1 call/escalate |
| 2.5 | **Hard-conversation policy per-intent** (bảng đầy đủ trong T06): trả giá → draft GIÁ GỐC + note "xin giảm X", agent được nêu KM trong catalog, KHÔNG BAO GIỜ counter; khiếu nại → xoa dịu + thu 3 fact trong ≤2 lượt rồi luôn chuyển; mơ hồ-trong-catalog clarify 2 lần rồi handoff, ngoài-catalog decline lịch sự + gợi ý gần nhất (KHÔNG handoff). | T06 | 0 |
| 2.6 | **Telegram handoff UX**: tin admin 1 message HTML — 3 phần thẳng (tóm tắt+lý do, draft snapshot, gợi ý) + intent log sau nút callback; inline buttons [Duyệt]/[Counter]/[Từ chối] đổ vào flow `/hitl/review` sẵn có; Counter/Từ chối hỏi reason bằng force-reply (**reason bắt buộc**); khách nhận holding "~X phút" + kết quả LUÔN kèm reason (đóng FAIL O27). | T13 | 0 |
| 2.7 | **Đường về giữ `queue_consumer`** (keyword pre-classify → LLM fallback) + 2 nghĩa vụ: reason admin PHẢI vào response khách (O27); nhánh LLM fallback qua eval gate (case F2 "đổi ý" vào eval set). | T07 | giữ nguyên |

### P3 — Resilience + Observability (T09 · T12)

| # | Quyết định | Nguồn | Infra mới |
|---|---|---|---|
| 3.1 | **Fallback ladder rẽ theo intent**: `Groq 70b → Groq 8b → Ollama local → cache-only → holding+queue`. INFO/PRICING/SMALLTALK ăn mọi rung; **ORDER/NEGOTIATION/COMPLAINT không nhận trả lời từ rung local/cache** — rơi thẳng holding+queue→human (degraded là tín hiệu 20%, khớp 2.3). Thứ tự tắt: premium tool-loop (P4) trước → escalation premium về economy (cơ chế T064 sẵn có) → cuối cùng holding. Mọi câu degraded tag `model_used`. | T09 | 0 |
| 3.2 | **Timeout budget**: cap tổng ~30s/turn rồi holding; 15s/call cloud, 25s local; 0–1 retry; **429 KHÔNG retry** (free-tier 429 kéo dài hàng giờ) — cooldown + nhảy fallback ngay; per-exception retry policy của LiteLLM Router; không retry auth error. | T09 | 0 |
| 3.3 | **Backpressure**: semaphore in-process cap N turn LLM đồng thời; tràn → `queued_messages` (drain `FOR UPDATE SKIP LOCKED`, worker concurrency ≪ pool 20); **token-budget gate app-side** — 1 row Postgres đếm token/model/ngày UTC, chạm ~90% → chủ động degrade sớm (Groq không expose bộ đếm ngày). | T09 | 1 bảng đếm nhỏ |
| 3.4 | **Hiển thị**: endpoint stats JSON dưới `/admin` (deflection rate, queue depth, đếm degraded turn) + mini dashboard tĩnh trên khung `ui.py`/`api/static` sẵn có. Phoenix giữ vai trò trace, không gánh metric. | T12 | 0 |
| 3.5 | **Alert 3 loại qua Telegram admin** trên loop `timeout_scheduler` tái dụng: queue > N; case chờ > X phút (đóng gap O28); degrade event. Default demo: N=5, X=10 phút (config). | T12 | 0 |
| 3.6 | **Log 80/20 = span attributes** trên trace Phoenix sẵn có: `intent`, `model_used`/tier, `risk_signals` (4 tín hiệu của 2.3), `outcome` (self-handled/handoff/declined/queued). Không bảng mới; deflection tính từ bảng Postgres (2.3). | T12 | 0 |

### P4 — Hiệu quả: tool-loop 20% + cắt giảm đường 80% (T08 · T11, chốt cắt tại spec này)

| # | Quyết định | Nguồn | Ghi chú an toàn |
|---|---|---|---|
| 4.1 | **Hybrid escalation**: router enum giữ đường 80%; tool-calling loop trên Groq `llama-3.3-70b` CHỈ cho turn tư vấn mơ hồ/intent-gap (~20%), guardrails **G1–G8** (tool read-only, loop ≤2 hop, Pydantic-validate + 1 retry, schema tiếng Anh, ≤3 Groq call/turn, 429 → fallback local). Order/HITL giữ state machine. Đây là tính năng TẮT ĐẦU TIÊN khi degrade (3.1). | T02, T08 | qwen3-4b local chỉ fallback single-shot |
| 4.2 | **CẮT (chốt): SMALLTALK fast-path IN-GRAPH** — branch keyword đầu `router_node` + template branch trong `answer_node`; checkpoint vẫn ghi `messages`+`intent` (không bỏ đói combo 1.1). Gate conservative: full-match, ≤4 từ, không token sản phẩm/giá/order. Tiết kiệm 2 call/turn chào hỏi; ~6 file test update. | T11 | KHÔNG trả lời ngoài graph |
| 4.3 | **CẮT (chốt): keyword pre-classify whitelist** cho INFO_QUERY/PRICING/AVAILABILITY — skip router LLM, cache GIỮ in-graph (dạng pre-router bị bác: bypass HITL-pause/routing). **Điều kiện bắt buộc: hook invalidation semantic cache khi price/stock update** (hiện chỉ invalidate khi re-ingest). | T11 | dạng do-not-cut gốc đã loại |
| 4.4 | **CẮT (chốt): skip `memory_retrieval_node` có điều kiện** — CHỈ khi cache-hit hoặc similarity ≥ threshold; **không bao giờ skip FOLLOW_UP / query có time-reference** (node 0 LLM call, đang rescue borderline trong confidence — giá trị cắt thấp, chốt cắt theo lựa chọn owner với đầy đủ điều kiện an toàn T11). | T11 | latency-only |

---

## Ngân sách call/turn (hệ quả các quyết định)

- Đường 80%: 1 router call + 1 answer call (như hiện tại); SMALLTALK/whitelist hit: 0–1 call.
- Đường 20%: +≤2 tool hop trên 70b, trần ≤3 Groq call/turn (G8); +1 call handoff-suggestion lúc escalate.
- 70b chỉ phục vụ ~20% turn → trần 100K TPD đủ cho ngày demo ở SLO đã chốt.

## Eval gates (từ Hard-Scenario Inventory T01)

1. **Eval set**: 14 case đề cử từ T01 + case F2 ("đổi ý" qua nhánh LLM fallback của
   queue_consumer — nghĩa vụ 2.7) + ht_004 (hallucination trap đang không declined).
2. **Test 429/degradation**: hiện zero — P3 phải kèm test cho fallback ladder,
   timeout budget, token-budget gate (mock 429, không đâm Groq thật).
3. **Metric gate**: deflection rate theo session ≥ 80% trên eval set hội thoại;
   retrieval recall giữ 34/34 Tier-R (regression guard).
4. **8 FAIL mở của T01** = acceptance test của P1/P2 (mỗi FAIL một test tái hiện).

## Ranh giới agent/human (tóm tắt hợp đồng T06 + T07)

| Tình huống | Agent | Human |
|---|---|---|
| Order thường, đủ điều kiện Tier 1 | Tự tạo đơn (2.2) | — |
| Order giá trị cao/unknown | Draft + interrupt Tier ≥ 2 | Duyệt qua Telegram (2.6) |
| Trả giá | Draft giá gốc + note, nêu KM catalog | Quyết giá |
| Khiếu nại | Xoa dịu + thu 3 fact (≤2 lượt) | Luôn nhận sau khi thu fact |
| Mơ hồ trong catalog | Clarify ≤2 lần | Handoff nếu vẫn bí |
| Ngoài catalog | Decline + gợi ý gần nhất | KHÔNG handoff |
| Hạ tầng degraded + intent rủi ro | Holding message | Queue→human |

## Rà soát mâu thuẫn (đã thực hiện khi khóa)

Không phát hiện xung đột giữa 12 quyết định. Các điểm giáp ranh đã khớp chủ đích:
- "Degraded" vừa là rung fallback (3.1) vừa là tín hiệu 20% (2.3) — nhất quán: intent
  rủi ro khi degraded đi thẳng human, intent thường ăn rung.
- Tier 1 auto-proceed (2.2) chỉ áp cho ORDER thường; NEGOTIATION luôn qua human (2.5) —
  không giẫm nhau vì NEGOTIATION là tín hiệu 20% cứng.
- SMALLTALK fast-path (4.2) nằm trong graph nên không phá combo intent-tracking (1.1).
- Tool-loop (4.1) là feature tắt đầu tiên của ladder (3.1) — hai chiều cùng một thứ tự.

## Zero-cost compliance

Mọi item: Groq free tier + Ollama local + fastembed + Postgres sẵn có. Infra mới duy
nhất: 1 bảng đếm token (3.3) + 1 cột `supersedes_id` + migration items[] (2.1).
Không Redis, không dịch vụ trả phí, không background job mới (alert dùng
`timeout_scheduler` sẵn có; expiry lazy).

## Out of scope (kế thừa map)

Thiết kế bắt buộc model trả phí · scale lớn/multi-tenant/deployment thật ·
migrate khỏi LangGraph · kênh mới (voice, web widget).

## Kill switch / rollback (nguyên tắc cho mọi nhóm)

Theo tiền lệ v2-4: mỗi nhóm P1–P4 phải có kill switch config riêng khôi phục hành vi
trước đó; migration có downgrade round-trip; topology graph đổi thì bump
`GRAPH_SCHEMA_VERSION`.
