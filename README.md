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

