#!/usr/bin/env python3
"""Read, merge, validate, and atomically write handoff configuration.

This is deliberately a TOML subset implementation.  It splits the document
into raw section chunks, then parses only top-level metadata, [routing], and
the identity sections under hosts.claude_code.  Every other chunk -- comments,
[routing], unknown sections -- is preserved byte-for-byte, never reformatted.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple


HOST = "claude_code"
CORE_IDENTITIES = ("deep_reasoner", "fast_worker", "arbiter")
# Optional add-on identities. Appended, never inserted: emit_host_sections
# orders sections by IDENTITIES, so an existing three-identity document must
# keep writing back byte-identically.
OPTIONAL_IDENTITIES = ("e2e_specifier", "e2e_verifier")
IDENTITIES = CORE_IDENTITIES + OPTIONAL_IDENTITIES


def identities_for(with_e2e: bool) -> Tuple[str, ...]:
    """The identity set a setup run configures."""

    return IDENTITIES if with_e2e else CORE_IDENTITIES


def ordered(names: Iterable[str]) -> List[str]:
    """``names`` in canonical identity order."""

    return [identity for identity in IDENTITIES if identity in set(names)]


IDENTITY_FIELD_ORDER = ("backend", "model", "effort", "verified", "verified_at")
BACKENDS = ("claude", "codex")
V1_UPGRADE_MESSAGE = (
    'Detected a schema v1 config. Rerun /agent-handoff config to upgrade '
    "(setup replaces it with a schema v2 document and backs up the old file)."
)
SUBSET_GUIDE = "See docs/config-schema.md#supported-toml-subset."
SECTION_RE = re.compile(r"^[ \t]*\[([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)\][ \t]*(?:#.*)?(?:\r?\n)?$")
ARRAY_SECTION_RE = re.compile(r"^[ \t]*\[\[([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)\]\][ \t]*(?:#.*)?(?:\r?\n)?$")
KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
INTEGER_RE = re.compile(r"^[+-]?(?:0|[1-9](?:_?[0-9])*)$")


DEFAULTS: Dict[str, Any] = {
    "schema_version": 2,
    "revision": 0,
    "routing": {"always_on_host_rules": False},
    "hosts": {HOST: {"identities": {}}},
}


class ConfigError(Exception):
    """Base class for readable configuration failures."""


class ConfigParseError(ConfigError):
    """The input uses invalid or unsupported TOML syntax."""

    def __init__(self, line: int, column: int, message: str):
        self.line = line
        self.column = column
        super().__init__(f"line {line}, column {column}: {message} {SUBSET_GUIDE}")


class ConfigValidationError(ConfigError):
    """The parsed values do not satisfy schema v2."""


class ConfigLockError(ConfigError):
    """The configuration lock could not be acquired safely."""


@dataclass(frozen=True)
class SectionChunk:
    """One raw document chunk, including its table header when present."""

    name: Optional[str]
    text: str
    start_line: int


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _legacy_v1_error(path: Optional[Path] = None) -> ConfigValidationError:
    location = str(path) if path is not None else "<input>"
    return ConfigValidationError(f"{V1_UPGRADE_MESSAGE} File: {location}")


def split_sections(text: str) -> List[SectionChunk]:
    """Split on standard table-header lines without interpreting body text."""

    lines = text.splitlines(keepends=True)
    if not lines:
        return [SectionChunk(None, "", 1)]

    chunks: List[SectionChunk] = []
    current_name: Optional[str] = None
    current_lines: List[str] = []
    start_line = 1

    interpreted = ("routing", f"hosts.{HOST}.identities")
    for line_number, line in enumerate(lines, 1):
        stripped = line.lstrip(" \t")
        match = ARRAY_SECTION_RE.match(line) or SECTION_RE.match(line)
        if stripped.startswith("[["):
            name = match.group(1) if match else ""
            if any(name == owned or name.startswith(f"{owned}.") for owned in interpreted):
                raise ConfigParseError(line_number, len(line) - len(stripped) + 1, "array-of-tables headers are not supported.")
        if match:
            if current_lines:
                chunks.append(SectionChunk(current_name, "".join(current_lines), start_line))
            current_name = match.group(1)
            current_lines = [line]
            start_line = line_number
        else:
            current_lines.append(line)

    if current_lines:
        chunks.append(SectionChunk(current_name, "".join(current_lines), start_line))
    return chunks


def _strip_comment(raw: str) -> str:
    in_string = False
    escaped = False
    for index, char in enumerate(raw):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "#":
            return raw[:index]
    return raw


def _parse_value(raw: str, line: int, column: int) -> Any:
    value = _strip_comment(raw).strip()
    leading = len(raw) - len(raw.lstrip())
    value_column = column + leading
    if not value:
        raise ConfigParseError(line, value_column, "missing value.")
    if '"""' in value or "'''" in value:
        raise ConfigParseError(line, value_column, "multiline strings are not supported.")
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise ConfigParseError(line, value_column + error.pos, "invalid double-quoted string.") from None
        if not isinstance(parsed, str):
            raise ConfigParseError(line, value_column, "only double-quoted strings are supported here.")
        return parsed
    if value in ("true", "false"):
        return value == "true"
    if INTEGER_RE.fullmatch(value):
        return int(value.replace("_", ""))
    raise ConfigParseError(line, value_column, "value is outside the supported TOML subset.")


def _parse_assignments(chunk: SectionChunk) -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    lines = chunk.text.splitlines()
    body_offset = 1 if chunk.name is not None else 0
    for offset, raw_line in enumerate(lines[body_offset:], body_offset):
        line_number = chunk.start_line + offset
        content = _strip_comment(raw_line).strip()
        if not content:
            continue
        if '"""' in content or "'''" in content:
            column = raw_line.find('"""') if '"""' in raw_line else raw_line.find("'''")
            raise ConfigParseError(line_number, column + 1, "multiline strings are not supported.")
        if "=" not in content:
            column = len(raw_line) - len(raw_line.lstrip()) + 1
            raise ConfigParseError(line_number, column, "expected a key = value assignment.")
        left, right = raw_line.split("=", 1)
        key = left.strip()
        key_column = raw_line.find(key) + 1
        if "." in key:
            raise ConfigParseError(line_number, key_column + key.find("."), "dotted-key assignments are not supported.")
        if not KEY_RE.fullmatch(key):
            raise ConfigParseError(line_number, key_column, "only bare keys are supported.")
        if key in values:
            raise ConfigParseError(line_number, key_column, f"duplicate key {key!r}.")
        values[key] = _parse_value(right, line_number, raw_line.find("=") + 2)
    return values


def _deep_merge(base: MutableMapping[str, Any], overlay: Mapping[str, Any]) -> MutableMapping[str, Any]:
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), MutableMapping):
            _deep_merge(base[key], value)
        else:
            base[key] = _copy(value)
    return base


def parse_config(text: str, host: str = HOST, *, path: Optional[Path] = None) -> Dict[str, Any]:
    """Parse schema metadata, routing, and only ``host`` identity sections."""

    result: Dict[str, Any] = {"hosts": {host: {"identities": {}}}}
    seen_sections = set()
    identity_prefix = f"hosts.{host}.identities."
    chunks = split_sections(text)

    if any(
        chunk.name and re.match(r"^hosts\.[^.]+\.roles(?:\.|$)", chunk.name)
        for chunk in chunks
    ):
        raise _legacy_v1_error(path)

    for chunk in chunks:
        if chunk.name is None:
            top = _parse_assignments(chunk)
            schema_version = top.get("schema_version")
            if isinstance(schema_version, int) and not isinstance(schema_version, bool) and schema_version == 1:
                raise _legacy_v1_error(path)
            for key in ("schema_version", "revision"):
                if key in top:
                    result[key] = top[key]
        elif chunk.name == "routing":
            if chunk.name in seen_sections:
                raise ConfigParseError(chunk.start_line, 1, "duplicate [routing] section.")
            seen_sections.add(chunk.name)
            result["routing"] = _parse_assignments(chunk)
        elif chunk.name.startswith(identity_prefix):
            identity = chunk.name[len(identity_prefix):]
            if "." in identity or not identity:
                raise ConfigParseError(chunk.start_line, 1, f"invalid owned identity section [{chunk.name}].")
            if chunk.name in seen_sections:
                raise ConfigParseError(chunk.start_line, 1, f"duplicate [{chunk.name}] section.")
            seen_sections.add(chunk.name)
            result["hosts"][host]["identities"][identity] = _parse_assignments(chunk)
    return result


def _validate_data(
    data: Dict[str, Any],
    host: str,
    *,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    schema_version = data.get("schema_version")
    if isinstance(schema_version, int) and not isinstance(schema_version, bool) and schema_version == 1:
        raise _legacy_v1_error(path)
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version != 2:
        raise ConfigValidationError("schema_version must be integer 2")
    revision = data.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ConfigValidationError("revision must be a non-negative integer")
    routing = data.get("routing", {})
    if not isinstance(routing, Mapping):
        raise ConfigValidationError("routing must be a table")
    always_on = routing.get("always_on_host_rules", False)
    if not isinstance(always_on, bool):
        raise ConfigValidationError("routing.always_on_host_rules must be a boolean")
    try:
        identities = data["hosts"][host]["identities"]
    except (KeyError, TypeError):
        raise ConfigValidationError(f"hosts.{host}.identities must be a table") from None
    if not isinstance(identities, Mapping):
        raise ConfigValidationError(f"hosts.{host}.identities must be a table")
    for identity, fields in identities.items():
        if identity not in IDENTITIES:
            raise ConfigValidationError(f"unsupported identity in hosts.{host}: {identity!r}")
        backend = fields.get("backend")
        if backend not in BACKENDS:
            raise ConfigValidationError(
                f"hosts.{host}.identities.{identity}.backend must be one of {', '.join(BACKENDS)}"
            )
        for required in ("model", "effort"):
            if required not in fields or not isinstance(fields[required], str) or not fields[required].strip():
                raise ConfigValidationError(
                    f"hosts.{host}.identities.{identity}.{required} must be a non-empty string"
                )
        if "verified" in fields and not isinstance(fields["verified"], bool):
            raise ConfigValidationError(f"hosts.{host}.identities.{identity}.verified must be a boolean")
        if "verified_at" in fields and not isinstance(fields["verified_at"], str):
            raise ConfigValidationError(f"hosts.{host}.identities.{identity}.verified_at must be a string")
    return data


def validate_config(text: str, host: str = HOST, *, path: Optional[Path] = None) -> Dict[str, Any]:
    """Parse and validate the schema-v2 values visible to ``host``."""

    return _validate_data(parse_config(text, host, path=path), host, path=path)


def _format_value(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    raise ConfigValidationError(f"cannot emit unsupported value {value!r}")


def emit_host_sections(identities: Mapping[str, Mapping[str, Any]], host: str = HOST) -> str:
    """Return canonical identity sections for one host."""

    blocks: List[str] = []
    for identity in ordered(identities):
        fields = identities[identity]
        lines = [f"[hosts.{host}.identities.{identity}]"]
        ordered_fields = [field for field in IDENTITY_FIELD_ORDER if field in fields]
        ordered_fields.extend(sorted(set(fields) - set(IDENTITY_FIELD_ORDER)))
        lines.extend(f"{field} = {_format_value(fields[field])}" for field in ordered_fields)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _base_document() -> str:
    return "schema_version = 2\nrevision = 0\n"


def update_host(
    text: str,
    host: str = HOST,
    identities: Optional[Mapping[str, Mapping[str, Any]]] = None,
    *,
    path: Optional[Path] = None,
) -> str:
    """Replace the identity chunks, preserving every other chunk byte-for-byte."""

    if identities is None:
        identities = {}

    candidate_identities = _copy(dict(identities))
    for identity in candidate_identities:
        if identity not in IDENTITIES:
            raise ConfigValidationError(f"unsupported identity: {identity!r}")
    emitted = emit_host_sections(candidate_identities, host)
    if not text:
        routing = "[routing]\nalways_on_host_rules = false\n"
        candidate = _base_document() + "\n" + emitted + ("\n" if emitted else "") + routing
        validate_config(candidate, host, path=path)
        return candidate

    chunks = split_sections(text)
    if any(
        chunk.name and re.match(r"^hosts\.[^.]+\.roles(?:\.|$)", chunk.name)
        for chunk in chunks
    ):
        raise _legacy_v1_error(path)
    prefix = f"hosts.{host}.identities."
    indexes = [index for index, chunk in enumerate(chunks) if chunk.name and chunk.name.startswith(prefix)]
    insert_at = indexes[0] if indexes else len(chunks)
    kept = [chunk.text for index, chunk in enumerate(chunks) if index not in indexes]
    if emitted:
        if insert_at > len(kept):
            insert_at = len(kept)
        insertion = emitted
        if insert_at and kept[insert_at - 1] and not kept[insert_at - 1].endswith(("\n", "\r")):
            insertion = "\n" + insertion
        if insert_at < len(kept) and insertion and not insertion.endswith("\n"):
            insertion += "\n"
        kept.insert(insert_at, insertion)
    candidate = "".join(kept)
    validate_config(candidate, host, path=path)
    return candidate


def project_config_path(repo: Path) -> Path:
    return repo.resolve() / ".handoff" / "config.toml"


def global_config_path(env: Optional[Mapping[str, str]] = None) -> Path:
    environ = os.environ if env is None else env
    if environ.get("XDG_CONFIG_HOME"):
        root = Path(environ["XDG_CONFIG_HOME"]).expanduser()
    elif environ.get("HOME"):
        root = Path(environ["HOME"]).expanduser() / ".config"
    else:
        raise ConfigError("HOME is unset and XDG_CONFIG_HOME is not available")
    return root / "handoff" / "config.toml"


def _read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def atomic_write(path: Path, text: str) -> None:
    """Write UTF-8 text through a same-directory temporary and os.replace."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


class ConfigLock:
    """A mkdir lock over the config directory, reclaimed once it goes stale.

    Writes here take milliseconds and come from one user at a time, so the
    only real job is stopping a wizard apply from interleaving with a
    `handoff-config.py set` from a running session.
    """

    # ponytail: staleness is age-only; a writer stuck past stale_after loses the
    # lock. Add owner liveness only if writes ever become long-running.
    def __init__(self, config_path: Path, *, stale_after: float = 15.0):
        self.path = Path(config_path).parent / ".config.lock"
        self.stale_after = stale_after
        self.acquired = False

    def acquire(self) -> "ConfigLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.mkdir(str(self.path))
        except FileExistsError:
            age = time.time() - self.path.stat().st_mtime
            if age <= self.stale_after:
                raise ConfigLockError(
                    f"another handoff config write is in progress ({self.path}, "
                    f"held {age:.0f}s); retry, or remove that directory if no writer is active"
                ) from None
            os.utime(str(self.path))
        self.acquired = True
        return self

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            self.path.rmdir()
        except OSError:
            pass
        self.acquired = False

    def __enter__(self) -> "ConfigLock":
        return self.acquire()

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.release()


def write_host_config(
    path: Path,
    host: str = HOST,
    identities: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> str:
    """Lock, read, replace the identity sections, and atomically persist."""

    if identities is None:
        identities = {}

    with ConfigLock(path):
        current = _read_text(path) if Path(path).exists() else ""
        updated = update_host(current, host, identities, path=Path(path))
        atomic_write(Path(path), updated)
    return updated


def _host_overlay(data: Mapping[str, Any], host: str = HOST) -> Dict[str, Any]:
    overlay: Dict[str, Any] = {}
    for key in ("schema_version", "revision", "routing"):
        if key in data:
            overlay[key] = _copy(data[key])
    identities = data.get("hosts", {}).get(host, {}).get("identities", {})
    if identities:
        overlay["hosts"] = {host: {"identities": _copy(identities)}}
    return overlay


def _invalidate_inherited_verification(
    resolved: MutableMapping[str, Any],
    overlay: Mapping[str, Any],
    host: str,
) -> None:
    identities = (
        overlay.get("hosts", {})
        .get(host, {})
        .get("identities", {})
    )
    resolved_identities = (
        resolved.setdefault("hosts", {})
        .setdefault(host, {})
        .setdefault("identities", {})
    )
    for identity, fields in identities.items():
        if not isinstance(fields, Mapping):
            continue
        identity_changed = any(
            field in fields for field in ("backend", "model", "effort")
        )
        if identity_changed:
            current = resolved_identities.setdefault(identity, {})
            if "verified" not in fields:
                current["verified"] = False
            if "verified_at" not in fields:
                current.pop("verified_at", None)


def resolve_config(
    repo: Path,
    host: str = HOST,
    session_override: Optional[Mapping[str, Any]] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Resolve defaults < global < project < session and report the top source."""

    resolved = _copy(DEFAULTS)
    source = "default"
    global_path = global_config_path(env)
    project_path = project_config_path(Path(repo))
    for label, path in (("global", global_path), ("project", project_path)):
        if path.is_file():
            parsed = validate_config(_read_text(path), host, path=path)
            overlay = _host_overlay(parsed, host)
            _deep_merge(resolved, overlay)
            _invalidate_inherited_verification(resolved, overlay, host)
            source = label
    if session_override:
        _deep_merge(resolved, session_override)
        _invalidate_inherited_verification(resolved, session_override, host)
        source = "session"
    _validate_data(resolved, host)
    resolved["source"] = source
    return resolved


def _scope_path(scope: str, repo: Path, env: Optional[Mapping[str, str]] = None) -> Path:
    return project_config_path(repo) if scope == "project" else global_config_path(env)


def _get_nested(data: Mapping[str, Any], dotted: str) -> Any:
    value: Any = data
    for component in dotted.split("."):
        if not isinstance(value, Mapping) or component not in value:
            raise ConfigError(f"key not found: {dotted}")
        value = value[component]
    return value


def _parse_override(items: Iterable[str], host: str = HOST) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise ConfigError(f"override must be IDENTITY.FIELD=VALUE: {item!r}")
        dotted, raw = item.split("=", 1)
        parts = dotted.split(".")
        if len(parts) != 2 or parts[0] not in IDENTITIES or parts[1] not in IDENTITY_FIELD_ORDER:
            raise ConfigError(f"override must target IDENTITY.FIELD: {dotted!r}")
        field = parts[1]
        if field == "verified":
            if raw not in ("true", "false"):
                raise ConfigError("verified override must be true or false")
            value: Any = raw == "true"
        elif raw.startswith('"'):
            value = _parse_value(raw, 1, len(dotted) + 2)
        elif raw:
            value = raw
        else:
            raise ConfigError(f"override value must not be empty: {dotted}")
        result.setdefault("hosts", {}).setdefault(host, {}).setdefault("identities", {}).setdefault(parts[0], {})[parts[1]] = value
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage agent-handoff schema-v2 configuration.")
    parser.add_argument("--scope", choices=("project", "global"), default="project", help="Configuration file to read or write (default: project).")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root for project scope and resolution.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    get_parser = subparsers.add_parser("get", help="Read the selected scope without resolving lower layers.")
    get_parser.add_argument("key", nargs="?", help="Optional dotted key; default prints visible config as JSON.")

    set_parser = subparsers.add_parser("set", help="Set one identity, preserving every other chunk byte-for-byte.")
    set_parser.add_argument("--role", choices=IDENTITIES, required=True, help="Identity to update.")
    set_parser.add_argument("--backend", choices=BACKENDS)
    set_parser.add_argument("--model")
    set_parser.add_argument("--effort")
    verified = set_parser.add_mutually_exclusive_group()
    verified.add_argument("--verified", dest="verified", action="store_true")
    verified.add_argument("--unverified", dest="verified", action="store_false")
    set_parser.set_defaults(verified=None)
    set_parser.add_argument("--verified-at")

    resolve_parser = subparsers.add_parser("resolve", help="Resolve session > project > global > defaults.")
    resolve_parser.add_argument("--override", action="append", default=[], metavar="IDENTITY.FIELD=VALUE")

    subparsers.add_parser("validate", help="Validate the selected file.")
    subparsers.add_parser("init", help="Create an empty schema-v2 identity document.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    path = _scope_path(args.scope, args.repo)
    try:
        if args.command == "resolve":
            override = _parse_override(args.override)
            print(json.dumps(resolve_config(args.repo, HOST, override), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.command == "init":
            if path.is_file():
                validate_config(_read_text(path), path=path)
            else:
                write_host_config(path)
            print(path)
            return 0
        if not path.is_file():
            raise ConfigError(f"config does not exist: {path}; run init first")
        text = _read_text(path)
        data = validate_config(text, path=path)
        if args.command == "validate":
            print(f"PASS {path}")
            return 0
        if args.command == "get":
            value = _get_nested(data, args.key) if args.key else data
            if isinstance(value, (dict, list)):
                print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
            elif isinstance(value, bool):
                print("true" if value else "false")
            else:
                print(value)
            return 0
        if args.command == "set":
            identities = data["hosts"][HOST]["identities"]
            current = dict(identities.get(args.role, {}))
            updates = {
                "backend": args.backend,
                "model": args.model,
                "effort": args.effort,
                "verified": args.verified,
                "verified_at": args.verified_at,
            }
            identity_changed = any(
                value is not None
                and value != current.get(field)
                for field, value in (
                    ("backend", args.backend),
                    ("model", args.model),
                    ("effort", args.effort),
                )
            )
            for key, value in updates.items():
                if value is not None:
                    current[key] = value
            if identity_changed and args.verified is None:
                current["verified"] = False
                current.pop("verified_at", None)
            elif identity_changed and args.verified_at is None:
                current.pop("verified_at", None)
            if args.verified is False and args.verified_at is None:
                current.pop("verified_at", None)
            identities[args.role] = current
            write_host_config(path, HOST, identities)
            print(path)
            return 0
        parser.error(f"unknown command: {args.command}")
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
