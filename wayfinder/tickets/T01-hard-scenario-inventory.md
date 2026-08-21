---
id: T01
title: Hard-Scenario Inventory & Candidate Eval Set
type: wayfinder:task
mode: AFK
status: closed
assignee: thaivro-main-session
blocked-by: []
---

## Question

Gom toàn bộ "case khó" đã biết của agent thành một inventory có phân loại + tần suất
ước lượng, làm nền cho mọi quyết định 80/20 phía sau. Nguồn: 7 order-group live
scenario scripts (commit 7c5517e), test suite hiện có (unit/integration/eval),
`reports/eval_runs/`, bảng 7 edge case + 5 open questions từ phân tích v2-6
(2026-08-10), và các nhánh graph ít được test (queue_consumer, state_freshness,
clarify). Output: `wayfinder/research/hard-scenario-inventory.md` — mỗi scenario ghi
nhóm (order-edge / hội-thoại-khó / intent-flip / hạ-tầng), hiện trạng agent xử lý ra
sao, và đề cử vào eval set hay không. KHÔNG quyết định fix gì — chỉ kiểm kê để các
ticket quyết định sau có số liệu.

## Resolution

Đã kiểm kê **55 hard case** từ 6 nguồn (live scripts 7c5517e, tests/, gold_dataset, eval_runs, 6 báo cáo kịch bản trong reports/). Chi tiết: `wayfinder/research/hard-scenario-inventory.md`.

- **Số lượng theo nhóm**: order-edge 28 · hội-thoại-khó 13 · intent-flip 5 · hạ-tầng 9.
- **Hiện trạng**: ~27 pass · **8 FAIL/partially đang mở** (ambiguous auto-select O5, ADD-ON mất đơn gốc O14, reject reason không đến khách O27, mixed intent H6, hallucination trap ht_004 H5, flip "đổi ý" bị gán FOLLOW_UP F2, "đặt cái đó đi" F4, thiếu proactive alert O28) · 17 untested · 3 gap trắng.
- **Gap coverage lớn nhất**: (1) intent-flip ngoài HITL queue — zero test, script G7.2 còn bug session_id; (2) gold_dataset chỉ phủ RAG — không có SMALLTALK/ORDER/HITL/cancellation/negotiation; (3) nhánh LLM-classifier của queue_consumer đã fail thực tế nhưng không có eval chặn regress; (4) state_freshness_validator chỉ 3 unit test, thiếu concurrency; (5) không có test rate-limit 429.
- **Nguồn thất lạc**: tài liệu "v2-6 draft-order analysis (2026-08-10)" không tìm thấy trong repo — 7 edge case đã tái dựng từ `scripts/test_advanced_edge_cases.py`, 5 open questions không khôi phục được (cần owner cung cấp).
- **Đề cử eval set (14)**: Tier1 auto-order · Tier2 pause→approve · ambiguous→clarify · thiếu SĐT/địa chỉ · intent-flip cùng session · "đổi ý" khi paused · mind-changer→CANCEL · ADD-ON giữ đơn gốc · mixed intent · negotiate-or-cancel · reject reason · price manipulation · hallucination trap · SMALLTALK guardrail.
- Live scripts không có assertion (chỉ print) — mọi kịch bản chỉ-có-trong-script được đánh `untested`, không suy đoán pass.
