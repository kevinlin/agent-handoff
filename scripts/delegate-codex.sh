#!/usr/bin/env bash
set -euo pipefail

# delegate-codex.sh — Claude-driven Handoff delegation primitive.
#
# The name is historical: this is the delegation primitive for ALL backends.
# It wraps `codex exec --json`, `claude --print --output-format stream-json`,
# `copilot -p --output-format json`, `cursor-agent -p --output-format stream-json`,
# and `agy -p --output-format stream-json`
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
#                the effective backend mode, permission_posture_source records origin;
#                on an antigravity resume, resumed_session is the conversation id it
#                asked for, which its own init id is compared with
#   usage.json   copilot only: final usage counters (--usage-output-file)
#   copilot-logs copilot only: the CLI's own --log-dir output

usage() {
  cat <<'USAGE'
delegate-codex.sh — background delegation jobs for the Handoff flow
(the name is historical; it drives the codex, claude, copilot, cursor, and antigravity backends)

Usage:
  delegate-codex.sh submit --repo <path> --prompt-file <file>
                    [--label <name>] [--effort <level>] [--model <model>]
                    [--backend codex|claude|copilot|cursor|antigravity]
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
`--permission-mode plan` on claude, and `--mode plan` on copilot, cursor, and antigravity.

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

`--model auto` is refused on a copilot, cursor, or antigravity job. An identity is a
deliberate backend + model + effort choice; `auto` hands the model choice back to the
vendor per request, so the job's record would name what Copilot picked rather
than what the repo configured.

Cursor takes one effort, `model`: Cursor carries effort inside the model slug
(claude-opus-5-5-high), so the slug is the whole choice. A role-less cursor job
that names no --effort gets `model`.

On cursor the permission posture changes nothing: default and allow-all both
run in Cursor's force mode (-f), because no narrower Cursor mode let a probed
worker run its own checks. Deny rules in .cursor/cli.json are the only
narrowing, and every writing cursor job prints the bypass warning. A cursor job
never carries --worktree, --sandbox, --approve-mcps, --api-key,
--stream-partial-output, or --yolo.

The cursor backend's binary is `cursor-agent`, set with HANDOFF_CURSOR_BIN.
`cursor` on PATH is the Cursor IDE launcher and `agent` is too generic a name,
so neither is ever resolved.

Antigravity takes one effort, `model`: an Antigravity model id carries its effort
(gemini-3.8-flash-low), so the id is the whole choice. A role-less antigravity job
that names no --effort gets `model`. Unlike cursor it must name a --model: an empty
model and `auto` are refused, because a resume passes the model again and an
unpinned run would use whatever model the CLI has selected at that moment.

On antigravity the permission posture changes nothing: default and allow-all both
pass --dangerously-skip-permissions, because no narrower mode let a probed worker
run its own checks, and every writing antigravity job prints the bypass warning.
--read-only passes --mode plan and never the skip flag, since the two together
wrote files. Every antigravity job carries --print-timeout 0s, so a long job is
not cut short by a print timeout, and never carries --effort, --sandbox,
--mode auto, or --mode accept-edits.

The antigravity backend's binary is `agy`, set with HANDOFF_ANTIGRAVITY_BIN.
`antigravity` on PATH is the IDE launcher, so it is never resolved, and an IDE
launcher has been linked as `agy` before. Every way of resolving an agy binary
therefore applies the same identity check: its --help output, stdout and stderr
together, must list both --conversation and --dangerously-skip-permissions.
PATH discovery walks every match and takes the first that passes; HANDOFF_ANTIGRAVITY_BIN
is checked too; and a fix round re-checks the parent job's recorded binary.

An antigravity resume records the model it passed, never `inherit`, and the id it
asked for as resumed_session. Its session id is its own log's init id, so a resume
that agy could not find a conversation for is reported as context_lost rather than
continued on a conversation that no longer exists.

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
  local backend="$1" env_var configured candidate="" bin_name="$1"
  case "$backend" in
    claude) env_var="HANDOFF_CLAUDE_BIN" ;;
    copilot) env_var="HANDOFF_COPILOT_BIN" ;;
    cursor) env_var="HANDOFF_CURSOR_BIN" ;;
    # Never `antigravity`: that is the IDE launcher. The CLI is `agy`.
    antigravity) env_var="HANDOFF_ANTIGRAVITY_BIN"; bin_name="agy" ;;
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
    # agy collides too, though not by name alone: an IDE launcher has been linked
    # as agy, and --version prints a bare number (VERSION) that tells nothing.
    if [ "$backend" = "antigravity" ] && ! is_antigravity_bin "$candidate"; then
      die "$env_var does not point at Antigravity CLI: $candidate does not list both --conversation and --dangerously-skip-permissions in --help (an Antigravity IDE launcher can share the binary name); point $env_var at the agy CLI, or unset it to discover one on PATH"
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
  if [ -z "$candidate" ] && [ "$backend" = "antigravity" ]; then
    candidate="$(discover_antigravity_bin)" || die "$candidate"
    CODEX_BIN_SOURCE="path"
  fi

  # The cursor backend's agent is `cursor-agent`; `cursor` on PATH is the IDE.
  [ "$backend" = "cursor" ] && bin_name="cursor-agent"
  if [ -z "$candidate" ]; then
    candidate="$(command -v "$bin_name" 2>/dev/null || true)"
    CODEX_BIN_SOURCE="path"
  fi
  [ -n "$candidate" ] && [ -x "$candidate" ] || die "$bin_name CLI not found; install it or set $env_var"

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

is_antigravity_bin() {
  # An IDE launcher has been linked as `agy` before (Homebrew), and agy --version
  # prints only a bare number (VERSION), so neither name nor version identifies
  # the CLI. What identifies it is the protocol Handoff depends on: its --help
  # lists both --conversation and --dangerously-skip-permissions. The CLI prints
  # that help on stderr (HELP), so both streams are read together. python3 holds
  # the 10 second limit because macOS ships no `timeout`; output goes to a file,
  # not a pipe, so a launcher that hangs and leaves children behind cannot keep
  # the call open after the kill. A candidate that fails or times out is refused.
  python3 - "$1" <<'PY_AGY'
import subprocess, sys, tempfile

with tempfile.TemporaryFile() as out:
    try:
        done = subprocess.run([sys.argv[1], "--help"], stdin=subprocess.DEVNULL,
                              stdout=out, stderr=subprocess.STDOUT, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        sys.exit(1)
    out.seek(0)
    text = out.read().decode("utf-8", "replace")
ok = done.returncode == 0 and "--conversation" in text and "--dangerously-skip-permissions" in text
sys.exit(0 if ok else 1)
PY_AGY
}

discover_antigravity_bin() {
  # Prints the first PATH match that identifies as Antigravity CLI. On failure
  # prints an actionable message and returns 1, so the caller can die with it.
  # Walking every match matters: the IDE launcher can sit earlier on PATH.
  local match first=""
  while IFS= read -r match; do
    [ -n "$match" ] && [ -x "$match" ] || continue
    if is_antigravity_bin "$match"; then
      printf '%s\n' "$match"
      return 0
    fi
    [ -n "$first" ] || first="$match"
  done < <(type -a -p agy 2>/dev/null || true)
  if [ -n "$first" ]; then
    printf '%s\n' "the 'agy' on PATH is not Antigravity CLI: $first does not list both --conversation and --dangerously-skip-permissions in --help (an Antigravity IDE launcher can share the binary name); set HANDOFF_ANTIGRAVITY_BIN to the agy CLI path"
  else
    printf '%s\n' "agy CLI not found; install Antigravity CLI or set HANDOFF_ANTIGRAVITY_BIN"
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
    cursor) [ "$2" = "model" ] || die "invalid --effort for cursor: $2 (Cursor carries effort in the model id; name the variant in --model and use --effort model)" ;;
    # agy has an --effort flag, but it cannot be a routing field: a model id plus
    # any --effort is refused before a session, and Claude models refuse it
    # outright (MCONFLICT, MCLAUDE). So the id carries the effort, as on cursor.
    antigravity) [ "$2" = "model" ] || die "invalid --effort for antigravity: $2 (Antigravity carries effort in the model id; name the variant in --model and use --effort model, with 'handoff-config.py set --role <identity> --backend antigravity --model <model> --effort model')" ;;
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

resolve_cursor_permission_mode() {
  # Both postures map to Cursor's force mode: probed, no narrower mode let a
  # worker run its checks. Read-only is plan mode and never force.
  PERMISSION_MODE="force"
  [ "$1" = "true" ] && PERMISSION_MODE="plan"
  return 0
}

resolve_antigravity_permission_mode() {
  # Both postures map to --dangerously-skip-permissions: print mode never prompts
  # and denies what needs approval, and no narrower mode let a probed worker run
  # its own checks (P0, P1, P4). Read-only is plan mode and never the skip flag:
  # plan with the skip flag wrote files (RO2, RO2R).
  PERMISSION_MODE="skip-permissions"
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
    claude:bypassPermissions|copilot:allow-all-tools|copilot:allow-all|codex:allow-all|cursor:force|antigravity:skip-permissions) ;;
    *) return 0 ;;
  esac
  echo "WARN $1 is a $BACKEND worker running with permission checks bypassed." >&2
  echo "     A background job has no approval surface; verify its work on disk." >&2
  if [ "$BACKEND" = "cursor" ]; then
    echo "     Cursor has no narrower posture: add deny rules in .cursor/cli.json, or use --read-only for a job that must not write." >&2
  elif [ "$BACKEND" = "antigravity" ]; then
    echo "     Antigravity has no narrower posture Handoff can set; use --read-only for a job that must not write." >&2
  elif [ "$BACKEND" = "claude" ] && [ "${HANDOFF_CLAUDE_PERMISSION_MODE:-}" = "bypassPermissions" ]; then
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
  # The backend is passed in, never read from the global: cmd_result runs this
  # before it knows the backend any other way.
  local backend="${1:-}"
  # A cache holding only whitespace is absent. An early read that finds no id
  # yet prints an empty line, tee caches that newline, and a bare -s test would
  # then return the empty value for good, whatever the log says afterwards.
  if [ -s "$JOB/session_id" ] && grep -q '[^[:space:]]' "$JOB/session_id"; then
    cat "$JOB/session_id"
    return 0
  fi
  python3 - "$JOB/log.jsonl" "$backend" <<'PY' | tee "$JOB/session_id"
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
            if sys.argv[2] == "antigravity":
                # Only the top-level id of an init event. A pre-session result
                # carries an empty id nested under result, and a resumed round
                # must report its own conversation, not the one it asked for:
                # agy answers an id it cannot find with a new conversation
                # (RUNKNOWN), and only init names the one it really ran on.
                found = event.get("conversation_id") if isinstance(event, dict) and event.get("event") == "init" else ""
                if isinstance(found, str) and found:
                    sid = found
                    break
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
  case "$BACKEND" in codex|claude|copilot|cursor|antigravity) ;; *) die "invalid --backend: $BACKEND" ;; esac

  if [ -n "$WORKTREE_BRANCH" ]; then
    is_git_repo "$REPO" || die "--worktree requires --repo to be a git repository"
    # Pin to an immutable SHA: a branch name can advance between cutting the
    # worktree and reading the verdict, and then the verdict names a commit
    # nobody tested.
    BASE_COMMIT="$(git -C "$REPO" rev-parse --verify "${WORKTREE_BASE:-HEAD}^{commit}" 2>/dev/null)" \
      || die "--base is not a valid commit: ${WORKTREE_BASE:-HEAD}"
  fi

  if [ -n "$ROLE" ]; then
    local IDENTITY="$ROLE" FIELDS CONFIG_SOURCE ROLE_BACKEND ROLE_MODEL ROLE_EFFORT
    if ! FIELDS="$(python3 "$SCRIPT_DIR/handoff-config.py" --repo "$REPO" resolve \
        source "$IDENTITY.backend" "$IDENTITY.model" "$IDENTITY.effort" "$IDENTITY.permission_mode")"; then
      die "failed to resolve Handoff identity '$ROLE'; run 'python3 scripts/handoff-config.py init' and then 'set --role $ROLE --backend <codex|claude> --model <model> --effort <effort>'"
    fi
    { IFS= read -r CONFIG_SOURCE; IFS= read -r ROLE_BACKEND; IFS= read -r ROLE_MODEL
      IFS= read -r ROLE_EFFORT; IFS= read -r PERMISSION_POSTURE; } <<<"$FIELDS"
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
  # Cursor and antigravity have one effort value. A role-less job that named none
  # gets it rather than the codex-shaped default.
  if { [ "$BACKEND" = "cursor" ] || [ "$BACKEND" = "antigravity" ]; } && [ "$EFFORT_EXPLICIT" = "false" ] && [ -z "$ROLE" ]; then EFFORT="model"; fi
  if [ "$BACKEND_EXPLICIT" = "true" ] && [ -z "$ROLE" ]; then BACKEND_SOURCE="explicit"; fi
  # An identity is a deliberate backend + model + effort choice, and `auto`
  # hands the model choice back to the vendor per request — which would make
  # this job's record name what Copilot picked, not what the repo configured.
  if [ "$MODEL" = "auto" ] && { [ "$BACKEND" = "copilot" ] || [ "$BACKEND" = "cursor" ] || [ "$BACKEND" = "antigravity" ]; }; then
    local ARTICLE="a"
    [ "$BACKEND" = "antigravity" ] && ARTICLE="an"
    die "model 'auto' is refused on $ARTICLE $BACKEND job: name a concrete model instead, with 'handoff-config.py set --role ${ROLE:-<identity>} --backend $BACKEND --model <model>'"
  fi
  # Unlike cursor, a role-less antigravity job cannot run on the CLI default
  # model: without --model, agy runs whatever model is selected at that moment
  # and init does not name it (MDEFAULT, RMODEL), and a resume has to pass the
  # same model again because agy carries none over (decision 4).
  if [ "$BACKEND" = "antigravity" ] && [ -z "$MODEL" ]; then
    die "model is empty on an antigravity job: name a catalogue model instead, with 'handoff-config.py set --role ${ROLE:-<identity>} --backend antigravity --model <model>'"
  fi
  [[ "$MODEL" != *$'\n'* && "$MODEL" != *$'\r'* ]] || die "model must be a single line"
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
    NEW_SESSION_ID="$(python3 - <<'PY_UUID'
import uuid
print(uuid.uuid4())
PY_UUID
)" \
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
  if ! CAP="$(python3 "$SCRIPT_DIR/handoff-config.py" --repo "$REPO" resolve "review.${GATE}_max_rounds")"; then
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
  SESSION_ID="$(extract_session_id "$BACKEND")"
  [ -n "$SESSION_ID" ] || die "no session id found in $PARENT_JOB/log.jsonl; cannot resume"

  local EFFORT
  EFFORT="$(meta_value effort "$PARENT_JOB")"
  validate_effort "$BACKEND" "${EFFORT:-high}"
  # Every other backend resumes inside a session that carries its model, so the
  # round records `inherit` and passes none. agy carries no model over (RMODEL:
  # an unpinned resume reported the CLI selection, not the parent), so the round
  # passes the parent recorded one again. It is the real id, never `inherit`,
  # because submit and every resume write the real one; that is what lets a
  # resume of a resume read it from its own parent without walking the chain.
  local ROUND_MODEL="" META_MODEL="inherit"
  if [ "$BACKEND" = "antigravity" ]; then
    ROUND_MODEL="$(meta_value model "$PARENT_JOB")"
    case "$ROUND_MODEL" in
      ""|inherit|default) die "parent job $PARENT_ID recorded no model, so agy would run whatever model the CLI has selected; resubmit with the full packet on a configured model (handoff-config.py set --role <identity> --backend antigravity --model <model>)" ;;
    esac
    META_MODEL="$ROUND_MODEL"
  fi
  CODEX_BIN="$(meta_value codex_bin "$PARENT_JOB")"
  # The recorded path is reused only if it still identifies as the same tool.
  # On copilot that is not a formality: `copilot` is also AWS Copilot CLI's
  # binary name, so a path that was GitHub Copilot CLI at submit can be a
  # different program by the fix round.
  # agy gets the same check, for the same reason: a recorded path can be an IDE
  # launcher by the fix round.
  if [ -n "$CODEX_BIN" ] && [ -x "$CODEX_BIN" ] \
     && { [ "$BACKEND" != "copilot" ] || is_github_copilot_bin "$CODEX_BIN"; } \
     && { [ "$BACKEND" != "antigravity" ] || is_antigravity_bin "$CODEX_BIN"; }; then
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
  # Not on agy: given an id it cannot find, it warns, exits 0, and runs under a
  # NEW id (RUNKNOWN), and a round whose model has left the catalogue fails
  # before init (MUNKNOWN). A pre-written cache would give both a session id
  # that is the parent one, and extract_session_id returns the cache first. The
  # id it asked for goes in meta as resumed_session instead, and the round
  # session id is whatever its own log init names.
  if [ "$BACKEND" != "antigravity" ]; then
    printf '%s' "$SESSION_ID" >"$JOB/session_id"
  fi

  {
    printf 'label=resume\neffort=%s\nmodel=%s\nrole=%s\nbackend=%s\nbackend_source=parent\ncodex_bin=%s\ncodex_bin_source=%s\ncodex_version=%s\nread_only=%s\npermission_mode=%s\npermission_posture=%s\npermission_posture_source=%s\nsubmitted_at=%s\nmode=resume\nparent=%s\n' \
      "${EFFORT:-high}" "$META_MODEL" "${PARENT_ROLE:-none}" "$BACKEND" \
      "$CODEX_BIN" "$CODEX_BIN_SOURCE" "$CODEX_VERSION" "$READ_ONLY" "$PERMISSION_MODE" "$PERMISSION_POSTURE" "$PERMISSION_POSTURE_SOURCE" "$(now_utc)" "$PARENT_ID"
  } >"$JOB/meta"

  if [ "$BACKEND" = "antigravity" ]; then
    printf 'resumed_session=%s\n' "$SESSION_ID" >>"$JOB/meta"
  fi

  if [ "$PARENT_WORKDIR" != "$REPO" ]; then
    printf 'worktree=%s\n' "$PARENT_WORKDIR" >>"$JOB/meta"
  fi

  WORKDIR="$PARENT_WORKDIR"
  write_run_script "$JOB" "${EFFORT:-high}" "$ROUND_MODEL" "$READ_ONLY" "$SESSION_ID"
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
    # Values from config and worker logs must remain data in the generated shell.
    printf 'MODEL=%q\nSESSION_ID=%q\n' "$model" "$session_id"
    echo 'PROMPT="$(cat "$JOB/prompt.md")"'
    # </dev/null: a long prompt can make a worker CLI also wait on stdin for
    # more input ("Reading additional input from stdin..."); the background
    # job's stdin is never closed on its own, so without this the job hangs
    # forever with no further JSONL events.
    if [ "$BACKEND" = "claude" ]; then
      write_claude_exec_line "$effort" "$model" "$session_id"
    elif [ "$BACKEND" = "copilot" ]; then
      write_copilot_exec_line "$effort" "$model" "$read_only" "$session_id"
    elif [ "$BACKEND" = "cursor" ]; then
      write_cursor_exec_line "$model" "$read_only" "$session_id"
    elif [ "$BACKEND" = "antigravity" ]; then
      write_antigravity_exec_line "$model" "$read_only" "$session_id"
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
      [ -n "$model" ] && args="$args -m \"\$MODEL\""
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
    args="$args --resume \"\$SESSION_ID\""
  elif [ -n "$model" ]; then
    args="$args --model \"\$MODEL\""
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
    args="$args --resume \"\$SESSION_ID\""
  else
    args="-C \"\$WORKDIR\" $args --session-id $NEW_SESSION_ID"
    [ -n "$model" ] && args="$args --model \"\$MODEL\""
  fi
  echo 'mkdir -p "$JOB/copilot-logs"'
  printf '"$CODEX_BIN" -p "$PROMPT" %s >"$JOB/log.jsonl" 2>"$JOB/stderr.log" </dev/null\n' "$args"
}

write_cursor_exec_line() {
  local model="$1" read_only="$2" session_id="$3"
  # --trust always: a fresh worktree is a workspace Cursor has never seen.
  # Handoff owns the worktree protocol, so Cursor's own worktree flag is never
  # passed, and neither are its sandbox, MCP-approval, API-key, or
  # partial-output flags. No env scrubbing: Cursor reads none of the
  # ANTHROPIC_* or CLAUDE_CODE_* variables.
  local args="--output-format stream-json --trust"
  if [ -n "$session_id" ]; then
    # A resumed session carries its model and workspace; cwd comes from the cd.
    args="$args --resume \"\$SESSION_ID\""
  else
    args="$args --workspace \"\$WORKDIR\""
    [ -n "$model" ] && args="$args --model \"\$MODEL\""
  fi
  if [ "$read_only" = "true" ]; then
    args="$args --mode plan"
  else
    args="$args --force"  # risk-ok: Cursor CLI flag name
  fi
  echo 'cd "$WORKDIR"'
  printf '"$CODEX_BIN" -p "$PROMPT" %s >"$JOB/log.jsonl" 2>"$JOB/stderr.log" </dev/null\n' "$args"
}

write_antigravity_exec_line() {
  local model="$1" read_only="$2" session_id="$3"
  # agy takes its working directory from the shell and has no workspace flag
  # (P0, WORKTREE), so the cd carries it; init.cwd records where it landed.
  # Never passed: --effort (the model id carries it), --sandbox, --mode auto,
  # --mode accept-edits, -c/--continue, -i, --project, --new-project, --add-dir,
  # --agent, --remote-control, --input-format, --json-schema, --log-file. No env
  # scrubbing either: bogus ANTHROPIC_* and CLAUDE_CODE_* values had no effect (ENV).
  local args="--output-format stream-json"
  # A resume passes --conversation, and also the model and the posture again:
  # neither carries over from the parent (RKILL, RMODEL, MODERESUME).
  [ -n "$session_id" ] && args="$args --conversation \"\$SESSION_ID\""
  # --print-timeout 0s on every line: help says unlimited and the docs page says
  # five minutes, and a timed-out run exits 0 with status SUCCESS and partial
  # output (TIMEOUTR), so an unpinned default could truncate a job silently (DRV_PT0).
  args="$args --model \"\$MODEL\" --print-timeout 0s"
  if [ "$read_only" = "true" ]; then
    args="$args --mode plan"
  else
    args="$args --dangerously-skip-permissions"
  fi
  echo 'cd "$WORKDIR"'
  printf '"$CODEX_BIN" -p "$PROMPT" %s >"$JOB/log.jsonl" 2>"$JOB/stderr.log" </dev/null\n' "$args"
}

launch_job() {
  local job="$1"
  nohup bash "$job/run.sh" >/dev/null 2>&1 &
  echo $! >"$job/pid"
  disown || true
}

# Reads a job log once and prints the last event type, the denial count, and
# whether that count is known, one per line. Held as a string rather than a heredoc so it can run inside a
# command substitution. Keep apostrophes out of the block, comments included:
# bash 3.2 (macOS) mis-parses one in a quoted heredoc nested inside $(...).
STATUS_SCAN_PY="$(cat <<'SCAN'
import json, sys

path, backend = sys.argv[1], sys.argv[2]
last, denied, result_seen = "", 0, False
with open(path, encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if backend == "antigravity":
            # The discriminator is event, not type. Denials are the denied_actions
            # list of the terminal result and nowhere else: the same refused write
            # ended as a DONE tool step with no error in P0. Every shape is checked,
            # so an unexpected one is skipped and nothing is counted from it.
            kind = event.get("event") if isinstance(event, dict) else None
            if not isinstance(kind, str) or not kind:
                continue
            step = event.get("step_update")
            step_type = step.get("step_type") if isinstance(step, dict) else None
            if kind == "step_update" and isinstance(step_type, str) and step_type:
                last = "step_update:" + step_type
            else:
                last = kind
            payload = event.get("result")
            if kind == "result" and isinstance(payload, dict):
                result_seen = True
                actions = payload.get("denied_actions")
                denied = len(actions) if isinstance(actions, list) else 0
            continue
        etype = event.get("type", "")
        # The cursor reasoning stream, hundreds of events a job, would bury the
        # event that says where the worker is.
        if backend == "cursor" and etype == "thinking":
            continue
        if etype:
            last = etype
        if backend == "copilot":
            # Typed, not pattern-matched: tool.execution_complete carries the
            # rule that caused the refusal under data.error.
            error = (event.get("data") or {}).get("error") or {}
            if etype == "tool.execution_complete" and error.get("code") == "denied":
                denied += 1
        elif backend == "cursor":
            # Typed: a refused call completes with a `rejected` result object.
            call = event.get("tool_call")
            if etype == "tool_call" and event.get("subtype") == "completed" and isinstance(call, dict):
                if any(isinstance(body, dict) and isinstance(body.get("result"), dict)
                       and "rejected" in body["result"] for body in call.values()):
                    denied += 1
        elif event.get("subtype") == "permission_denied":
            denied += 1
print(last)
print(denied)
# Antigravity reports denials only when a run ends, so before a result event the
# count is unknown rather than zero. Every other backend reports as it goes.
print("unknown" if backend == "antigravity" and not result_seen else "known")
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
  local last_event="" denied=0 scan="" denial_known="known" third=""
  # Initialised outside the log guard: a terminal antigravity job with an empty
  # or missing log has no result event either, so its count is unknown too.
  [ "$BACKEND" != "antigravity" ] || denial_known="unknown"
  if [ -s "$JOB/log.jsonl" ]; then
    scan="$(python3 -c "$STATUS_SCAN_PY" "$JOB/log.jsonl" "$BACKEND" 2>/dev/null || true)"
    last_event="$(printf '%s\n' "$scan" | sed -n 1p)"
    denied="$(printf '%s\n' "$scan" | sed -n 2p)"
    third="$(printf '%s\n' "$scan" | sed -n 3p)"
    [ -z "$third" ] || denial_known="$third"
  fi
  echo "job: $JOB_ID"
  echo "state: $state"
  echo "last_event: ${last_event:-none}"
  echo "log: $JOB/log.jsonl"
  # Reported only when non-zero: status is polled in a loop, so a clean job
  # stays quiet. A blocked check does not move the exit code, so this is the
  # only signal the monitor gets that the worker could not verify its work.
  [ "${denied:-0}" -gt 0 ] && echo "permission_denied: $denied (worker blocked; its self-report is not evidence)"
  # A finished antigravity job with no result event cannot say what it was denied.
  # A running one prints nothing, as a clean job does, because status is polled.
  if [ "$denial_known" = "unknown" ] && [ "$state" != "RUNNING" ]; then
    echo "permission_denied: unknown (no result event; agy reports denials only when a run ends)"
  fi
  if [ "$BACKEND" = "antigravity" ]; then
    local asked observed
    asked="$(meta_value resumed_session)"
    # Measured only when the log names a session: a resume rejected before init
    # has no observed id, so nothing was lost that anyone can show.
    if [ -n "$asked" ]; then
      observed="$(extract_session_id "$BACKEND")"
      [ -z "$observed" ] || [ "$observed" = "$asked" ] \
        || echo "context_lost: true (round ran without its parent's context; resubmit with the full packet)"
    fi
  fi
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

  BACKEND="$(meta_value backend)"
  BACKEND="${BACKEND:-codex}"
  extract_session_id "$BACKEND" >/dev/null || true

  # stderr.log and the id the round asked for are inputs of the antigravity branch
  # only: a print timeout leaves nothing but a stderr line, and a lost session is
  # the log init id differing from resumed_session. Both are empty elsewhere.
  python3 - "$JOB/log.jsonl" "$JOB/session_id" "$AS_JSON" "$BACKEND" "$JOB/stderr.log" "$(meta_value resumed_session)" <<'PY'
import json, sys

# The backend is an input, never sniffed from the event shape: Copilot's
# terminal event is `type: "result"`, the same type name Claude's stream-json
# uses with a completely different payload. Cursor shares system, assistant,
# user, and result type names with Claude, so the parser needs the backend.
log_path, sid_path, as_json = sys.argv[1], sys.argv[2], sys.argv[3] == "true"
backend = sys.argv[4]
stderr_path, resumed_session = sys.argv[5], sys.argv[6]
messages, commands, reasoning, usage, denied = [], [], [], {}, []
errors = []
copilot_tools = {}
# Antigravity state. Denials and usage are known only once a result event exists,
# so None means unknown, never zero.
agent_steps, step_usage, final_result, init_id = {}, {}, None, ""
denied_known, context_lost = True, None
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
            if backend == "antigravity":
                # The discriminator is event, not type, and every shape is checked
                # so an unexpected one is skipped and nothing is counted from it.
                kind = event.get("event") if isinstance(event, dict) else None
                payload = event.get(kind) if isinstance(kind, str) else None
                if kind == "init" and not init_id and isinstance(event.get("conversation_id"), str):
                    init_id = event["conversation_id"]
                elif kind == "step_update" and isinstance(payload, dict):
                    index, step_type = payload.get("step_index"), payload.get("step_type")
                    if step_type == "agent_response" and isinstance(index, int) and not isinstance(index, bool):
                        # Deltas fold by step_index, in log order. The answer is the
                        # last such step, never result.response, which joins every
                        # agent message of the invocation (P3).
                        delta = payload.get("text_delta")
                        agent_steps.setdefault(index, []).append(delta if isinstance(delta, str) else "")
                    elif step_type == "tool" and payload.get("tool_name") == "run_command" \
                            and payload.get("state") == "ACTIVE":
                        # agy logs no exit code for a command (EXIT7), so none is shown.
                        info = payload.get("tool_info")
                        parameters = info.get("parameters") if isinstance(info, dict) else None
                        command = parameters.get("CommandLine") if isinstance(parameters, dict) else None
                        if isinstance(command, str) and command:
                            commands.append(command)
                    # This log's own steps, never result.usage: that one is cumulative
                    # over the whole conversation (RSAME: 23989 against a step sum of 12143).
                    counters = payload.get("usage")
                    if isinstance(counters, dict):
                        for name, count in counters.items():
                            if isinstance(count, (int, float)) and not isinstance(count, bool):
                                step_usage[name] = step_usage.get(name, 0) + count
                elif kind == "result" and isinstance(payload, dict):
                    final_result = payload
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
            if backend == "cursor":
                if etype == "thinking":
                    continue
                if etype == "assistant":
                    # One event per message. Cursor's result.result runs every
                    # message together, so the answer is the last event's text,
                    # taken literally: an empty last message is still the last.
                    blocks = (event.get("message") or {}).get("content") or []
                    messages.append("".join(
                        block.get("text") or "" for block in blocks
                        if isinstance(block, dict) and block.get("type") == "text"))
                elif etype == "tool_call":
                    # The tool kind is the one key under tool_call holding an object.
                    call = event.get("tool_call")
                    kind, body = next(((k, v) for k, v in (call.items() if isinstance(call, dict) else ())
                                       if isinstance(v, dict)), ("", {}))
                    result = body.get("result")
                    if event.get("subtype") == "started" and kind == "shellToolCall":
                        command = (body.get("args") or {}).get("command")
                        if command:
                            commands.append(command)
                    elif event.get("subtype") == "completed" and isinstance(result, dict) \
                            and "rejected" in result:
                        denied.append(kind)
                elif etype == "result":
                    # The last result's usage, never an earlier one's.
                    usage = event.get("usage") or {}
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

if backend == "antigravity":
    if agent_steps:
        # The last step to appear, its deltas joined literally: an empty last
        # message is still the last.
        messages.append("".join(agent_steps[list(agent_steps)[-1]]))
    if final_result is not None:
        usage = {name: step_usage.get(name, 0) for name in (
            "input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens")}
        actions = final_result.get("denied_actions")
        for action in actions if isinstance(actions, list) else []:
            name = action.get("display_name") if isinstance(action, dict) else None
            denied.append(name if isinstance(name, str) and name else "unknown")
        if final_result.get("status") == "ERROR":
            error = final_result.get("error")
            errors.append({
                "status_code": None,
                "error_type": "result_error",
                "message": error if isinstance(error, str) else ("" if error is None else json.dumps(error)),
            })
    else:
        denied_known = False
    try:
        with open(stderr_path, encoding="utf-8", errors="replace") as fh:
            timeout_line = next((text.rstrip("\r\n") for text in fh if "print timeout after" in text), None)
    except FileNotFoundError:
        timeout_line = None
    if timeout_line is not None:
        # A timed-out run exits 0 with status SUCCESS and partial output (TIMEOUTR);
        # this stderr line is the only trace, so the job must not read as clean.
        errors.append({"status_code": None, "error_type": "print_timeout", "message": timeout_line})
    # Measured only when the log names a session. A resume rejected before init
    # has no observed id, so nothing is reported either way.
    if resumed_session and init_id:
        context_lost = init_id != resumed_session

if as_json:
    print(json.dumps({
        "session_id": session_id,
        "agent_message": messages[-1] if messages else "",
        "commands": [c for c in commands if c],
        "permission_denied": len(denied) if denied_known else None,
        "denied_tools": sorted(set(denied)),
        "usage": usage,
        # Copilot's API failures can arrive with an empty stderr, so a JSON
        # consumer has nowhere else to see them. Present on every backend so the
        # payload shape does not vary by vendor.
        "errors": errors,
        # Measured only on an antigravity resume; null wherever it is not.
        "context_lost": context_lost,
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
    if not denied_known:
        print("permission_denied: unknown (no result event; agy reports denials only when a run ends)")
    else:
        print(f"permission_denied: {len(denied)}"
              + (f" ({', '.join(sorted(set(denied)))})" if denied else ""))
    if context_lost:
        print("context_lost: true (round ran without its parent's context; resubmit with the full packet)")
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
