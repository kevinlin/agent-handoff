#!/usr/bin/env bash
# Run the eval suite from a clean environment. skill-up starts nested `claude`
# sessions, which would otherwise inherit this shell's ANTHROPIC_* and
# CLAUDE_CODE_* variables and not authenticate like a fresh terminal.
# BROWSER=true makes webbrowser.open a no-op. SKILL_UP_BIN names the binary.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 - "$@" <<'PY'
import os, sys
sys.path.insert(0, "scripts")
from handoff_runtime import clean_claude_env

env = clean_claude_env(os.environ)
env["BROWSER"] = "true"
binary = env.get("SKILL_UP_BIN", "skill-up")
os.execvpe(binary, [binary, "run", "evals/eval.yaml", *sys.argv[1:]], env)
PY
