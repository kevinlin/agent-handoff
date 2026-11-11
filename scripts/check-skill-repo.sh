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

# Report each problem a Python check wrote to $SCAN_TMP as its own FAIL line. An
# empty file means the check itself did not run, which must still count.
fail_each() {
  local label="$1" problem count=0
  while IFS= read -r problem; do
    [ -n "$problem" ] || continue
    echo "FAIL $label: $problem"
    count=$((count + 1))
    fail=$((fail + 1))
  done <"$SCAN_TMP"
  if [ "$count" -eq 0 ]; then
    echo "FAIL $label: the check did not run"
    fail=$((fail + 1))
  fi
}

check_file "SKILL.md"
check_file "README.md"
check_file "test-prompts.json"
check_file "install.sh"
check_file "LICENSE"
check_file "assets/config-switch-demo.mp4"
check_file "assets/config-switch-demo.gif"
check_file "assets/v3.6.1-conversation-cost-receipt.png"
check_file "examples/showcase-cost-ledger.json"
check_file "assets/receipt-schema.json"
check_file "examples/session-receipt.md"
check_file "references/packet-templates.md"
check_file "references/skill-change-gate.md"
check_file "references/e2e-acceptance.md"
check_file "references/artefact-rows.md"
check_file "assets/verdict-schema.json"
check_file "scripts/make-receipt.py"
check_file "scripts/validate-receipt.py"
check_file "scripts/validate-verdict.py"
check_file "scripts/run-test-prompts.py"
check_file "scripts/delegate-job.sh"
check_file "scripts/handoff-config.py"
check_file "scripts/handoff_runtime.py"
check_file "scripts/migrate-sessions.py"
check_file "scripts/handoff-setup.py"
check_file "scripts/handoff-setup-ui.py"
check_file "scripts/goal-sync.py"
check_file "scripts/render-transcript.py"
check_file "assets/transcript-viewer.html"
check_file "scripts/render-cost-receipt.py"
check_file "assets/cost-receipt.html"
check_file "scripts/handoff-session-ui.py"
check_file "assets/session-view.html"
check_file "assets/setup-ui.html"
check_file "examples/v3.6.1-conversation-cost-receipt.md"
check_file "examples/v3.6.1-conversation-cost-receipt.html"
check_file "references/delegation-flow.md"
check_file "references/setup-wizard.md"
check_file "references/identity-tryout.md"
check_file "references/pr-delivery.md"
check_file "references/goal-file-template.md"
check_file "references/wrap-up-memory.md"
check_file "references/session-evidence.md"
check_file "references/session-receipt.md"
check_file "references/arbitration.md"
check_file "references/backends/codex.md"
check_file "references/backends/claude.md"
check_file "references/backends/copilot.md"
check_file "references/backends/cursor.md"
check_file "references/backends/antigravity.md"
check_file "docs/skill-structure.md"
check_file "docs/skill-structure.svg"
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

# The description is what Claude Code lists for the skill, and the Agent Skills
# limit is 1,024 characters. An over-long one is cut mid-sentence, and the part
# that goes is the end, where the "do not trigger" rule sits. Count the folded
# text, without the key and the indentation.
if python3 - >"$SCAN_TMP" <<'PY'
import re
import sys

LIMIT = 1024

lines = open("SKILL.md", encoding="utf-8").read().split("\n")
if lines[0] != "---" or "---" not in lines[1:]:
    print("SKILL.md has no frontmatter")
    sys.exit(1)
front = lines[1 : lines.index("---", 1)]
start = next((i for i, line in enumerate(front) if line.startswith("description:")), None)
if start is None:
    print("SKILL.md frontmatter has no description")
    sys.exit(1)

head = front[start].split(":", 1)[1].strip()
block = []
for line in front[start + 1 :]:
    if line.strip() and not line[0].isspace():
        break
    block.append(line)
if not re.fullmatch(r"[|>][0-9+-]*", head):
    # Not a block scalar: the text starts on the key's own line.
    block.insert(0, head)

# Fold the lines the way a YAML parser does: one space between lines, one
# newline between paragraphs, no indentation.
paragraphs, current = [], []
for line in block:
    if line.strip():
        current.append(line.strip())
    elif current:
        paragraphs.append(" ".join(current))
        current = []
if current:
    paragraphs.append(" ".join(current))
text = "\n".join(paragraphs)

print(f"{len(text)} of {LIMIT} characters")
sys.exit(0 if len(text) <= LIMIT else 1)
PY
then
  echo "PASS SKILL.md description budget: $(cat "$SCAN_TMP")"
else
  fail_each "SKILL.md description budget"
fi

# SKILL.md is a router: it loads on every trigger, so what it holds is paid for
# on every run. Everything after the frontmatter's closing --- counts.
if python3 - >"$SCAN_TMP" <<'PY'
import sys

LIMIT = 900

lines = open("SKILL.md", encoding="utf-8").read().split("\n")
if lines[0] != "---" or "---" not in lines[1:]:
    print("SKILL.md has no frontmatter")
    sys.exit(1)
words = len("\n".join(lines[lines.index("---", 1) + 1 :]).split())
print(f"{words} of {LIMIT} words")
sys.exit(0 if words <= LIMIT else 1)
PY
then
  echo "PASS SKILL.md router budget: $(cat "$SCAN_TMP")"
else
  fail_each "SKILL.md router budget"
fi

# Every reference is one hop from SKILL.md and listed in the structure doc, and
# every reference SKILL.md names exists. The five backend files are named through
# the pattern references/backends/<backend>.md, so SKILL.md must also list the
# backend names beside it; a backend file outside those five has to be named in
# full. A missing structure doc is reported once, not once per reference.
if python3 - >"$SCAN_TMP" <<'PY'
import re
import sys
from pathlib import Path

BACKENDS = ("codex", "claude", "copilot", "cursor", "antigravity")
PATTERN = "references/backends/<backend>.md"

skill = Path("SKILL.md").read_text(encoding="utf-8")
structure_path = Path("docs/skill-structure.md")
structure = structure_path.read_text(encoding="utf-8") if structure_path.is_file() else None
refs = sorted(path.as_posix() for path in Path("references").rglob("*.md"))
pattern_lines = [line for line in skill.splitlines() if PATTERN in line]
problems = []

if structure is None:
    problems.append("docs/skill-structure.md is missing, so no reference was checked against it")


def backend_of(ref):
    match = re.fullmatch(r"references/backends/([a-z]+)\.md", ref)
    return match.group(1) if match and match.group(1) in BACKENDS else None


for ref in refs:
    backend = backend_of(ref)
    if ref not in skill:
        if backend and pattern_lines:
            if not any(re.search(rf"\b{backend}\b", line) for line in pattern_lines):
                problems.append(f"SKILL.md names {PATTERN} but not '{backend}' beside it")
        else:
            problems.append(f"SKILL.md does not name {ref}")
    if structure is not None and ref not in structure and not (backend and PATTERN in structure):
        problems.append(f"docs/skill-structure.md does not name {ref}")

for named in sorted(set(re.findall(r"references/[A-Za-z0-9_<>./-]*\.md", skill))):
    if "<" in named or ">" in named:
        if named != PATTERN:
            problems.append(f"SKILL.md names {named}, a placeholder other than {PATTERN}")
    elif not Path(named).is_file():
        problems.append(f"SKILL.md names {named}, which does not exist")

if problems:
    print("\n".join(problems))
    sys.exit(1)
print(f"{len(refs)} reference files, each named in SKILL.md and docs/skill-structure.md")
PY
then
  echo "PASS references reachability: $(cat "$SCAN_TMP")"
else
  fail_each "references reachability"
fi

# The skill surface never sends the agent into docs/ or tests/, which are for
# people, and no script points at a design doc. A path is matched where it
# starts, so a URL such as https://cursor.com/docs/... is not a pointer. The
# required-file list in this script is exempt: it is not part of the surface.
if python3 - >"$SCAN_TMP" <<'PY'
import re
import sys
from pathlib import Path

# docs/ or tests/ at the start of a path: not behind a path or URL character
# (cursor.com/docs/x, https://host/docs/x), but behind ./, ../ or $HANDOFF_DIR/.
START = r"(?<![\w.:/@%~$}-])(?:(?:\.{1,2}|\$\{?HANDOFF_DIR\}?\"?)/)*"
TAIL = r"[^\s`'\")\]>,;]*"
SURFACE = re.compile(START + r"(?:docs|tests)/" + TAIL)
SPECS = re.compile(START + r"docs/specs/" + TAIL)

problems = []


def scan(path, pattern, text):
    for number, line in enumerate(text.splitlines(), 1):
        for match in pattern.finditer(line):
            problems.append(f"{path}:{number} points at {match.group(0)}")


surface = [Path("SKILL.md"), *sorted(Path("references").rglob("*.md"))]
for path in surface:
    scan(path, SURFACE, path.read_text(encoding="utf-8"))

scripts = [p for p in sorted(Path("scripts").glob("*")) if p.is_file() and p.name != "check-skill-repo.sh"]
for path in scripts:
    scan(path, SPECS, path.read_text(encoding="utf-8", errors="replace"))

if problems:
    print("\n".join(problems))
    sys.exit(1)
print(f"{len(surface)} skill files point at no docs/ or tests/ path, and {len(scripts)} scripts name nothing under docs/specs/")
PY
then
  echo "PASS no pointers out of the skill surface: $(cat "$SCAN_TMP")"
else
  fail_each "pointers out of the skill surface"
fi

if grep -qF '# Agent Handoff' README.md && grep -qF 'every handoff leaves a receipt' README.md; then
  echo "PASS README identity"
else
  echo "FAIL README must include Agent Handoff identity and slogan"
  fail=$((fail + 1))
fi

if [ -f assets/v3.6.1-conversation-cost-receipt.png ] && \
  grep -qF 'assets/v3.6.1-conversation-cost-receipt.png' README.md; then
  echo "PASS cost receipt showcase image"
else
  echo "FAIL cost receipt showcase image must exist and be linked by README.md"
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

if grep -qF 'Handoff Session Receipt' references/session-receipt.md && \
  grep -qF 'codex_jobs' references/session-receipt.md && \
  grep -qF 'duration: <' references/session-receipt.md && \
  grep -qF 'roles_used' references/session-receipt.md && \
  grep -qF 'Handoff Session Receipt' README.md && \
  grep -qF 'session-receipt-required' test-prompts.json; then
  echo "PASS Handoff Session Receipt contract"
else
  echo "FAIL Handoff Session Receipt contract must be present in references/session-receipt.md, README.md, and test-prompts.json"
  fail=$((fail + 1))
fi

if python3 scripts/validate-receipt.py examples/session-receipt.md >/dev/null; then
  echo "PASS examples/session-receipt.md validates against the v8 schema"
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
if grep -RInE 'git[[:space:]]+reset[[:space:]]+--hard|[Hh]ard [Gg]it [Rr]eset|rm -rf|force push|--force' \
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

echo "SUMMARY fail=$fail warn=$warn"
[ "$fail" -eq 0 ]
