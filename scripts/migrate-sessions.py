#!/usr/bin/env python3
"""Move an existing repo's evidence into .handoff/sessions/<date>_<claude_session>/.

Until v3.11.0 one run's evidence sat in four places: .handoff/receipts/,
.handoff/receipts/diagram/, .handoff/cost-receipts/ and .handoff/transcripts/.
Nothing reads those folders any more. This is the one-time migration, run once
per repo.

Usage:
    python3 migrate-sessions.py [--repo PATH] [--projects-root PATH]
    python3 migrate-sessions.py [--repo PATH] [--projects-root PATH] --apply

--repo defaults to the current directory. --projects-root defaults to
~/.claude/projects, where Claude Code keeps its session transcripts; it is only
read, and only to recover the session of a receipt that says `none`.

Without --apply it prints the plan and writes nothing. One line per action:

    move <src> -> <dst>
    rewrite <src>: claude_session none -> <id> (matched <transcript file>)
    skip <src>: <reason>
    leave <src>: unrecognised
    delete <src>: .DS_Store

then any `re-render:` commands, then a `summary:` line.

What it does, and what it will not do:

- Every destination is worked out before anything moves, from the legacy
  receipts and from the receipts already under sessions/. A run that was
  interrupted and is started again therefore finishes the job instead of
  misfiling what is left.
- A session's folder is looked up by session id. One that exists keeps its
  name. A session with none takes the date of its earliest legacy receipt, the
  recovered ones included, worked out before the first file is placed, so a
  session with receipts on two dates ends in one folder. `none` stays the one
  bare sessions/none/ folder.
- It never overwrites and never loses a file. A file is installed with a hard
  link, which fails when the target exists, and only then is the source
  removed. A dangling symlink at the target counts as a target. A receipt
  whose target exists stays where it is, and so do the cost receipt, diagram
  and transcripts that belong to it.
- One case changes a receipt rather than moving it. A legacy receipt reading
  `claude_session: none` was saved by make-receipt.py, which printed
  `saved: <absolute path>` into the driver's own Claude Code transcript. When
  exactly one session's transcript holds that line, the one `claude_session`
  line is rewritten to that session's id and the receipt is filed under it.
  Every other field of the block is checked to be unchanged first. Zero or
  several matching sessions: the receipt goes to sessions/none/ as it is.
- Legacy receipts span schemas 3 to 8, so they are read with the validator's
  tolerant block reader and never validated.
- A symlinked file, and any file it does not recognise, stays where it is and is
  reported. The legacy folders are removed only once they are empty.
- Cost receipts rendered before a rewrite still say `none`. The script prints
  the command that re-renders each one, from the folders alone, so the list
  survives an interrupted run.

Exit codes: 0 when nothing was skipped, left behind or failed (a second run
with nothing to do included); 1 otherwise; 2 for bad arguments or no .handoff/.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from handoff_runtime import (  # noqa: E402
    RECEIPT_STAMP, SESSION_ENV, SESSION_ID, EvidencePathError, _validator,
    folder_session, owner_key, receipt_job_ids, run_date, saved_receipts, session_path)

COST_RECEIPT = re.compile(r"^cost-receipt-(.+)\.(?:md|html)$")
# receipt-<stamp>[_-]handoff-session-timeline[@2x].{svg,png}, or receipt-<stamp>.svg
DIAGRAM = re.compile(
    r"^receipt-(\d{8}T\d{6}Z)(?:[_-]handoff-session-timeline(@2x)?\.(svg|png)|\.(svg))$")
TRANSCRIPT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\.html$")
# The same field line extract_block reads. Only used to find the block's lines;
# the result is then checked against extract_block itself.
BLOCK_FIELD = re.compile(r"^([a-z_]+):\s*(.+)$")
SESSION_NONE_LINE = re.compile(r"claude_session:[ \t]*none[ \t]*")
COST_SESSION_LINE = re.compile(r"^- Session:[ \t]*(.*?)[ \t]*$", re.M)


class RewriteError(ValueError):
    """The one-line receipt rewrite could not be made safely."""


def extract_block(text: str):
    # The validator module handoff_runtime already loads, so there is no third loader.
    return _validator().extract_block(text)


def decode(data: bytes) -> str:
    # surrogateescape round-trips every byte, so a receipt with stray bytes
    # still comes back byte for byte outside the one line that changes.
    return data.decode("utf-8", "surrogateescape")


# --- the plan ---------------------------------------------------------------


@dataclass
class Action:
    kind: str                      # move | rewrite | skip | leave | delete
    src: Path
    dst: Optional[Path] = None
    sid: str = ""                  # session id of dst's folder; dst is rebuilt from it and `date` at apply time
    date: Optional[str] = None     # the date a new folder for `sid` takes, fixed at plan time
    parts: tuple = ()
    note: str = ""                 # the reason for a skip, or the transcript a rewrite matched
    new_id: str = ""
    after: Optional[Path] = None   # the legacy receipt this action follows
    receipt: bool = False          # a receipt: when it stays, what follows it stays too
    done: bool = False

    def line(self) -> str:
        if self.kind == "move":
            return f"move {self.src} -> {self.dst}"
        if self.kind == "rewrite":
            return (f"rewrite {self.src}: claude_session none -> {self.new_id} "
                    f"(matched {self.note})")
        if self.kind == "delete":
            return f"delete {self.src}: .DS_Store"
        return f"{self.kind} {self.src}: {self.note}"


@dataclass
class Plan:
    repo: Path
    receipts: list = field(default_factory=list)
    costs: list = field(default_factory=list)
    diagrams: list = field(default_factory=list)
    transcripts: list = field(default_factory=list)
    junk: list = field(default_factory=list)
    removed: list = field(default_factory=list)   # folders cleanup removed

    @property
    def moves(self) -> list:
        # The order apply runs them in.
        return [*self.receipts, *self.costs, *self.diagrams, *self.transcripts]

    @property
    def actions(self) -> list:
        return [*self.moves, *self.junk]

    def count(self, kind: str) -> int:
        return sum(1 for action in self.actions if action.kind == kind)


@dataclass
class Rec:
    """One receipt in the owner rankings: a legacy one, or one under sessions/."""
    stamp: str
    session: str
    jobs: set
    src: Optional[Path] = None      # the legacy file, when it is still legacy
    blocked: Optional[str] = None   # why it stays, so what it owns stays too


def newest_owners(recs: list) -> dict:
    """job id -> the receipt that owns it.

    A job has no session of its own: the newest receipt that lists it owns it.
    owner_key is the ordering job_owner uses, so a transcript lands where the
    readers will look for it. On an exact tie a blocked receipt wins, so a
    transcript is never filed beside an older receipt while the newer one is
    stuck; ranking, rather than first-seen, keeps that independent of which
    list a receipt came from.
    """
    ranked: dict = {}
    for rec in recs:
        rank = (owner_key(rec.stamp, rec.session), rec.blocked is not None)
        for job in rec.jobs:
            if job not in ranked or rank > ranked[job][0]:
                ranked[job] = (rank, rec)
    return {job: rec for job, (_, rec) in ranked.items()}


def classify(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "dir"
    if path.is_file():
        return "junk" if path.name == ".DS_Store" else "file"
    return "other"


class Planner:
    def __init__(self, repo: Path, projects_root: Path, env: Mapping[str, str]):
        self.repo = repo
        self.handoff = repo / ".handoff"
        self.projects_root = projects_root
        self.env = env
        self.plan = Plan(repo)
        self.recs: list = []            # every receipt, for the owner rankings
        self.stamp_sessions: dict = {}  # stamp -> the sessions (ids) holding that stamp
        self.held: dict = {}            # stamp -> why its dependents stay where they are
        self.moving: dict = {}          # stamp -> the legacy receipt that will move
        self.claimed: dict = {}         # destination -> the source that claimed it
        self.dates: dict = {}           # session id -> the date its new folder takes
        self.unreadable: Optional[str] = None
        self.diagram_dir: Optional[Path] = None

    def run(self) -> Plan:
        self.legacy_receipts()
        self.migrated_receipts()
        for stamp, sessions in self.stamp_sessions.items():
            if len(sessions) > 1:
                self.held.setdefault(
                    stamp, f"stamp {stamp} is in more than one session ({', '.join(sorted(sessions))})")
        self.cost_receipts()
        self.diagrams()
        self.transcripts()
        return self.plan

    # --- reading ---

    def scan(self, folder: Path, bucket: list):
        """The entries of a legacy folder, or None when it is absent or not to be read."""
        if not os.path.lexists(folder):
            return None
        if folder.is_symlink():
            bucket.append(Action("skip", folder, note="a symlinked folder is not followed"))
            return None
        if not folder.is_dir():
            bucket.append(Action("leave", folder, note="unrecognised"))
            return None
        try:
            return sorted(folder.iterdir(), key=lambda entry: entry.name)
        except OSError as error:
            bucket.append(Action("skip", folder, note=f"cannot be read: {error}"))
            return None

    def read(self, path: Path) -> Optional[str]:
        try:
            return decode(path.read_bytes())
        except OSError as error:
            # Without this receipt the job owners are unknown, so no transcript moves.
            self.unreadable = self.unreadable or f"{path}: {error}"
            return None

    # --- receipts ---

    def legacy_receipts(self):
        bucket = self.plan.receipts
        entries = self.scan(self.handoff / "receipts", bucket)
        if entries is None:
            return
        found = []   # (stamp, path)
        for entry in entries:
            kind = classify(entry)
            match = RECEIPT_STAMP.match(entry.name)
            if kind == "junk":
                self.plan.junk.append(Action("delete", entry))
            elif kind == "dir" and entry.name == "diagram":
                self.diagram_dir = entry
            elif kind == "symlink":
                bucket.append(Action("skip", entry, note="a symlink is not followed",
                                     receipt=bool(match)))
                if match:
                    self.held[match.group(1)] = f"{entry.name} is a symlink and stays in place"
            elif kind == "file" and match:
                found.append((match.group(1), entry))
            else:
                bucket.append(Action("leave", entry, note="unrecognised"))

        # First read every receipt, so recovery can look for all the none ones in one pass.
        items = []   # (stamp, path, jobs, raw session or None, skip reason or None)
        for stamp, path in found:
            text = self.read(path)
            if text is None:
                items.append((stamp, path, set(), None, "cannot be read"))
                continue
            block = extract_block(text)
            if block is None:
                items.append((stamp, path, set(), None, "no [Handoff session receipt] block"))
                continue
            raw = block.get("claude_session")
            jobs = receipt_job_ids(text)
            if raw == "none" or (raw is not None and SESSION_ID.fullmatch(raw)):
                items.append((stamp, path, jobs, raw, None))
            else:
                why = f"claude_session {raw!r} is neither none nor a session id"
                items.append((stamp, path, jobs, raw or "", why))
                # It still indexes jobs, so a transcript it would own must stay too.
                self.recs.append(Rec(stamp, raw or "", jobs, path, blocked=why))

        recovered, recovery_error = self.recover(
            [path.name for _, path, _, raw, why in items if why is None and raw == "none"])

        # Every session's date is fixed here, before the first place() call. The
        # loop below places receipts one at a time, so a session's first
        # placement would otherwise take a later receipt's date and split the
        # session in two. The earliest stamp wins, recovered ids included; a
        # session that already has a folder keeps that name whatever this says.
        earliest: dict = {}   # session id -> (stamp, receipt file)
        for stamp, path, _, raw, why in items:
            if why is not None:
                continue
            sid = raw
            if raw == "none" and not recovery_error and path.name in recovered:
                sid = recovered[path.name][0]
            if sid != "none" and (sid not in earliest or stamp < earliest[sid][0]):
                earliest[sid] = (stamp, path)
        self.dates = {sid: run_date(path) for sid, (_, path) in earliest.items()}

        for stamp, path, jobs, raw, why in items:
            if why is not None:
                bucket.append(Action("skip", path, note=why, receipt=True))
                self.held[stamp] = f"{path.name} stays in place: {why}"
                continue
            source = None
            sid = raw
            if raw == "none":
                if recovery_error:
                    why = f"claude_session is none and recovery is unreliable: {recovery_error}"
                    bucket.append(Action("skip", path, note=why, receipt=True))
                    self.held[stamp] = f"{path.name} stays in place: {why}"
                    self.recs.append(Rec(stamp, "none", jobs, path, blocked=why))
                    continue
                if path.name in recovered:
                    sid, source = recovered[path.name]
            action = self.place(path, sid, (path.name,), receipt=True)
            if source is not None and action.kind == "move":
                action.kind, action.new_id, action.note = "rewrite", sid, str(source)
            bucket.append(action)
            blocked = action.note if action.kind == "skip" else None
            self.recs.append(Rec(stamp, sid, jobs, path, blocked=blocked))
            self.stamp_sessions.setdefault(stamp, set()).add(sid)
            if blocked:
                self.held[stamp] = f"{path.name} stays in place: {blocked}"
            else:
                self.moving[stamp] = path

    def recover(self, names: list):
        """Which session wrote each none receipt, by the `saved:` line in its transcript.

        Every transcript is read once, whatever the number of receipts. A match
        is only a line starting `saved: ` and then the exact path, because the
        driver's own stderr is the one place that line is printed.
        Returns ({receipt name: (session id, transcript file)}, error or None).
        """
        if not names:
            return {}, None
        prefix = f"saved: {self.repo}/.handoff/receipts/"
        needles = {os.fsencode(prefix + name): name for name in names}
        prefix_bytes = os.fsencode(prefix)
        current = self.env.get(SESSION_ENV) or ""
        found: dict = {name: {} for name in names}
        for path in sorted(self.projects_root.glob("*/*.jsonl")):
            if not path.is_file():
                continue
            # The session running this migration has the path in its own
            # output, which says nothing about who drove the receipt.
            if current and path.stem == current:
                continue
            try:
                with open(path, "rb") as handle:
                    for line in handle:
                        if prefix_bytes not in line:
                            continue
                        for needle, name in needles.items():
                            if needle in line:
                                found[name].setdefault(path.stem, path)
            except OSError as error:
                # One unreadable file could hold a second session, so "exactly one" cannot be shown.
                return {}, f"could not read {path}: {error}"
        recovered = {}
        for name, sessions in found.items():
            if len(sessions) != 1:
                continue
            (stem, path), = sessions.items()
            if SESSION_ID.fullmatch(stem) and stem != "none":
                recovered[name] = (stem, path)
        return recovered, None

    def migrated_receipts(self):
        for (stamp, session), path in saved_receipts(self.repo):
            text = self.read(path)
            if text is None:
                continue
            self.recs.append(Rec(stamp, session, receipt_job_ids(text)))
            if stamp:
                self.stamp_sessions.setdefault(stamp, set()).add(session)

    # --- what follows a receipt ---

    def place(self, src: Path, sid: str, parts: tuple, after=None, receipt=False) -> Action:
        """A move of `src` to sessions/<date>_<sid>/<parts>, or a skip saying why it cannot be.

        The date is the session's, from `self.dates`, so every file of one
        session names the same folder however the moves are ordered.
        """
        date = self.dates.get(sid)
        try:
            dst = session_path(self.repo, sid, *parts, date=date)
        except EvidencePathError as error:
            return Action("skip", src, note=str(error), receipt=receipt)
        # lexists, not exists: a dangling symlink at the target is still a target.
        if os.path.lexists(dst):
            return Action("skip", src, note=f"target exists: {dst}", receipt=receipt)
        if dst in self.claimed:
            return Action("skip", src, receipt=receipt,
                          note=f"{self.claimed[dst]} already maps to {dst}")
        self.claimed[dst] = src
        return Action("move", src, dst=dst, sid=sid, date=date, parts=parts, after=after,
                      receipt=receipt)

    def stamp_target(self, stamp: str):
        """(session id, the legacy receipt to follow, why it must stay) for a stamp's files."""
        if stamp in self.held:
            return None, None, self.held[stamp]
        sessions = self.stamp_sessions.get(stamp)
        if not sessions:
            return "none", None, None
        (sid,) = sessions
        return sid, self.moving.get(stamp), None

    def dependents(self, folder: Path, bucket: list, match_name):
        """Walk a legacy folder in name order, yielding the plain files `match_name`
        recognises. Everything else is added to `bucket` as it is met: .DS_Store
        to the deletions, a symlink as a skip, the rest as left."""
        entries = self.scan(folder, bucket)
        for entry in entries or []:
            kind = classify(entry)
            if kind == "junk":
                self.plan.junk.append(Action("delete", entry))
            elif kind == "symlink":
                bucket.append(Action("skip", entry, note="a symlink is not followed"))
            elif kind == "file" and match_name(entry.name):
                yield entry
            else:
                bucket.append(Action("leave", entry, note="unrecognised"))

    def cost_receipts(self):
        bucket = self.plan.costs
        for entry in self.dependents(self.handoff / "cost-receipts", bucket,
                                     lambda name: COST_RECEIPT.match(name)):
            stamp = COST_RECEIPT.match(entry.name).group(1)
            sid, after, why = self.stamp_target(stamp)
            bucket.append(Action("skip", entry, note=why) if why else
                          self.place(entry, sid, (entry.name,), after))

    def diagrams(self):
        if self.diagram_dir is None:
            return
        bucket = self.plan.diagrams
        for entry in self.dependents(self.diagram_dir, bucket,
                                     lambda name: DIAGRAM.match(name)):
            match = DIAGRAM.match(entry.name)
            stamp, retina = match.group(1), match.group(2) or ""
            name = f"session-timeline-{stamp}{retina}.{match.group(3) or match.group(4)}"
            sid, after, why = self.stamp_target(stamp)
            bucket.append(Action("skip", entry, note=why) if why else
                          self.place(entry, sid, ("diagram", name), after))

    def transcripts(self):
        owners = newest_owners(self.recs)
        bucket = self.plan.transcripts
        for entry in self.dependents(self.handoff / "transcripts", bucket,
                                     lambda name: TRANSCRIPT.match(name)):
            job = TRANSCRIPT.match(entry.name).group(1)
            rec = owners.get(job)
            if self.unreadable:
                bucket.append(Action(
                    "skip", entry,
                    note=f"job owners are unknown: could not read {self.unreadable}"))
            elif rec is not None and rec.blocked:
                bucket.append(Action(
                    "skip", entry,
                    note=f"its newest receipt (stamp {rec.stamp}, session {rec.session}) "
                         f"stays in place: {rec.blocked}"))
            else:
                bucket.append(self.place(
                    entry, rec.session if rec else "none", (f"transcript-{job}.html",),
                    rec.src if rec else None))


def build_plan(repo: Path, projects_root: Path, env: Mapping[str, str]) -> Plan:
    """The whole inventory, with nothing written. `repo` is already resolved."""
    return Planner(repo, projects_root, env).run()


# --- carrying it out ---------------------------------------------------------


def replace_session_line(text: str, new_id: str) -> str:
    """`text` with the one `claude_session: none` line of the receipt block set to `new_id`.

    Only a line inside the block counts, found the way extract_block walks it:
    a decoy line further down the file is left alone. More or fewer than one
    match raises. Every other byte, line endings included, is kept.
    """
    header = _validator().RECEIPT_HEADER
    start = text.find(header)
    if start < 0:
        raise RewriteError("no receipt block")
    position = start + len(header)
    block_lines = []   # (offset, line without its ending, the ending)
    seen_field = False
    for raw in text[position:].splitlines(keepends=True):
        stripped = raw.strip()
        if not stripped:
            if seen_field:
                break
        elif BLOCK_FIELD.match(stripped):
            seen_field = True
            body = raw.splitlines()[0]
            block_lines.append((position, body, raw[len(body):]))
        else:
            break
        position += len(raw)
    hits = [item for item in block_lines if SESSION_NONE_LINE.fullmatch(item[1])]
    if len(hits) != 1:
        raise RewriteError(
            f"expected exactly one 'claude_session: none' line in the block, found {len(hits)}")
    offset, body, ending = hits[0]
    return text[:offset] + f"claude_session: {new_id}" + ending + text[offset + len(body) + len(ending):]


def unlink_source(src: Path):
    try:
        os.unlink(src)
    except OSError as error:
        raise OSError(f"linked into place, but the source could not be removed ({error}); "
                      f"remove {src} by hand") from error


def install(src: Path, dst: Path):
    """Move by link, then unlink. Not a rename: rename silently replaces an
    existing target, and os.link fails on one, even one that appeared after the
    plan was made."""
    if os.path.islink(src):
        raise OSError("the source became a symlink")
    os.link(src, dst)
    unlink_source(src)


def rewrite(src: Path, dst: Path, new_id: str):
    """Install a copy of `src` with its claude_session set, and only then remove `src`."""
    if os.path.islink(src):
        raise OSError("the source became a symlink")
    old_text = decode(src.read_bytes())
    new_text = replace_session_line(old_text, new_id)
    # Nothing but claude_session may differ in the block, the schema version included.
    expected = {**(extract_block(old_text) or {}), "claude_session": new_id}
    if extract_block(new_text) != expected:
        raise RewriteError("the rewritten block differs from the original in more than claude_session")
    dst.parent.mkdir(parents=True, exist_ok=True)
    # The temp file is in the target folder, so the final os.link stays on one
    # filesystem; os.link then refuses an existing target, as install() does.
    handle, name = tempfile.mkstemp(dir=dst.parent, prefix=".migrate-", suffix=".tmp")
    temp = Path(name)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(new_text.encode("utf-8", "surrogateescape"))
            out.flush()
            os.fsync(out.fileno())
        shutil.copystat(src, temp)
        os.link(temp, dst)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
    unlink_source(src)


def apply_plan(plan: Plan) -> list:
    """Run the moves in order, then clean up. Returns one message per failure."""
    failures = []
    stayed: set = set()   # legacy receipts that did not install
    for action in plan.moves:
        if action.kind not in ("move", "rewrite"):
            continue
        if action.after in stayed:
            action.kind = "skip"
            action.note = f"{action.after.name} did not move, so this stays with it"
            print(action.line())
            continue
        try:
            # Rebuilt here: a symlink may have appeared since the plan was made.
            dst = session_path(plan.repo, action.sid, *action.parts, date=action.date)
            if action.kind == "rewrite":
                rewrite(action.src, dst, action.new_id)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                install(action.src, dst)
            action.done = True
        except (OSError, ValueError) as error:
            failures.append(f"FAILED {action.kind} {action.src}: {error}")
            print(failures[-1])
            if action.receipt:
                stayed.add(action.src)
    cleanup(plan, failures)
    return failures


def cleanup(plan: Plan, failures: list):
    for action in plan.junk:
        try:
            if classify(action.src) == "junk":
                os.unlink(action.src)
                action.done = True
        except OSError as error:
            failures.append(f"FAILED delete {action.src}: {error}")
            print(failures[-1])
    handoff = plan.repo / ".handoff"
    for folder in (handoff / "receipts" / "diagram", handoff / "receipts",
                   handoff / "cost-receipts", handoff / "transcripts"):
        if folder.is_dir() and not folder.is_symlink():
            try:
                folder.rmdir()   # fails unless empty, which is the whole condition
                plan.removed.append(folder)
            except OSError:
                pass


# --- the reminders -------------------------------------------------------------


def reminders(repo: Path) -> list:
    """A re-render command for each cost receipt that still says `none` beside a receipt that names a session."""
    sessions = repo / ".handoff" / "sessions"
    if sessions.is_symlink() or not sessions.is_dir():
        return []
    render = SCRIPT_DIR / "render-cost-receipt.py"
    lines = []
    for folder in sorted(sessions.iterdir(), key=lambda entry: entry.name):
        # folder_session is `none` for the bare folder and None for a name that is
        # not a session folder; neither holds a cost receipt to re-render.
        if (folder_session(folder.name) in (None, "none")
                or folder.is_symlink() or not folder.is_dir()):
            continue
        for cost in sorted(folder.glob("cost-receipt-*.md")):
            stem = cost.name[len("cost-receipt-"):-len(".md")]
            receipt = folder / f"receipt-{stem}.md"
            if cost.is_symlink() or not cost.is_file() or not receipt.is_file():
                continue
            try:
                text = cost.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            line = COST_SESSION_LINE.search(text)
            if line and line.group(1) == "`none`":
                lines.append(f"re-render: python3 {shlex.quote(str(render))} "
                             f"{shlex.quote(str(receipt))} --repo {shlex.quote(str(repo))} --no-open")
    return lines


# --- command line --------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Move a repo's legacy evidence folders into .handoff/sessions/<date>_<claude_session>/.")
    parser.add_argument("--repo", default=".", help="the repo to migrate (default: the current directory)")
    parser.add_argument("--apply", action="store_true",
                        help="carry the plan out; without it the plan is printed and nothing is written")
    parser.add_argument("--projects-root", default="~/.claude/projects",
                        help="where Claude Code keeps session transcripts, read to recover a none session")
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    handoff = repo / ".handoff"
    if handoff.is_symlink():
        print(f"ERROR: {handoff} is a symlink; refusing to migrate through it", file=sys.stderr)
        return 2
    if not handoff.is_dir():
        print(f"ERROR: no .handoff/ folder in {repo}", file=sys.stderr)
        return 2

    plan = build_plan(repo, Path(args.projects_root).expanduser().absolute(), os.environ)
    for action in plan.actions:
        print(action.line())
    failures = apply_plan(plan) if args.apply else []
    for line in reminders(repo):
        print(line)

    problems = plan.count("skip") + plan.count("leave") + len(failures)
    counts = (f"{plan.count('move')} move, {plan.count('rewrite')} rewrite, "
              f"{plan.count('skip')} skip, {plan.count('leave')} leave, {plan.count('delete')} delete")
    if args.apply:
        done = {kind: sum(1 for a in plan.actions if a.kind == kind and a.done)
                for kind in ("move", "rewrite", "delete")}
        print(f"summary: applied {done['move']} move, {done['rewrite']} rewrite, "
              f"{done['delete']} delete; removed {len(plan.removed)} empty folder(s); "
              f"{plan.count('skip')} skipped, {plan.count('leave')} left, {len(failures)} failed")
    else:
        print(f"summary: {counts}. Dry run: nothing was written; pass --apply to carry it out.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
