---
id: T11
title: "Research: Impact Analysis — 3 ứng viên cắt giảm trên đường 80% case"
type: wayfinder:research
mode: AFK
status: closed
assignee: research-subagent
blocked-by: []
---

## Question

T08 chốt kiến trúc nhưng user chưa dám cắt gì trên đường 80% case vì "sợ cắt lại ảnh
hưởng những thứ khác". Phân tích blast radius trong codebase cho từng ứng viên:

1. **Keyword fast-path SMALLTALK** (bỏ router call + answer call cho chào hỏi):
   ảnh hưởng gì đến intent tracking (turn SMALLTALK có cần ghi vào history/intent
   state cho combo T03 không?), semantic memory, sales_intent_logs, eval hiện có?
2. **Semantic-cache check trước router** (trả từ cache, 0 LLM call): cache hit có
   bypass mất HITL guard/intent tracking không? Câu hỏi cá nhân hóa (giá theo khách,
   tồn kho realtime) có nguy cơ trả cache cũ? Cache invalidation khi giá/stock đổi?
3. **Skip memory_retrieval_node có điều kiện**: node này hiện đóng góp gì vào
   confidence score và answer context cho từng intent? Khách cũ hỏi FAQ mà thiếu
   memory có làm câu trả lời mất cá nhân hóa?

Output: `wayfinder/research/cost-cut-impact-analysis.md` — mỗi ứng viên: các file/node
bị ảnh hưởng, rủi ro cụ thể, điều kiện an toàn để cắt (hoặc kết luận không nên cắt).
Quyết định cắt cuối cùng chốt ở T10 khi lắp spec.

## Resolution

Impact analysis xong 2026-08-11, chi tiết: `wayfinder/research/cost-cut-impact-analysis.md`.
Ba verdict:

1. **Keyword fast-path SMALLTALK — safe-with-conditions.** Cắt được 2 LLM call/turn chào hỏi,
   NHƯNG phải implement TRONG graph (branch keyword ở đầu `router_node` + template branch trong
   `answer_node`) để checkpoint vẫn ghi `messages` + `intent` — nếu trả lời ngoài graph sẽ bỏ đói
   combo T03. Gate keyword phải conservative (full-match, ≤4 từ, không token sản phẩm/giá/order);
   ~6 file test cần update; eval gold set không có SMALLTALK nên không ảnh hưởng.
2. **Semantic cache trước router — do-not-cut ở dạng đề xuất.** Pre-router cache bypass
   HITL-pause queue, escalation/cancel routing, pronoun-expansion cache key, và T03 history;
   chỉ tiết kiệm đúng 1 router call (L1 hit hôm nay đã 0 LLM call). Dạng an toàn: giữ cache
   in-graph, skip router LLM bằng keyword pre-classify cho whitelist INFO_QUERY/PRICING/AVAILABILITY.
   Cache hiện chỉ invalidate khi re-ingest — cần hook invalidation cho price/stock update.
3. **Skip memory_retrieval_node có điều kiện — safe-with-conditions nhưng giá trị thấp.**
   Node này 0 LLM call (1 embed local + SQL); skip làm khách cũ bị decline/clarify nhiều hơn
   (memory_context đang rescue borderline trong confidence_node). Chỉ nên skip khi cache-hit
   hoặc similarity ≥ threshold, không bao giờ skip FOLLOW_UP / time-reference.

Quyết định cắt cuối cùng chốt tại T10.
