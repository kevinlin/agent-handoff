#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Agent Handoff installer

Usage:
  bash install.sh [--dry-run]
  bash install.sh --status
  bash install.sh --config [handoff-setup-ui.py args...]
  bash install.sh --config-cli [handoff-setup.py args...]

Installs to ~/.claude/skills/agent-handoff.

--status compares the installed copy's .install-meta commit against this
repository's HEAD so a stale copy is visible before it causes confusion.

--config opens the localhost-only single-page setup UI; any extra arguments
are passed to handoff-setup-ui.py. --config-cli keeps the terminal fallback.
USAGE
}

DRY_RUN="false"
STATUS="false"
BACKUP_KEEP=3
DEST="$HOME/.claude/skills/agent-handoff"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    --status)
      STATUS="true"
      shift
      ;;
    --config)
      shift
      exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/handoff-setup-ui.py" "$@"
      ;;
    --config-cli)
      shift
      exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/handoff-setup.py" --interactive "$@"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ ! -f "$ROOT/SKILL.md" ]; then
  echo "ERROR: install.sh must run from the agent-handoff repository." >&2
  exit 1
fi

source_commit() {
  if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "$ROOT" rev-parse HEAD
  else
    echo "unknown"
  fi
}

write_install_meta() {
  {
    printf 'source_commit=%s\n' "$(source_commit)"
    printf 'installed_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >"$1"
}

if [ "$STATUS" = "true" ]; then
  head_commit="$(source_commit)"
  echo "repo HEAD: $head_commit"
  meta="$DEST/.install-meta"
  if [ ! -d "$DEST" ]; then
    echo "MISSING  $DEST"
  elif [ ! -f "$meta" ]; then
    echo "UNKNOWN  $DEST (no install meta)"
  else
    installed_commit="$(sed -n 's/^source_commit=//p' "$meta")"
    if [ "$installed_commit" = "$head_commit" ]; then
      echo "CURRENT  $DEST ($installed_commit)"
    else
      echo "STALE    $DEST (installed $installed_commit, repo at $head_commit)"
    fi
  fi
  exit 0
fi

copy_payload() {
  # Package only the skill payload. Tracked files when git is available, so
  # local scratch files and logs never ship into the user's skills directory.
  local dest="$1"
  if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "$ROOT" ls-files -z -- . ':!:.github' \
      | (cd "$ROOT" && tar -cf - --null -T -) \
      | tar -xf - -C "$dest"
  else
    (cd "$ROOT" && find . -type f \
      ! -path './.git/*' \
      ! -path './.github/*' \
      ! -name '.DS_Store' \
      -print0 | tar -cf - --null -T -) \
      | tar -xf - -C "$dest"
  fi
  write_install_meta "$dest/.install-meta"
}

if [ "$ROOT" = "$DEST" ]; then
  echo "Already installed at $DEST"
  exit 0
fi

echo "Install Agent Handoff -> $DEST"
if [ "$DRY_RUN" = "true" ]; then
  exit 0
fi

mkdir -p "$(dirname "$DEST")"
if [ -e "$DEST" ]; then
  backup="$DEST.backup.$(date +%Y%m%d%H%M%S)"
  echo "Existing install found. Moving it to $backup"
  mv "$DEST" "$backup"
  ls -dt "$DEST".backup.* 2>/dev/null | tail -n +"$((BACKUP_KEEP + 1))" \
    | while IFS= read -r old_backup; do
        rm -rf "$old_backup" # risk-ok: prunes only our own timestamped backups beyond BACKUP_KEEP
      done
fi

mkdir -p "$DEST"
copy_payload "$DEST"

echo "Done. Try: hand the mechanical parts off to Codex in the background, then full-review."
