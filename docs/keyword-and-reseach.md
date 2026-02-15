# TEMPLATE: KẾ HOẠCH TỪ KHÓA NGHIÊN CỨU (KEYWORD SEARCH PLAN)
_Đây là định dạng đầu ra mà AI bắt buộc phải trả về._

# **🎯 MISSION: [Tên vấn đề/Tính năng trong Project Log cần giải quyết]**
**Context Check:** (AI xác nhận: Bạn đang ở Tuần [X], cần giải quyết [Vấn đề A] để hoàn thành [Milestone B]).

### **PHẦN 1: CÔNG CỤ & TỪ KHÓA (The Tools)**
_(Cung cấp vũ khí để đi săn)_
**1. Core Tech Stack (Công nghệ chỉ định):**
- Primary Lib/Tool: `[Tên thư viện]` (Version: `[Version mới nhất/ổn định]`)
- Alternative (Optional): `[Tên thư viện thay thế]` (Chỉ đưa ra nếu cần so sánh).
- **Why:** (Giải thích 1 dòng tại sao chọn cái này dựa trên Tech Design hoặc dựa trên update mới nhất).

**2. Smart Search Queries (Copy-paste để tìm kiếm):**

- _Tìm hiểu concept:_ ( How it works), ví dụ : 
	- `"[Keyword] architecture pattern best practices 2024"`
	- `"[Keyword] vs [Competitor] for [Context: e.g., High concurrency API]"`
- _Tìm cách code:_ best-practive được chuyên gia khuyên dùng hiện nay , ví dụ : 
	- `"[Keyword] implementation best practices [Framework]"`
    - `"[Keyword] implementation with [Framework tên] [Version] example"`
	- `"How to handle [Specific Edge Case] in [Keyword]"`
	- `"Best folder structure for [Keyword] in [Project Type]"`
- _Tìm cách fix lỗi:_ (fix trước khi có - best-practive được chuyên gia ưu tiên chặn sớm). Ví dụ : 
	- `"Common pitfalls when using [Keyword] with [Database]"`
	- `"Common mistakes when using [Keyword] with [Database/Tool]"`
	- `"[Keyword] performance issues and optimization"`

**3. Documentation Navigator (Định vị tài liệu):**
- Must-Read Section: `[Tên mục trong Docs]` (Link tới trang chủ của công nghệ).
- Skip this Section: `[Tên mục nâng cao/cũ chưa cần thiết]` (Để tiết kiệm thời gian).

### **PHẦN 2: TƯ DUY CỐT LÕI (The Mindset)**
_(Các câu hỏi định hướng để đảm bảo không học vẹt)
**Critical Thinking Questions (Phải trả lời được sau khi research):**
1. **Bản chất:** `[Keyword]` hoạt động như thế nào ở tầng dưới (under the hood)?
2. **Trade-off:** Tại sao dùng cách này mà không dùng `[Giải pháp thay thế]`? Đánh đổi là gì?
3. **Contextual Fit:** Trong dự án này, ta nên cấu hình nó như thế nào để tối ưu?
### **PHẦN 3: NHIỆM VỤ THỰC THI (The Execution)**
_(Hành động cụ thể để update Project Log)_
**1. Sandbox Task (Thử nghiệm nhỏ):**
- Viết một đoạn code mẫu (POC) để test `[Tính năng]`.
**2. Project Integration (Ghép vào dự án):**
- Áp dụng vào module `[Tên module]` để giải quyết vấn đề trong Log.
**3. Definition of Done (Tiêu chuẩn hoàn thành):**
- **Artifact:** Code chạy được, không lỗi logic.
- **Edge Case Test:** Đã xử lý được tình huống `[Tình huống lỗi giả định]`.