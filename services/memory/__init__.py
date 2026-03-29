"""Week 5: Async Persistence & Memory Layer

This module provides long-term memory capabilities for the AI Sales Agent:
- Conversation summarization (compress long threads into structured summaries)
- Semantic memory retrieval (vector-based recall of past customer interactions)
- Sales intent tracking (structured extraction and persistence of customer signals)
- Background task orchestration (summarization, embedding, intent extraction)

Key principles (Article XI - Modularity):
- All memory services are stateless (DB-backed, no in-process caching of customer data)
- Strict customer_id isolation prevents cross-customer memory contamination
- Graceful degradation: LLM/DB failures don't block customer response (FR-006)
- Async-first: all I/O is non-blocking (Article IV)

Author: AI Sales Agent System
Date: 2026-03-11
"""
