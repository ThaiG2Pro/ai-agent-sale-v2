#!/bin/bash
set -e

# Lint helper script for the repo.
# Usage:
#   ./scripts/lint.sh            # default: runs checks (check)
#   ./scripts/lint.sh check      # run ruff check + format --check + uv sync --dry-run
#   ./scripts/lint.sh fix        # run ruff format (auto-fix)
#   ./scripts/lint.sh install-hook  # install this script as .git/hooks/pre-commit

usage() {
	echo "Usage: $0 [check|fix|install-hook]"
	exit 1
}

action="$1"
if [ -z "$action" ]; then
	action=check
fi

# Ensure common user-local/bin and project venv are on PATH so git hooks
# (which run in a minimal non-login shell) can find tools installed by pipx/venv.
export PATH="$HOME/.local/bin:$PATH"
if [ -d ".venv/bin" ]; then
	export PATH="$(pwd)/.venv/bin:$PATH"
fi

# Try to locate `uv`. Add common locations (mise installs) to PATH when found.
if ! command -v uv >/dev/null 2>&1; then
	# Look for mise-installed uv binary under ~/.local/share/mise/installs/uv/*/uv
	mise_uv_glob="$HOME/.local/share/mise/installs/uv"/*/uv
	for candidate in $mise_uv_glob; do
		if [ -x "$candidate" ]; then
			export PATH="$(dirname "$candidate"):$PATH"
			break
		fi
	done
fi

# Final check: ensure `uv` is available, otherwise print guidance and exit.
if ! command -v uv >/dev/null 2>&1; then
	echo "Error: 'uv' command not found in PATH."
	echo "Possible fixes:"
	echo "  - Add the installer bin to PATH (mise):"
	echo "      export PATH=\"$HOME/.local/share/mise/installs/uv/<version>/:\$PATH\""
	echo "  - Add pipx user bin to PATH:"
	echo "      export PATH=\"$HOME/.local/bin:\$PATH\""
	echo "  - Activate your project's virtualenv (if uv is installed there)."
	echo "After fixing PATH, reinstall the pre-commit hook: ./scripts/lint.sh install-hook"
	exit 1
fi

case "$action" in
	check)
		echo "Running ruff check..."
		uv run ruff check .

		echo "Running ruff format check..."
		uv run ruff format --check .

		echo "Environment verification..."
		uv sync --dry-run

		echo "All quality checks passed!"
		;;

	fix)
		echo "Running ruff format (auto-fix)..."
		uv run ruff format .
		echo "Format complete. You may re-run './scripts/lint.sh check' to verify."
		;;

	install-hook)
		if [ ! -d .git/hooks ]; then
			echo "Error: not a git repository or .git/hooks missing"
			exit 2
		fi
		cp "$0" .git/hooks/pre-commit
		chmod +x .git/hooks/pre-commit
		echo "Installed pre-commit hook at .git/hooks/pre-commit"
		;;

	*)
		usage
		;;
esac
