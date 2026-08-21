---
id: T13
title: Telegram handoff UX — hiển thị package bàn giao & human-reply qua webhook
type: wayfinder:grilling
mode: HITL
status: closed
assignee: thaivro-main-session
blocked-by: []
---

## Question

Delegation Contract (T07) đã chốt handoff package 4 thành phần (tóm tắt + lý do
structured, draft order snapshot, intent log, gợi ý hành động) và đường về giữ
`queue_consumer` + reason phải đến tay khách (O27). Chốt cách thể hiện trên Telegram:
1. Format tin nhắn admin nhận: bao nhiêu phần của package hiển thị thẳng vs thu gọn/xem thêm?
2. Thao tác approve / reject / counter-offer nhập thế nào — inline buttons, lệnh text,
   và nhập reason ra sao?
3. Khách thấy gì trong lúc chờ (holding message) và khi có kết quả (thông báo kèm
   reason — nghĩa vụ O27)?

## Resolution

Chốt 2026-08-12 qua grilling với owner. Fact nền: webhook đã parse `callback_query`
(inline buttons đang dùng cho nút retry inventory); `TelegramService` gửi HTML tới
support chat (giới hạn 4096 ký tự/tin); review hiện đi qua REST `/hitl/review`;
`pause_info["message"]` đã trả lời khách nhắn lúc session paused.

1. **Format tin admin — 3 phần thẳng + intent log thu gọn**: 1 tin HTML gồm
   tóm tắt + lý do structured, draft order snapshot (items/giá/SĐT/địa chỉ),
   gợi ý hành động — admin quyết được ngay không cần bấm thêm. Intent log (dài,
   ít dùng) thu sau nút "📋 Xem intent log" — callback trả tin thứ hai. An toàn
   giới hạn 4096 ký tự. Layout mẫu (đã duyệt qua preview):
   header 🔔 CẦN DUYỆT + mã case, khách, lý do → 📝 tóm tắt → 🗒 draft →
   💡 gợi ý → hàng nút `[✅ Duyệt] [✏️ Counter] [❌ Từ chối] [📋 Xem intent log]`.
2. **Thao tác — inline buttons + reply 2 bước cho reason**: 3 nút callback đổ vào
   đúng flow `/hitl/review` sẵn có. Duyệt = 1 chạm xong. Counter/Từ chối = bot hỏi
   tiếp bằng tin force-reply ("Nhập giá counter" / "Nhập lý do từ chối") — admin gõ
   text trả lời; reason là BẮT BUỘC ở 2 đường này để O27 luôn có nội dung gửi khách.
3. **Phía khách — holding có hẹn giờ + kết quả luôn kèm reason**: ngay khi pause,
   holding message nêu rõ "đang chuyển nhân viên, phản hồi trong ~X phút" (X khớp
   threshold alert chờ-quá-X-phút của T12). Kết quả về qua `queue_consumer`:
   approve → xác nhận đơn; counter → nêu giá mới + hỏi khách chốt; reject → lý do
   cụ thể admin đã nhập + gợi ý bước tiếp (đóng FAIL O27 trong Hard-Scenario
   Inventory). Khách nhắn thêm lúc chờ → giữ pause_info message như hiện tại.
