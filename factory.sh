#!/usr/bin/env bash
# Ole-K2-Free-Video launcher (Linux / macOS). Usage: ./factory.sh [make story.txt | write "idea" | doctor]
cd "$(dirname "$(readlink -f "$0")")"
PY=$(command -v python3 || command -v python)
[ -z "$PY" ] && { echo "Python 3 is not installed (see INSTALL.md)"; exit 1; }
exec "$PY" factory.py "$@"
