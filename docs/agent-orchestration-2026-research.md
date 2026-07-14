# Agent Orchestration (LangGraph) trong repo — so với xu hướng 2026

Research dựa trên web search tháng 7/2026 + đọc trực tiếp code hiện tại (`core/agent/graph.py`, `core/agent/nodes/*`). Mục tiêu: đánh giá xem kiến trúc orchestration hiện tại có "hiệu quả và tối ưu" so với cách ngành đang làm agent bán hàng năm 2026 hay không.

## Kết luận nhanh

**Lựa chọn framework (LangGraph) đúng, kiến trúc tổng thể hợp lý cho use-case, nhưng cách dùng nó vẫn ở mức "workflow cố định" chứ chưa phải "agent" theo nghĩa 2026 đang tiến hoá tới.** 3 khoảng cách lớn nhất so với state-of-the-art: (1) RAG vẫn là pipeline tĩnh một lượt, chưa "agentic RAG" (reasoning loop tự phản biện/retry); (2) memory chỉ có tầng semantic, thiếu episodic; (3) HITL trigger dùng ngưỡng đơn giản thay vì risk-score tổng hợp. Quan trọng hơn cả việc "tối ưu theo 2026": **hệ thống hiện đang có bug chặn hoàn toàn luồng Telegram (xem `docs/break-down.md` mục 7) — bàn tối ưu hoá trước khi fix bug nền tảng là vô nghĩa.**

Ngoài ra, cần làm rõ: repo này là **conversational sales agent** (chat inbound, trả lời khách qua Telegram, chốt đơn) — khác với **AI SDR** (outbound prospecting, multi-agent "prospector/copywriter/outreach coordinator") đang là chủ đề nóng nhất 2026. Không nên áp toàn bộ pattern của AI SDR vào đây; điểm so sánh đúng là các agent hội thoại/customer-facing (shopping assistant), nơi agentic RAG, memory, và durable execution mới là trọng tâm.

## 1. Framework choice: LangGraph — vẫn là lựa chọn đúng năm 2026

Repo dùng `langgraph==1.0.8` (bản 1.0 stable, phát hành 10/2025) — **không lạc hậu**. Theo khảo sát 2026: LangGraph tiếp tục dẫn đầu về lượng tìm kiếm và vượt CrewAI về GitHub star đầu 2026, được coi là "production standard cho agentic workflow có state + auditability + human-in-the-loop", trong khi CrewAI thắng ở tốc độ prototype (2-4h) chứ không phải production.

Điểm mạnh của LangGraph so với alternative (CrewAI/AutoGen/OpenAI Agents SDK) mà repo đang tận dụng đúng: checkpointing có "time travel", framework model-agnostic (repo dùng LiteLLM đa model), hỗ trợ cả single-agent lẫn multi-agent trong cùng 1 framework.

**Đánh giá: không cần đổi framework.** Vấn đề của repo không nằm ở chọn sai công cụ, mà ở cách dùng công cụ đó.

## 2. Durable execution — điểm SÁNG, đã bắt kịp 2026

2026 là năm ngành hội tụ mạnh về "durable execution" cho agent (Temporal series D $300M/$5B valuation, DBOS, event-sourced state). Nguyên tắc chung được khuyến nghị: *"Replace in-memory storage with database-backed checkpoints before launch. Postgres works well. Survives restarts, supports horizontal scaling, enables recovery from mid-task failures."*

Repo **đã làm đúng chính xác điều này** từ trước: `langgraph-checkpoint-postgres`, `GRAPH_SCHEMA_VERSION` để phát hiện checkpoint không tương thích, `HITLService.get_session_state` xử lý lỗi schema mismatch → đánh dấu `INCOMPATIBLE` thay vì crash âm thầm. Đây là điểm hiếm hoi mà toy project thường bỏ qua nhưng repo này đã có.

**Đánh giá: giữ nguyên, đây là phần đã đúng chuẩn 2026, không cần đổi sang Temporal/DBOS** — lợi ích của Temporal/DBOS chủ yếu ở workflow *rất* dài hạn, nhiều ngày/tuần (ví dụ outbound SDR sequence) — không cần thiết cho hội thoại chat theo phiên như repo này.

## 3. Đây là "workflow", không phải "agent" đúng nghĩa 2026 — khoảng cách lớn nhất

Phân biệt kinh điển: **workflow** = code path định sẵn, LLM chỉ điền vào từng bước; **agent** = LLM tự quyết định bước tiếp theo, tự phản biện, tự lặp lại cho tới khi đạt mục tiêu. Graph hiện tại của repo là **12 node cố định, mỗi node gọi LLM đúng 1 lần cho 1 việc hẹp** (router phân loại 1 lần, confidence tính điểm 1 lần, answer sinh câu trả lời 1 lần) — không có node nào tự phản biện/retry/lặp cho tới khi tự tin. Đây chính xác là mô hình "workflow" trong tài liệu Anthropic, không phải "agent" theo nghĩa mà 2026 đang đẩy mạnh (agentic RAG, agentic reasoning loop).

Điều này **không sai** — với domain hẹp (bán hàng, câu hỏi sản phẩm, đặt hàng), workflow cố định *rẻ hơn, dễ audit hơn, dễ đoán hơn* agent tự do, và với ngành có yêu cầu compliance (EU AI Act Article 14 về human oversight, hiệu lực 2/8/2026) thì workflow tường minh dễ chứng minh tuân thủ hơn "agent tự quyết". Nhưng nó có nghĩa là marketing "AI agent" hơi rộng so với triển khai thật — về bản chất kỹ thuật đây là "agentic workflow", và có 1 khoảng cách cụ thể đáng nâng cấp: **retrieval (mục 4 dưới đây) — chỗ mà 2026 coi "reasoning loop" là bắt buộc, không phải tuỳ chọn.**

## 4. RAG: static pipeline — đây là khoảng cách rõ nhất với 2026

2026 được các nguồn mô tả là *"paradigm shift lớn nhất"*: RAG cổ điển (embed → retrieve top-k → rerank → generate, một lượt, không có cơ chế phục hồi khi retrieval kém) đang bị thay bằng **agentic RAG** — LLM đóng vai trò orchestrator: tự quyết định cần retrieve gì, tự đánh giá chất lượng kết quả, tự quyết định generate hay thử lại với query khác, lặp cho tới khi tự tin hoặc hết ngân sách.

Pipeline hiện tại của repo (`services/rag/pipeline.py::search_and_retrieve`) là **static, một lượt**: `classify_query → normalize → cache → embed → hybrid RRF search → compress → confidence guard`. Không có bước "đánh giá kết quả retrieval rồi tự quyết định retry với query khác" — trừ một ngoại lệ thủ công viết tay: `retrieval_node.py` có logic split COMPARISON query bằng regex (`và/vs/với`) làm 2 lần search rồi merge — đây là **một mảnh vá thủ công cho đúng 1 trường hợp cụ thể**, không phải cơ chế lặp tổng quát mà agentic RAG hướng tới.

**Cụ thể nên nâng cấp gì** (không phải viết lại toàn bộ, mà thêm 1 vòng lặp có kiểm soát):
- Sau khi `search_and_retrieve` trả về, thêm 1 bước LLM rẻ (economy/light model) tự chấm "kết quả này có đủ trả lời câu hỏi không?" — nếu không, tự viết lại query 1 lần rồi retry (giới hạn 1-2 vòng để tránh runaway cost).
- Đây chính là điều `confidence_node` đã làm một nửa (chấm điểm similarity/rerank) nhưng chỉ để quyết định "decline hay không", chưa dùng để quyết định "thử lại truy vấn khác".

## 5. Memory: chỉ có 1 trong 3 tầng chuẩn 2026

Ngành đã hội tụ về taxonomy 3 tầng: **episodic** (sự kiện có mốc thời gian — hội thoại/tool-call cụ thể), **semantic** (fact/preference không phụ thuộc thời gian — đã distill), **procedural** (kỹ năng/quy trình, thường nằm trong prompt). Production 2026 phổ biến nhất: vector phẳng hoặc tiered cho experiential memory + 1 tầng "organizational context" riêng.

Repo hiện có **đúng 1 tầng**: `services/memory/semantic_memory.py` — lưu summary đã tóm tắt (semantic, đã distill), qua `pgvector` thuần (không phải hybrid vector-graph — nghiên cứu 2026 nói hybrid mới khuyến nghị cho workload phức tạp, pure-vector "vẫn ổn cho use-case đơn giản"). Repo **không có episodic memory** — không lưu lại "khách đã hỏi cụ thể câu gì, agent đã trả lời gì, tool nào đã gọi" dưới dạng có thể truy vấn lại theo thời gian; chỉ có summary đã nén. Khi cần debug "tại sao agent trả lời sai ở lượt thứ 5" hoặc audit chi tiết, hiện chỉ có summary tóm tắt (mất chi tiết), không có "record of what happened" nguyên bản để trace lại.

**Đánh giá:** với quy mô hiện tại (SME sales agent), thiếu episodic memory chưa phải vấn đề khẩn cấp, nhưng nếu roadmap có "audit toàn bộ decision", "debug tại sao AI trả lời sai" thì cần thêm 1 bảng episodic (lưu message + tool call + kết quả theo timeline, có thể chính là `messages` trong checkpoint hiện tại nhưng chưa được expose thành API truy vấn riêng).

## 6. HITL: đúng pattern, nhưng ngưỡng trigger thô so với "risk-score" 2026

2026 nhấn mạnh vấn đề **"approval fatigue"**: nếu mọi hành động rủi ro đều cần duyệt, reviewer ngập trong hàng trăm request/ngày và bắt đầu duyệt hàng loạt cho qua — mất hết ý nghĩa an toàn. Giải pháp khuyến nghị: risk-score tổng hợp (blast radius, khả năng hồi phục, giá trị khách hàng) thay vì ngưỡng đơn lẻ, để Tier 1 (tự động) chiếm phần lớn, Tier 2 (cần duyệt) hiếm, Tier 3 (rủi ro cao) gần như không xảy ra.

Repo: `hitl_guard_node` trigger dựa trên 2 điều kiện tách rời — `confidence_score < 0.70` HOẶC `intent == ORDER_PLACEMENT` — không có risk-score kết hợp (giá trị đơn hàng, khách VIP hay khách mới, số lần đã escalate trước đó...). Cộng thêm `cost_guard.py` ước lượng token bằng heuristic 4 ký tự/token (không dùng tokenizer thật dù `tiktoken` đã có sẵn trong dependency) — nghĩa là ngưỡng "cost-based HITL trigger" (`HITL_COST_THRESHOLD_TOKENS`) đang dựa trên số ước lượng khá thô, đặc biệt sai lệch nhiều hơn với tiếng Việt (mật độ token/ký tự khác tiếng Anh).

**Điểm cộng đã đúng theo 2026:** pattern "synchronous gate-keeping" (agent dừng hẳn, dùng LangGraph `interrupt()`) là 1 trong 3 pattern chuẩn được liệt kê (cùng với async escalation và parallel feedback) — phù hợp cho quyết định giá trị cao (đặt hàng) như repo đang dùng cho `ORDER_PLACEMENT`. Không cần đổi pattern, chỉ cần tinh chỉnh **ngưỡng trigger** cho đúng tinh thần "Tier 1 volume cao, Tier 2 hiếm" — hiện `confidence < 0.70` khá rộng, có thể đang tạo Tier 2 volume cao hơn cần thiết.

## 7. Model routing/cascade: đã đúng hướng, chỉ thiếu "verification check" trước khi escalate

2026: phân biệt rõ *routing* (1 quyết định, chọn 1 model rồi dùng luôn) và *cascade* (chạy model rẻ trước, có rule/confidence-check quyết định có escalate lên model đắt hơn không). Cách phổ biến nhất trong production: rule rẻ xử lý case rõ ràng → classifier xử lý vùng mơ hồ → cascade (trả lời bằng model rẻ trước, chỉ escalate nếu confidence/verification check fail) xử lý phần đuôi khó nhất.

Repo đã có đúng cấu trúc 3 tier model (`economy-chat`/`LIGHT_CHAT_MODEL`/`PREMIUM_MODEL`) và **có cascade thật**: `router_node` (economy) → nếu COMPLAINT/NEGOTIATION → `escalation_node` dùng premium model. Đây là điểm phù hợp xu hướng cost-aware routing 2026 (kiểu HybridLLM/RouteLLM nhưng rule-based thay vì learned router — hợp lý ở quy mô SME, không cần learned router phức tạp).

**Khoảng cách nhỏ:** escalation hiện dựa trên *intent* (COMPLAINT/NEGOTIATION) là rule tĩnh, chưa có "confidence/verification check" sau khi trả lời bằng model rẻ để tự quyết định escalate (đúng nghĩa cascade). Có thể cải thiện bằng: trả lời bằng economy model trước, nếu `confidence_score` sau đó thấp mới escalate — nhưng đây là cải tiến nhỏ, không cấp thiết.

## 8. Multi-agent vs single-agent: đúng đắn khi KHÔNG multi-agent hoá

AI SDR 2026 (outbound prospecting) gần như luôn multi-agent (prospector/copywriter/outreach coordinator) vì các bước độc lập, chạy song song, khác domain kiến thức. Nhưng đó là bài toán khác — **conversational sales agent trả lời khách theo thời gian thực** (bài toán của repo) không có nhu cầu tự nhiên để tách nhiều "agent" độc lập; 12 node hiện tại đóng vai trò các bước tuần tự trong 1 luồng hội thoại, đúng với khuyến nghị *"LangGraph hỗ trợ single-agent, multi-agent, hierarchical trong cùng 1 framework"* — chọn single-agent-nhiều-node ở đây là hợp lý, **không nên multi-agent hoá chỉ để chạy theo trend.**

## 9. Tool integration: chưa dùng MCP — chấp nhận được ở quy mô hiện tại

2026 thấy MCP Gateway nổi lên như "control plane thống nhất" cho cả tool request lẫn model selection, đặc biệt khi agent cần nói chuyện với nhiều hệ thống ngoài (CRM, ERP...). Repo hiện dùng LangChain `@tool` decorator nội bộ (`inventory_lookup` là stub, comment ghi rõ "Real ERP integration deferred to Week 6") — chưa cần MCP vì chưa có tích hợp hệ thống ngoài thật sự.

**Đánh giá:** khi nào bắt đầu nối ERP/CRM thật (đúng lúc bỏ stub `inventory_lookup`), lúc đó nên cân nhắc expose các integration đó qua MCP server thay vì viết thẳng LangChain tool — dễ tái sử dụng cho công cụ khác (Claude Desktop, IDE...) và chuẩn hoá theo hướng ngành đang đi. Chưa cấp thiết bây giờ.

## Tổng kết: đã bắt kịp gì, còn thiếu gì

| Khía cạnh | Đánh giá | So với 2026 |
|---|---|---|
| Framework (LangGraph 1.0) | ✅ Đúng | Vẫn là production standard |
| Durable execution (Postgres checkpointer) | ✅ Đúng, làm tốt | Khớp chính xác best practice |
| Model cascade (economy→premium) | ✅ Đúng hướng | Thiếu verification-check để cascade "thật" |
| HITL pattern (interrupt) | ✅ Đúng pattern | Ngưỡng trigger thô, chưa risk-score |
| Single-agent, nhiều node | ✅ Đúng lựa chọn | Không cần multi-agent hoá |
| RAG | ⚠️ Static pipeline | Thiếu vòng lặp tự đánh giá/retry (agentic RAG) |
| Memory | ⚠️ Chỉ có semantic | Thiếu episodic; dùng pure-vector (chấp nhận được ở quy mô này) |
| Tool integration | ⚠️ Chưa MCP | Chưa cấp thiết, chưa có tích hợp ngoài thật |
| **Nền tảng vận hành** | 🔴 **Có bug chặn hoàn toàn luồng Telegram** | Ưu tiên trên tất cả các mục ở trên |

**Khuyến nghị thứ tự hành động:**
1. Fix các bug P0 đã tìm thấy trước (`docs/break-down.md`) — không có ý nghĩa tối ưu hoá kiến trúc trên nền đang crash.
2. Thêm 1 vòng lặp "tự đánh giá kết quả retrieval → retry có giới hạn" vào RAG pipeline — đây là nâng cấp có ROI cao nhất, đúng trọng tâm dịch chuyển 2026, và tận dụng được `confidence_node` đã có sẵn.
3. Thay ngưỡng HITL đơn lẻ bằng risk-score kết hợp (giá trị đơn hàng + độ tin cậy + lịch sử khách) để giảm approval fatigue khi scale.
4. Cân nhắc thêm episodic memory nếu roadmap cần audit/debug chi tiết từng lượt hội thoại.
5. MCP hoá tool integration khi thực sự nối ERP/CRM ngoài (chưa cấp thiết).

## Nguồn tham khảo

- [LangGraph: Agent Orchestration Framework for Reliable AI Agents](https://www.langchain.com/langgraph)
- [LangGraph Multi-Agent Orchestration 2026: Complete Enterprise Guide](https://devops.gheware.com/blog/posts/langgraph-multi-agent-orchestration-enterprise-2026.html)
- [The best AI agent frameworks in 2026 (LangChain)](https://www.langchain.com/resources/ai-agent-frameworks)
- [2026 AI Agent Framework Showdown: LangGraph vs CrewAI vs AG2 vs Claude SDK vs Strands vs OpenAI](https://qubittool.com/blog/ai-agent-framework-comparison-2026)
- [LangGraph vs CrewAI vs OpenAI Agents SDK: 2026 Guide](https://www.codebridge.tech/articles/choosing-a-multi-agent-framework-langgraph-crewai-microsoft-agent-framework-or-openai-agents-sdk)
- [AI SDR Dream Teams: Multi-Agent Strategies for 7x ROI (2026)](https://www.landbase.com/blog/the-ai-sdr-dream-team-multi-agent-systems)
- [Agent Orchestration 101: Making Multiple AI Agents Work as One](https://www.lyzr.ai/blog/agent-orchestration/)
- [Temporal for AI Agents: Durable Execution Guide 2026](https://effloow.com/articles/temporal-ai-agents-durable-execution-guide-2026)
- [Durable Agent Execution in Production 2026: Temporal, LangGraph, and Event-Sourced State Management](https://agentmarketcap.ai/blog/2026/04/10/durable-agent-execution-production-temporal-modal-event-sourced)
- [Durable Execution for AI Agent Runtimes: Checkpointing, Replay, and Recovery](https://zylos.ai/research/2026-04-24-durable-execution-agent-runtimes/)
- [Human-in-the-Loop Escalation Design for AI Agents 2026](https://www.digitalapplied.com/blog/human-in-the-loop-escalation-design-ai-agents-2026)
- [Why the best GTM agents keep humans in the loop](https://getsliq.com/blog/human-in-the-loop-gtm-agents)
- [AI Agent Approval Workflows: Human Oversight That Scales](https://waxell.ai/blog/ai-agent-approval-workflows)
- [LLM Model Routing in 2026: Cost-Quality Optimization](https://www.digitalapplied.com/blog/llm-model-routing-2026-cost-quality-optimization-engineering-guide)
- [AI Agent Model Routing and Dynamic Model Selection Strategies](https://zylos.ai/research/2026-03-02-ai-agent-model-routing/)
- [Cluster, Route, Escalate: Cascaded Framework for Cost-Aware LLM Serving](https://arxiv.org/abs/2606.27457)
- [Agentic RAG: The 2026 Enterprise Implementation Guide](https://heeya.fr/en/blog/agentic-rag-implementation-enterprise-2026)
- [Agentic Retrieval-Augmented Generation: A Survey on Agentic RAG](https://arxiv.org/abs/2501.09136)
- [SoK: Agentic Retrieval-Augmented Generation (RAG)](https://arxiv.org/html/2603.07379v1)
- [Agent Memory Architectures: 5 Patterns and Trade-offs](https://atlan.com/know/agent-memory-architectures/)
- [AI Agent Memory Architectures: From Context Windows to Persistent Knowledge](https://zylos.ai/research/2026-04-05-ai-agent-memory-architectures-persistent-knowledge/)
- [Long-Term Memory Architectures for AI Agents (Redis)](https://redis.io/blog/long-term-memory-architectures-ai-agents/)
- [How to Build AI Agent Memory in 2026](https://fountaincity.tech/resources/blog/how-to-build-and-operate-ai-agent-memory-in-2026/)
