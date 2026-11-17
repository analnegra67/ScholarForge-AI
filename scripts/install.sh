#!/bin/sh
# ScholarForge installer for Linux and macOS (user profile only).
# Usage: sh scripts/install.sh [--tools] [--claude] [--codex]
set -e
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
    PY="$candidate"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.12+ not found. Install it (python.org, Homebrew or your distribution) and retry." >&2
  exit 1
fi
if ! "$PY" -c 'import venv, ensurepip' >/dev/null 2>&1; then
  echo "Python's venv/ensurepip module is missing (Debian/Ubuntu: sudo apt install python3-venv)." >&2
  exit 1
fi
exec "$PY" "$(dirname "$0")/install.py" "$@"
