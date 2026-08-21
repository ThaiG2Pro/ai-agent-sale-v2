# Research: Multi-turn Intent Tracking for Small Models (T03)

Date: 2026-08-11 · Ticket: [T03](../tickets/T03-multiturn-intent-tracking.md) · Decision owner: T08

## Problem restated

`core/agent/nodes/router.py` classifies each turn with **only** `state["user_message"]` in
the classifier prompt — no history, no previous intent — despite the graph already having:

- `messages: Annotated[list, add_messages]` in `AgentState` (full turn history, persisted by
  the Postgres checkpointer per `thread_id = session_id`) — currently unread by the router;
- `intent`, `secondary_intents`, `intent_confidence` channels that could survive across turns
  via the checkpointer (the clarify-loop fields `awaiting_clarification` etc. already exploit
  this by being omitted from `make_initial_state`) — but today `make_initial_state` resets
  them every invoke;
- a keyword fast-path for CANCEL that is the only "cross-turn awareness" today.

So the 180° flip ("đặt cái này đi" → "thôi để xem thêm") fails because turn 2, read alone, is
ambiguous (FOLLOW_UP? SMALLTALK? CANCEL?) and the router can't see that an ORDER_PLACEMENT was
in flight.

## Comparison table

| # | Technique | Extra LLM calls / turn | Extra tokens / turn (approx) | Implementation complexity | Expected robustness on 180° flips | Notes for this repo |
|---|-----------|------------------------|------------------------------|---------------------------|-----------------------------------|---------------------|
| 1 | **History-aware classification** — put last N turns (N=3–5, truncated) + `previous_intent` into the router prompt | **0** (same single call) | +150–500 prompt tokens | **Low** — router already has `state["messages"]`; format last N as a context block | **High**: the classifier sees "order was just proposed", so "thôi để xem thêm" reads as CANCEL/hesitation, not noise. Literature: context-aware multi-turn classification beats per-turn (~+5% even from label simplification alone, arXiv 2411.12307); stateless per-turn is a known failure mode ("LLMs get lost in multi-turn") | Best cost/benefit. Risk on qwen3-4b class: long context dilutes instruction-following — keep N≤3, put the current message LAST, clearly delimited |
| 2 | **Query rewriting / coreference resolution before routing** — rewrite "thôi để xem thêm" → self-contained "tôi không muốn đặt Vivobook nữa, để tôi cân nhắc" | **+1** (sequential; adds latency before router) | +200–400 in, ~30–60 out | **Medium** — new node before router; rewrite must also feed retrieval or the two diverge | **Medium** — great for coreference/ellipsis ("cái này" → product name), helping retrieval AND routing; but small-model CQR failure modes are well documented: over-rewriting clean queries, hallucinating entities not in history, and dropping the negation ("thôi") that carries the flip (CHIQ, InfoCQR) | Doubles router-path calls — hurts on Groq free tier (30 RPM / ~6K TPM / 14.4K req/day org-wide). Only worth it if retrieval on elliptical follow-ups is also a proven problem |
| 3 | **Sticky intent state machine + explicit shift detection** — persist `intent` across turns; switch only on (a) keyword/regex shift signal, or (b) classifier reports new intent with confidence ≥ τ and `intent_shift=true`; else inherit previous intent | **0** (fold `intent_shift: bool` into the existing `IntentClassification` schema) | +50–100 (schema + previous-intent line in prompt) | **Medium** — deterministic transition table (ORDER_PLACEMENT → {CANCEL, FOLLOW_UP, NEGOTIATION} are "expected next"; unexpected jumps need high confidence) + escape hatch | **Highest once tuned**: production bots (Rasa-era dialogue policies, LivePerson routing) treat intent as dialogue *state*, not a per-utterance label — the tracker "admits a change in intention" only on a clear signal, avoiding both flip-flop and missed flips. Risk: sticky-wrong state; needs an escape (2 consecutive disagreements → accept new intent) | Fits the repo: `intent`/`secondary_intents` channels + checkpointer already exist; the CANCEL keyword list is the embryo of the shift-signal table. Transition table is pure Python — no extra calls |
| 4 | **LangGraph-native state/memory facilities** — thread-scoped checkpointer state (already in place), `add_messages` history, optional cross-thread `Store` | **0** | 0 | **Low** (mostly already built) | Enabler, not a technique by itself: the checkpointer gives "previous intent survives the turn" for free; the repo's clarify-loop fields prove the pattern — an in-flight-order flag works identically. Cross-thread `Store`/semantic memory targets cross-session recall, not adjacent-turn flips | The missing piece is not infrastructure — router_node simply never reads what is already persisted, and `make_initial_state` wipes the intent channels each invoke |

Cost baseline: router today = 1 LLM call/turn (economy-chat). Options 1+3+4 combined keep it
at 1 call/turn; option 2 makes it 2. On Groq free tier (30 RPM, 6K TPM, 14.4K req/day
org-wide), each extra call/turn roughly halves conversational throughput — extra *calls* cost
far more than extra *prompt tokens*.

## Failure modes to design against

- **History-aware (1)**: small models over-anchor on history — a genuinely new topic gets
  classified as continuation. Mitigate: cap N=3; instruct "classify the LAST message; history
  is context only"; keep current message visually last.
- **Rewriting (2)**: negation loss ("thôi…" rewritten into an affirmative), entity
  hallucination, latency on every turn even without coreference. If ever adopted, gate the
  rewrite on a cheap heuristic (message < ~8 tokens or contains a pronoun/demonstrative).
- **Sticky state (3)**: wrong intent persists ("sticky-wrong"); flip-flop if τ too low.
  Mitigate: transition table + confidence hysteresis (switch at ≥0.7, keep at <0.5, in-between
  → treat as FOLLOW_UP of previous intent) + hard escape after 2 disagreeing turns.
- **All**: `make_initial_state` overwrites `intent`/`secondary_intents` with defaults every
  invoke — to make intent sticky those keys must be OMITTED from initial state, exactly like
  the clarify fields (see the comment at the bottom of `core/agent/state.py`).

## Recommendation ranking for THIS repo

1. **Do (1) + (3) + (4) together as one change, 0 extra LLM calls/turn.** Router reads the
   last 3 turns from `state["messages"]` + `previous_intent` from checkpointed state; the
   `IntentClassification` schema gains `intent_shift: bool`; a small deterministic transition
   table decides whether to honor the new intent or stick; stop wiping
   `intent`/`secondary_intents` in `make_initial_state`. This directly fixes the ticket
   scenario: history makes turn 2 legible, and ORDER_PLACEMENT→CANCEL is an *expected*
   transition so a modest-confidence signal suffices.
2. **Extend the CANCEL keyword fast-path into a bilingual shift-signal list** (hesitation/
   defer: "thôi", "để xem", "để suy nghĩ", "khoan đã", "chưa vội") that deterministically
   flags a probable flip before the LLM call — zero cost, catches the exact ticket scenario.
3. **Defer (2) query rewriting.** Adopt later only if retrieval quality on elliptical
   follow-ups ("cái này", "cái rẻ hơn") proves to be a separate problem — and then gated by a
   short-message heuristic, not unconditionally, given the Groq free-tier call budget.

## Sources

- Balancing Accuracy and Efficiency in Multi-Turn Intent Classification for LLM-Powered Dialog Systems in Production (arXiv 2411.12307) — https://arxiv.org/abs/2411.12307
- Building Multi-turn Intent Classification with LLM-based pipelines (CustomNLP4U @ ACL 2026) — https://aclanthology.org/2026.customnlp4u-1.8.pdf
- Multi-Intent Recognition in Dialogue Understanding: Smaller Open-Source LLMs (arXiv 2509.10010) — https://arxiv.org/abs/2509.10010
- From Intents to Conversations: Contrastive Learning for Multi-Turn Classification (CIKM 2025) — https://dl.acm.org/doi/10.1145/3746252.3761117
- CHIQ: Contextual History Enhancement for Query Rewriting in Conversational Search (arXiv 2406.05013) — https://arxiv.org/html/2406.05013v1
- InfoCQR: Informative Conversational Query Rewriting — https://github.com/smartyfh/infocqr
- Intent Classification Isn't Enough: Failure Modes in a WhatsApp LLM Pipeline (Towards AI) — https://pub.towardsai.net/intent-classification-isnt-enough-failure-modes-in-a-whatsapp-llm-pipeline-that-had-to-ask-before-2131e1df13ef
- Hybrid Dialogue State Tracking for Persian Chatbots (arXiv 2510.01052) — intent-shift tracker design — https://arxiv.org/pdf/2510.01052
- Rasa: Breaking Free from Intents / multi-turn conversation design — https://rasa.com/blog/breaking-free-from-intents-a-new-dialogue-model · https://rasa.com/blog/multi-turn-conversation
- LivePerson Conversation Builder — routing failure modes incl. mid-flow intent switching — https://developers.liveperson.com/conversation-builder-generative-ai-routing-ai-agents-route-consumers-conversationally.html
- LangGraph Persistence docs (checkpointer, threads, state) — https://docs.langchain.com/oss/python/langgraph/persistence
- Persistent Agent Memory in LangGraph: Cross-Thread State and Memory Stores — https://focused.io/lab/persistent-agent-memory-in-langgraph
- Groq free tier limits 2026 (30 RPM / 6K TPM / 14.4K req/day) — https://tokenmix.ai/blog/groq-free-tier-limits-2026 · https://www.grizzlypeaksoftware.com/articles/p/groq-api-free-tier-limits-in-2026-what-you-actually-get-uwysd6mb
