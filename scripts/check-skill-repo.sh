#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"
cd "$ROOT"

SCAN_TMP="$(mktemp)"
trap 'rm -f "$SCAN_TMP"' EXIT

fail=0
warn=0

check_file() {
  local file="$1"
  if [ -f "$file" ]; then
    echo "PASS file $file"
  else
    echo "FAIL missing $file"
    fail=$((fail + 1))
  fi
}

check_dir() {
  local dir="$1"
  if [ -d "$dir" ]; then
    echo "PASS dir  $dir"
  else
    echo "WARN missing dir $dir"
    warn=$((warn + 1))
  fi
}

check_file "SKILL.md"
check_file "README.md"
check_file "test-prompts.json"
check_file "install.sh"
check_file "LICENSE"
check_file "assets/config-switch-demo.mp4"
check_file "assets/config-switch-demo.gif"
check_file "assets/v2.0.1-conversation-cost-receipt.png"
check_file "examples/showcase-cost-ledger.json"
check_file "docs/showcase-cost-model.md"
check_file "docs/receipt-schema.json"
check_file "examples/session-receipt.md"
check_file "examples/v2.0.0-conversation-cost-receipt.md"
check_file "examples/v2.0.1-conversation-cost-receipt.md"
check_file "references/handoff-template.md"
check_file "references/darwin-ratchet.md"
check_file "references/e2e-gauntlet.md"
check_file "docs/verdict-schema.json"
check_file "scripts/make-receipt.py"
check_file "scripts/validate-receipt.py"
check_file "scripts/validate-verdict.py"
check_file "scripts/run-test-prompts.py"
check_file "scripts/delegate-codex.sh"
check_file "scripts/handoff-config.py"
check_file "scripts/handoff_runtime.py"
check_file "scripts/handoff-setup.py"
check_file "scripts/handoff-setup-ui.py"
check_file "scripts/goal-sync.py"
check_file "scripts/english-only-scan.py"
check_file "references/claude-driven.md"
check_file "references/setup.md"
check_file "references/tryout.md"
check_file "references/goal-to-pr.md"
check_file "references/goal-template.md"
check_file "references/fable5-principles.md"
check_file "references/memory-protocol.md"
check_dir "references"
check_dir "examples"
check_dir "scripts"

if python3 scripts/run-test-prompts.py >/dev/null; then
  echo "PASS test-prompts static regression checks"
else
  echo "FAIL scripts/run-test-prompts.py static checks"
  fail=$((fail + 1))
fi

if command -v jq >/dev/null 2>&1; then
  jq -e 'type == "array" and length >= 4 and all(.[]; has("id") and has("prompt") and has("expected_behavior") and has("must_not"))' test-prompts.json >/dev/null
  echo "PASS test-prompts.json schema"
  if jq -e '[.[] | (.expected_behavior[]?, .prompt)] | map(select(test("git reset --hard|rm -rf|force push|--force"))) | length == 0' test-prompts.json >/dev/null; then
    echo "PASS test-prompts risky text confined to must_not"
  else
    echo "FAIL test-prompts.json has risky command text outside must_not"
    fail=$((fail + 1))
  fi
else
  if python3 - <<'PY'
import json
import sys

with open("test-prompts.json", encoding="utf-8") as handle:
    data = json.load(handle)

required = {"id", "prompt", "expected_behavior", "must_not"}
ok = (
    isinstance(data, list)
    and len(data) >= 4
    and all(isinstance(entry, dict) and required <= set(entry) for entry in data)
)
sys.exit(0 if ok else 1)
PY
  then
    echo "PASS test-prompts.json schema (python3 fallback)"
  else
    echo "FAIL test-prompts.json schema (python3 fallback)"
    fail=$((fail + 1))
  fi
fi

if grep -q '^name: agent-handoff$' SKILL.md; then
  echo "PASS SKILL.md name"
else
  echo "FAIL SKILL.md frontmatter name must be agent-handoff"
  fail=$((fail + 1))
fi

if grep -qE '^version: [0-9]+\.[0-9]+\.[0-9]+$' SKILL.md; then
  echo "PASS SKILL.md version"
else
  echo "FAIL SKILL.md frontmatter must declare a semantic version"
  fail=$((fail + 1))
fi

if grep -qF '"agent handoff"' SKILL.md; then
  echo "PASS SKILL.md bare trigger"
else
  echo "FAIL SKILL.md description must include \"agent handoff\" as a trigger"
  fail=$((fail + 1))
fi

if grep -qF '# Agent Handoff' README.md && grep -qF 'every handoff leaves a receipt' README.md; then
  echo "PASS README identity"
else
  echo "FAIL README must include Agent Handoff identity and slogan"
  fail=$((fail + 1))
fi

if grep -qF 'docs/showcase-cost-model.md' README.md; then
  echo "PASS docs entrypoints"
else
  echo "FAIL README must link the cost model doc"
  fail=$((fail + 1))
fi

if [ -f assets/v2.0.1-conversation-cost-receipt.png ] && \
  grep -qF 'assets/v2.0.1-conversation-cost-receipt.png' README.md; then
  echo "PASS v2.0.1 conversation cost receipt image"
else
  echo "FAIL v2.0.1 conversation cost receipt image must exist and be linked by README.md"
  fail=$((fail + 1))
fi

if [ -f assets/config-switch-demo.mp4 ] && \
  [ -f assets/config-switch-demo.gif ] && \
  grep -qF 'assets/config-switch-demo.mp4' README.md && \
  grep -qF 'assets/config-switch-demo.gif' README.md; then
  echo "PASS configuration demo video and README preview"
else
  echo "FAIL configuration demo video/preview must exist and be linked by README.md"
  fail=$((fail + 1))
fi

if [ -s examples/showcase-cost-ledger.json ] && \
  grep -qF 'examples/showcase-cost-ledger.json' README.md; then
  echo "PASS showcase cost ledger"
else
  echo "FAIL showcase cost ledger must exist and be linked from README.md"
  fail=$((fail + 1))
fi

if grep -qF 'Handoff Session Receipt' SKILL.md && \
  grep -qF 'codex_jobs' SKILL.md && \
  grep -qF 'duration: <' SKILL.md && \
  grep -qF 'roles_used' SKILL.md && \
  grep -qF 'Handoff Session Receipt' README.md && \
  grep -qF 'session-receipt-required' test-prompts.json; then
  echo "PASS Handoff Session Receipt contract"
else
  echo "FAIL Handoff Session Receipt contract must be present in SKILL.md, README.md, and test-prompts.json"
  fail=$((fail + 1))
fi

if python3 scripts/validate-receipt.py examples/session-receipt.md >/dev/null; then
  echo "PASS examples/session-receipt.md validates against the v5 schema"
else
  echo "FAIL examples/session-receipt.md must be a valid receipt"
  fail=$((fail + 1))
fi

if find . -path './.git' -prune -o -type f \( -name '.env' -o -name '.env.*' \) -print | grep -q .; then
  echo "FAIL .env-like files are tracked or present in the package tree"
  fail=$((fail + 1))
elif grep -RInE 'gho_[A-Za-z0-9_]+|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|BEGIN (RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}' \
  --exclude-dir='.git' \
  --exclude='check-skill-repo.sh' \
  . >"$SCAN_TMP"; then
  echo "FAIL possible secret-like text:"
  cat "$SCAN_TMP"
  fail=$((fail + 1))
else
  echo "PASS secret scan"
fi

# High-risk command text is allowed only in prohibition context (safety
# boundaries that forbid the command) or on lines annotated with risk-ok.
# test-prompts.json is checked structurally above: risky text must stay
# inside must_not arrays.
if grep -RInE 'git reset --hard|rm -rf|force push|--force' \
  --exclude-dir='.git' \
  --exclude='check-skill-repo.sh' \
  --exclude='test-prompts.json' \
  . \
  | grep -vE 'risk-ok|[Dd]o not|not \`' \
  >"$SCAN_TMP"; then
  echo "WARN high-risk command text found:"
  cat "$SCAN_TMP"
  warn=$((warn + 1))
else
  echo "PASS high-risk command scan"
fi

# This repo is English-only. CJK in a tracked file is a regression.
# python3 rather than grep -P: BSD/macOS grep has no -P. The regex below is
# written as ASCII escapes so this file stays clean under its own scan.
if python3 scripts/english-only-scan.py >"$SCAN_TMP"; then
  echo "PASS English-only scan"
else
  echo "FAIL non-English (CJK) text found:"
  cat "$SCAN_TMP"
  fail=$((fail + 1))
fi

echo "SUMMARY fail=$fail warn=$warn"
[ "$fail" -eq 0 ]
