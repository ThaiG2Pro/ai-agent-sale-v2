# Proposal: v2-1-groundedness-cascade (WP-V2-1 — trục CHÍNH XÁC)

**Type**: cr (fast-track docs sync — implement trước theo chỉ đạo trực tiếp của user, spec delta
ghi lại sau khi đo xong; xem `docs/upgrade-plan-v2.md` §WP-V2-1)
**Date**: 2026-07-30 · **Branch**: `feature/V2-1-groundedness-cascade`

## Problem

1. Answer path không có bước kiểm chứng: mọi claim (giá/tồn kho/thuộc tính) model sinh ra được
   gửi thẳng cho khách. Similarity gate Layer-1 (0.45) calibrate cho bge-m3; khi đổi embed model
   (`local/multilingual-e5-large`, WP-V2-0) similarity dồn band cao → câu hỏi ngoài catalog
   ("tủ lạnh", "PS5") lọt qua gate và được TRẢ LỜI thay vì decline. Đo được: Tier-F baseline
   out_of_catalog **0/4**.
2. Escalation COMPLAINT/NEGOTIATION nhảy thẳng PREMIUM_MODEL — tốn tiền cho cả những câu
   chat-tier trả lời tốt (research §7 cascade verification).

## Solution

1. **Groundedness self-check** (`services/rag/groundedness.py`): 1 call economy-chat chấm verdict
   Pydantic `{answerable, supported, unsupported_claims}` sau generation, wire vào CẢ HAI answer
   path. `answerable=false` → decline ngay; `supported=false` → regen với prompt siết grounding
   (tối đa `GROUNDEDNESS_MAX_REGEN`), vẫn fail → decline. Fail-open; kill switch
   `GROUNDEDNESS_CHECK_ENABLED`. Verdict ghi vào `model_traces.metadata`; answer bị reject không
   bao giờ vào semantic cache.
2. **Cascade verification** (graph answer_node): intent escalation trả lời bằng economy trước,
   chỉ đốt premium khi verdict fail. Kill switch `CASCADE_VERIFY_ENABLED`. LOW_CONFIDENCE giữ
   premium thẳng.

## Measured Results (2026-07-30)

| Metric | Before | After |
|--------|--------|-------|
| Tier-F tổng | 8/12 (67%) | **12/12 (100%)** |
| Tier-F out_of_catalog | 0/4 | **4/4** |
| Tier-F pricing/multi-intent/hallucination-trap | 8/8 | 8/8 (không false-decline) |
| Tier-R recall@k | 34/34 | 34/34 (không đổi — không chạm retrieval) |
| Unit suite | 376 pass | **395 pass** (19 test mới) |

Ghi chú đo lường: phát hiện semantic cache giữ answer của run cũ làm mờ phép đo Tier-F → thêm
`--flush-cache` vào `scripts/eval_gate.py`.

## Spec Delta

`specs/rag-pipeline/spec.md` — 2 ADDED requirements (groundedness self-check, cascade
verification), merge vào living spec khi archive change này.
