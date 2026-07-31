# Proposal: v2-2-cache-key-citations (WP-V2-2 — trục CHÍNH XÁC)

**Type**: cr (fast-track — implement theo chỉ đạo trực tiếp của user, spec delta ghi lại kèm
kết quả đo; xem `docs/upgrade-plan-v2.md` §WP-V2-2)
**Date**: 2026-07-31 · **Branch**: `feature/V2-2-cache-key-citations`

## Problem

1. **L1 cache key phụ thuộc output LLM.** `search_and_retrieve` chạy normalize (LLM call)
   TRƯỚC, rồi mới lookup L1 bằng `canonical_query` — output normalize là non-deterministic nên
   cùng một raw query có thể sinh canonical khác nhau giữa hai lần → hash khác → L1 miss dù câu
   trả lời đã có trong cache. Hit-rate thấp + mỗi lần "hit hụt" vẫn tốn 1 chat call normalize.
2. **Citation chỉ ở mức chunk.** `Citation.source_text` là cả chunk (FR-011 yêu cầu chỉ ra
   BẰNG CHỨNG cụ thể) — người review không biết CÂU nào trong chunk đã ground câu trả lời.

## Solution

1. **L1 key trên raw query, lookup TRƯỚC normalize** (`services/rag/pipeline.py`): hash SHA256
   trên raw user query (strip+lower sẵn có trong `canonicalize_query`); L1 hit giờ tốn **0 chat
   call** (bỏ qua luôn normalize). Cache write trong `answer_with_rag` cũng key raw. Graph path
   (`_write_cache` trong answer_node) GIỮ key `canonical_query`: ở path đó normalize bị skip
   (intent pre-classified) nên canonical == query đã pronoun-expand — deterministic sẵn, và raw
   `user_message` chứa đại từ ("nó giá bao nhiêu") key theo raw sẽ nhiễm ngữ cảnh chéo sản phẩm.
   L2 vector giữ nguyên (embed trên raw từ trước).
2. **Fragment-level citations** (`services/rag/fragments.py`): sau khi answer được chấp nhận,
   mỗi citation nhận field optional `fragment_text` = câu trong `source_text` khớp answer nhất
   (SequenceMatcher — pattern sẵn có trong compression, 0 LLM call; dưới ratio 0.35 → None).
   `Citation` model thêm `fragment_text: str | None = None` (bắt buộc — thiếu field thì
   `Citation(**cached_dict)` trong retrieval_node raise và citation bị drop im lặng). Citations
   ghi vào cache cũng mang fragment → cache hit replay đủ grounding. `QueryResponse.citations`
   là `list[dict]` → không breaking.

## Bonus fix (phát hiện khi đo)

Generation fail (Groq 429) → fallback `DECLINE_MESSAGE` với `declined=False` bị **ghi vào
semantic cache** như answer hợp lệ → mọi hit sau replay câu từ chối giả cho tới hết TTL. Đã vá
cả hai path: `answer_with_rag` bỏ cache write khi `llm_response is None`; `answer_node` gate
`metrics is not None`. Bug tiềm ẩn từ trước V2-1, lộ ra khi eval chạy lặp làm cạn quota Groq.

## Measured results

| Metric | Before (main @ 09fe4a6) | After |
|---|---|---|
| Unit suite | 395 pass | 408 pass (13 test mới) |
| Tier-R recall@k | 34/34 (100%) | 34/34 (100%) — Δ 0.0pp |
| Tier-F smoke (--flush-cache) | 12/12 (100%) | 12/12 (100%) — Δ 0.0pp |
| L1 hit cùng raw query khi normalize đổi output | miss (bug) | hit, 0 chat call |

> Ghi chú đo: Tier-F post-change chạy trên `groq/llama-3.1-8b-instant` (cả CHAT/POWERFUL) vì
> quota TPD 100k/ngày của `llama-3.3-70b-versatile` đã cạn do 4 lần chạy eval trong ngày —
> model YẾU hơn baseline mà vẫn 12/12 (groundedness gánh phần decline). Đây cũng là bằng chứng
> trực tiếp cho bonus fix: 3 run đầu fail vì fallback-decline bị cache/generation 429.

## Kill switch / rollback

Không thêm setting mới: fragment extraction là pure-string (không LLM, không I/O); đổi cache key
là bug fix — rollback = revert commit. Cache entries cũ key theo canonical vẫn TTL-expire tự
nhiên (`CACHE_TTL_SECONDS`), không cần migration.
