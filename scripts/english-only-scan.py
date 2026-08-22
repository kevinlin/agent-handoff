#!/usr/bin/env python3
"""Fail if any tracked file contains CJK text. This repo is English-only."""

from __future__ import annotations

import re
import subprocess
import sys

# ASCII escapes on purpose, so this file stays clean under its own scan.
CJK = re.compile("[\u3000-\u303f\u3040-\u30ff\u4e00-\u9fff\uff00-\uffef]")


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, check=True
    ).stdout.decode()
    return [name for name in out.split("\0") if name]


def main() -> int:
    try:
        files = tracked_files()
    except (OSError, subprocess.CalledProcessError):
        return 0  # not a git checkout: nothing tracked to scan

    hits = []
    for name in files:
        try:
            text = open(name, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if CJK.search(line):
                hits.append(f"{name}:{number}: {line.strip()[:120]}")

    for hit in hits:
        print(hit)
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
