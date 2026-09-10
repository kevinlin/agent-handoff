#!/usr/bin/env bash
set -euo pipefail

# delegate-codex.sh — Claude-driven Handoff delegation primitive.
#
# The name is historical: this is the delegation primitive for ALL backends.
# It wraps `codex exec --json`, `claude --print --output-format stream-json`,
# and `copilot -p --output-format json`
# as background jobs with durable state under <repo>/.handoff/jobs/<jobId>/ so
# a Claude Code session (or a /loop tick) can submit work to a worker CLI, poll
# it, collect the result, and send follow-up fix rounds against the same worker
# session. The identity's configured backend decides which CLI runs the job.
#
# Job directory layout:
#   prompt.md    the exact prompt sent to the worker CLI
#   run.sh       the command actually executed (audit trail)
#   log.jsonl    the worker's JSON event stream
#   stderr.log   worker stderr (tokens, warnings, auth errors)
#   pid          background worker pid
#   exit_code    written when the worker finishes
#   session_id   worker thread/session id, extracted from log.jsonl — or, on
#                copilot, assigned at submit and written before launch
#   meta         label, backend, effort, mode, permission mode, parent job,
#                timestamps; permission_posture is requested, permission_mode is
#                the effective backend mode, permission_posture_source records origin
#   usage.json   copilot only: final usage counters (--usage-output-file)
#   copilot-logs copilot only: the CLI's own --log-dir output

usage() {
  cat <<'USAGE'
delegate-codex.sh — background delegation jobs for the Handoff flow
(the name is historical; it drives the codex, claude, and copilot backends)

Usage:
  delegate-codex.sh submit --repo <path> --prompt-file <file>
                    [--label <name>] [--effort <level>] [--model <model>]
                    [--backend codex|claude|copilot]
                    [--role deep_reasoner|fast_worker|arbiter|e2e_specifier|e2e_verifier]
                    [--worktree <branch>] [--base <commit-ish>]
                    [--read-only] [--dry-run]
  delegate-codex.sh status  <jobId> --repo <path> [--wait] [--timeout <seconds>]
  delegate-codex.sh result  <jobId> --repo <path> [--json]
  delegate-codex.sh resume  <jobId> --repo <path> --prompt-file <file> [--read-only]
  delegate-codex.sh cancel  <jobId> --repo <path>
  delegate-codex.sh cleanup <jobId> --repo <path>
  delegate-codex.sh list    --repo <path>

Defaults: --backend codex, --effort high (Handoff default for delegated
work), permission_mode=default: codex inherits the user's config, claude uses
dontAsk with Read Glob Grep Edit Write Bash, copilot keeps --allow-all-tools. Use --read-only for review/adversarial
jobs that must not touch the repo; it maps to `-s read-only` on codex,
`--permission-mode plan` on claude, and `--mode plan` on copilot.

On copilot, `--mode plan` and `--allow-all-tools` are never generated together.
Passing both was probed: plan mode still won on disk, but the worker emitted no
denial events, exited 0, and reported writes that had not happened. A read-only
copilot job therefore drops `--allow-all-tools` entirely.

A copilot job never carries `--share`, `--share-gist`,
`--worktree`, or `--enable-memory`, and always carries
`--no-remote --no-remote-export`: session export to GitHub web and mobile is on
by default, and a delegated job's prompt and repository contents are not
exportable material.

--role resolves backend, model, effort, and permission_mode from Handoff config, and the
identity's backend decides which CLI executes the job — always. --backend is
for role-less ad-hoc jobs only: passing one that contradicts a named role is
refused rather than silently overriding the config.

resume refuses a round past the review cap in Handoff config: [review]
spec_max_rounds for a chain whose first job is labelled spec-review,
implementation_max_rounds for any other. A round is a review pass; the first
job is round 1. Escalate to the arbiter instead.

Efforts are per CLI, never one shared enum: codex takes
minimal|low|medium|high|xhigh|max|ultra, claude takes low|medium|high|xhigh|max,
copilot takes none|minimal|low|medium|high|xhigh|max. Copilot's enum is the
CLI's superset — which efforts a given copilot model accepts is decided per
model by the API, and a rejected pair surfaces as Copilot's own error rather
than being silently downgraded.

`--model auto` is refused on a copilot job. An identity is a deliberate
backend + model + effort choice; `auto` hands the model choice back to the
vendor per request, so the job's record would name what Copilot picked rather
than what the repo configured.

A copilot job's session id is assigned at submit rather than extracted at the
end: Copilot emits `sessionId` only in its terminal event, so a job that dies
mid-run would have no id to resume. `meta` records
`session_id_source=assigned`. An assigned id is a claim on a session, not proof
one exists — a job that failed before Copilot created a session cannot be
resumed, and `copilot --resume` says so on stderr.

--worktree runs the job in a dedicated Git worktree under
<repo>/.handoff/worktrees/<jobId>, cut from --base (default HEAD) resolved to
an immutable commit SHA. The worker commits on that branch; merging, pushing,
and removal stay with the driver. `cleanup <jobId>` removes the worktree and
refuses one holding uncommitted changes. --repo always names the main repo,
never a worktree.

Worker binary: set HANDOFF_CODEX_BIN, HANDOFF_CLAUDE_BIN, or
HANDOFF_COPILOT_BIN to an executable path or command name to override
discovery. On macOS the ChatGPT/Codex app-bundled CLI is preferred when present
so app-only models use a compatible client; otherwise PATH is used.

`copilot` is also the binary name of AWS Copilot CLI, an unrelated ECS
deployment tool, so every way of resolving a copilot binary applies the same
`--version` identity check. PATH discovery walks every match and takes the first
reporting `GitHub Copilot CLI`; HANDOFF_COPILOT_BIN is checked too, since a
stale variable is the likeliest way to hold the wrong path; and a fix round
re-checks the parent job's recorded binary before reusing it. Each fails closed
naming what it found instead.

permission_mode=allow-all selects the provider's native unrestricted mode:
claude bypassPermissions, codex --dangerously-bypass-approvals-and-sandbox,
copilot --allow-all-tools --allow-all-paths --allow-all-urls.
HANDOFF_CLAUDE_PERMISSION_MODE overrides claude's configured posture:
acceptEdits|auto|bypassPermissions|manual|dontAsk|plan. It is validated before
--read-only wins. Resume inherits posture and read_only; missing posture means
default. meta and --dry-run show permission_posture, permission_posture_source
(config:<layer>|explicit|env|read-only|parent|default), and effective permission_mode.

A background job never prompts: unapproved actions deny. Claude default changes
permission rules only, with no OS-level sandbox. Codex default inherits its
config without a sandbox flag. Copilot default retains path and URL checks.
permission_denied is advisory, not enforced; Codex denials are not counted.

Exit codes: status prints RUNNING/DONE/FAILED/CANCELLED; `status --wait`
returns non-zero on timeout or failure so callers can branch on it.
USAGE
}

JOBS_SUBDIR=".handoff/jobs"
PERMISSION_MODE=""
PERMISSION_POSTURE="default"
PERMISSION_POSTURE_SOURCE="default"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() {
  echo "ERROR: $*" >&2
  exit 1
}

require_repo() {
  [ -n "${REPO:-}" ] || die "--repo is required"
  [ -d "$REPO" ] || die "repo not found: $REPO"
  REPO="$(cd "$REPO" && pwd)"
}

job_dir() {
  echo "$REPO/$JOBS_SUBDIR/$1"
}

require_job() {
  JOB="$(job_dir "$1")"
  [ -d "$JOB" ] || die "job not found: $1 (looked in $REPO/$JOBS_SUBDIR)"
}

now_utc() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

is_git_repo() {
  # `codex exec` refuses to run outside a trusted (git) directory unless
  # --skip-git-repo-check is passed. Detect via rev-parse rather than a
  # bare `-d "$1/.git"` check so worktrees/submodules (where .git is a
  # file, not a directory) are still recognized as git repos.
  git -C "$1" rev-parse --git-dir >/dev/null 2>&1
}

resolve_worker_bin() {
  # Finds the CLI for one backend. The CODEX_* variable names are kept so the
  # meta keys and resume's parent-binary reuse stay unchanged across backends.
  local backend="$1" env_var configured candidate=""
  case "$backend" in
    claude) env_var="HANDOFF_CLAUDE_BIN" ;;
    copilot) env_var="HANDOFF_COPILOT_BIN" ;;
    *) env_var="HANDOFF_CODEX_BIN" ;;
  esac
  configured="${!env_var:-}"

  CODEX_BIN_SOURCE="path"
  if [ -n "$configured" ]; then
    CODEX_BIN_SOURCE="env"
    if [[ "$configured" == */* ]]; then
      candidate="$configured"
    else
      candidate="$(command -v "$configured" 2>/dev/null || true)"
    fi
    [ -n "$candidate" ] && [ -x "$candidate" ] || die "$env_var is not executable: $configured"
    # The override runs through the same identity check as PATH discovery. It
    # is the likeliest way to hold a stale or mistyped path, and an unchecked
    # one launches AWS Copilot CLI with GitHub Copilot flags: the job dies with
    # a confusing error and a near-empty log instead of failing legibly here.
    # Only copilot needs this — codex and claude have no name collision.
    if [ "$backend" = "copilot" ] && ! is_github_copilot_bin "$candidate"; then
      die "$env_var does not point at GitHub Copilot CLI: $candidate reports '$("$candidate" --version 2>/dev/null | head -1 || true)' (AWS Copilot CLI shares the binary name); point $env_var at the GitHub Copilot CLI, or unset it to discover one on PATH"
    fi
  elif [ "$backend" = "codex" ] && [ "$(uname -s)" = "Darwin" ]; then
    # App-bundle probe is codex-only: app-only models need a compatible client.
    for candidate in "/Applications/ChatGPT.app/Contents/Resources/codex" "/Applications/Codex.app/Contents/Resources/codex"; do
      if [ -x "$candidate" ]; then
        CODEX_BIN_SOURCE="app"
        break
      fi
      candidate=""
    done
  fi

  if [ -z "$candidate" ] && [ "$backend" = "copilot" ]; then
    candidate="$(discover_copilot_bin)" || die "$candidate"
    CODEX_BIN_SOURCE="path"
  fi

  if [ -z "$candidate" ]; then
    candidate="$(command -v "$backend" 2>/dev/null || true)"
    CODEX_BIN_SOURCE="path"
  fi
  [ -n "$candidate" ] && [ -x "$candidate" ] || die "$backend CLI not found; install it or set $env_var"

  CODEX_BIN="$candidate"
  CODEX_VERSION="$("$CODEX_BIN" --version 2>/dev/null | head -1 || true)"
  CODEX_VERSION="${CODEX_VERSION:-unknown}"
}

is_github_copilot_bin() {
  # `copilot` is also the binary name of AWS Copilot CLI, an unrelated ECS
  # deployment tool, and PATH order decides which one wins. The two are
  # distinguishable only by --version output: "GitHub Copilot CLI 1.0.83."
  # against "copilot version: v1.34.1". Every place that resolves a copilot
  # binary — submit, resume, and anything reusing a recorded path — goes
  # through this one check, so they cannot disagree about which install is the
  # real one.
  case "$("$1" --version 2>/dev/null | head -1 || true)" in
    *"GitHub Copilot CLI"*) return 0 ;;
    *) return 1 ;;
  esac
}

discover_copilot_bin() {
  # Prints the first PATH match that identifies as GitHub Copilot CLI. On
  # failure prints an actionable message and returns 1, so the caller can die
  # with it.
  local match first="" first_version=""
  while IFS= read -r match; do
    [ -n "$match" ] && [ -x "$match" ] || continue
    if is_github_copilot_bin "$match"; then
      printf '%s\n' "$match"
      return 0
    fi
    if [ -z "$first" ]; then
      first="$match"
      first_version="$("$match" --version 2>/dev/null | head -1 || true)"
    fi
  done < <(type -a -p copilot 2>/dev/null || true)
  if [ -n "$first" ]; then
    printf '%s\n' "the 'copilot' on PATH is not GitHub Copilot CLI: $first reports '${first_version:-no --version output}' (AWS Copilot CLI shares the binary name); set HANDOFF_COPILOT_BIN to the GitHub Copilot CLI path"
  else
    printf '%s\n' "copilot CLI not found; install GitHub Copilot CLI or set HANDOFF_COPILOT_BIN"
  fi
  return 1
}

validate_effort() {
  # Efforts are per CLI, never one shared enum.
  case "$1" in
    claude) case "$2" in low|medium|high|xhigh|max) ;; *) die "invalid --effort for claude: $2" ;; esac ;;
    # Copilot's enum is the CLI's superset; which values a given model accepts
    # is decided per model by the API, and that rejection surfaces as Copilot's
    # own error rather than being silently downgraded here.
    copilot) case "$2" in none|minimal|low|medium|high|xhigh|max) ;; *) die "invalid --effort for copilot: $2" ;; esac ;;
    *) case "$2" in minimal|low|medium|high|xhigh|max|ultra) ;; *) die "invalid --effort: $2" ;; esac ;;
  esac
}

resolve_claude_permission_mode() {
  local read_only="$1" posture="$2"
  PERMISSION_MODE="dontAsk"
  [ "$posture" = "allow-all" ] && PERMISSION_MODE="bypassPermissions"
  # Validate the legacy override before read-only wins, including on resume.
  if [ -n "${HANDOFF_CLAUDE_PERMISSION_MODE:-}" ]; then
    PERMISSION_MODE="$HANDOFF_CLAUDE_PERMISSION_MODE"
    PERMISSION_POSTURE_SOURCE="env"
  fi
  case "$PERMISSION_MODE" in
    acceptEdits|auto|bypassPermissions|manual|dontAsk|plan) ;;
    *) die "invalid HANDOFF_CLAUDE_PERMISSION_MODE: $PERMISSION_MODE (accepts acceptEdits|auto|bypassPermissions|manual|dontAsk|plan)" ;;
  esac
  [ "$read_only" = "true" ] && PERMISSION_MODE="plan"
  return 0
}

resolve_copilot_permission_mode() {
  PERMISSION_MODE="allow-all-tools"
  [ "$2" = "allow-all" ] && PERMISSION_MODE="allow-all"
  [ "$1" = "true" ] && PERMISSION_MODE="plan"
  return 0
}

resolve_codex_permission_mode() {
  PERMISSION_MODE=""
  [ "$2" = "allow-all" ] && PERMISSION_MODE="allow-all"
  [ "$1" = "true" ] && PERMISSION_MODE="read-only"
  return 0
}

resolve_permission_mode() {
  case "$PERMISSION_POSTURE" in
    default|allow-all) ;;
    *) die "invalid permission_posture: $PERMISSION_POSTURE" ;;
  esac
  "resolve_${BACKEND}_permission_mode" "$READ_ONLY" "$PERMISSION_POSTURE"
  [ "$READ_ONLY" = "true" ] && PERMISSION_POSTURE_SOURCE="read-only"
  return 0
}

warn_permission_bypass() {
  case "$BACKEND:$PERMISSION_MODE" in
    claude:bypassPermissions|copilot:allow-all-tools|copilot:allow-all|codex:allow-all) ;;
    *) return 0 ;;
  esac
  echo "WARN $1 is a $BACKEND worker running with permission checks bypassed." >&2
  echo "     A background job has no approval surface; verify its work on disk." >&2
  if [ "$BACKEND" = "claude" ] && [ "${HANDOFF_CLAUDE_PERMISSION_MODE:-}" = "bypassPermissions" ]; then
    echo "     Set HANDOFF_CLAUDE_PERMISSION_MODE=dontAsk or unset the override." >&2
  elif [ -n "${PARENT_ID:-}" ]; then
    echo "     Resume with --read-only, or submit a new job with permission_mode=default." >&2
  elif [ -n "${ROLE:-}" ]; then
    echo "     Set identity $ROLE permission_mode=default; use --read-only to prohibit writes." >&2
  else
    echo "     Submit with --read-only for a job that must not touch the repo." >&2
  fi
}

make_job_id() {
  local label="$1"
  printf 'job-%s-%s-%s\n' "$(date +%Y-%m-%dT%H-%M-%S)" "$$" "${label:-task}"
}

kill_tree() {
  local pid="$1"
  local child
  local children
  if command -v pgrep >/dev/null 2>&1; then
    children="$(pgrep -P "$pid" 2>/dev/null || true)"
  else
    children="$(ps -o pid= -P "$pid" 2>/dev/null || true)"
  fi
  for child in $children; do
    kill_tree "$child"
  done
  kill "$pid" 2>/dev/null || true
}

meta_value() {
  # Prints the value of one `key=value` line from a job's meta file.
  sed -n "s/^$1=//p" "${2:-$JOB}/meta"
}

job_state() {
  # Prints RUNNING | DONE | FAILED | CANCELLED for $JOB.
  if [ -f "$JOB/cancelled" ]; then
    echo "CANCELLED"
  elif [ -f "$JOB/exit_code" ]; then
    if [ "$(cat "$JOB/exit_code")" = "0" ]; then echo "DONE"; else echo "FAILED"; fi
  elif [ -f "$JOB/pid" ] && kill -0 "$(cat "$JOB/pid")" 2>/dev/null; then
    echo "RUNNING"
  else
    # Worker died without writing exit_code (crash, reboot).
    echo "FAILED"
  fi
}

extract_session_id() {
  # Best-effort session/thread id from the JSONL stream; caches into session_id.
  if [ -s "$JOB/session_id" ]; then
    cat "$JOB/session_id"
    return 0
  fi
  python3 - "$JOB/log.jsonl" <<'PY' | tee "$JOB/session_id"
import json, sys
sid = ""
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            # sessionId is Copilot's spelling, and it arrives only on the
            # terminal event. A Handoff-submitted copilot job has the cache
            # populated at submit already; this covers one submitted by hand.
            for key in ("thread_id", "session_id", "sessionId"):
                found = event.get(key) or (event.get("thread") or {}).get("id")
                if found:
                    sid = found
            if sid:
                break
except FileNotFoundError:
    pass
print(sid)
PY
}

cmd_submit() {
  local PROMPT_FILE="" LABEL="task" EFFORT="high" MODEL="" ROLE="" READ_ONLY="false" DRY_RUN="false"
  local WORKTREE_BRANCH="" WORKTREE_BASE="" BASE_COMMIT=""
  local EFFORT_EXPLICIT="false" MODEL_EXPLICIT="false" BACKEND_EXPLICIT="false"
  local EFFORT_SOURCE="default" MODEL_SOURCE="default" BACKEND_SOURCE="default"
  BACKEND="codex"
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --repo) REPO="${2:-}"; shift 2 ;;
      --prompt-file) PROMPT_FILE="${2:-}"; shift 2 ;;
      --label) LABEL="${2:-}"; shift 2 ;;
      --effort) EFFORT="${2:-}"; EFFORT_EXPLICIT="true"; shift 2 ;;
      --model) MODEL="${2:-}"; MODEL_EXPLICIT="true"; shift 2 ;;
      --role) ROLE="${2:-}"; shift 2 ;;
      --backend) BACKEND="${2:-}"; BACKEND_EXPLICIT="true"; shift 2 ;;
      --worktree) WORKTREE_BRANCH="${2:-}"; shift 2 ;;
      --base) WORKTREE_BASE="${2:-}"; shift 2 ;;
      --read-only) READ_ONLY="true"; shift ;;
      --dry-run) DRY_RUN="true"; shift ;;
      *) die "unknown submit argument: $1" ;;
    esac
  done
  require_repo
  [ -n "$PROMPT_FILE" ] && [ -f "$PROMPT_FILE" ] || die "--prompt-file is required and must exist"
  case "$ROLE" in ""|deep_reasoner|fast_worker|arbiter|e2e_specifier|e2e_verifier) ;; *) die "invalid --role: $ROLE" ;; esac
  case "$BACKEND" in codex|claude|copilot) ;; *) die "invalid --backend: $BACKEND" ;; esac

  if [ -n "$WORKTREE_BRANCH" ]; then
    is_git_repo "$REPO" || die "--worktree requires --repo to be a git repository"
    # Pin to an immutable SHA: a branch name can advance between cutting the
    # worktree and reading the verdict, and then the verdict names a commit
    # nobody tested.
    BASE_COMMIT="$(git -C "$REPO" rev-parse --verify "${WORKTREE_BASE:-HEAD}^{commit}" 2>/dev/null)" \
      || die "--base is not a valid commit: ${WORKTREE_BASE:-HEAD}"
  fi

  if [ -n "$ROLE" ]; then
    local CONFIG_JSON CONFIG_SOURCE ROLE_BACKEND ROLE_MODEL ROLE_EFFORT
    if ! CONFIG_JSON="$(python3 "$SCRIPT_DIR/handoff-config.py" --repo "$REPO" resolve)"; then
      die "failed to resolve Handoff identity config; run 'python3 scripts/handoff-config.py init' and then 'set --role $ROLE --backend <codex|claude> --model <model> --effort <effort>'"
    fi
    CONFIG_SOURCE="$(printf '%s' "$CONFIG_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("source", ""))')" || die "invalid JSON from handoff-config.py resolve"
    ROLE_BACKEND="$(printf '%s' "$CONFIG_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("hosts", {}).get("claude_code", {}).get("identities", {}).get(sys.argv[1], {}).get("backend", ""))' "$ROLE")" || die "invalid JSON from handoff-config.py resolve"
    ROLE_MODEL="$(printf '%s' "$CONFIG_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("hosts", {}).get("claude_code", {}).get("identities", {}).get(sys.argv[1], {}).get("model", ""))' "$ROLE")" || die "invalid JSON from handoff-config.py resolve"
    ROLE_EFFORT="$(printf '%s' "$CONFIG_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("hosts", {}).get("claude_code", {}).get("identities", {}).get(sys.argv[1], {}).get("effort", ""))' "$ROLE")" || die "invalid JSON from handoff-config.py resolve"
    if [ -z "$ROLE_BACKEND" ] || [ -z "$ROLE_MODEL" ] || [ -z "$ROLE_EFFORT" ]; then
      die "Handoff identity '$ROLE' is missing backend, model, or effort; run 'python3 scripts/handoff-config.py init' and then 'set --role $ROLE --backend <codex|claude> --model <model> --effort <effort>'"
    fi
    # The configured backend decides which CLI runs the job — always. An
    # explicit --backend that contradicts the config is refused rather than
    # silently moving the work onto another vendor and meter.
    if [ "$BACKEND_EXPLICIT" = "true" ] && [ "$BACKEND" != "$ROLE_BACKEND" ]; then
      die "--backend $BACKEND contradicts identity $ROLE, configured as backend=$ROLE_BACKEND; change the config with 'handoff-config.py set --role $ROLE --backend $BACKEND' instead of overriding it per job"
    fi
    BACKEND="$ROLE_BACKEND"
    BACKEND_SOURCE="config:$CONFIG_SOURCE"
    PERMISSION_POSTURE="$(printf '%s' "$CONFIG_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin)["hosts"]["claude_code"]["identities"][sys.argv[1]]["permission_mode"])' "$ROLE")"
    PERMISSION_POSTURE_SOURCE="config:$CONFIG_SOURCE"
    if [ "$MODEL_EXPLICIT" = "false" ]; then
      MODEL="$ROLE_MODEL"
      MODEL_SOURCE="config:$CONFIG_SOURCE"
    fi
    if [ "$EFFORT_EXPLICIT" = "false" ]; then
      EFFORT="$ROLE_EFFORT"
      EFFORT_SOURCE="config:$CONFIG_SOURCE"
    fi
  fi
  [ "$MODEL_EXPLICIT" = "false" ] || MODEL_SOURCE="explicit"
  [ "$EFFORT_EXPLICIT" = "false" ] || EFFORT_SOURCE="explicit"
  if [ "$BACKEND_EXPLICIT" = "true" ] && [ -z "$ROLE" ]; then BACKEND_SOURCE="explicit"; fi
  # An identity is a deliberate backend + model + effort choice, and `auto`
  # hands the model choice back to the vendor per request — which would make
  # this job's record name what Copilot picked, not what the repo configured.
  if [ "$BACKEND" = "copilot" ] && [ "$MODEL" = "auto" ]; then
    die "model 'auto' is refused on a copilot job: name a concrete model instead, with 'handoff-config.py set --role ${ROLE:-<identity>} --backend copilot --model <model>'"
  fi
  validate_effort "$BACKEND" "$EFFORT"
  resolve_worker_bin "$BACKEND"
  resolve_permission_mode

  LABEL="$(echo "$LABEL" | tr -cs 'A-Za-z0-9_-' '-' | sed 's/^-//;s/-$//')"
  if [ "$DRY_RUN" = "true" ]; then
    local SKIP_GIT_REPO_CHECK=""
    [ "$BACKEND" = "codex" ] && { is_git_repo "$REPO" || SKIP_GIT_REPO_CHECK="--skip-git-repo-check"; }
    printf 'role=%s\nbackend=%s\nbackend_source=%s\nmodel=%s\neffort=%s\nmodel_source=%s\neffort_source=%s\ncodex_bin=%s\ncodex_bin_source=%s\ncodex_version=%s\nskip_git_repo_check=%s\npermission_mode=%s\npermission_posture=%s\npermission_posture_source=%s\n' \
      "${ROLE:-none}" "$BACKEND" "$BACKEND_SOURCE" "${MODEL:-default}" "$EFFORT" "$MODEL_SOURCE" "$EFFORT_SOURCE" \
      "$CODEX_BIN" "$CODEX_BIN_SOURCE" "$CODEX_VERSION" "$SKIP_GIT_REPO_CHECK" "$PERMISSION_MODE" "$PERMISSION_POSTURE" "$PERMISSION_POSTURE_SOURCE"
    return 0
  fi
  local JOB_ID
  JOB_ID="$(make_job_id "$LABEL")"
  JOB="$(job_dir "$JOB_ID")"
  [ -e "$JOB" ] && die "job dir already exists: $JOB"
  mkdir -p "$JOB"
  cp "$PROMPT_FILE" "$JOB/prompt.md"

  # Copilot emits `sessionId` only in its terminal event, so a job that dies
  # mid-run would have no id to resume from. --session-id sets the UUID for a
  # new session, so assign it here and persist it before launch; a crashed
  # copilot job then has the same resume parity the other two backends get for
  # free.
  if [ "$BACKEND" = "copilot" ]; then
    NEW_SESSION_ID="$(python3 -c 'import uuid; print(uuid.uuid4())')" \
      || die "failed to generate a session id for the copilot job"
    printf '%s' "$NEW_SESSION_ID" >"$JOB/session_id"
  fi

  WORKDIR="$REPO"
  if [ -n "$WORKTREE_BRANCH" ]; then
    WORKDIR="$REPO/.handoff/worktrees/$JOB_ID"
    mkdir -p "$REPO/.handoff/worktrees"
    git -C "$REPO" worktree add --quiet -b "$WORKTREE_BRANCH" "$WORKDIR" "$BASE_COMMIT" \
      || die "git worktree add failed for branch: $WORKTREE_BRANCH"
  fi

  {
    printf 'label=%s\neffort=%s\nmodel=%s\nrole=%s\nbackend=%s\nbackend_source=%s\nmodel_source=%s\neffort_source=%s\ncodex_bin=%s\ncodex_bin_source=%s\ncodex_version=%s\nread_only=%s\npermission_mode=%s\npermission_posture=%s\npermission_posture_source=%s\nsubmitted_at=%s\nmode=fresh\n' \
      "$LABEL" "$EFFORT" "${MODEL:-default}" "${ROLE:-none}" "$BACKEND" "$BACKEND_SOURCE" "$MODEL_SOURCE" "$EFFORT_SOURCE" \
      "$CODEX_BIN" "$CODEX_BIN_SOURCE" "$CODEX_VERSION" "$READ_ONLY" "$PERMISSION_MODE" "$PERMISSION_POSTURE" "$PERMISSION_POSTURE_SOURCE" "$(now_utc)"
  } >"$JOB/meta"

  if [ -n "$NEW_SESSION_ID" ]; then
    printf 'session_id_source=assigned\n' >>"$JOB/meta"
  fi

  if [ -n "$WORKTREE_BRANCH" ]; then
    printf 'worktree=%s\nbranch=%s\nbase_commit=%s\n' \
      "$WORKDIR" "$WORKTREE_BRANCH" "$BASE_COMMIT" >>"$JOB/meta"
  fi

  write_run_script "$JOB" "$EFFORT" "$MODEL" "$READ_ONLY" ""
  warn_permission_bypass "$JOB_ID"
  launch_job "$JOB"
  echo "$JOB_ID"
}

cmd_resume() {
  local PARENT_ID="$1"; shift
  local PROMPT_FILE="" READ_ONLY="false"
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --repo) REPO="${2:-}"; shift 2 ;;
      --prompt-file) PROMPT_FILE="${2:-}"; shift 2 ;;
      --read-only) READ_ONLY="true"; shift ;;
      *) die "unknown resume argument: $1" ;;
    esac
  done
  require_repo
  require_job "$PARENT_ID"
  [ -n "$PROMPT_FILE" ] && [ -f "$PROMPT_FILE" ] || die "--prompt-file is required and must exist"
  [ "$(job_state)" = "RUNNING" ] && die "parent job still running; wait or cancel first"

  local PARENT_JOB="$JOB"
  # The review cap counts real resume ancestry. The -r<n> suffix is label text
  # a fresh job can carry too, so walk parent= back to the chain's first job,
  # failing closed on anything that would make the count wrong.
  local ROOT_JOB="$PARENT_JOB" ROUND=2 ANCESTOR SEEN=" $PARENT_ID "
  while :; do
    [ -f "$ROOT_JOB/meta" ] || die "cannot count review rounds: $(basename "$ROOT_JOB") has no meta"
    ANCESTOR="$(meta_value parent "$ROOT_JOB")"
    if [ -z "$ANCESTOR" ]; then
      [ "$(meta_value mode "$ROOT_JOB")" != "resume" ] \
        || die "cannot count review rounds: $(basename "$ROOT_JOB") is a resume with no parent"
      break
    fi
    case "$SEEN" in *" $ANCESTOR "*) die "cannot count review rounds: the parent chain loops at $ANCESTOR" ;; esac
    SEEN="$SEEN$ANCESTOR "
    ROOT_JOB="$(job_dir "$ANCESTOR")"
    [ -d "$ROOT_JOB" ] || die "cannot count review rounds: ancestor job $ANCESTOR is missing"
    ROUND=$((ROUND + 1))
  done
  local ROOT_ID GATE="implementation" CAP
  ROOT_ID="$(basename "$ROOT_JOB")"
  if [ "$(meta_value label "$ROOT_JOB")" = "spec-review" ]; then
    GATE="spec"
  fi
  if ! CAP="$(python3 "$SCRIPT_DIR/handoff-config.py" --repo "$REPO" resolve \
      | python3 -c 'import json, sys; print(json.load(sys.stdin)["review"][sys.argv[1] + "_max_rounds"])' "$GATE")"; then
    die "failed to resolve the review caps from Handoff config; run 'python3 scripts/handoff-config.py validate'"
  fi
  if [ "$ROUND" -gt "$CAP" ]; then
    die "review cap reached: $ROOT_ID has had $CAP review passes ($GATE gate, [review] ${GATE}_max_rounds = $CAP); escalate to the arbiter instead of resuming (references/claude-driven.md, Phase 4)"
  fi
  if [ "$READ_ONLY" != "true" ]; then
    READ_ONLY="$(meta_value read_only "$PARENT_JOB")"
    READ_ONLY="${READ_ONLY:-false}"
  fi
  PERMISSION_POSTURE="$(meta_value permission_posture "$PARENT_JOB")"
  PERMISSION_POSTURE_SOURCE="parent"
  if [ -z "$PERMISSION_POSTURE" ]; then
    PERMISSION_POSTURE="default"
    PERMISSION_POSTURE_SOURCE="default"
  fi
  # A fix round resumes on the parent job's backend, never on a re-decided one.
  # Jobs written before backend dispatch carry no `backend=` line; they are
  # codex by construction.
  BACKEND="$(meta_value backend "$PARENT_JOB")"
  BACKEND="${BACKEND:-codex}"
  local PARENT_ROLE
  PARENT_ROLE="$(meta_value role "$PARENT_JOB")"
  # `codex exec resume` and `claude --resume` both take cwd from the shell, so
  # without this a fix round would land in the main repo instead of the
  # parent's worktree.
  local PARENT_WORKDIR
  PARENT_WORKDIR="$(meta_value worktree "$PARENT_JOB")"
  if [ -n "$PARENT_WORKDIR" ]; then
    [ -d "$PARENT_WORKDIR" ] || die "parent job worktree is missing: $PARENT_WORKDIR"
  else
    PARENT_WORKDIR="$REPO"
  fi
  local SESSION_ID
  SESSION_ID="$(extract_session_id)"
  [ -n "$SESSION_ID" ] || die "no session id found in $PARENT_JOB/log.jsonl; cannot resume"

  local EFFORT
  EFFORT="$(meta_value effort "$PARENT_JOB")"
  CODEX_BIN="$(meta_value codex_bin "$PARENT_JOB")"
  # The recorded path is reused only if it still identifies as the same tool.
  # On copilot that is not a formality: `copilot` is also AWS Copilot CLI's
  # binary name, so a path that was GitHub Copilot CLI at submit can be a
  # different program by the fix round.
  if [ -n "$CODEX_BIN" ] && [ -x "$CODEX_BIN" ] \
     && { [ "$BACKEND" != "copilot" ] || is_github_copilot_bin "$CODEX_BIN"; }; then
    CODEX_BIN_SOURCE="parent"
    CODEX_VERSION="$("$CODEX_BIN" --version 2>/dev/null | head -1 || true)"
    CODEX_VERSION="${CODEX_VERSION:-unknown}"
  else
    resolve_worker_bin "$BACKEND"
  fi
  resolve_permission_mode
  local JOB_ID="${ROOT_ID}-r${ROUND}"
  JOB="$(job_dir "$JOB_ID")"
  [ -e "$JOB" ] && die "job dir already exists: $JOB"
  mkdir -p "$JOB"
  cp "$PROMPT_FILE" "$JOB/prompt.md"
  printf '%s' "$SESSION_ID" >"$JOB/session_id"

  {
    printf 'label=resume\neffort=%s\nmodel=inherit\nrole=%s\nbackend=%s\nbackend_source=parent\ncodex_bin=%s\ncodex_bin_source=%s\ncodex_version=%s\nread_only=%s\npermission_mode=%s\npermission_posture=%s\npermission_posture_source=%s\nsubmitted_at=%s\nmode=resume\nparent=%s\n' \
      "${EFFORT:-high}" "${PARENT_ROLE:-none}" "$BACKEND" \
      "$CODEX_BIN" "$CODEX_BIN_SOURCE" "$CODEX_VERSION" "$READ_ONLY" "$PERMISSION_MODE" "$PERMISSION_POSTURE" "$PERMISSION_POSTURE_SOURCE" "$(now_utc)" "$PARENT_ID"
  } >"$JOB/meta"

  if [ "$PARENT_WORKDIR" != "$REPO" ]; then
    printf 'worktree=%s\n' "$PARENT_WORKDIR" >>"$JOB/meta"
  fi

  WORKDIR="$PARENT_WORKDIR"
  write_run_script "$JOB" "${EFFORT:-high}" "" "$READ_ONLY" "$SESSION_ID"
  warn_permission_bypass "$JOB_ID"
  launch_job "$JOB"
  echo "$JOB_ID"
}

write_run_script() {
  local job="$1" effort="$2" model="$3" read_only="$4" session_id="$5"
  {
    echo '#!/usr/bin/env bash'
    echo 'set -uo pipefail'
    printf 'JOB=%q\n' "$job"
    printf 'WORKDIR=%q\n' "$WORKDIR"
    printf 'CODEX_BIN=%q\n' "$CODEX_BIN"
    echo 'PROMPT="$(cat "$JOB/prompt.md")"'
    # </dev/null: a long prompt can make a worker CLI also wait on stdin for
    # more input ("Reading additional input from stdin..."); the background
    # job's stdin is never closed on its own, so without this the job hangs
    # forever with no further JSONL events.
    if [ "$BACKEND" = "claude" ]; then
      write_claude_exec_line "$effort" "$model" "$session_id"
    elif [ "$BACKEND" = "copilot" ]; then
      write_copilot_exec_line "$effort" "$model" "$read_only" "$session_id"
    elif [ -n "$session_id" ]; then
      # `codex exec resume` accepts no -C/-s flags: cwd comes from the shell,
      # sandbox and effort go through -c config overrides.
      local args="--json -c 'model_reasoning_effort=\"$effort\"'"
      [ "$read_only" = "true" ] && args="$args -c 'sandbox_mode=\"read-only\"'"
      [ "$PERMISSION_MODE" = "allow-all" ] && args="$args --dangerously-bypass-approvals-and-sandbox"
      echo 'cd "$WORKDIR"'
      printf '"$CODEX_BIN" exec resume %q "$PROMPT" %s >"$JOB/log.jsonl" 2>"$JOB/stderr.log" </dev/null\n' "$session_id" "$args"
    else
      local args="--json -C \"\$WORKDIR\" -c 'model_reasoning_effort=\"$effort\"'"
      # Non-git --repo targets need --skip-git-repo-check or codex exec
      # refuses to run ("Not inside a trusted directory").
      is_git_repo "$WORKDIR" || args="$args --skip-git-repo-check"
      [ -n "$model" ] && args="$args -m \"$model\""
      [ "$read_only" = "true" ] && args="$args -s read-only"
      [ "$PERMISSION_MODE" = "allow-all" ] && args="$args --dangerously-bypass-approvals-and-sandbox"
      printf '"$CODEX_BIN" exec "$PROMPT" %s >"$JOB/log.jsonl" 2>"$JOB/stderr.log" </dev/null\n' "$args"
    fi
    echo 'echo $? >"$JOB/exit_code"'
  } >"$job/run.sh"
  chmod +x "$job/run.sh"
}

write_claude_exec_line() {
  local effort="$1" model="$2" session_id="$3"
  # Credential boundary: a nested Claude Code host injects provider URLs and
  # credentials that would override the user's normal first-party CLI login.
  # Mirrors handoff_runtime.clean_claude_env(). Bash prefix expansion is exact
  # and needs no subprocess; a sed alternation over `env` fails silently on
  # BSD sed, which has no `\|` in a basic regular expression.
  echo 'for name in ${!ANTHROPIC_@} ${!CLAUDE_CODE_@}; do unset "$name"; done'
  echo 'export CLAUDECODE=""'
  # `claude` takes its cwd from the shell; it has no -C.
  echo 'cd "$WORKDIR"'
  # PERMISSION_MODE comes from resolve_claude_permission_mode, which validates
  # it. --permission-prompts none stays: under bypassPermissions nothing prompts
  # so it is inert, and under a dialled-down mode it keeps the job from hanging
  # on a prompt nobody is there to answer.
  local args="--print --output-format stream-json --verbose --permission-prompts none --permission-mode $PERMISSION_MODE --effort $effort"
  [ "$PERMISSION_MODE" = "dontAsk" ] && args="$args --allowed-tools Read Glob Grep Edit Write Bash"
  if [ -n "$session_id" ]; then
    args="$args --resume $session_id"
  elif [ -n "$model" ]; then
    args="$args --model \"$model\""
  fi
  printf '"$CODEX_BIN" %s -- "$PROMPT" >"$JOB/log.jsonl" 2>"$JOB/stderr.log" </dev/null\n' "$args"
}

write_copilot_exec_line() {
  local effort="$1" model="$2" read_only="$3" session_id="$4"
  # Egress defaults are overridden explicitly. Session export to GitHub web and
  # mobile is on by default, and a delegated job carries the prompt and the
  # repository contents. --share, --share-gist,
  # --worktree, and --enable-memory are never passed: Handoff owns the worktree
  # protocol, pinned to an immutable base SHA.
  local args="--output-format json --effort $effort --no-ask-user"
  args="$args --no-remote --no-remote-export --no-auto-update"
  # --mode plan and --allow-all-tools are mutually exclusive here, and that is a
  # correctness requirement rather than a preference. Probed together, plan mode
  # still won on disk, but the worker attempted only its read, emitted ZERO
  # denial events, exited 0, and its final answer claimed two writes that never
  # happened. Nothing in the event stream marks that failure, so the monitor has
  # nothing to catch and the exclusivity is the only defence.
  if [ "$read_only" = "true" ]; then
    args="$args --mode plan"
  else
    args="$args --allow-all-tools"
    [ "$PERMISSION_MODE" = "allow-all" ] && args="$args --allow-all-paths --allow-all-urls"
  fi
  # Job evidence lives with the rest of the job state. cmd_cleanup keeps the job
  # directory, so this co-locates the logs without making them disposable.
  args="$args --usage-output-file \"\$JOB/usage.json\" --log-dir \"\$JOB/copilot-logs\""
  if [ -n "$session_id" ]; then
    # `copilot --resume` takes cwd from the shell and the session already
    # carries its model, same shape as the codex resume path.
    echo 'cd "$WORKDIR"'
    args="$args --resume $session_id"
  else
    args="-C \"\$WORKDIR\" $args --session-id $NEW_SESSION_ID"
    [ -n "$model" ] && args="$args --model \"$model\""
  fi
  echo 'mkdir -p "$JOB/copilot-logs"'
  printf '"$CODEX_BIN" -p "$PROMPT" %s >"$JOB/log.jsonl" 2>"$JOB/stderr.log" </dev/null\n' "$args"
}

launch_job() {
  local job="$1"
  nohup bash "$job/run.sh" >/dev/null 2>&1 &
  echo $! >"$job/pid"
  disown || true
}

# Reads a job log once and prints the last event type and the denial count, one
# per line. Held as a string rather than a heredoc so it can run inside a
# command substitution.
STATUS_SCAN_PY="$(cat <<'SCAN'
import json, sys

path, backend = sys.argv[1], sys.argv[2]
last, denied = "", 0
with open(path, encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = event.get("type", "")
        if etype:
            last = etype
        if backend == "copilot":
            # Typed, not pattern-matched: tool.execution_complete carries the
            # rule that caused the refusal under data.error.
            error = (event.get("data") or {}).get("error") or {}
            if etype == "tool.execution_complete" and error.get("code") == "denied":
                denied += 1
        elif event.get("subtype") == "permission_denied":
            denied += 1
print(last)
print(denied)
SCAN
)"

cmd_status() {
  local JOB_ID="$1"; shift
  local WAIT="false" TIMEOUT=1800
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --repo) REPO="${2:-}"; shift 2 ;;
      --wait) WAIT="true"; shift ;;
      --timeout) TIMEOUT="${2:-}"; shift 2 ;;
      *) die "unknown status argument: $1" ;;
    esac
  done
  require_repo
  require_job "$JOB_ID"

  local state elapsed=0
  state="$(job_state)"
  if [ "$WAIT" = "true" ]; then
    while [ "$state" = "RUNNING" ] && [ "$elapsed" -lt "$TIMEOUT" ]; do
      sleep 10
      elapsed=$((elapsed + 10))
      state="$(job_state)"
    done
  fi

  BACKEND="$(meta_value backend)"
  BACKEND="${BACKEND:-codex}"
  # One parsed pass over the log for both the last event type and the denial
  # count. Counting denials by grep would miss valid JSON with whitespace after
  # a colon and would match an unrelated `code` field anywhere on the line; the
  # denial shapes also differ per backend, which a pattern cannot tell apart.
  local last_event="" denied=0 scan=""
  if [ -s "$JOB/log.jsonl" ]; then
    scan="$(python3 -c "$STATUS_SCAN_PY" "$JOB/log.jsonl" "$BACKEND" 2>/dev/null || true)"
    last_event="$(printf '%s\n' "$scan" | sed -n 1p)"
    denied="$(printf '%s\n' "$scan" | sed -n 2p)"
  fi
  echo "job: $JOB_ID"
  echo "state: $state"
  echo "last_event: ${last_event:-none}"
  echo "log: $JOB/log.jsonl"
  # Reported only when non-zero: status is polled in a loop, so a clean job
  # stays quiet. A blocked check does not move the exit code, so this is the
  # only signal the monitor gets that the worker could not verify its work.
  [ "${denied:-0}" -gt 0 ] && echo "permission_denied: $denied (worker blocked; its self-report is not evidence)"
  if [ "$state" = "RUNNING" ] && [ "$WAIT" = "true" ]; then
    echo "note: timed out after ${TIMEOUT}s while still running"
    return 2
  fi
  [ "$state" = "DONE" ] || [ "$state" = "RUNNING" ]
}

cmd_result() {
  local JOB_ID="$1"; shift
  local AS_JSON="false"
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --repo) REPO="${2:-}"; shift 2 ;;
      --json) AS_JSON="true"; shift ;;
      *) die "unknown result argument: $1" ;;
    esac
  done
  require_repo
  require_job "$JOB_ID"
  extract_session_id >/dev/null || true

  BACKEND="$(meta_value backend)"
  BACKEND="${BACKEND:-codex}"

  python3 - "$JOB/log.jsonl" "$JOB/session_id" "$AS_JSON" "$BACKEND" <<'PY'
import json, sys

# The backend is an input, never sniffed from the event shape: Copilot's
# terminal event is `type: "result"`, the same type name Claude's stream-json
# uses with a completely different payload, so a backend-blind parser mis-reads
# a copilot log without erroring.
log_path, sid_path, as_json = sys.argv[1], sys.argv[2], sys.argv[3] == "true"
backend = sys.argv[4]
messages, commands, reasoning, usage, denied = [], [], [], {}, []
errors = []
copilot_tools = {}
try:
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = event.get("type", "")
            if backend == "copilot":
                # Streaming noise: one small job produced 82 ephemeral deltas.
                if event.get("ephemeral"):
                    continue
                data = event.get("data") or {}
                if etype == "assistant.message":
                    # A tool-calling turn also carries assistant.message with
                    # empty content, so select on the phase, not on text.
                    if data.get("phase") == "final_answer" and data.get("content"):
                        messages.append(data["content"])
                elif etype == "tool.execution_start":
                    copilot_tools[data.get("toolCallId")] = data.get("toolName") or ""
                    command = (data.get("arguments") or {}).get("command")
                    if command:
                        commands.append(command)
                elif etype == "tool.execution_complete":
                    if (data.get("error") or {}).get("code") == "denied":
                        # Typed rather than pattern-matched, and named from the
                        # matching start event.
                        denied.append(copilot_tools.get(data.get("toolCallId")) or "unknown")
                elif etype == "session.error":
                    errors.append({
                        "error_type": data.get("errorType") or "",
                        "message": data.get("message") or "",
                        "status_code": data.get("statusCode"),
                    })
                elif etype == "result":
                    # Copilot's terminal event is flat, not nested under data.
                    usage = event.get("usage") or usage
                continue
            if etype == "system" and event.get("subtype") == "permission_denied":
                # A denied tool call still leaves exit 0 behind, so without this
                # the driver reads a blocked acceptance check as a passed one.
                denied.append(event.get("tool_name") or "unknown")
                continue
            item = event.get("item") or {}
            itype = item.get("type") or item.get("item_type") or ""
            if etype == "item.completed":
                text = item.get("text") or item.get("content") or ""
                if itype == "agent_message" and text:
                    messages.append(text)
                elif itype == "reasoning" and text:
                    reasoning.append(text)
                elif itype == "command_execution":
                    commands.append(item.get("command", ""))
            elif etype == "turn.completed":
                usage = event.get("usage") or {}
            elif etype == "assistant":
                # Claude stream-json: content blocks on the assistant message.
                for block in (event.get("message") or {}).get("content") or []:
                    if block.get("type") == "text" and block.get("text"):
                        messages.append(block["text"])
                    elif block.get("type") == "tool_use":
                        arguments = block.get("input") or {}
                        commands.append(arguments.get("command") or block.get("name", ""))
            elif etype == "result":
                # Claude stream-json terminal event.
                if event.get("result"):
                    messages.append(event["result"])
                usage = event.get("usage") or usage
except FileNotFoundError:
    print("ERROR: no log.jsonl for this job", file=sys.stderr)
    sys.exit(1)

try:
    session_id = open(sid_path, encoding="utf-8").read().strip()
except FileNotFoundError:
    session_id = ""

if as_json:
    print(json.dumps({
        "session_id": session_id,
        "agent_message": messages[-1] if messages else "",
        "commands": [c for c in commands if c],
        "permission_denied": len(denied),
        "denied_tools": sorted(set(denied)),
        "usage": usage,
        # Copilot's API failures can arrive with an empty stderr, so a JSON
        # consumer has nowhere else to see them. Present on every backend so the
        # payload shape does not vary by vendor.
        "errors": errors,
    }, ensure_ascii=False))
else:
    print(f"session_id: {session_id or 'unknown'}")
    if usage:
        print(f"usage: {json.dumps(usage)}")
    if commands:
        print(f"commands_run: {len(commands)}")
    for error in errors:
        status = error["status_code"]
        print(f"session_error: {error['error_type'] or 'unknown'}"
              + (f" (status {status})" if status is not None else "")
              + f": {error['message']}")
    # Always printed, including the zero: the driver needs positive evidence
    # that nothing was blocked, not merely the absence of a warning.
    print(f"permission_denied: {len(denied)}"
          + (f" ({', '.join(sorted(set(denied)))})" if denied else ""))
    if denied:
        print("WARNING: the worker was blocked from actions it attempted, so it could not")
        print("verify its own work. Treat this job as failed whatever its exit code says,")
        print("and re-run the blocked checks yourself before accepting the diff.")
    print("--- agent message ---")
    print(messages[-1] if messages else "(no agent_message found — check stderr.log)")
PY
}

cmd_cancel() {
  local JOB_ID="$1"; shift
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --repo) REPO="${2:-}"; shift 2 ;;
      *) die "unknown cancel argument: $1" ;;
    esac
  done
  require_repo
  require_job "$JOB_ID"
  if [ -f "$JOB/pid" ]; then
    kill_tree "$(cat "$JOB/pid")"
  fi
  touch "$JOB/cancelled"
  remove_worktree || echo "worktree kept for inspection: $JOB_ID"
  echo "cancelled: $JOB_ID"
}

# Returns rather than dies so cmd_cancel can report a kept worktree and still
# finish; $JOB and $REPO are already resolved by both callers.
remove_worktree() {
  local WT
  WT="$(meta_value worktree)"
  if [ -z "$WT" ] || [ ! -d "$WT" ]; then
    echo "no worktree: $JOB_ID"
    return 0
  fi
  if [ -n "$(git -C "$WT" status --porcelain 2>/dev/null)" ]; then
    echo "worktree has uncommitted changes: $WT" >&2
    return 1
  fi
  git -C "$REPO" worktree remove "$WT" || { echo "git worktree remove failed: $WT" >&2; return 1; }
  echo "removed worktree: $WT"
}

cmd_cleanup() {
  local JOB_ID="$1"; shift
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --repo) REPO="${2:-}"; shift 2 ;;
      *) die "unknown cleanup argument: $1" ;;
    esac
  done
  require_repo
  require_job "$JOB_ID"
  remove_worktree
}

cmd_list() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --repo) REPO="${2:-}"; shift 2 ;;
      *) die "unknown list argument: $1" ;;
    esac
  done
  require_repo
  local base="$REPO/$JOBS_SUBDIR"
  [ -d "$base" ] || { echo "(no jobs)"; return 0; }
  local found="false"
  for dir in "$base"/job-*; do
    [ -d "$dir" ] || continue
    found="true"
    JOB="$dir"
    printf '%-40s %s\n' "$(basename "$dir")" "$(job_state)"
  done
  [ "$found" = "true" ] || echo "(no jobs)"
}

[ "$#" -ge 1 ] || { usage; exit 2; }
COMMAND="$1"; shift
REPO="${REPO:-}"
# Working directory for the worker process: the repo, or a job's worktree.
WORKDIR="$REPO"
# Which CLI runs the job; submit and resume set it from config or parent meta.
BACKEND="codex"
# Set only for a fresh copilot job, whose session id is assigned at submit.
NEW_SESSION_ID=""

case "$COMMAND" in
  submit) cmd_submit "$@" ;;
  status|result|resume|cancel|cleanup)
    [ "$#" -ge 1 ] || die "$COMMAND requires a jobId"
    JOB_ID_ARG="$1"; shift
    "cmd_$COMMAND" "$JOB_ID_ARG" "$@"
    ;;
  list) cmd_list "$@" ;;
  -h|--help|help) usage ;;
  *) usage; die "unknown command: $COMMAND" ;;
esac
