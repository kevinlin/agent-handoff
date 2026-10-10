#!/usr/bin/env python3
"""Run the Handoff setup wizard as a local, single-page web UI."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import hmac
import importlib.util
import json
import os
import re
import secrets
import selectors
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


SCRIPT_DIR = Path(__file__).resolve().parent
ENGINE_PATH = SCRIPT_DIR / "handoff-setup.py"
SPEC = importlib.util.spec_from_file_location("handoff_setup_ui_engine", ENGINE_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - broken install
    raise RuntimeError(f"cannot load {ENGINE_PATH}")
engine = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = engine
SPEC.loader.exec_module(engine)

MODES = ("balanced", "quality", "cost", "custom")
SCOPES = ("project", "global")
EXCLUDE_CHOICES = ("git-exclude", "self", "track")
ROUTING_ACTIONS = ("none", "write", "remove")
CLAUDE_MODEL_ALIASES = ("fable", "opus", "sonnet", "haiku")
COPILOT_MODELS_URL = "https://api.githubcopilot.com/models"
# Fixed text. The page shows these commands and links as written, so nothing in
# a release response is ever interpolated into one.
CLI_RELEASES = {
    "codex": {
        "label": "Codex",
        "latest_url": "https://registry.npmjs.org/@openai/codex/latest",
        "install": "npm install -g @openai/codex",
        "update": "codex update",
        "docs": "https://developers.openai.com/codex/cli",
    },
    "copilot": {
        "label": "GitHub Copilot",
        "latest_url": "https://registry.npmjs.org/@github/copilot/latest",
        "install": "npm install -g @github/copilot",
        "update": "copilot update",
        "docs": "https://docs.github.com/en/copilot/how-tos/set-up/install-copilot-cli",
    },
    "cursor": {
        "label": "Cursor",
        "latest_url": "https://cursor.com/install",
        "install": "curl https://cursor.com/install -fsS | bash",
        "update": "cursor-agent update",
        "docs": "https://cursor.com/docs/cli/installation",
    },
}
IDENTITY_META = {
    "deep_reasoner": {
        "label": "Deep reasoning",
        "hint": "Architecture, diagnosis, and hard tradeoffs",
    },
    "fast_worker": {
        "label": "Fast execution",
        "hint": "Mechanical implementation, tests, and fixes",
    },
    "arbiter": {
        "label": "Independent arbitration",
        "hint": "Blind second solve for contested conclusions",
    },
    "e2e_specifier": {
        "label": "Acceptance authoring",
        "hint": "Gherkin scenarios and repo-native executable tests",
    },
    "e2e_verifier": {
        "label": "Acceptance execution",
        "hint": "Runs the reviewed tests and reports a validated verdict",
    },
}


class UIError(Exception):
    """A user-actionable local UI error."""


def _binary_version(path: Optional[str], env: Mapping[str, str], source: str) -> Dict[str, Any]:
    if not path:
        return {"available": False, "path": None, "version": None, "source": source}
    try:
        result = subprocess.run(
            [path, "--version"],
            env=engine.clean_claude_env(env),
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
        lines = (result.stdout or result.stderr).strip().splitlines()
        # A CLI that prints nothing for --version is installed all the same; an
        # IndexError here would escape controller construction.
        version = lines[0] if lines else "Found, version unreadable"
    except (OSError, subprocess.TimeoutExpired):
        version = "Found, version unreadable"
    return {"available": True, "path": path, "version": version, "source": source}


def _codex_path(env: Mapping[str, str]) -> Tuple[Optional[str], str]:
    configured = env.get("HANDOFF_CODEX_BIN")
    if configured:
        path = configured if "/" in configured else shutil.which(configured, path=env.get("PATH"))
        return path, "HANDOFF_CODEX_BIN"
    if sys.platform == "darwin":
        for candidate in (
            "/Applications/ChatGPT.app/Contents/Resources/codex",
            "/Applications/Codex.app/Contents/Resources/codex",
        ):
            if os.access(candidate, os.X_OK):
                return candidate, "app"
    return shutil.which("codex", path=env.get("PATH")), "PATH"


def _codex_version(env: Mapping[str, str]) -> Dict[str, Any]:
    path, source = _codex_path(env)
    return _binary_version(path, env, source)


# Release lookups are plain, unauthenticated GETs. They never share
# _copilot_urlopen, whose opener exists to keep a bearer on one host.
_release_urlopen = urlopen


def _version_tuple(text: Any) -> Optional[Tuple[int, ...]]:
    match = re.search(r"\d+(?:\.\d+)+", text) if isinstance(text, str) else None
    return tuple(int(part) for part in match.group(0).split(".")) if match else None


def _cli_status(probe: Mapping[str, Any], latest: Optional[str]) -> str:
    """``missing``, ``unknown``, ``outdated`` or ``current`` for one probed CLI.

    ``unknown`` is the honest answer when either side has no readable
    version; the page then claims neither an update nor "up to date".
    """

    if not probe.get("available"):
        return "missing"
    installed = _version_tuple(probe.get("version"))
    newest = _version_tuple(latest)
    if installed is None or newest is None:
        return "unknown"
    width = max(len(installed), len(newest))
    installed += (0,) * (width - len(installed))
    newest += (0,) * (width - len(newest))
    return "outdated" if installed < newest else "current"


def _latest_version(backend: str) -> Optional[str]:
    """The newest published version of one backend's CLI, or None.

    npm serves Codex and Copilot as JSON. Cursor has no registry behind its
    installer, but the install script names its build in the download URL, so
    that is read from the script.
    """

    request = Request(
        CLI_RELEASES[backend]["latest_url"], headers={"User-Agent": "handoff-setup"}
    )
    try:
        with _release_urlopen(request, timeout=3) as response:
            body = response.read(1_000_000)
        if backend == "cursor":
            # A scrape, so it must be unambiguous: one distinct build, or
            # nothing. Two builds mean the script changed shape, and picking
            # one would report an update or "current" on a guess.
            builds = set(
                re.findall(
                    r"https://downloads\.cursor\.com/lab/([0-9A-Za-z.\-]+)/",
                    body.decode("utf-8"),
                )
            )
            return builds.pop() if len(builds) == 1 else None
        version = json.loads(body)["version"]
    except HTTPError as error:
        error.close()
        return None
    except (OSError, HTTPException, ValueError, KeyError, TypeError, AttributeError):
        # Nothing may escape: build_state runs during controller construction,
        # so an exception here would take the whole page down with one tile.
        return None
    return version if isinstance(version, str) and _version_tuple(version) is not None else None


def _send_json_line(process: subprocess.Popen[str], payload: Mapping[str, Any]) -> None:
    if process.stdin is None:
        raise OSError("Codex app-server stdin is unavailable")
    process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    process.stdin.flush()


def _read_json_response(
    process: subprocess.Popen[str], request_id: int, *, timeout: float
) -> Dict[str, Any]:
    if process.stdout is None:
        raise OSError("Codex app-server stdout is unavailable")
    deadline = time.monotonic() + timeout
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise subprocess.TimeoutExpired(process.args, timeout)
            line = process.stdout.readline()
            if not line:
                raise OSError("Codex app-server closed before returning the model list")
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") == request_id:
                if "error" in message:
                    raise OSError(str(message["error"]))
                result = message.get("result")
                if not isinstance(result, dict):
                    raise OSError("Codex app-server returned an invalid response")
                return result
    finally:
        selector.close()


def _codex_model_options(
    path: Optional[str], env: Mapping[str, str]
) -> Tuple[List[Dict[str, Any]], str]:
    if not path:
        return [], "Codex CLI not installed"
    process: Optional[subprocess.Popen[str]] = None
    try:
        process = subprocess.Popen(
            [path, "app-server", "--listen", "stdio://"],
            env=dict(env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        _send_json_line(
            process,
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "handoff-setup", "version": "1"},
                    "capabilities": {"experimentalApi": True},
                },
            },
        )
        _read_json_response(process, 1, timeout=5)
        _send_json_line(process, {"method": "initialized", "params": {}})
        _send_json_line(
            process,
            {
                "id": 2,
                "method": "model/list",
                "params": {"includeHidden": False, "limit": 100},
            },
        )
        response = _read_json_response(process, 2, timeout=5)
        data = response.get("data")
        if not isinstance(data, list):
            raise OSError("Codex model/list did not return a list")
        options: List[Dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("model"), str):
                continue
            value = item["model"].strip()
            if not value:
                continue
            effort_items = item.get("supportedReasoningEfforts", [])
            efforts = [
                entry["reasoningEffort"]
                for entry in effort_items
                if isinstance(entry, dict)
                and entry.get("reasoningEffort") in engine.CODEX_EFFORTS
            ]
            options.append(
                {
                    "value": value,
                    "label": item.get("displayName") or value,
                    "description": item.get("description") or "",
                    "source": "codex model/list",
                    "efforts": efforts,
                    "is_default": bool(item.get("isDefault")),
                }
            )
        return options, "Read from Codex CLI" if options else "Codex CLI returned no models"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return [], "Could not read the Codex CLI model list"
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        if process is not None:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()


def _canonical_claude_model(value: str) -> str:
    """Collapse Claude context-window variants into one model choice."""
    normalized = re.sub(r"\[1m\]", "", value.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s+1m$", "", normalized, flags=re.IGNORECASE).strip()


def _claude_model_options(
    path: Optional[str], env: Mapping[str, str], detected: Mapping[str, str]
) -> Tuple[List[Dict[str, Any]], str]:
    aliases: List[str] = []
    efforts = list(engine.CLAUDE_EFFORTS)
    if path:
        try:
            result = subprocess.run(
                [path, "--help"],
                env=engine.clean_claude_env(env),
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            help_text = result.stdout or result.stderr
            example = re.search(
                r"Provide\s+an\s+alias.*?\(e\.g\.(.*?)\)", help_text, flags=re.DOTALL
            )
            if example:
                aliases = re.findall(r"'([^']+)'", example.group(1))
            effort_help = re.search(
                r"--effort\s+<level>.*?\(([^)\n]+)\)", help_text, flags=re.DOTALL
            )
            if effort_help:
                detected_efforts = [
                    value.strip() for value in effort_help.group(1).split(",")
                ]
                if detected_efforts and all(
                    value in engine.CLAUDE_EFFORTS for value in detected_efforts
                ):
                    efforts = detected_efforts
        except (OSError, subprocess.TimeoutExpired):
            pass
    aliases = [
        normalized
        for alias in (*aliases, *CLAUDE_MODEL_ALIASES)
        if (normalized := _canonical_claude_model(alias))
    ]
    options = [
        {
            "value": alias,
            "label": alias.capitalize(),
            "description": "Official Claude Code rolling alias",
            "source": "claude --help",
            "efforts": efforts,
            "is_default": alias == "opus",
        }
        for alias in dict.fromkeys(aliases)
    ]
    known = {option["value"] for option in options}
    for detected_value in detected.values():
        value = _canonical_claude_model(detected_value)
        if value and value not in known:
            options.append(
                {
                    "value": value,
                    "label": value,
                    "description": "Already used in the local Claude config",
                    "source": "local claude config",
                    "efforts": efforts,
                    "is_default": False,
                }
            )
            known.add(value)
    source = "Official Claude CLI aliases" if path else "Built-in Claude alias fallback"
    return options, source


def _github_token(env: Mapping[str, str]) -> Optional[str]:
    """Read the bearer the Copilot entitlement API is queried with.

    `gh` is asked rather than the token store under ``~/.copilot``: that store
    is the CLI's own business, and `gh auth token` is the supported way to get
    a bearer for the authenticated account.
    """

    path = shutil.which("gh", path=env.get("PATH"))
    if not path:
        return None
    try:
        result = subprocess.run(
            [path, "auth", "token"],
            env=dict(env),
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    token = (result.stdout or "").strip()
    return token if result.returncode == 0 and token else None


def _copilot_model_offered(item: Mapping[str, Any]) -> bool:
    """Keep the models a coding session can actually select.

    The endpoint also returns embeddings and legacy chat models, and models an
    org policy has not enabled. A policy key that is absent is not a refusal.
    """

    if item.get("model_picker_enabled") is not True:
        return False
    policy = item.get("policy")
    if policy is None:
        return True
    # A policy in a shape this code does not understand is not an approval.
    return isinstance(policy, dict) and policy.get("state") == "enabled"


_UNREADABLE = object()


def _copilot_reported_efforts(item: Mapping[str, Any]) -> Any:
    """The efforts an entry reports: a list, ``None`` for absent, or unreadable."""

    capabilities = item.get("capabilities")
    if capabilities is None:
        return None
    if not isinstance(capabilities, dict):
        return _UNREADABLE
    supports = capabilities.get("supports")
    if supports is None:
        return None
    if not isinstance(supports, dict):
        return _UNREADABLE
    reported = supports.get("reasoning_effort")
    if reported is None:
        return None
    return reported if isinstance(reported, list) else _UNREADABLE


def _copilot_model_option(item: Any) -> Optional[Dict[str, Any]]:
    """Turn one catalogue entry into a model option, or decide it is not one."""

    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        return None
    value = item["id"].strip()
    if not value or not _copilot_model_offered(item):
        return None
    # Absent says nothing about what the CLI takes, so the superset stands and
    # the model stays configurable. Present in a shape this code cannot read is
    # a different thing, and not a licence to offer every effort: drop the
    # entry rather than guess at it.
    reported = _copilot_reported_efforts(item)
    if reported is _UNREADABLE:
        return None
    if reported is None:
        efforts = list(engine.COPILOT_EFFORTS)
    else:
        efforts = [effort for effort in reported if effort in engine.COPILOT_EFFORTS]
        # Reported efforts that Handoff cannot pass are not a reason to offer
        # every effort instead: this model is not configurable here.
        if not efforts:
            return None
    label = item.get("name")
    vendor = item.get("vendor")
    return {
        "value": value,
        "label": label if isinstance(label, str) and label else value,
        "description": vendor if isinstance(vendor, str) else "",
        "source": "copilot models",
        "efforts": efforts,
        "is_default": False,
    }


class _NoCopilotRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # urllib forwards Authorization on redirects, even to another host.
        # The catalogue has one fixed endpoint; fail closed if it moves.
        return None


_copilot_urlopen = build_opener(_NoCopilotRedirect()).open


def _copilot_model_options(env: Mapping[str, str]) -> Tuple[List[Dict[str, Any]], str]:
    """Offer the models this account's Copilot entitlement carries.

    The Copilot CLI publishes no catalogue of its own - it has no model-list
    subcommand, and a wrong ``--model`` is refused without naming the
    alternatives - so the entitlement API is read directly. Nothing here spawns
    ``copilot``, which is what keeps opening the page free of a premium
    request. Every failure returns an empty list and a reason the page shows;
    the wizard then offers no Copilot model rather than guessing one.
    """

    token = _github_token(env)
    if not token:
        return [], "Run `gh auth login`, then start the wizard again, to list your Copilot models"
    request = Request(
        COPILOT_MODELS_URL,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with _copilot_urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        error.close()
        # 401 and 403 are the data-residency case too: a tenant serves its
        # catalogue from its own host and rejects a bearer minted for this one.
        return [], (
            f"Copilot refused the model list ({error.code}). Check "
            "`gh auth status`, then start the wizard again"
        )
    except OSError:
        return [], "Could not reach the Copilot model list. Check your network, then start the wizard again"
    except ValueError:
        return [], "The Copilot model list came back unreadable"
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return [], "The Copilot model list came back in an unknown shape"
    options: List[Dict[str, Any]] = []
    for item in data:
        try:
            option = _copilot_model_option(item)
        except (AttributeError, TypeError, ValueError):
            # One entry in a shape this code does not expect costs that entry,
            # never the catalogue and never the page: build_state runs during
            # controller construction, so an escape here takes Claude and Codex
            # down with it.
            continue
        if option is not None:
            options.append(option)
    if not options:
        return [], "Your Copilot entitlement lists no usable models"
    return options, "Read from your Copilot entitlement"


def _cursor_model_options(env: Mapping[str, str]) -> Tuple[List[Dict[str, Any]], str]:
    """Offer the slugs ``cursor-agent models`` lists, read at page load.

    The call is free and takes about a second, the same cost profile as
    codex's ``model/list``. Nothing is guessed when it fails.
    """

    binary = engine.cursor_bin(env)
    if not binary:
        return [], ("Cursor CLI not found. Install it, or set HANDOFF_CURSOR_BIN, "
                    "then start the wizard again")
    catalogue, error = engine.cursor_catalogue(binary, env)
    if error:
        return [], error
    return [
        {
            "value": slug,
            "label": name or slug,
            "description": "",
            "source": "cursor-agent models",
            "efforts": list(engine.CURSOR_EFFORTS),
            "is_default": False,
        }
        for slug, name in catalogue
    ], "Read from cursor-agent models"


def _ensure_model_option(
    options: List[Dict[str, Any]],
    value: str,
    source: str,
    backend: str,
    effort: Optional[str] = None,
) -> None:
    """Keep a configured model selectable even when no catalogue lists it.

    Its efforts are the open question. On copilot they vary per model and the
    catalogue is the only thing that knows them, so a model kept for this
    reason offers the effort already configured and nothing wider - preserving
    a working config must not become permission to configure an unchecked pair.
    Claude and codex take the backend enum, which is per-model truth there.
    """

    if not value or any(option["value"] == value for option in options):
        return
    efforts = list(engine.BACKEND_EFFORTS[backend])
    if backend == "copilot":
        efforts = [effort] if effort in engine.BACKEND_EFFORTS[backend] else []
    # Slugs leave the catalogue between Cursor releases. Keep the configured
    # one and say so, rather than replace it.
    label = "Not in the current catalogue" if backend == "cursor" else value
    options.append(
        {
            "value": value,
            "label": label,
            "description": "Already used in the current config",
            "source": source,
            "efforts": efforts,
            "is_default": False,
        }
    )


def _preset_matrices(env: Mapping[str, str]) -> Dict[str, Dict[str, Dict[str, str]]]:
    codex = engine.detect_codex(env)
    codex_model = codex.get("model", "")
    matrices: Dict[str, Dict[str, Dict[str, str]]] = {}
    for mode, preset in engine.PRESETS.items():
        matrix: Dict[str, Dict[str, str]] = {}
        for identity in engine.IDENTITIES:
            backend, configured_model, effort = preset[identity]
            if backend == "codex":
                model = configured_model or codex_model
                source = "detected" if model else "custom (required)"
            else:
                model = configured_model or ""
                source = "built-in alias"
            matrix[identity] = {
                "backend": backend,
                "model": model,
                "effort": effort,
                "permission_mode": "default",
                "model_source": source,
            }
        matrices[mode] = matrix
    return matrices


def build_state(repo: Path, env: Mapping[str, str]) -> Dict[str, Any]:
    repo = repo.resolve()
    presets = _preset_matrices(env)
    resolved = engine.handoff_config.resolve_config(repo, env=env)
    identities = engine.handoff_config.identities_of(resolved)
    current = {
        identity: {
            "backend": values["backend"],
            "model": values["model"],
            "effort": values["effort"],
            "permission_mode": values.get("permission_mode", "default"),
            "model_source": "existing config",
        }
        for identity, values in identities.items()
    }
    # The release lookups wait on the network and the probes below wait on
    # subprocesses, so the lookups start first and are collected last: the two
    # waits overlap instead of adding up.
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(CLI_RELEASES)) as pool:
        latest_futures = {name: pool.submit(_latest_version, name) for name in CLI_RELEASES}
        codex_detected = engine.detect_codex(env)
        claude_detected = engine.detect_claude(env)
        claude_cli = _binary_version(shutil.which("claude", path=env.get("PATH")), env, "PATH")
        codex_cli = _codex_version(env)
        codex_options, codex_discovery = _codex_model_options(codex_cli["path"], env)
        claude_options, claude_discovery = _claude_model_options(
            claude_cli["path"], env, claude_detected
        )
        # Like _codex_path, say where the binary came from, so the page can
        # tell a user whose override points at nothing from one with no install.
        copilot_cli = _binary_version(
            engine.copilot_bin(env),
            env,
            "HANDOFF_COPILOT_BIN" if env.get("HANDOFF_COPILOT_BIN") else "PATH",
        )
        cursor_cli = _binary_version(
            engine.cursor_bin(env),
            env,
            "HANDOFF_CURSOR_BIN" if env.get("HANDOFF_CURSOR_BIN") else "PATH",
        )
        copilot_options, copilot_discovery = _copilot_model_options(env)
        cursor_options, cursor_discovery = _cursor_model_options(env)
        latest = {name: future.result() for name, future in latest_futures.items()}
    release_clis = {"codex": codex_cli, "copilot": copilot_cli, "cursor": cursor_cli}
    option_sets = {
        "claude": claude_options,
        "codex": codex_options,
        "copilot": copilot_options,
        "cursor": cursor_options,
    }
    # Taken before configured values are added back: a retained model fills
    # the list, and the page must still say that discovery failed.
    discovery_failed = {backend: not options for backend, options in option_sets.items()}
    for matrix in (*presets.values(), current):
        for values in matrix.values():
            backend = values.get("backend")
            model = values.get("model")
            if backend in option_sets and isinstance(model, str):
                _ensure_model_option(
                    option_sets[backend],
                    model,
                    values.get("model_source", "existing config"),
                    backend,
                    values.get("effort"),
                )
    initial_mode = "custom" if current else "balanced"
    # Identities absent from config (usually the optional e2e pair) start from Balanced.
    initial_matrix = {**presets["balanced"], **current}
    return {
        "repo": str(repo),
        "config_source": resolved["source"],
        "clis": {
            "claude": claude_cli,
            **{
                name: {
                    **probe,
                    **CLI_RELEASES[name],
                    "latest": latest[name],
                    "status": _cli_status(probe, latest[name]),
                }
                for name, probe in release_clis.items()
            },
        },
        "detected": {
            "codex_model": codex_detected.get("model"),
            "codex_effort": codex_detected.get("model_reasoning_effort"),
            "claude_models": claude_detected,
        },
        "model_options": option_sets,
        "model_discovery": {
            "claude": claude_discovery,
            "codex": codex_discovery,
            "copilot": copilot_discovery,
            "cursor": cursor_discovery,
        },
        "model_discovery_failed": discovery_failed,
        "presets": presets,
        "initial_mode": initial_mode,
        "initial_matrix": initial_matrix,
        "permission_modes": list(engine.PERMISSION_MODES),
        "permission_labels": {"default": "Default", "allow-all": "Allow all"},
        "efforts_by_backend": {
            backend: list(efforts) for backend, efforts in engine.BACKEND_EFFORTS.items()
        },
        "identity_meta": IDENTITY_META,
        "initial_review": dict(resolved["review"]),
        "core_identities": list(engine.CORE_IDENTITIES),
        "optional_identities": list(engine.OPTIONAL_IDENTITIES),
        "write_agents_available": True,
    }


def _clean_string(value: Any, label: str, *, limit: int = 200) -> str:
    if not isinstance(value, str):
        raise UIError(f"{label} must be a string")
    value = value.strip()
    if not value or len(value) > limit or any(ord(char) < 32 for char in value):
        raise UIError(f"{label} must be non-empty, free of control characters, and at most {limit} characters")
    return value


def normalize_payload(
    raw: Any,
    *,
    repo: Path,
    env: Mapping[str, str],
    model_options: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise UIError("Request must be a JSON object")
    mode = raw.get("mode")
    if mode not in MODES:
        raise UIError("Invalid work mode")
    scope = raw.get("scope")
    if scope not in SCOPES:
        raise UIError("Invalid scope")
    exclude_choice = raw.get("exclude_choice", "git-exclude")
    if exclude_choice not in EXCLUDE_CHOICES:
        raise UIError("Invalid Git handling option")
    routing_action = raw.get("routing_action", "none")
    if routing_action not in ROUTING_ACTIONS:
        raise UIError("Invalid persistent routing setting")
    supplied = raw.get("identities")
    if not isinstance(supplied, dict):
        raise UIError("Missing identity matrix")
    with_e2e = bool(raw.get("with_e2e"))
    required = engine.identities_for(with_e2e)
    identities: Dict[str, Dict[str, str]] = {}
    for identity in required:
        values = supplied.get(identity)
        if not isinstance(values, dict):
            raise UIError(f"Missing settings for {identity}")
        backend = values.get("backend")
        if backend not in engine.BACKENDS:
            raise UIError(f"Invalid CLI for {identity}")
        model = _clean_string(values.get("model"), f"{identity} model")
        permission = values.get("permission_mode", "default")
        if permission not in engine.PERMISSION_MODES:
            raise UIError(f"Invalid permission_mode for {identity}")
        effort = values.get("effort")
        supported_efforts = engine.BACKEND_EFFORTS[backend]
        if model_options:
            option = next(
                (
                    candidate
                    for candidate in model_options.get(backend, ())
                    if candidate.get("value") == model
                ),
                None,
            )
            if option and option.get("efforts"):
                supported_efforts = tuple(option["efforts"])
            # On copilot the catalogue is the list of valid models, so a name
            # that is not in it is refused here rather than at the first job.
            # `auto` is exempt only so the engine's own reasoned refusal is
            # what the user reads; it is rejected either way.
            if option is None and backend in ("copilot", "cursor") and model != "auto":
                if backend == "copilot":
                    raise UIError(
                        f"model {model} for {identity} is not in your Copilot model list; "
                        "pick one the list offers, or fix the login it names and start "
                        "the wizard again"
                    )
                raise UIError(
                    f"model {model} for {identity} is not in `cursor-agent models`; "
                    "pick one the list offers, or fix what the page names and start "
                    "the wizard again"
                )
        if effort not in supported_efforts:
            raise UIError(
                f"effort {effort} for {identity} is not supported by {backend}/{model}; "
                f"allowed values: {', '.join(supported_efforts)}"
            )
        identities[identity] = {
            "backend": backend,
            "model": model,
            "effort": effort,
            "permission_mode": permission,
        }
    if mode != "custom":
        expected = _preset_matrices(env)[mode]
        comparable = {
            identity: {
                field: expected[identity][field]
                for field in ("backend", "model", "effort", "permission_mode")
            }
            for identity in required
        }
        if identities != comparable:
            raise UIError("Identity settings changed. Switch to custom mode and preview again.")
    review = raw.get("review")
    if not isinstance(review, dict):
        raise UIError("Missing review gate settings")
    caps: Dict[str, int] = {}
    for key in engine.handoff_config.REVIEW_FIELDS:
        value = review.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise UIError(f"{key} must be a whole number of at least 1")
        caps[key] = value
    return {
        "repo": str(repo.resolve()),
        "mode": mode,
        "scope": scope,
        "exclude_choice": exclude_choice,
        "routing_action": routing_action,
        "write_agents": bool(raw.get("write_agents")),
        "smoke": bool(raw.get("smoke", True)),
        "with_e2e": with_e2e,
        "review": caps,
        "identities": identities,
    }


def engine_arguments(payload: Mapping[str, Any], action: str) -> list[str]:
    args = [
        action,
        "--repo",
        str(payload["repo"]),
        "--scope",
        str(payload["scope"]),
        "--mode",
        str(payload["mode"]),
        "--exclude-choice",
        str(payload["exclude_choice"]),
    ]
    if payload["mode"] == "custom":
        for identity in payload["identities"]:
            values = payload["identities"][identity]
            args.extend(("--role-backend", f"{identity}={values['backend']}"))
            args.extend(("--role-model", f"{identity}={values['model']}"))
            args.extend(("--role-effort", f"{identity}={values['effort']}"))
            args.extend(("--role-permission-mode", f"{identity}={values['permission_mode']}"))
    args.append("--with-e2e" if payload["with_e2e"] else "--no-with-e2e")
    for key in engine.handoff_config.REVIEW_FIELDS:
        args.extend((f"--{key.replace('_', '-')}", str(payload["review"][key])))
    args.append("--write-agents" if payload["write_agents"] else "--no-write-agents")
    if payload["routing_action"] == "write":
        args.append("--routing-block")
    elif payload["routing_action"] == "remove":
        args.append("--remove-routing-block")
    return args


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def parse_preview_files(stdout: str, repo: Path) -> list[dict]:
    files = []
    in_files = False
    for line in stdout.splitlines():
        if not in_files:
            in_files = line == "Files:"
            continue
        match = re.fullmatch(r"  \[(WRITE|DELETE|UNCHANGED|REFUSED)\] (/\S.*)", line)
        if not match:
            break
        state, path = match.groups()
        file_path = Path(path)
        try:
            display = str(file_path.relative_to(repo))
        except ValueError:
            try:
                home_relative = file_path.relative_to(Path.home())
                display = "~" if home_relative == Path(".") else f"~/{home_relative}"
            except ValueError:
                display = path
        files.append({"state": state, "path": path, "display": display})
    return files


class SetupController:
    def __init__(self, repo: Path, env: Mapping[str, str]):
        self.repo = repo.resolve()
        self.env = dict(env)
        self.initial_state = build_state(self.repo, self.env)
        self.lock = threading.Lock()
        self.preview_digest: Optional[str] = None
        self.preview_stdout = ""
        self.preview_stderr = ""
        self.preview_code: Optional[int] = None

    def state(self) -> Dict[str, Any]:
        return json.loads(json.dumps(self.initial_state, ensure_ascii=False))

    def _run(self, arguments: Sequence[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ENGINE_PATH), *arguments],
            cwd=self.repo,
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )

    def preview(self, raw: Any) -> Dict[str, Any]:
        payload = normalize_payload(
            raw,
            repo=self.repo,
            env=self.env,
            model_options=self.initial_state["model_options"],
        )
        with self.lock:
            result = self._run(engine_arguments(payload, "--preview"))
            self.preview_digest = _digest(payload) if result.returncode == 0 else None
            self.preview_stdout = result.stdout
            self.preview_stderr = result.stderr
            self.preview_code = result.returncode
        return {
            "ok": result.returncode == 0,
            "code": result.returncode,
            "output": result.stdout,
            "error": result.stderr,
            "files": parse_preview_files(result.stdout, self.repo),
        }

    def apply(self, raw: Any) -> Dict[str, Any]:
        payload = normalize_payload(
            raw,
            repo=self.repo,
            env=self.env,
            model_options=self.initial_state["model_options"],
        )
        with self.lock:
            if self.preview_digest != _digest(payload) or self.preview_code != 0:
                raise UIError('The current selection has no exact preview yet. Click "Preview install" first.')
            fresh = self._run(engine_arguments(payload, "--preview"))
            if (
                fresh.returncode != self.preview_code
                or fresh.stdout != self.preview_stdout
                or fresh.stderr != self.preview_stderr
            ):
                self.preview_digest = None
                raise UIError("File state changed after the preview. Regenerate the preview before confirming.")
            applied = self._run(engine_arguments(payload, "--apply"), timeout=120)
            self.preview_digest = None
            smoke = None
            if applied.returncode == 0 and payload["smoke"]:
                smoke_args = [
                    "--smoke",
                    "--repo",
                    str(self.repo),
                    "--scope",
                    str(payload["scope"]),
                ]
                smoke = self._run(smoke_args, timeout=120)
        return {
            "ok": applied.returncode == 0,
            "code": applied.returncode,
            "output": applied.stdout,
            "error": applied.stderr,
            "smoke": None
            if smoke is None
            else {
                "ok": smoke.returncode == 0,
                "code": smoke.returncode,
                "output": smoke.stdout,
                "error": smoke.stderr,
            },
        }


HTML = (SCRIPT_DIR.parent / "assets" / "setup-ui.html").read_text(encoding="utf-8")


def make_handler(controller: SetupController, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "HandoffSetupUI/1"

        def _host_allowed(self) -> bool:
            host = self.headers.get("Host", "")
            return host.startswith("127.0.0.1:") or host.startswith("localhost:")

        def _authorized(self) -> bool:
            supplied = self.headers.get("X-Handoff-Token")
            if not supplied:
                supplied = parse_qs(urlparse(self.path).query).get("token", [""])[0]
            return self._host_allowed() and hmac.compare_digest(supplied, token)

        def _headers(self, status: int, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()

        def _json(self, status: int, data: Mapping[str, Any]) -> None:
            encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self._headers(status, "application/json; charset=utf-8")
            self.wfile.write(encoded)

        def _drain(self) -> None:
            # Read and discard the request body before an early refusal, so the client
            # gets the rejection JSON instead of a connection reset from unread bytes.
            try:
                remaining = min(int(self.headers.get("Content-Length") or 0), 131072)
            except ValueError:
                return
            self.connection.settimeout(2)
            try:
                while remaining > 0:
                    chunk = self.rfile.read1(remaining)
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except OSError:
                pass

        def _body(self) -> Any:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise UIError("Invalid Content-Length") from None
            if length < 1 or length > 131072:
                raise UIError("Invalid request size")
            try:
                return json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise UIError("Request is not valid JSON") from None

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if not self._authorized():
                self._json(HTTPStatus.FORBIDDEN, {"error": "Invalid local access token"})
                return
            path = urlparse(self.path).path
            if path == "/":
                self._headers(HTTPStatus.OK, "text/html; charset=utf-8")
                self.wfile.write(HTML.encode("utf-8"))
            elif path == "/api/state":
                self._json(HTTPStatus.OK, controller.state())
            elif path == "/favicon.ico":
                self._headers(HTTPStatus.NO_CONTENT, "image/x-icon")
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if not self._authorized():
                self._drain()
                self._json(HTTPStatus.FORBIDDEN, {"error": "Invalid local access token"})
                return
            try:
                body = self._body()
                path = urlparse(self.path).path
                if path == "/api/preview":
                    result = controller.preview(body)
                elif path == "/api/apply":
                    result = controller.apply(body)
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                self._json(HTTPStatus.OK, result)
            except UIError as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except subprocess.TimeoutExpired:
                self._json(HTTPStatus.GATEWAY_TIMEOUT, {"error": "Setup engine timed out"})
            except (OSError, ValueError, engine.handoff_config.ConfigError) as error:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(error)})

        def log_message(self, _format: str, *args: Any) -> None:
            return

    return Handler


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Open the Handoff setup wizard in a local web UI.")
    result.add_argument("--repo", type=Path, default=Path.cwd(), help="Target repository (default: current directory).")
    result.add_argument("--port", type=int, default=0, help="Loopback port (default: choose an available port).")
    result.add_argument("--no-open", action="store_true", help="Print the URL without opening the default browser.")
    return result


def main(argv: Optional[Sequence[str]] = None, env: Optional[Mapping[str, str]] = None) -> int:
    args = parser().parse_args(argv)
    environ = dict(os.environ if env is None else env)
    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"error: repository directory does not exist: {repo}", file=sys.stderr)
        return 2
    try:
        controller = SetupController(repo, environ)
        controller.state()
    except (OSError, ValueError, engine.SetupError, engine.handoff_config.ConfigError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    token = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(controller, token))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}/?token={token}"
    print(f"Handoff Setup UI: {url}", flush=True)
    print("Only localhost can connect. Press Ctrl-C to stop.", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
