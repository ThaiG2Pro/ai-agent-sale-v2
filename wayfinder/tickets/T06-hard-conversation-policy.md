---
id: T06
title: Hard-Conversation Policy (trả giá · khiếu nại · ngoài catalog · v2-6 Q4)
type: wayfinder:grilling
mode: HITL
status: closed
assignee: thaivro-main-session
blocked-by: []
---

## Question

Với mỗi loại hội thoại khó, agent được phép làm gì trước khi bàn giao human:
- **Trả giá/khuyến mại** (v2-6 Q4): từ chối thẳng, hay tạo draft ở giá gốc + note
  "khách xin giảm" cho human xử lý? Agent có được nói về khuyến mại đang chạy không?
- **Khiếu nại**: agent xoa dịu + thu thập thông tin đến đâu rồi mới chuyển human?
- **Câu hỏi ngoài catalog / mơ hồ**: clarify tối đa mấy lần trước khi nhận thua?
  (clarify_node hiện hỏi 1 câu) — và "nhận thua" nghĩa là decline hay handoff?
- **Đa ý định trong 1 message** (hỏi giá + đòi hủy + khiếu nại): ưu tiên xử lý nhánh
  nào trước?

Output: bảng policy per-intent (agent tự làm / làm-rồi-báo / bàn giao ngay) — đầu vào
trực tiếp cho spec.

## Resolution

Chốt 2026-08-12 qua grilling với owner. Fact nền: `escalation_node` hiện là pure-Python
chỉ đổi model tier (COMPLAINT/NEGOTIATION → premium) — agent đang TỰ trả lời các case
này; T06 bổ sung ranh giới hành động trước khi bàn giao theo tín hiệu 20% của T07.

1. **Trả giá / xin khuyến mại**: agent tạo **draft ở GIÁ GỐC** + note structured
   "khách xin giảm X" vào handoff package (T07) rồi chuyển human quyết giá. Agent
   ĐƯỢC nói về khuyến mại đang chạy trong catalog (fact retrieval được) nhưng
   KHÔNG BAO GIỜ tự hứa giảm thêm hay counter-offer — chống prompt-injection ép giá
   trên model nhỏ.
2. **Khiếu nại**: agent xoa dịu + hỏi tối đa **2 lượt** để thu 3 fact: đơn nào
   (order id / sản phẩm), vấn đề gì, khách mong muốn gì — điền vào handoff package
   rồi chuyển human. Không tự xử lý khiếu nại (kể cả "nhẹ") — không thêm classifier
   nặng/nhẹ.
3. **Ngoài catalog / mơ hồ — "nhận thua" tách 2 đường**: mơ hồ về sản phẩm CÓ trong
   catalog mà clarify 2 lần (quota T07) vẫn không chốt được → **handoff** (vẫn là
   lead); câu rõ ràng NGOÀI catalog → **decline lịch sự + gợi ý sản phẩm gần nhất**,
   KHÔNG handoff (không làm loãng queue/deflection rate).
4. **Đa ý định trong 1 message**: thứ tự ưu tiên cứng
   **CANCEL > COMPLAINT > NEGOTIATION > ORDER > INFO/PRICING**. Agent xử lý nhánh
   cao nhất, ACKNOWLEDGE các nhánh còn lại trong cùng câu trả lời; nhánh chưa xử lý
   giữ trong `secondary_intents` để turn sau xử tiếp — khớp cơ chế router sẵn có,
   fix FAIL H6 (T01).

### Bảng policy per-intent (đầu vào spec)

| Loại hội thoại | Agent tự làm | Làm-rồi-báo (chuyển kèm package) | Bàn giao |
|---|---|---|---|
| Trả giá / xin KM | Nêu KM đang chạy trong catalog | Tạo draft giá gốc + note "xin giảm X" | Human quyết giá; agent không counter |
| Khiếu nại | Xoa dịu | Thu 3 fact (≤2 lượt hỏi): đơn nào · vấn đề gì · mong muốn gì | Luôn chuyển sau khi thu fact |
| Mơ hồ (SP trong catalog) | Clarify tối đa 2 lần (T07) | — | Handoff nếu vẫn không chốt |
| Ngoài catalog rõ ràng | Decline lịch sự + gợi ý SP gần nhất | — | KHÔNG handoff |
| Đa ý định | Xử lý nhánh ưu tiên cao nhất + acknowledge phần còn lại | Nhánh còn lại giữ ở `secondary_intents` | Theo policy của nhánh ưu tiên |
