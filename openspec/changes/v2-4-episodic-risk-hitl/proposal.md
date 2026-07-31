# Proposal: v2-4-episodic-risk-hitl (WP-V2-4 — trục THÔNG MINH)

**Type**: cr (fast-track — implement theo chỉ đạo trực tiếp của user, spec delta ghi lại kèm
kết quả đo; xem `docs/upgrade-plan-v2.md` §WP-V2-4)
**Date**: 2026-07-31 · **Branch**: `feature/V2-4-episodic-risk-hitl`

## Problem

1. **Thiếu tầng episodic memory (research §5).** Bộ nhớ hiện chỉ có tầng semantic (summary đã
   nén qua embedding) — khách hỏi "cái máy hôm qua em tư vấn ấy" là chịu: summary không giữ
   sự kiện cụ thể theo thời gian (hôm nào, nói về sản phẩm gì).
2. **HITL binary gây approval fatigue (research §6).** `hitl_guard_node` trigger bằng 2 điều
   kiện rời: `intent == ORDER_PLACEMENT` HOẶC `confidence < threshold`. Mọi đơn hàng — 50
   nghìn hay 50 triệu, khách quen hay khách lạ — đều dừng chờ người duyệt. Người duyệt ngập
   yêu cầu lặt vặt → duyệt cho có, nguy hiểm hơn không duyệt.

## Solution

1. **Episodic memory** (phương án (b) của plan — bảng mới, không đọc checkpoint):
   - Bảng `agent_v1.episodic_events` append-only (migration `b7e4d2a91c05` + downgrade —
     R-DB-001): customer_id, thread_id, user_message, response_summary (cắt 500 ký tự),
     intent, products (JSONB [{name, sku}] từ citations), created_at. Index
     (customer_id, created_at).
   - **Ghi**: `answer_node` (universal trace point) append best-effort sau khi có response —
     Path 0 (business), Path 1 (cache hit), Path 3 (accepted, không ghi khi groundedness
     decline). Bỏ qua SMALLTALK/declined (không có nội dung tư vấn đáng nhớ). Service không
     bao giờ raise.
   - **Đọc**: `memory_retrieval_node` — query có tham chiếu thời gian (regex tiếng Việt:
     "hôm qua", "lần trước", "bữa trước", "tuần trước", "đã tư vấn"…) → kéo
     `EPISODIC_RECENT_LIMIT` (default 5) event mới nhất của ĐÚNG customer_id vào
     memory_context (source="episodic") — hưởng luôn cơ chế memory-override decline sẵn có.
     Lỗi episodic không phá path semantic (best-effort).
   - **API**: `GET /memory/episodic/{customer_id}?limit=` (sau `require_admin_key`).
   - **RTBF**: cascade delete mở rộng thành 8 bảng (`episodic_events` thêm vào cả vòng thu
     thập thread_ids lẫn vòng delete).
   - Kill switch `EPISODIC_MEMORY_ENABLED=False` → không ghi, không đọc (pre-V2-4).
2. **Risk-score HITL 3 tier** (`hitl_guard_node`):
   - `risk = W_CONF·(1-confidence) + W_VALUE·order_value_norm + W_HISTORY·history_factor`
     (defaults 0.4/0.4/0.2). `order_value_norm` = giá×số lượng / `HITL_ORDER_VALUE_NORM_CAP`
     (clamp 1.0) — CHỈ áp cho ORDER_PLACEMENT (intent khác không có tiền tại chỗ = 0);
     thiếu/không parse được giá trị đơn → 1.0 (conservative). `history_factor` từ
     `intent_tracking`: CONVERTED=0.0 · ENGAGED/AWAITING_QUOTE/CONTACTED=0.5 · NEW/LOST=0.8 ·
     không có row / không customer_id / DB lỗi = 1.0 (khách lạ = rủi ro tối đa).
   - Bảng quyết định tier (S2-style, hành vi an toàn):

     | Điều kiện | Tier | Hành vi |
     |---|---|---|
     | risk < `HITL_RISK_TIER1_THRESHOLD` (0.35) | 1 | Tự chạy: ORDER → `queue_consumer_node` với `hitl_approved=True` (cùng đường đi như admin approve); intent khác → `answer_node` |
     | TIER1 ≤ risk < `HITL_RISK_TIER3_THRESHOLD` (0.75) | 2 | interrupt() chờ duyệt — đúng hành vi hiện tại |
     | risk ≥ TIER3 | 3 | `customer_support_node` thẳng (reason `high_risk_tier3`), không interrupt |
     | **INVARIANT** ORDER_PLACEMENT có giá trị > `HITL_HIGH_VALUE_ORDER_THRESHOLD` (5M VND) HOẶC giá trị unknown | ≥ 2 | Hardcode trong `_resolve_risk_tier` — chỉnh weight/threshold KHÔNG THỂ auto-approve đơn giá trị cao |

   - Cost guard (token threshold) giữ nguyên, chạy cả cho Tier 1. Escalation-limit guard giữ
     nguyên. Guard "ORDER không có order_info → answer_node" giữ nguyên, chạy trước risk.
   - Kill switch `RISK_HITL_ENABLED=False` → binary trigger pre-V2-4 nguyên bản.
   - Không đổi topology graph / channels → `GRAPH_SCHEMA_VERSION` giữ "006".

## Measured results

| Metric | Before (branch base f944c46) | After |
|---|---|---|
| Unit suite | 424 pass (455 sau test-cleanup 5371207) | 457 pass (33 test mới: 20 risk + 13 episodic) |
| Tier-R recall@k | 34/34 (100%) | 34/34 (100%) — Δ 0.0pp |
| Tier-F smoke (--rerun --flush-cache) | 12/12 (100%) | 12/12 (100%) — Δ 0.0pp |
| "cái máy hôm qua em tư vấn ấy" | semantic miss → decline | episodic events vào memory_context → trả lời được |
| Đơn nhỏ + khách CONVERTED + confidence cao | interrupt (chờ duyệt) | Tier1 tự chạy (unit-verified) |
| Đơn > 5M / thiếu giá trị đơn | interrupt | interrupt (invariant giữ nguyên — unit-verified) |
| RTBF | 7 bảng | 8 bảng (thêm episodic_events) |

> Ghi chú đo: Tier-R/Tier-F đi qua `hybrid_search_rrf`/`answer_with_rag` — không qua
> memory_retrieval/hitl_guard, hai gate là regression guard (flat = đúng kỳ vọng). Hành vi
> V2-4 nằm ở graph path — đo bằng 33 unit test (ma trận risk: giá trị đơn × confidence ×
> lịch sử; time-reference wiring; RTBF 8 bảng). Tier-F chạy trên `groq/llama-3.1-8b-instant`
> (70b TPM burst 429 — cùng caveat V2-2/V2-3). Fix kèm: 2 test embed trong
> `test_ai_offline.py` (relocate ở 5371207) pin `EMBED_MODEL=ollama/bge-m3` vì `local/`
> prefix bypass router seam đang test.

## Kill switch / rollback

`EPISODIC_MEMORY_ENABLED=False` + `RISK_HITL_ENABLED=False` khôi phục hành vi pre-V2-4.
Schema rollback: `alembic downgrade -1` (đã round-trip verify). Rollback code = revert commit.
