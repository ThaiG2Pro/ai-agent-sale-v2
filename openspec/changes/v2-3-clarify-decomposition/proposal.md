# Proposal: v2-3-clarify-decomposition (WP-V2-3 — trục THÔNG MINH)

**Type**: cr (fast-track — implement theo chỉ đạo trực tiếp của user, spec delta ghi lại kèm
kết quả đo; xem `docs/upgrade-plan-v2.md` §WP-V2-3)
**Date**: 2026-07-31 · **Branch**: `feature/V2-3-clarify-decomposition`

## Problem

1. **Borderline = từ chối.** Query qua được L1 (sim ≥ 0.45 — catalog CÓ thứ liên quan) nhưng
   fused confidence < 0.70 bị `declined=True` ngay tại confidence_node. Phần lớn các câu này
   là khách hỏi MƠ HỒ ("cấu hình có mạnh không" — máy nào?), không phải catalog thiếu dữ liệu.
   Một câu hỏi làm rõ sẽ chuyển decline thành câu trả lời đúng ở turn sau.
2. **COMPARISON regex là mảnh vá.** `retrieval_node` chỉ split `và/vs/với…` cho intent
   COMPARISON. Multi-intent thực tế ("Giá Galaxy A55 và còn hàng không?") rơi vào PRICING/
   AVAILABILITY và không bao giờ được split; regex cũng split sai khi "và" nằm trong tên/ngữ
   cảnh không phải ranh giới intent.

## Solution

1. **Clarify-question loop** (`core/agent/nodes/clarify.py` mới, graph edge
   `confidence → clarify_node → answer_node → END`):
   - confidence_node: Layer-2 decline (KHÔNG áp dụng cho Layer-1 decline, ORDER_PLACEMENT,
     hay các borderline-answer intents INFO_QUERY/PRICING/AVAILABILITY/COMPARISON vốn đi
     escalation) → `needs_clarification=True` thay vì `declined=True`.
   - clarify_node: sinh ĐÚNG 1 câu hỏi làm rõ bằng economy model (Pydantic
     `ClarifyingQuestion`, top-3 tên sản phẩm retrieved làm ứng viên "[X] hay [Y]"); LLM lỗi
     → câu hỏi tĩnh fallback. Set `awaiting_clarification=True`, lưu `clarify_original_query`,
     tăng `clarify_count`.
   - Turn kế tiếp: retrieval_node merge reply vào query gốc (`"{gốc} {reply}"`) rồi retrieve
     như thường; `clarify_count` giữ 1 nên merged query vẫn borderline → decline như cũ
     (chống loop, tối đa 1 clarify/query gốc). Query mới (không pending) reset count về 0.
   - State persist qua checkpointer sẵn có (thread_id = session_id, xuyên turn cả Telegram).
     3 field xuyên-turn (`awaiting_clarification`, `clarify_original_query`, `clarify_count`)
     cố ý KHÔNG nằm trong `make_initial_state` — input keys overwrite checkpoint channels.
   - Kill switch `CLARIFY_ENABLED=False` → decline như pre-V2-3. `GRAPH_SCHEMA_VERSION`
     005 → 006 (node + channels mới).
2. **LLM query decomposition** (`retrieval_node`): query declined với intent ∈ {COMPARISON,
   INFO_QUERY, PRICING, AVAILABILITY} → decompose bằng economy model (Pydantic
   `DecomposedQuery`, cap 3 sub-queries, mỗi sub-query tự đủ nghĩa) → search từng sub-query,
   merge citations (dedup chunk_id — pattern SC10 sẵn có, extract thành
   `_merge_subquery_results`). LLM lỗi/kill switch `QUERY_DECOMPOSITION_ENABLED=False` →
   regex COMPARISON split giữ nguyên làm fallback (hành vi pre-V2-3). LLM trả 1 sub-query
   (single intent) → giữ declined, không re-search.

## Measured results

| Metric | Before (main @ b3bfadf) | After |
|---|---|---|
| Unit suite | 428 pass | 447 pass (21 test mới, 2 test Layer-2 cập nhật hành vi) |
| Tier-R recall@k | 34/34 (100%) | 34/34 (100%) — Δ 0.0pp |
| Tier-F smoke (--flush-cache) | 12/12 (100%) | 12/12 (100%) — Δ 0.0pp |
| Borderline query (graph) | DECLINE_MESSAGE | 1 câu hỏi làm rõ → answer đúng turn 2 (2-turn graph test) |
| Multi-intent declined (graph) | decline (trừ COMPARISON regex) | LLM decompose → merged answer |

> Ghi chú đo: Tier-R/Tier-F đi qua `hybrid_search_rrf`/`answer_with_rag` — pipeline KHÔNG bị
> WP này chạm tới, hai gate là regression guard (flat = đúng kỳ vọng). Multi_intent Tier-R đã
> 34/34 từ trước (top-10 raw retrieval đủ chứa cả 2 SKU); giá trị decomposition nằm ở graph
> path khi main retrieval decline — đo bằng unit + 2-turn graph flow test (mock LLM, graph
> compiled thật). Tier-F post-change chạy trên `groq/llama-3.1-8b-instant` vì 70b TPM burst
> 429 giữa run làm answer rỗng (pipeline fail-open) — cùng caveat như V2-2. Case mi_002 flaky
> trên 8b: sim 0.894 nhưng groundedness verdict fail-closed từ chối nhầm ~1/2 lần chạy (probe
> 2 lần: 1 decline / 1 pass với 15 citations) — retry qua checkpoint resume thì pass; không
> phải regression của WP này (diff không chạm `services/`).

## Kill switch / rollback

`CLARIFY_ENABLED=False` + `QUERY_DECOMPOSITION_ENABLED=False` khôi phục hành vi pre-V2-3
(regex COMPARISON split vẫn chạy). Checkpoint cũ (schema 005) bị `GRAPH_SCHEMA_VERSION`
guard chặn như thiết kế FR-018. Rollback = revert commit.
