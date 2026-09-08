"""Shared subprocess boundaries for Handoff helper scripts."""

from __future__ import annotations

import json
import os
import re
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
    """Mirror delegate-codex.sh job_state(). Absence of exit_code is never
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
