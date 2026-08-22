# Báo cáo Phân Tích & Nghiên Cứu Kiến Trúc AI Sales Agent (2026-08-22)
**Chuyên đề**: Hiện tượng Overfitting vs Agent System Flow, sự bất đồng bộ giữa các Model Provider, và Lộ trình Chuẩn hóa Trí thông minh của Agent.

---

## 1. Tổng Quan & Căn Nguyên Vấn Đề (Executive Summary)

### Câu hỏi Cốt lõi
> *"Tại sao Profile 1 (Ollama Qwen) chạy pass 14/14 test cases nhưng khi chuyển sang Profile 3 (Groq) lại phát sinh lỗi và phải thêm các đoạn regex/heuristic để vá? Liệu hệ thống đang fix đối phó cho từng bài test hay thực sự có một kiến trúc Agent thông minh, độc lập với model?"*

### Nhận định Kiến trúc
1. **Luồng nghiệp vụ cấp cao (LangGraph + HITL + Postgres Single-DB) được thiết kế đúng và vững chắc**: Cơ chế ngắt trạng thái (Interrupt), hàng đợi tin nhắn đồng bộ (`QueuedMessage`), kiểm soát gian lận giá, và vòng lặp phân loại ý định đều là các cấu trúc chuẩn cho Agent 2026.
2. **Điểm nghẽn nằm ở Tầng Giao tiếp Model Gateway (LiteLLM & Structured Output Parsing)**:
   * Các Model Provider khác nhau (`Ollama`, `Groq`, `OpenAI`, `vLLM`) có cách thực thi *Structured Output / Tool Calling* hoàn toàn khác nhau.
   * Khi model cloud (Groq `gpt-oss-20b`) trả về schema không tương thích hoàn toàn với Pydantic parser, exception xuất hiện và đẩy Agent rơi vào nhánh fallback ngầm (`primary_intent = INFO_QUERY`).
   * Việc fallback thành `INFO_QUERY` làm "tê liệt" toàn bộ các node phía sau (Node đặt hàng, Node kiểm tra giá, Node mặc cả không nhận được đúng intent để kích hoạt).
3. **Hiện tượng "Vá đối phó" (Overfitting by Heuristics)**:
   * Trong quá trình debug nhanh cho Groq, một số đoạn regex tiếng Việt (ví dụ: `re.search(r"^(?:tôi muốn đặt|tôi đặt...)")`) đã được thêm vào tầng routing để giải cứu việc LLM bị parse lỗi.
   * Đây là giải pháp **chữa cháy ngắn hạn**, làm giảm tính tổng quát và sự thanh thoát của Agent System Flow.

---

## 2. Phân Tích Kỹ Thuật Chi Tiết Các Điểm Lỗi

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         LANGGRAPH AGENT GRAPH                            │
│  Router ──► Retrieval ──► Confidence / Clarify ──► HITL Guard ──► Exec   │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     │
                 ┌───────────────────┴───────────────────┐
                 ▼                                       ▼
         [Profile 1: Qwen]                       [Profile 3: Groq]
   • Parser: Native JSON Schema            • Parser: Chuyển thành Tool Calling
   • Boolean: `false` (chuẩn)             • Boolean: Sinh ra `False` (lỗi)
   • Exception: Không                      • Exception: `400 Bad Request`
   • Kết quả: Router ra đúng Intent        • Kết quả: Fallback sang `INFO_QUERY`
   • Đồ thị: Kích hoạt đúng Node           • Đồ thị: Bị chệch toàn bộ luồng
```

### A. Sự Bất đồng bộ Structured Output giữa các LLM
* **Ollama (Qwen 2.5 / 3)**: Hỗ trợ JSON format trực tiếp từ prompt/schema. Trả về JSON chuẩn, Pydantic `model_validate_json()` đọc mượt mà.
* **Groq / OpenAI Hosted**:
  * Ép `response_format=PydanticModel` thành *OpenAI Tool Calling (`json_tool_call`)*.
  * Khi model sinh ra dạng `<parameter=intent_shift>False</parameter>` (chữ F hoa kiểu Python thay vì `false` của JSON), bộ validator nội bộ của Groq tự văng lỗi:
    ```
    BadRequestError: tool call validation failed: parameters for tool json_tool_call 
    did not match schema: errors: [/intent_shift: expected boolean, but got string]
    ```
  * Khi LiteLLM gặp lỗi này, `router_node` bắt exception và gán `primary_intent = INFO_QUERY` (độ tự tin 0.0), khiến câu đặt hàng bị biến thành câu hỏi tra cứu thông tin.

### B. Vấn đề "Bộ nhớ Mơ hồ" (Stale Clarification Loop) trong LangGraph
* **Kịch bản**: 
  * Turn 1: Khách hỏi mơ hồ về tai nghe $\rightarrow$ Agent đặt cờ `awaiting_clarification = True` và lưu `clarify_original_query`.
  * Turn 2: Khách đổi ý hoàn toàn, không trả lời câu làm rõ mà đặt thẳng sản phẩm khác: *"Tôi muốn đặt 1 cái tai nghe Samsung Buds2 Pro..."*.
* **Lỗi kiến trúc**:
  * Node `retrieval_node` kiểm tra thấy `state.get("awaiting_clarification") == True` nên tự động gộp query cũ và mới lại thành: `"Tai nghe Sony... Tôi muốn đặt Samsung Buds2..."`.
  * Hậu quả: Tìm kiếm Vector trả về cả 2 sản phẩm và điểm tương đồng bị giằng co, kích hoạt lại câu hỏi làm rõ của Turn 1.

### C. Vấn đề Giới hạn Hạ tầng (Rate Limits & Database State)
1. **Groq Free-Tier Rate Limits**:
   * Giới hạn: 30 RPM (Requests Per Minute).
   * Trong 1 lượt hội thoại, Agent chạy qua `Router` $\rightarrow$ `Confidence` $\rightarrow$ `Answer` (mất 2-3 requests/turn). Chạy liên tục 14 cases tạo ra ~45 requests/phút $\rightarrow$ Groq trả về HTTP 429.
   * Hệ thống kích hoạt *Degraded Turns* ("Dạ hệ thống đang hơi quá tải...").
2. **Cạn kiệt Tồn kho (Database Depletion)**:
   * Các test case chốt đơn thành công tự động trừ tồn kho trong DB Postgres.
   * Sau nhiều lần chạy test suite lặp lại, `stock_quantity` của sản phẩm về 0. Khi đó, các lần test sau bị từ chối đơn hàng vì lý do hết hàng thật.

---

## 3. Phân Biệt: Logic Nghiệp Vụ (Business Invariants) vs Hardcode Đối Phó (Overfitting)

Để hệ thống chuyên nghiệp và đáng tin cậy, cần vạch rõ ranh giới giữa 2 nhóm logic:

| Tiêu chí | Logic Nghiệp Vụ Bắt Buộc (Business Invariants) | Hardcode Đối Phó (Language Overfitting) |
| :--- | :--- | :--- |
| **Bản chất** | Quy tắc an toàn vận hành, tài chính, pháp lý của doanh nghiệp (SME). | Tập luật `if/else` và Regex nhằm ép model pass một chuỗi test cụ thể. |
| **Ví dụ** | • Khách mặc cả giá / đòi mua Macbook 54tr với giá 5tr $\rightarrow$ Hệ thống so khớp giá catalog và chuyển sang HITL duyệt.<br>• Khách nhắn hủy đơn trong lúc chờ $\rightarrow$ Queue ưu tiên hủy đơn trước khi thanh toán.<br>• Thiếu SĐT/Địa chỉ $\rightarrow$ Bắt buộc hỏi lại trước khi chốt đơn. | • Bắt regex `r"^tôi muốn đặt \d+"` để ép intent thành `ORDER_PLACEMENT`.<br>• Bắt từ khóa `"giảm còn"` để ép intent thành `NEGOTIATION`.<br>• Gán cứng câu trả lời cho từng SKU sản phẩm trong code. |
| **Đánh giá** | 🟢 **BẮT BUỘC CÓ**: Không bao giờ được giao phó các quyết định tài chính/kho bãi cho LLM tính toán. | 🔴 **CẦN LOẠI BỎ**: Làm Agent mất tính linh hoạt, dễ vỡ khi khách nói câu tự nhiên khác mẫu. |

---

## 4. Kiến Trúc Đề Xuất Tối Ưu Triệt Để (Architectural Blueprint)

Để hệ thống đạt độ thông minh thực sự, hoạt động nhất quán 100% trên mọi Model Provider (Ollama, Groq, Gemini, OpenAI, Claude) mà không cần bất kỳ dòng Regex hardcode nào:

### 1. Chuẩn Hóa Universal JSON Extractor (Thay thế Tool Calling)
* **Nguyên tắc**: Không dùng native tool-calling của từng hãng (vốn hay thay đổi và không đồng nhất).
* **Giải pháp**: Sử dụng kỹ thuật **System Prompt Injection với Strict JSON Output**:
  ```python
  # Gửi schema JSON mẫu vào System Prompt
  # Dùng response_format={"type": "json_object"}
  # Áp dụng Pydantic validate trên chuỗi JSON trả về
  ```
* **Lợi ích**: Hoạt động đồng nhất 100% trên cả model 1.5B local lẫn model 70B cloud.

### 2. State Machine Quản Lý Chuyển Dịch Ngữ Cảnh (Context Reset Invariant)
* Khi phát hiện Intent mới hoặc `intent_shift == True`, LangGraph State Graph tự động:
  * Xóa bỏ cờ `awaiting_clarification = False`.
  * Xóa bỏ `clarify_original_query = None`.
  * Reset `clarify_count = 0`.
* **Lợi ích**: Tự động giải phóng ngữ cảnh cũ một cách tự nhiên trong luồng dữ liệu, không phụ thuộc vào việc khách hàng dùng từ ngữ gì.

### 3. Rate-Limit Aware Resilience Layer
* Tích hợp Adaptive Throttle trong Gateway: Khi phát hiện Provider là Cloud Free-tier (như Groq 30 RPM), Gateway tự động phân phối nhịp gọi (`TokenBucket` hoặc `LeakyBucket`) để không bao giờ chạm trần 429.

---

## 5. Kết Luận & Lộ Trình Nghiên Cứu Tiếp Theo

1. **Hiện trạng**: Hệ thống hiện đã đạt **14/14 PASS trên Profile 1 (Ollama)** và **11-13/14 PASS trên Profile 3 (Groq)**. Hệ thống đã có khả năng chuyển đổi hạ tầng 100% qua file `.env`.
2. **Kế hoạch Refactor tương lai**:
   * **Giai đoạn 1**: Loại bỏ toàn bộ các regex heuristic trong `router.py` và `confidence.py`.
   * **Giai đoạn 2**: Chuẩn hóa lớp Prompt-based JSON Extractor dùng chung cho mọi Model Gateway.
   * **Giai đoạn 3**: Xây dựng bộ Test Suite mở rộng (Dynamic Paraphrasing) để kiểm thử tính khái quát hóa của Agent trên nhiều cách diễn đạt khác nhau của khách hàng.
