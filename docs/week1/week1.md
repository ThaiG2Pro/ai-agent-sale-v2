✦ This is the Architectural Trace of the AI Sales Agent foundation as of the end of Week 1 (Infrastructure & Terminal Core).

  ---

# File Map (Artifacts Created)



  | Layer |Files    |Purpose |
  |--------------------|--------|-----|
  | Infrastructure     | docker-compose.yml, .env, secrets/db_password.txt                   | Postgres 17 + pgvector, Arize Phoenix, Environment secrets.            |
  | Core & Config      | core/config.py, core/ai_config.py, core/logging.py                  | Pydantic Settings, LiteLLM Router config, OTLP Observability logic.    |
  | Data Layer         | models/schema.py, services/database.py, migrations/                 | SQLAlchemy models (8 tables), Async connection pool, Alembic history.  |
  | Services           | services/ai.py, services/rag.py, services/semantic_cache.py         | Intelligence Gateway, Ingestion/Search logic, L1/L2 Cache logic.       |
  | API Layer          | api/main.py, api/routes/, api/middleware/                           | FastAPI Entry, Health/Admin routes, Latency/Exception middleware.      |
  | CLI & Tools        | cli/rag_admin.py, scripts/seed_bulk.py, scripts/lint.sh             | Administrative RAG CLI, Data Factory (10k items), Quality Gate.        |
  | Eval & Test        | scripts/tier1_eval.py, tests/eval/, tests/integration/              | Quality Lab (Gold Dataset), Performance benchmarks, Integration tests. |

  ---

# System Connection (Dependency Chains)


  The system is built as a Recursive Dependency Stack:


   1. Environment Chain:
      uv (Runtime) → pyproject.toml (Deps) → core/config.py (Validates .env) → All Components.
   2. Observability Chain (The "Nervous System"):
      core/logging.py (OTLP Gateway) → api/middleware (Traces) + services/ai.py (Callbacks) → Arize Phoenix (UI).
   3. Intelligence Chain:
      services/rag.py (Logic) → services/ai.py (AIGateway) → litellm (Router) → Ollama (Local) / Cloud.
   4. Database Chain:
      api/main.py (Lifespan) → services/database.py (AsyncEngine) → models/schema.py (Models) → PostgreSQL 17.
   5. Interface Chain:
      cli/rag_admin.py → httpx (Client) → api/main.py (Server) → services/rag.py.

  ---

# Core System Components


  A. The OTLP Gateway (Observability)
   * Feature: Protocol-first, vendor-agnostic tracing.
   * Logic: Uses OpenTelemetry SDK to pipe standard logs and spans to local Arize Phoenix or cloud Logfire. It captures every SQL query and AI call automatically.


  B. The Intelligence Gateway (Safe-Switch)
   * Feature: Multi-tier model routing.
   * Logic: Implements an "Economy Tier" (Ollama) by default and a "Premium Tier" (Cloud) fallback. It monitors latency and errors to decide when to switch models.


  C. The High-Performance Storage (RAG Brain)
   * Feature: Multi-layer semantic caching.
   * Logic:
       * L1 (Exact): SHA256 hash lookup (0 token cost).
       * L2 (Semantic): pgvector HNSW similarity search (High speed for 10k+ items).


  D. The Data Factory (Seeding & Eval)
   * Feature: Vietnamese-optimized data generation.
   * Logic: Uses LLMs to generate realistic SME product data and provides a "Gold Dataset" to measure AI quality numerically (Keyword score + Human grade).

  ---

  4. System Status: READY
  The foundation is Stateless, Async-First, and Zero-Cost-Capable.
  Every component is instrumented and every database interaction is non-blocking.


  Trace Status: SUCCESS | Week 1: ARCHIVED | Next: Week 2 Implementation