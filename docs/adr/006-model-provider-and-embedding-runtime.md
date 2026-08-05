# ADR 006: Chat Model Provider & Embedding Runtime Choice

**Status**: ACCEPTED
**Date**: 2026-08-05
**Authors**: AI Sales Agent Team
**Supersedes**: refines ADR-001 item 5 ("LiteLLM with local Ollama endpoint")
**Related**: ADR-005 (embedding governance for `semantic_memory`)

---

## Context

ADR-001 chose **LiteLLM → Ollama** as the AI gateway for the Zero-Cost-First /
Offline-First principle. Since then two things changed:

1. **Community consensus shifted**: Ollama is widely criticized as a
   *development convenience*, not a *serving engine* — opaque defaults
   (`num_ctx` truncation, silent q4 quantization), non-standard model
   management, and no continuous batching. The criticism is valid, but it
   applies to *how* Ollama is deployed, not to this codebase's architecture.
2. **The project already drifted off Ollama in practice**: chat runs on Groq
   (`groq/llama-3.3-70b-versatile`), embeddings run **in-process via fastembed
   ONNX** (`local/multilingual-e5-large`). Ollama has been down in dev for days
   with zero breakage.

This ADR records the decision framework so "should we drop Ollama for
llama.cpp/vLLM?" does not get re-litigated per deployment.

### Key architectural fact

Every model call goes through the **LiteLLM gateway** (`services/ai.py`) using
tier aliases (`light` / `chat` / `powerful` / `embed`). Backends are **pure
configuration** (`CHAT_MODEL=...` env). Swapping Ollama → llama.cpp → vLLM →
cloud API is a config change, not a code change. Therefore this is a
**per-deployment decision**, and no migration debt accumulates by deferring it.

---

## Decision

### A. Chat model backend — choose per deployment context

| Deployment context | Backend | Rationale |
|---|---|---|
| **Dev laptop / demo** | Ollama (via LiteLLM `ollama/...`) | One-command model pull; fastest onboarding. Mitigations below are MANDATORY. |
| **Prod self-host, CPU or small GPU, low concurrency (typical SME)** | **`llama.cpp` (`llama-server`)** via LiteLLM `openai/...` pointing at its OpenAI-compatible endpoint | Lightweight, explicit GGUF quant choice, explicit `--ctx-size`, no hidden model rewrites. The "production Ollama" without the magic. |
| **Prod, real GPU, many concurrent users** | **vLLM** (or SGLang) | Continuous batching, PagedAttention — actual throughput engine. Overkill below ~double-digit concurrent sessions. |
| **Prod, no model-ops budget** | Cloud API (Groq / Gemini / OpenAI via LiteLLM) | Current default. Eval Tier-F 12/12 on Groq. Trades Offline-First for zero ops. |

**Ollama mitigations (mandatory wherever Ollama is used):**

- **Set `num_ctx` explicitly** — Ollama's default context (~2k–4k) *silently
  truncates* the prompt. For a RAG prompt (system + chunks + history) this is
  the single worst silent-quality-killer: retrieval succeeds but the model
  never sees the chunks. Set it ≥ the max assembled prompt size via the
  LiteLLM call options or a Modelfile.
- **Pin the exact model tag + quant** (e.g. `qwen3:4b-q8_0`, never bare
  `qwen3:4b`) so `ollama pull` cannot silently change quantization.

**Non-decision**: we do NOT proactively migrate anything today. The LiteLLM
seam means the llama.cpp/vLLM choice is made the day a real self-host
deployment exists, by setting env vars.

### B. Embedding runtime — fastembed in-process, exact-pinned

**Decision: keep fastembed (ONNX, in-process, CPU) as the embedding runtime.**
Do not serve embeddings through Ollama or any HTTP sidecar at current scale.

Rationale — embeddings are *more* sensitive to runtime choice than chat:

- **In-process beats HTTP** at SME scale: no per-batch round-trip, no extra
  failure point, works in CI without a GPU or a service container.
- **Index-compatibility is the real risk, not latency.** Chat model drift
  changes answer *style*; embedding drift makes **every stored vector
  incompatible** — recall degrades silently with no error. Three tables store
  vectors (`text_embeddings`, `semantic_cache`, `semantic_memory`); all three
  filter reads by model name, but a *library-level* change (see below) does
  not change the model name and slips through name filters.
- **Lived incident**: fastembed changed `multilingual-e5-large` pooling
  (CLS → mean) between minor releases — same model name, different embedding
  space. This is why `fastembed` is pinned **exactly** (`==0.8.0`) in
  `pyproject.toml`, not `>=`.

Alternatives considered:

| Option | Verdict |
|---|---|
| sentence-transformers | Rejected: drags in torch (~2 GB) for models fastembed already serves via ONNX. |
| TEI (HF Text Embeddings Inference) / Infinity | Deferred: correct answer *if* ingest volume grows large or multiple apps share one embedding service (GPU, real batching). Re-open then. |
| Ollama embeddings | Rejected: HTTP overhead + tag-mutation risk on top of the library-drift risk; no upside vs in-process ONNX. |

### C. Version-identity contract (what protects the index)

Two independent guards, both required:

1. **DB-level model identity** (guards *config* changes): every vector row
   carries the embed model name (+ dimension), and every read filters on it —
   `text_embeddings.model_name` (+ `model_version = "{model}@{dim}"`),
   `semantic_cache.model_name`, `semantic_memory.embedding_model`
   (+ `flag_stale()` on transition, per ADR-005).
2. **Exact library pin** (guards *runtime* changes the DB cannot see):
   `fastembed==0.8.0`. Bumping this pin is treated as changing the embedding
   model (see runbook), unless the release notes prove the output space is
   bit-identical for the pinned model.

---

## Runbook: changing the embedding model IS a migration event

Changing `EMBED_MODEL`, `EMBED_DIMENSION`, **or the `fastembed` pin** is never
a plain config tweak. Checklist:

1. **Bump identity**: new `EMBED_MODEL` string (for a library bump where the
   model name is unchanged, suffix it — e.g. `local/multilingual-e5-large-fe0.9`)
   so name-filtered reads exclude old vectors immediately.
2. **Dimension check**: pgvector columns are `Vector(EMBED_DIMENSION)`;
   a different dimension requires an Alembic migration, not just env.
3. **Re-embed catalog**: re-run ingest (`text_embeddings`) with the new model.
4. **Semantic memory**: run `flag_stale()` (marks old-model rows STALE), then
   optionally re-embed via the CLI (ADR-005).
5. **Semantic cache**: no action needed — name filter makes old rows dead;
   optionally flush (`scripts/eval_gate.sh --flush-cache` path or TTL expiry).
6. **Re-baseline evals**: Tier-R recall baseline is embedding-dependent —
   re-run `./scripts/eval_gate.sh --tier r --rerun` and commit the new
   baseline; then Tier-F.
7. **CI cache key**: update the fastembed model cache key in
   `.github/workflows/{ci,nightly-eval}.yml` if the model file changes.

---

## Consequences

- ✅ "Ollama in production" criticism is structurally defused: production
  backends are env-selected per deployment; Ollama remains a dev convenience
  with documented mandatory mitigations.
- ✅ Embedding index integrity has two guards (DB identity filter + exact pin);
  the silent-drift failure mode is closed.
- ⚠️ `fastembed` security/feature updates are no longer picked up by
  `uv lock --upgrade` automatically — bumps are deliberate migration events.
- ⚠️ Offline-First is currently softened in practice (chat on Groq). A fully
  offline deployment re-enables it via llama.cpp/Ollama env config — no code
  change needed.
