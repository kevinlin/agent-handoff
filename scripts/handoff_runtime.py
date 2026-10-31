"""Shared subprocess boundaries for Handoff helper scripts."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Mapping


def clean_claude_env(env: Mapping[str, str]) -> Dict[str, str]:
    """Remove host-injected Claude variables before starting a child CLI.

    Nested Claude Code hosts can inject provider URLs, credentials, and
    session markers that override the user's normal first-party CLI login.
    Handoff child processes must resolve authentication exactly as a fresh
    terminal invocation would.
    """

    cleaned = {
        key: value
        for key, value in env.items()
        if not key.startswith("ANTHROPIC_") and not key.startswith("CLAUDE_CODE_")
    }
    cleaned["CLAUDECODE"] = ""
    return cleaned


PAYLOAD_PATTERN = re.compile(
    r'(<script id="handoff-payload" type="application/json">)(.*?)(</script>)', re.S)


def read_meta(job_dir: Path) -> Dict[str, str]:
    """Parse a job's `key=value` meta file. A missing file is not an error:
    resumed jobs legitimately omit backend and role."""
    meta: Dict[str, str] = {}
    path = job_dir / "meta"
    if not path.is_file():
        return meta
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            meta[key.strip()] = value.strip()
    return meta


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError, PermissionError) as error:
        return isinstance(error, PermissionError)
    return True


def job_state(job_dir: Path) -> str:
    """Mirror delegate-job.sh job_state(). Absence of exit_code is never
    RUNNING on its own: a worker that died without writing one is FAILED."""
    if (job_dir / "cancelled").is_file():
        return "CANCELLED"
    exit_code = job_dir / "exit_code"
    if exit_code.is_file():
        return "DONE" if exit_code.read_text(encoding="utf-8").strip() == "0" else "FAILED"
    pid_file = job_dir / "pid"
    if pid_file.is_file():
        raw = pid_file.read_text(encoding="utf-8").strip()
        if raw.isdigit() and _pid_alive(int(raw)):
            return "RUNNING"
    return "FAILED"


def inject(template: str, payload: dict) -> str:
    """Replace the payload slot. Escaping `<` as \\u003c keeps any closing-script
    sequence inside captured output from ending the element early. This guards
    the element boundary only; DOM insertion is the page's own boundary."""
    if not PAYLOAD_PATTERN.search(template):
        raise ValueError("template has no handoff-payload slot")
    encoded = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    # A lambda, not a replacement string: re.sub interprets backslashes in a
    # replacement, and the encoded payload is full of them.
    return PAYLOAD_PATTERN.sub(
        lambda m: m.group(1) + encoded + m.group(3), template, count=1)


# --- Where one session's evidence lives: .handoff/sessions/<date>_<session_id>/ ---
#
# One module owns the folder rules, so a writer and a reader (or the standalone
# renderers and the session page) can never derive the same path differently.

# The variable Claude Code sets in Bash, hook and MCP subprocesses.
SESSION_ENV = "CLAUDE_CODE_SESSION_ID"

# Used with fullmatch. The leading alphanumeric rules out ".", "..", hidden
# names and a leading "-", so an id is always one ordinary path segment.
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# Moved here from render-cost-receipt.py, which keeps the name as an alias.
RECEIPT_STAMP = re.compile(r"^receipt-(\d{8}T\d{6}Z)\.md$")


class EvidencePathError(ValueError):
    """An unsafe session id or path part, or a symlink inside the evidence tree.

    A ValueError so the callers' existing `except ValueError` handlers catch it.
    """


# ASCII digits only: `\d` would also take other scripts' digits, which are not a date.
_DATE = re.compile(r"[0-9]{8}")
_DATED_FOLDER = re.compile(r"[0-9]{8}_")


def folder_session(name: str) -> str | None:
    """The session id a folder name under `sessions/` belongs to, else None.

    `none` is the one bare folder; every other session folder is
    `<8 digits>_<id>` with an id that fullmatches SESSION_ID and is not `none`.
    None means "not a session folder", so a reader skips it.

    Why one function: the folder name carries a date that only sorts the
    listing, so ranking and ownership must read the id from here and never
    from the raw name, or two callers could disagree on what a folder is.
    """
    if name == "none":
        return "none"
    if not _DATED_FOLDER.match(name):
        return None
    session_id = name[len("00000000_"):]
    if session_id == "none" or not SESSION_ID.fullmatch(session_id):
        return None
    return session_id


def session_path(repo, session_id: str, *parts: str, date: str | None = None) -> Path:
    """`<repo.resolve()>/.handoff/sessions/<folder>/<parts...>`, creating nothing.

    `<folder>` is `none` for `none`, else the first name (sorted) under
    `sessions/` that belongs to `session_id`, else `<date>_<session_id>`. A
    `None` date means today in UTC.

    Every writer builds its path here, so this is where an id or a part that
    came from a receipt or the environment is checked. The raw `session_id` is
    checked, never a stripped copy: validate() strips, and " abc" must not reach
    a path as "abc". Any component from `.handoff` down that already exists as a
    symlink, dangling or not, is refused: a write through it would land
    wherever it points. Components above `.handoff` are deliberately not
    checked, because on macOS a temp repo lives under /var -> /private/var.
    """
    if not isinstance(session_id, str) or not (
            session_id == "none" or SESSION_ID.fullmatch(session_id)):
        raise EvidencePathError(
            f"not a usable session id: {session_id!r}; it must be 'none' or match "
            f"{SESSION_ID.pattern}")
    if date is not None and not (isinstance(date, str) and _DATE.fullmatch(date)):
        raise EvidencePathError(f"not a usable folder date: {date!r}; it must be eight digits")
    for part in parts:
        if (not isinstance(part, str) or part in ("", ".", "..")
                or any(char in part for char in ("/", "\\", "\x00"))):
            raise EvidencePathError(f"not a single safe path segment: {part!r}")
    handoff = Path(repo).resolve() / ".handoff"
    sessions = handoff / "sessions"
    # Before `sessions` is listed: a symlinked folder must be refused, not read through.
    for component in (handoff, sessions):
        if component.is_symlink():
            raise EvidencePathError(f"refusing a symlink in the evidence path: {component}")
    if session_id == "none":
        folder = "none"
    else:
        # Looked up by id first, so one session keeps one folder: a later run on
        # another date must not open a second one. Only a session with no folder
        # takes a new date, and that name then never changes.
        known = (sorted(entry.name for entry in sessions.iterdir())
                 if sessions.is_dir() else [])
        folder = next((name for name in known if folder_session(name) == session_id), None)
        if folder is None:
            stamp = date or datetime.now(timezone.utc).strftime("%Y%m%d")
            folder = f"{stamp}_{session_id}"
    path = sessions / folder
    chain = [path]
    for part in parts:
        path = path / part
        chain.append(path)
    for component in chain:
        if component.is_symlink():
            raise EvidencePathError(f"refusing a symlink in the evidence path: {component}")
    return path


def run_stamp(receipt_path) -> str:
    """The run's stamp: the saved name's own, else the file stem (as notes.md -> notes).

    Names a run's artifacts, so one receipt always yields the same file names.
    """
    path = Path(receipt_path)
    match = RECEIPT_STAMP.match(path.name)
    return match.group(1) if match else path.stem


def run_date(receipt_path) -> str | None:
    """The `YYYYMMDD` of the receipt name's stamp, else None (as for notes.md).

    A new session folder takes this date, so the folder and the receipt that
    created it agree. None lets session_path fall back to today.
    """
    match = RECEIPT_STAMP.match(Path(receipt_path).name)
    date = match.group(1)[:8] if match else None
    return date if date is not None and _DATE.fullmatch(date) else None


_VALIDATOR = None


def _validator():
    # validate-receipt.py has a hyphen, so it cannot be imported by name; this is
    # how make-receipt.py loads it too. Loaded on first use and then kept: a
    # session page asks once per job per request.
    global _VALIDATOR
    if _VALIDATOR is None:
        spec = importlib.util.spec_from_file_location(
            "validate_receipt", Path(__file__).resolve().parent / "validate-receipt.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _VALIDATOR = module
    return _VALIDATOR


def receipt_job_ids(text: str) -> set[str]:
    """Job ids from every `*_job_durations` field of the receipt block in `text`.

    Schema-tolerant on purpose: stored receipts span schemas 3 to 8, so this
    reads only those fields, needs no job directory, and never calls validate()
    or the cost reader's fail-closed loader, which insist on v8. Text with no
    block indexes nothing.
    """
    block = _validator().extract_block(text) or {}
    ids: set[str] = set()
    for key, value in block.items():
        if not key.endswith("_job_durations") or value == "none":
            continue
        for entry in value.split(";"):
            job_id, separator, _ = entry.partition("=")
            if separator and job_id.strip():
                ids.add(job_id.strip())
    return ids


def owner_key(stamp: str, session_id: str) -> tuple[str, str]:
    """The one ordering that picks the newest of several receipts: a greater
    stamp wins and ties go to the greater session id. A receipt whose name has
    no stamp uses "" and so sorts before every stamped one."""
    return (stamp, session_id)


def saved_receipts(repo) -> list[tuple[tuple[str, str], Path]]:
    """Every `sessions/*/receipt-*.md`, oldest first by `owner_key`.

    One listing for the two readers that rank receipts (`job_owner` and the cost
    renderer's default input), so they cannot rank differently. A folder that
    `folder_session` rejects is skipped: it is not a session folder. The key
    carries the session id `folder_session` returns, never the folder name, so
    a folder's date has no effect on which receipt is newest.
    """
    root = Path(repo).resolve() / ".handoff" / "sessions"
    found: list[tuple[tuple[str, str], Path]] = []
    if not root.is_dir():
        return found
    for folder in root.iterdir():
        session = folder_session(folder.name)
        if session is None or not folder.is_dir():
            continue
        for path in folder.glob("receipt-*.md"):
            if not path.is_file():
                continue
            match = RECEIPT_STAMP.match(path.name)
            found.append((owner_key(match.group(1) if match else "", session), path))
    found.sort(key=lambda item: (item[0], item[1].name))
    return found


def job_owner(repo, job_id: str) -> str | None:
    """The session id of the newest receipt that indexes `job_id`, else None.

    A job has no session of its own: a receipt claims it by listing it. Every
    receipt is read, and one that cannot be read raises OSError. Skipping it
    would read as "does not index this job" and could file a transcript under
    an older session. A readable file with no block indexes nothing.
    """
    owner = None
    for (_stamp, session_id), path in saved_receipts(repo):
        # errors="replace": a receipt with stray bytes still names its jobs in ASCII.
        if job_id in receipt_job_ids(path.read_text(encoding="utf-8", errors="replace")):
            owner = session_id  # oldest first, so the last match is the newest
    return owner


def transcript_path(repo, job_id: str, env: Mapping[str, str] = os.environ) -> Path:
    """Where `job_id`'s transcript page goes. Both renderers call this, so the
    session page and the standalone command write and look in one place.

    Owner: the newest receipt indexing the job. Mid-run, before any receipt
    exists: the current Claude Code session, and `none` outside one. That
    fallback is attribution at render time, not proof of which session submitted
    the job.
    """
    owner = job_owner(repo, job_id)
    if owner is None:
        owner = env.get(SESSION_ENV) or "none"
    return session_path(repo, owner, f"transcript-{job_id}.html")


def legacy_layout_note(repo) -> str | None:
    """One line for a repo that still has the pre-3.11 evidence folders.

    Nothing reads those folders any more, so a reader that finds nothing under
    sessions/ points here. The migration path is computed from this file, so it
    names the installed copy of the script rather than a source checkout.
    """
    handoff = Path(repo).resolve() / ".handoff"
    # The only place in scripts/ that names the legacy folders.
    legacy = [name for name in ("receipts", "cost-receipts", "transcripts")
              if (handoff / name).is_dir()]
    if not legacy:
        return None
    script = Path(__file__).resolve().parent / "migrate-sessions.py"
    named = ", ".join(f".handoff/{name}" for name in legacy)
    return (f"legacy evidence folders found ({named}); nothing reads them any more. "
            f"Move them into .handoff/sessions/ with: python3 {shlex.quote(str(script))} "
            f"--repo {shlex.quote(str(handoff.parent))} --apply")
