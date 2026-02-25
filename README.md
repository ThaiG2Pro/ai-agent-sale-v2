# AI Sales Agent (SME-Ready)

This is a high-performance, asynchronous AI Sales Agent designed for SMEs, following a "Zero-Cost-First" and "Offline-First" philosophy.

## Hardware Requirements

To run the system locally with AI capabilities (via Ollama), the following minimum hardware is required:

- **CPU**: ≥ 4 cores
- **RAM**: ≥ 8 GB
- **VRAM (GPU)**: ≥ 4 GB (Recommended for smooth local model performance)

## Development Setup

1. **Install uv**: `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. **Setup environment**: `uv sync`
3. **Start Infrastructure**: `docker-compose up -d`
4. **Local AI**: Ensure [Ollama](https://ollama.com/) is installed and running.

## Linting

- Run project lint checks and environment verification:

```bash
./scripts/lint.sh check
```

- Auto-format the code with `ruff`:

```bash
./scripts/lint.sh fix
```

- Install as a local Git pre-commit hook (copies the script to `.git/hooks/pre-commit`):

```bash
./scripts/lint.sh install-hook
```

Notes:
- These commands use `uv` (the project's task runner). Ensure you have the environment set up and `uv` installed.
- The `check` action runs `ruff check`, `ruff format --check` and `uv sync --dry-run` to validate environment rules for `uv sync`.
