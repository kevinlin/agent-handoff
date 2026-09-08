#!/usr/bin/env bash
set -euo pipefail

# delegate-codex.sh — Claude-driven Handoff delegation primitive.
#
# The name is historical: this is the delegation primitive for BOTH backends.
# It wraps `codex exec --json` and `claude --print --output-format stream-json`
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
#   session_id   worker thread/session id, extracted from log.jsonl
#   meta         label, backend, effort, mode, parent job, timestamps

usage() {
  cat <<'USAGE'
delegate-codex.sh — background delegation jobs for the Handoff flow
(the name is historical; it drives both the codex and claude backends)

Usage:
  delegate-codex.sh submit --repo <path> --prompt-file <file>
                    [--label <name>] [--effort <level>] [--model <model>]
                    [--backend codex|claude]
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
work), read-write sandbox per the user's codex config. Use --read-only for
review/adversarial jobs that must not touch the repo; it maps to `-s read-only`
on codex and `--permission-mode plan` on claude.

--role resolves backend, model, and effort from Handoff config, and the
identity's backend decides which CLI executes the job — always. --backend is
for role-less ad-hoc jobs only: passing one that contradicts a named role is
refused rather than silently overriding the config.

Efforts are per CLI, never one shared enum: codex takes
minimal|low|medium|high|xhigh|max|ultra, claude takes low|medium|high|xhigh|max.

--worktree runs the job in a dedicated Git worktree under
<repo>/.handoff/worktrees/<jobId>, cut from --base (default HEAD) resolved to
an immutable commit SHA. The worker commits on that branch; merging, pushing,
and removal stay with the driver. `cleanup <jobId>` removes the worktree and
refuses one holding uncommitted changes. --repo always names the main repo,
never a worktree.

Worker binary: set HANDOFF_CODEX_BIN or HANDOFF_CLAUDE_BIN to an executable
path or command name to override discovery. On macOS the ChatGPT/Codex
app-bundled CLI is preferred when present so app-only models use a compatible
client; otherwise PATH is used.

HANDOFF_CLAUDE_PERMISSION_MODE overrides a claude worker's default
`acceptEdits`. A read-only job stays `plan` regardless.

Exit codes: status prints RUNNING/DONE/FAILED/CANCELLED; `status --wait`
returns non-zero on timeout or failure so callers can branch on it.
USAGE
}

JOBS_SUBDIR=".handoff/jobs"
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
  if [ "$backend" = "claude" ]; then env_var="HANDOFF_CLAUDE_BIN"; else env_var="HANDOFF_CODEX_BIN"; fi
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

  if [ -z "$candidate" ]; then
    candidate="$(command -v "$backend" 2>/dev/null || true)"
    CODEX_BIN_SOURCE="path"
  fi
  [ -n "$candidate" ] && [ -x "$candidate" ] || die "$backend CLI not found; install it or set $env_var"

  CODEX_BIN="$candidate"
  CODEX_VERSION="$("$CODEX_BIN" --version 2>/dev/null | head -1 || true)"
  CODEX_VERSION="${CODEX_VERSION:-unknown}"
}

validate_effort() {
  # Efforts are per CLI, never one shared enum.
  case "$1" in
    claude) case "$2" in low|medium|high|xhigh|max) ;; *) die "invalid --effort for claude: $2" ;; esac ;;
    *) case "$2" in minimal|low|medium|high|xhigh|max|ultra) ;; *) die "invalid --effort: $2" ;; esac ;;
  esac
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
            for key in ("thread_id", "session_id"):
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
  case "$BACKEND" in codex|claude) ;; *) die "invalid --backend: $BACKEND" ;; esac

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
  validate_effort "$BACKEND" "$EFFORT"
  resolve_worker_bin "$BACKEND"

  LABEL="$(echo "$LABEL" | tr -cs 'A-Za-z0-9_-' '-' | sed 's/^-//;s/-$//')"
  if [ "$DRY_RUN" = "true" ]; then
    local SKIP_GIT_REPO_CHECK=""
    [ "$BACKEND" = "codex" ] && { is_git_repo "$REPO" || SKIP_GIT_REPO_CHECK="--skip-git-repo-check"; }
    printf 'role=%s\nbackend=%s\nbackend_source=%s\nmodel=%s\neffort=%s\nmodel_source=%s\neffort_source=%s\ncodex_bin=%s\ncodex_bin_source=%s\ncodex_version=%s\nskip_git_repo_check=%s\n' \
      "${ROLE:-none}" "$BACKEND" "$BACKEND_SOURCE" "${MODEL:-default}" "$EFFORT" "$MODEL_SOURCE" "$EFFORT_SOURCE" \
      "$CODEX_BIN" "$CODEX_BIN_SOURCE" "$CODEX_VERSION" "$SKIP_GIT_REPO_CHECK"
    return 0
  fi
  local JOB_ID
  JOB_ID="$(make_job_id "$LABEL")"
  JOB="$(job_dir "$JOB_ID")"
  [ -e "$JOB" ] && die "job dir already exists: $JOB"
  mkdir -p "$JOB"
  cp "$PROMPT_FILE" "$JOB/prompt.md"

  WORKDIR="$REPO"
  if [ -n "$WORKTREE_BRANCH" ]; then
    WORKDIR="$REPO/.handoff/worktrees/$JOB_ID"
    mkdir -p "$REPO/.handoff/worktrees"
    git -C "$REPO" worktree add --quiet -b "$WORKTREE_BRANCH" "$WORKDIR" "$BASE_COMMIT" \
      || die "git worktree add failed for branch: $WORKTREE_BRANCH"
  fi

  {
    printf 'label=%s\neffort=%s\nmodel=%s\nrole=%s\nbackend=%s\nbackend_source=%s\nmodel_source=%s\neffort_source=%s\ncodex_bin=%s\ncodex_bin_source=%s\ncodex_version=%s\nread_only=%s\nsubmitted_at=%s\nmode=fresh\n' \
      "$LABEL" "$EFFORT" "${MODEL:-default}" "${ROLE:-none}" "$BACKEND" "$BACKEND_SOURCE" "$MODEL_SOURCE" "$EFFORT_SOURCE" \
      "$CODEX_BIN" "$CODEX_BIN_SOURCE" "$CODEX_VERSION" "$READ_ONLY" "$(now_utc)"
  } >"$JOB/meta"

  if [ -n "$WORKTREE_BRANCH" ]; then
    printf 'worktree=%s\nbranch=%s\nbase_commit=%s\n' \
      "$WORKDIR" "$WORKTREE_BRANCH" "$BASE_COMMIT" >>"$JOB/meta"
  fi

  write_run_script "$JOB" "$EFFORT" "$MODEL" "$READ_ONLY" ""
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
  if [ -n "$CODEX_BIN" ] && [ -x "$CODEX_BIN" ]; then
    CODEX_BIN_SOURCE="parent"
    CODEX_VERSION="$("$CODEX_BIN" --version 2>/dev/null | head -1 || true)"
    CODEX_VERSION="${CODEX_VERSION:-unknown}"
  else
    resolve_worker_bin "$BACKEND"
  fi
  local ROUND=2
  case "$PARENT_ID" in *-r[0-9]*) ROUND=$(( ${PARENT_ID##*-r} + 1 )) ;; esac
  local JOB_ID="${PARENT_ID%-r[0-9]*}-r${ROUND}"
  JOB="$(job_dir "$JOB_ID")"
  [ -e "$JOB" ] && die "job dir already exists: $JOB"
  mkdir -p "$JOB"
  cp "$PROMPT_FILE" "$JOB/prompt.md"
  printf '%s' "$SESSION_ID" >"$JOB/session_id"

  {
    printf 'label=resume\neffort=%s\nmodel=inherit\nrole=%s\nbackend=%s\nbackend_source=parent\ncodex_bin=%s\ncodex_bin_source=%s\ncodex_version=%s\nread_only=%s\nsubmitted_at=%s\nmode=resume\nparent=%s\n' \
      "${EFFORT:-high}" "${PARENT_ROLE:-none}" "$BACKEND" \
      "$CODEX_BIN" "$CODEX_BIN_SOURCE" "$CODEX_VERSION" "$READ_ONLY" "$(now_utc)" "$PARENT_ID"
  } >"$JOB/meta"

  if [ "$PARENT_WORKDIR" != "$REPO" ]; then
    printf 'worktree=%s\n' "$PARENT_WORKDIR" >>"$JOB/meta"
  fi

  WORKDIR="$PARENT_WORKDIR"
  write_run_script "$JOB" "${EFFORT:-high}" "" "$READ_ONLY" "$SESSION_ID"
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
      write_claude_exec_line "$effort" "$model" "$read_only" "$session_id"
    elif [ -n "$session_id" ]; then
      # `codex exec resume` accepts no -C/-s flags: cwd comes from the shell,
      # sandbox and effort go through -c config overrides.
      local args="--json -c 'model_reasoning_effort=\"$effort\"'"
      [ "$read_only" = "true" ] && args="$args -c 'sandbox_mode=\"read-only\"'"
      echo 'cd "$WORKDIR"'
      printf '"$CODEX_BIN" exec resume %q "$PROMPT" %s >"$JOB/log.jsonl" 2>"$JOB/stderr.log" </dev/null\n' "$session_id" "$args"
    else
      local args="--json -C \"\$WORKDIR\" -c 'model_reasoning_effort=\"$effort\"'"
      # Non-git --repo targets need --skip-git-repo-check or codex exec
      # refuses to run ("Not inside a trusted directory").
      is_git_repo "$WORKDIR" || args="$args --skip-git-repo-check"
      [ -n "$model" ] && args="$args -m \"$model\""
      [ "$read_only" = "true" ] && args="$args -s read-only"
      printf '"$CODEX_BIN" exec "$PROMPT" %s >"$JOB/log.jsonl" 2>"$JOB/stderr.log" </dev/null\n' "$args"
    fi
    echo 'echo $? >"$JOB/exit_code"'
  } >"$job/run.sh"
  chmod +x "$job/run.sh"
}

write_claude_exec_line() {
  local effort="$1" model="$2" read_only="$3" session_id="$4"
  # Credential boundary: a nested Claude Code host injects provider URLs and
  # credentials that would override the user's normal first-party CLI login.
  # Mirrors handoff_runtime.clean_claude_env(). Bash prefix expansion is exact
  # and needs no subprocess; a sed alternation over `env` fails silently on
  # BSD sed, which has no `\|` in a basic regular expression.
  echo 'for name in ${!ANTHROPIC_@} ${!CLAUDE_CODE_@}; do unset "$name"; done'
  echo 'export CLAUDECODE=""'
  # `claude` takes its cwd from the shell; it has no -C.
  echo 'cd "$WORKDIR"'
  local mode="${HANDOFF_CLAUDE_PERMISSION_MODE:-acceptEdits}"
  # ponytail: acceptEdits lets the worker edit files, but its Bash calls still
  # follow the user's own settings.json allowlist, so a worker may be unable to
  # run its own acceptance check. HANDOFF_CLAUDE_PERMISSION_MODE is the escape.
  [ "$read_only" = "true" ] && mode="plan"
  local args="--print --output-format stream-json --verbose --permission-prompts none --permission-mode $mode --effort $effort"
  if [ -n "$session_id" ]; then
    args="$args --resume $session_id"
  elif [ -n "$model" ]; then
    args="$args --model \"$model\""
  fi
  printf '"$CODEX_BIN" %s -- "$PROMPT" >"$JOB/log.jsonl" 2>"$JOB/stderr.log" </dev/null\n' "$args"
}

launch_job() {
  local job="$1"
  nohup bash "$job/run.sh" >/dev/null 2>&1 &
  echo $! >"$job/pid"
  disown || true
}

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

  local last_event=""
  if [ -s "$JOB/log.jsonl" ]; then
    last_event="$(tail -1 "$JOB/log.jsonl" | python3 -c 'import json,sys
try: print(json.loads(sys.stdin.read()).get("type",""))
except Exception: print("")' 2>/dev/null || true)"
  fi
  echo "job: $JOB_ID"
  echo "state: $state"
  echo "last_event: ${last_event:-none}"
  echo "log: $JOB/log.jsonl"
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

  python3 - "$JOB/log.jsonl" "$JOB/session_id" "$AS_JSON" <<'PY'
import json, sys

log_path, sid_path, as_json = sys.argv[1], sys.argv[2], sys.argv[3] == "true"
messages, commands, reasoning, usage = [], [], [], {}
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
        "usage": usage,
    }, ensure_ascii=False))
else:
    print(f"session_id: {session_id or 'unknown'}")
    if usage:
        print(f"usage: {json.dumps(usage)}")
    if commands:
        print(f"commands_run: {len(commands)}")
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
