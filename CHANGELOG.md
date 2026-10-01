# Changelog

All notable changes to this project are documented in this file.

## [spacely-support-groundwork] — 2026-10-01

### Fixed
- `AIGateway.embed`: `EMBED_MODEL=local/<name>` embeds in-process via fastembed
  again (ADR-006 §B). The branch was dropped in d042a82 and "local/" silently
  routed to an unreachable Ollama entry.

### Added
- Spacely support graph (`core/support/`): `support_router_node` (4 intents),
  `support_answer_node`, `support_clarify_node` over the shared
  `retrieval_node` → `memory_retrieval_node` → `confidence_node`; exposed at
  `POST /support/query` behind `SUPPORT_GRAPH_ENABLED` (default off) and the
  optional `X-Agent-Key`. The sales graph is untouched.
- Groundwork for the Spacely support graph (docs/upgrade-plan-support-graph.md):
  `core/support/persona.py` (support prompts), optional `AGENT_API_KEY`
  (`X-Agent-Key` header) via `api.dependencies.verify_agent_key` — not yet
  wired to any route — `scripts/ingest_spacely_faq.py` (FAQ corpus from
  Spacely's `/api/v1/support/knowledge`) and `scripts/run_spacely_local.sh`
  (separate database).

## [006-telegram-docker]

### Added
- Telegram webhook endpoint at `POST /webhooks/telegram` with async processing path.
- Webhook security guard using `X-Telegram-Bot-Api-Secret-Token` validation.
- Replay/timestamp validation for Telegram updates.
- Postgres persistence for Telegram updates with de-duplication by `update_id`.
- Tool timeout guard wiring for inventory/order execution paths and retry UX support.
- Health endpoints:
  - `GET /health/liveness`
  - `GET /health/readiness` (DB + event loop + pool checks)
- Production-oriented Docker artifacts:
  - Multi-stage `Dockerfile`
  - Compose orchestration for API + Postgres + Phoenix
  - Container health checks and restart policy
- Deployment and Telegram setup documentation:
  - `docs/deployment.md`
  - `docs/telegram-setup.md`
  - README Telegram setup section

### Changed
- Test infrastructure updated for DB override stability and Docker integration mode handling.
- Compatibility defaults added for legacy call sites:
  - `make_initial_state(..., customer_id=None)` now falls back to `session_id`
  - `astream_agent(..., customer_id=None)` supports legacy usage
  - `IntentTracker` methods accept legacy call signatures used by existing tests

### Validation Notes
- Key regression tests passing on modified surfaces:
  - `tests/contract/test_health_endpoints.py`
  - `tests/unit/test_health.py`
  - `tests/contract/test_telegram_webhook_response_time.py`
  - `tests/unit/test_intent_tracker.py`
- Current Docker API image size remains `1.52GB` (SC-006 / T138 still open).
