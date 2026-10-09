#!/usr/bin/env python3
"""Run the Handoff setup wizard as a local, single-page web UI."""

from __future__ import annotations

import argparse
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


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
        version = (result.stdout or result.stderr).strip().splitlines()[0]
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
    identities = resolved["hosts"][engine.handoff_config.HOST]["identities"]
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
    codex_detected = engine.detect_codex(env)
    claude_detected = engine.detect_claude(env)
    claude_cli = _binary_version(shutil.which("claude", path=env.get("PATH")), env, "PATH")
    codex_cli = _codex_version(env)
    codex_options, codex_discovery = _codex_model_options(codex_cli["path"], env)
    claude_options, claude_discovery = _claude_model_options(
        claude_cli["path"], env, claude_detected
    )
    copilot_options, copilot_discovery = _copilot_model_options(env)
    cursor_options, cursor_discovery = _cursor_model_options(env)
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
            "codex": codex_cli,
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


HTML = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent Handoff Setup</title>
  <style>
    :root {
      color-scheme:light dark;
      --paper:light-dark(#f1f3f6, #141519);
      --surface:light-dark(#fbfcfd, #1c1e24);
      --ink:light-dark(#171a20, #e8e9ec);
      --muted:light-dark(#59616d, #99a1ae);
      --line:light-dark(#cfd5de, #2d3038);
      --rule:light-dark(#171a20, #6c7382);
      --cobalt:light-dark(#1748d2, #93a9ff);
      --cobalt-soft:light-dark(#e9efff, #1e2540);
      --cobalt-line:light-dark(#c6d3fb, #33406c);
      --coral:light-dark(#b53d34, #e2705a);
      --coral-ink:light-dark(#713029, #f0a496);
      --hover:light-dark(#eef1f5, #23262e);
      --ok-soft:light-dark(#e4f1e8, #1b2d22);
      --ok-ink:light-dark(#256240, #5fbe84);
      --ok-line:light-dark(#bcdfc8, #2f5a3f);
      --bad-soft:light-dark(#fff0ed, #32201c);
      --bad-ink:light-dark(#8d3227, #e88b74);
      --bad-line:light-dark(#f0c5b8, #5e3229);
      --serif:Georgia, "Times New Roman", serif;
      --mono:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      --gutter:clamp(1rem,3vw,2.5rem);
    }
    :root[data-theme="light"] { color-scheme:light; }
    :root[data-theme="dark"] { color-scheme:dark; }
    * { box-sizing:border-box; }
    ::selection { background:var(--cobalt-soft); color:var(--ink); }
    html { background:var(--paper); -webkit-font-smoothing:antialiased;
      -moz-osx-font-smoothing:grayscale; text-rendering:optimizeLegibility; }
    body { margin:0; min-width:320px; background:var(--paper); color:var(--ink);
      font:14px/1.6 ui-sans-serif,-apple-system,"Segoe UI",sans-serif; }
    h1,h2,h3,p { margin:0; }
    p { text-wrap:pretty; }
    button,select,input { font:inherit; }
    button { cursor:pointer; -webkit-tap-highlight-color:transparent; }
    button:disabled { background:var(--line); border-color:var(--line); color:var(--muted); cursor:not-allowed; }
    select:focus-visible,input:focus-visible,button:focus-visible,summary:focus-visible,pre:focus-visible,
    .perm-switch input:focus-visible + .perm-track { outline:3px solid var(--cobalt); outline-offset:3px; }
    .shell { max-width:90rem; min-width:0; margin:0 auto 5rem; padding:clamp(16px,2vh,24px) var(--gutter); }
    .masthead { display:flex; justify-content:space-between; align-items:flex-start; gap:24px;
      padding:4px 2px 14px; border-bottom:2px solid var(--rule); }
    .masthead-copy { min-width:0; }
    .masthead h1 { margin:0 0 6px; font:600 clamp(30px,4vw,50px)/.96 var(--serif);
      letter-spacing:-.045em; text-wrap:balance; }
    .masthead .subtitle { max-width:68ch; color:var(--muted); font-size:15px; line-height:1.5; }
    .repo-block { min-width:0; margin-top:12px; }
    .repo-block span,.k,label,.mode strong,.setup-item strong { display:block;
      color:var(--muted); font:750 11px/1.3 var(--mono); letter-spacing:.09em; text-transform:uppercase; }
    .repo-block code { display:block; max-width:100%; color:var(--ink); font:13px/1.55 var(--mono);
      overflow-wrap:anywhere; }
    .masthead-tools { flex:0 0 auto; display:flex; align-items:center; gap:16px; }
    .local-state { color:var(--muted); font:13px/1.55 var(--mono); }
    .segmented { display:flex; gap:2px; padding:3px; border:1px solid var(--line);
      border-radius:8px; background:var(--surface); }
    .segmented button { min-height:44px; padding:0 10px; border:1px solid transparent; border-radius:8px;
      background:transparent; color:var(--muted); font:750 11px/1.3 var(--mono);
      letter-spacing:.09em; text-transform:uppercase; }
    .segmented button[aria-pressed="true"] { background:var(--cobalt-soft); color:var(--cobalt);
      border-color:var(--cobalt-line); }
    .detect { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); margin-top:16px;
      border:1px solid var(--line); border-radius:16px; background:var(--surface); overflow:hidden; }
    .detect .item { min-width:0; padding:12px 14px; border-left:1px solid var(--line); }
    .detect .item:first-child { border-left:0; }
    .k { margin-bottom:5px; }
    .pill { display:inline-block; max-width:100%; padding:.1rem .5rem; border:1px solid;
      border-radius:999px; font:750 11px/1.3 var(--mono); letter-spacing:.06em;
      text-transform:uppercase; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .pill.ok { background:var(--ok-soft); color:var(--ok-ink); border-color:var(--ok-line); }
    .pill.bad { background:var(--bad-soft); color:var(--bad-ink); border-color:var(--bad-line); }
    .pill.cobalt { background:var(--cobalt-soft); color:var(--cobalt); border-color:var(--cobalt-line); }
    /* Wraps instead of truncating: the detail is what the probe found, so none of it is hidden. */
    .detail { min-width:0; margin-top:5px; color:var(--ink); font:13px/1.55 var(--mono); overflow-wrap:anywhere; }
    .detect .item.failed { grid-column:1 / -1; }
    .config-section { margin-top:16px; }
    h2 { color:var(--muted); font:600 15px/1.3 var(--mono); letter-spacing:.06em; text-transform:uppercase; }
    .config-heading { margin-bottom:10px; }
    .config-heading p { max-width:68ch; margin-top:4px; color:var(--muted); }
    .modes { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:5px; padding:5px;
      border:1px solid var(--line); border-radius:16px; background:var(--surface); }
    /* A button centres its content vertically, so a tile whose description wraps would sit its label
       higher than its neighbours'. Stacking from the top keeps all four labels on one baseline. */
    .mode { appearance:none; display:flex; flex-direction:column; justify-content:flex-start;
      min-width:0; min-height:70px; padding:10px 12px;
      border:1px solid transparent; border-radius:8px; background:transparent; color:var(--ink); text-align:left; }
    .mode strong { margin-bottom:4px; color:inherit; }
    .mode small { display:block; color:inherit; font-size:13px; line-height:1.4; overflow-wrap:anywhere; }
    .mode.active { border-color:var(--cobalt-line); background:var(--cobalt-soft); color:var(--cobalt); }
    .config-grid { display:grid; grid-template-columns:minmax(0,1.55fr) minmax(300px,.7fr);
      gap:16px; align-items:start; margin-top:16px; }
    .matrix-panel,.settings-panel,.output-section { min-width:0; border:1px solid var(--line);
      border-radius:16px; background:var(--surface); }
    .main-heading,.settings-head { padding:16px 20px; border-bottom:1px solid var(--line); }
    .main-heading { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; }
    .main-heading p,.settings-head p { max-width:68ch; margin-top:5px; color:var(--muted); }
    .main-heading code { font:13px/1.55 var(--mono); overflow-wrap:anywhere; }
    .current-mode { flex:0 0 auto; color:var(--cobalt); font:13px/1.55 var(--mono); }
    .matrix { padding:0 20px; }
    /* The backend column's floor holds "GitHub Copilot" and the effort column's floor holds "Set by
       the model id" (19 characters of 13px mono plus the select's padding, border and arrow) unclipped. */
    .identity { display:grid; grid-template-columns:minmax(125px,.8fr) minmax(148px,.7fr)
      minmax(180px,1.2fr) minmax(190px,.6fr) minmax(127px,.6fr);
      gap:12px; align-items:start; min-width:0; padding:16px 0; border-bottom:1px solid var(--line); }
    .identity:last-child { border-bottom:0; }
    .identity-head,.field { min-width:0; }
    .identity h3,.review-gates h3 { margin:0 0 4px; color:var(--cobalt);
      font:750 11px/1.3 var(--mono); letter-spacing:.09em; text-transform:uppercase; }
    .identity-head small { display:block; color:var(--muted); font-size:13px; line-height:1.4; }
    .identity-code { display:block; margin-top:6px; color:var(--muted); font:13px/1.55 var(--mono);
      overflow-wrap:anywhere; }
    label { margin-bottom:6px; }
    select,input[type=text],input[type=number] { width:100%; min-width:0; min-height:44px;
      padding:8px 10px; border:1px solid var(--line); border-radius:8px;
      background:var(--surface); color:var(--ink); font:13px/1.55 var(--mono); }
    input[type=text],input[type=number] { caret-color:var(--cobalt); }
    input[type=number] { font-variant-numeric:tabular-nums; }
    input[type=checkbox] { accent-color:var(--cobalt); }
    .source { margin-top:5px; color:var(--muted); font:13px/1.45 var(--mono); overflow-wrap:anywhere; }
    .field-error { margin-top:5px; color:var(--bad-ink); font:13px/1.45 var(--mono); overflow-wrap:anywhere; }
    [aria-invalid="true"] { border-color:var(--bad-line); }
    /* The track is one fixed size in both states, so flipping the switch moves nothing around it.
       --perm-label is the longer label ("Allow all", 9 characters) in the track's own mono type;
       the knob and the label slide with transform, which never touches layout. */
    .perm-switch { position:relative; width:max-content; max-width:100%; margin:0; cursor:pointer; }
    .perm-switch input { position:absolute; width:1px; height:1px; margin:0; opacity:0; }
    .perm-track { --perm-label:calc(9ch + 9 * .09em); position:relative; display:flex; align-items:center;
      width:calc(58px + var(--perm-label)); min-height:44px; padding:0 44px 0 12px;
      border:1px solid var(--line); border-radius:999px; background:var(--surface); color:var(--muted);
      font:750 11px/1.3 var(--mono); letter-spacing:.09em; text-transform:uppercase; white-space:nowrap; }
    .perm-track::before { content:""; position:absolute; left:5px; top:50%; width:32px; height:32px;
      border-radius:50%; background:var(--line); transform:translateY(-50%); }
    .perm-track [data-value] { transform:translateX(32px); }
    .perm-switch input:checked + .perm-track { border-color:var(--bad-line); background:var(--bad-soft);
      color:var(--coral-ink); }
    .perm-switch input:checked + .perm-track::before { background:var(--surface);
      transform:translate(calc(14px + var(--perm-label)),-50%); }
    .perm-switch input:checked + .perm-track [data-value] { transform:none; }
    .perm-switch input:checked + .perm-track [data-value="default"],
    .perm-switch input:not(:checked) + .perm-track [data-value="allow-all"] { display:none; }
    .permission-hint,.permission-note { margin-top:6px; color:var(--muted); font-size:13px; line-height:1.5; }
    .permission-note { margin:12px 20px; }
    .e2e-addon,.review-gates { margin:0 20px; padding:16px 0; border-top:1px solid var(--line); }
    .e2e-addon > summary { width:max-content; max-width:100%; color:var(--ink);
      font:750 11px/1.3 var(--mono); letter-spacing:.09em; text-transform:uppercase; cursor:pointer; }
    .e2e-addon > p,.review-gates > p { max-width:68ch; margin-top:6px; color:var(--muted); }
    .e2e-toggle { display:flex; align-items:center; gap:8px; width:max-content; max-width:100%;
      margin:12px 0 0; color:var(--ink); font:13px/1.55 var(--mono); cursor:pointer; }
    .e2e-addon .matrix { padding:8px 0 0; }
    .review-gates h3 { color:var(--cobalt); }
    .review-caps { display:flex; flex-wrap:wrap; gap:12px; margin-top:14px; }
    .review-caps .field { flex:1 1 200px; }
    .settings-panel { position:sticky; top:18px; }
    .settings-body { padding:0 20px; }
    .warning-banner { margin:12px 20px; padding:10px 12px; border-left:3px solid var(--coral);
      background:var(--bad-soft); color:var(--coral-ink); font-size:13px; line-height:1.5; white-space:pre-line; }
    .warning-banner p + p { margin-top:6px; }
    .setup-summary { list-style:none; margin:0; padding:0; }
    .setup-item { padding:12px 0; border-bottom:1px solid var(--line); }
    .setup-item:last-child { border-bottom:0; }
    .setup-item strong { color:var(--ink); }
    .setup-item small { display:block; margin-top:3px; color:var(--muted); font-size:13px; line-height:1.45; overflow-wrap:anywhere; }
    .choice { display:flex; align-items:flex-start; gap:8px; color:var(--ink); font-size:13px; cursor:pointer; }
    .choice input { margin:2px 0 0; }
    .actions { display:grid; gap:10px; margin:12px 20px 18px; padding:14px 0 0; border-top:1px solid var(--line); }
    .status,.install-reason { color:var(--muted); font-size:13px; }
    .link-button { min-height:44px; padding:0 8px; border:0; background:transparent;
      color:var(--cobalt); font:12px var(--mono); text-decoration:underline; text-underline-offset:2px; }
    button.primary,button.apply { min-height:44px; padding:9px 16px; border:1px solid var(--cobalt);
      border-radius:8px; background:var(--cobalt); color:var(--surface); font:750 11px/1.3 var(--mono);
      letter-spacing:.09em; text-transform:uppercase; }
    button.primary { width:100%; }
    button.primary:disabled,button.apply:disabled { background:var(--line); border-color:var(--line);
      color:var(--muted); cursor:not-allowed; }
    .output-section { display:none; margin-top:16px; padding:20px; }
    .output-section h2 { margin-bottom:10px; }
    /* The script moves focus here after Preview and Install. :focus-visible draws the ring after that
       programmatic focus only when the last interaction was the keyboard, never after a mouse click. */
    .output-section h2:focus-visible { outline:3px solid var(--cobalt); outline-offset:3px; }
    .output-section.stale { opacity:.5; }
    .preview-plan { list-style:none; margin:12px 0; padding:0 12px; border:1px solid var(--line);
      border-radius:8px; background:var(--surface); }
    .preview-plan .setup-item { display:flex; align-items:baseline; gap:12px; min-width:0; }
    /* The state column is fixed at a width that holds UNCHANGED, so every path starts at one x. */
    .preview-plan .file-row { display:grid; grid-template-columns:5.5rem minmax(0,1fr); }
    .preview-plan code { font:13px/1.55 var(--mono); overflow-wrap:anywhere; }
    /* An identity row's Allow all pill sits on the code's own line, 8px after it. */
    .preview-plan code + .state-pill { display:inline-block; margin-left:8px; }
    .state-pill { flex:0 0 auto; justify-self:start; padding:2px 7px; border:1px solid var(--line);
      border-radius:999px; color:var(--muted); font:750 11px/1.3 var(--mono); }
    .state-pill.write { border-color:var(--cobalt-line); background:var(--cobalt-soft); color:var(--cobalt); }
    .state-pill.delete,.state-pill.refused { border-color:var(--bad-line); background:var(--bad-soft); color:var(--coral-ink); }
    .preview-explainer { color:var(--muted); font-size:13px; line-height:1.5; }
    .technical-details { margin-top:12px; }
    .technical-details summary { width:max-content; max-width:100%; color:var(--cobalt);
      font:750 11px/1.3 var(--mono); letter-spacing:.09em; text-transform:uppercase; cursor:pointer; }
    .technical-details pre { margin-top:12px; }
    pre { max-height:430px; margin:0; overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere;
      padding:12px; border:1px solid var(--line); border-radius:8px; background:var(--surface);
      color:var(--ink); font:13px/1.55 var(--mono); }
    .confirm { display:none; align-items:center; justify-content:flex-end; flex-wrap:wrap; gap:14px; margin-top:12px; }
    .install { display:flex; align-items:center; gap:12px; }
    .confirm label { margin:0; color:var(--ink); font:13px/1.55 var(--mono);
      letter-spacing:0; text-transform:none; }
    .result pre { border:0; border-left:3px solid var(--cobalt); border-radius:0 8px 8px 0;
      background:var(--cobalt-soft); }
    .output-section.has-error pre { border:0; border-left:3px solid var(--coral);
      border-radius:0 8px 8px 0; background:var(--bad-soft); color:var(--coral-ink); }
    .loading-copy { padding:12px 0; color:var(--muted); }
    /* The install verdict: two stamped words (written, checked), then how to undo it.
       The session view closes a run with the same stamp. */
    .verdict { display:flex; flex-wrap:wrap; align-items:center; gap:6px 10px; margin:0 0 12px;
      color:var(--ink); font:13px/1.5 var(--mono); }
    .verdict .pill { padding:.2rem .65rem; font-size:12px; letter-spacing:.09em; text-transform:uppercase; }
    .verdict code { font:inherit; color:var(--cobalt); overflow-wrap:anywhere; }
    @media (hover:hover) and (pointer:fine) {
      .mode:not(.active):hover,.segmented button:not([aria-pressed="true"]):hover { background:var(--hover); }
      select:hover,input[type=text]:hover,input[type=number]:hover { border-color:var(--muted); }
      /* ink inverts with the theme, so the same mix is a darker cobalt in light and a lighter one in dark. */
      button.primary:not(:disabled):hover,button.apply:not(:disabled):hover {
        background:color-mix(in oklab,var(--cobalt) 82%,var(--ink));
        border-color:color-mix(in oklab,var(--cobalt) 82%,var(--ink)); }
    }
    @media (prefers-reduced-motion:no-preference) {
      html { scroll-behavior:smooth; }
      .masthead { animation:masthead-in 460ms cubic-bezier(.2,.72,.2,1) both; }
      button:active:not(:disabled) { transform:scale(.97); }
      .verdict .pill { animation:stamp 380ms cubic-bezier(.2,.9,.25,1.25) both; }
      .verdict .pill + .pill { animation-delay:140ms; }
      .perm-track { transition:background-color 180ms ease-out, border-color 180ms ease-out, color 180ms ease-out; }
      .perm-track::before { transition:transform 180ms ease-out, background-color 180ms ease-out; }
    }
    @keyframes masthead-in { from { transform:translateY(10px); } to { transform:none; } }
    @keyframes stamp { from { transform:scale(1.18) rotate(-3deg); } to { transform:none; } }
    @media (max-width:1368px) {
      .identity { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .identity-head,.field.model-field { grid-column:1 / -1; }
    }
    @media (max-width:1040px) {
      .config-grid { grid-template-columns:minmax(0,1fr); }
      .settings-panel { position:static; }
    }
    @media (max-width:780px) {
      .masthead { flex-wrap:wrap; gap:12px; }
      .masthead-tools { width:100%; justify-content:space-between; flex-wrap:wrap; gap:8px; }
      .detect { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .detect .item { border-top:1px solid var(--line); }
      .detect .item:nth-child(odd) { border-left:0; }
      .detect .item:nth-child(1),.detect .item:nth-child(2) { border-top:0; }
      .modes { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .mode { min-height:62px; }
      .main-heading { flex-wrap:wrap; }
      .confirm { align-items:stretch; flex-direction:column; }
      .install { align-items:stretch; flex-direction:column; gap:8px; }
    }
    @media (max-width:500px) {
      .modes { grid-template-columns:minmax(0,1fr); }
      .shell { margin-bottom:3rem; }
      .segmented button { padding:0 8px; }
      .detect { grid-template-columns:minmax(0,1fr); }
      .detect .item,.detect .item:nth-child(2) { display:grid; grid-template-columns:minmax(0,1fr) auto;
        align-items:center; column-gap:12px; padding:10px; border-top:1px solid var(--line); border-left:0; }
      .detect .item:first-child { border-top:0; }
      .detect .k { margin-bottom:0; }
      .detect .pill { justify-self:end; }
      .detect .detail { grid-column:1 / -1; }
      .preview-plan .file-row { grid-template-columns:minmax(0,1fr); gap:4px; }
      .identity { grid-template-columns:minmax(0,1fr); }
      .identity-head,.field.model-field { grid-column:1; }
      .main-heading,.settings-head { padding:14px; }
      .matrix,.settings-body { padding-left:14px; padding-right:14px; }
      .e2e-addon,.review-gates { margin-left:14px; margin-right:14px; }
      .actions { margin-left:14px; margin-right:14px; }
      .output-section { padding:14px; }
      button.primary,button.apply { width:100%; }
    }
    @media (prefers-reduced-motion:reduce) {
      html { scroll-behavior:auto; }
      *,*::before,*::after { animation:none!important; transition:none!important; }
    }
  </style>
  <script>
    (() => {
      let preference = 'system';
      try { preference = localStorage.getItem('handoff-theme') || 'system'; } catch (error) { preference = 'system'; }
      if (!['system','light','dark'].includes(preference)) preference = 'system';
      const dark = preference === 'dark' || (preference === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
      document.documentElement.dataset.theme = dark ? 'dark' : 'light';
      document.documentElement.dataset.themePreference = preference;
    })();
  </script>
</head>
<body>
  <main class="shell">
    <header class="masthead" aria-label="Handoff setup">
      <div class="masthead-copy">
        <h1>Set up Agent Handoff</h1>
        <p class="subtitle">Pick the CLI, model and effort for each identity. Nothing is written until you confirm the exact diff.</p>
        <div class="repo-block"><span>Project</span><code id="repo"></code></div>
      </div>
      <div class="masthead-tools">
        <span class="local-state">Runs on this machine only</span>
        <div class="segmented" id="themeSwitch" role="group" aria-label="Theme">
          <button type="button" data-theme-choice="system">System</button>
          <button type="button" data-theme-choice="light">Light</button>
          <button type="button" data-theme-choice="dark">Dark</button>
        </div>
      </div>
    </header>

    <section class="detect" id="detect" aria-label="Local environment detection">
      <p class="loading-copy">Reading the local environment...</p>
    </section>

    <section class="config-section" id="configWorkspace" aria-busy="true">
      <div class="config-heading"><h2>Pick a work mode</h2><p>Start from a recommended combination, or set each identity's CLI, model, effort, and permission directly.</p></div>
      <div class="modes" id="modes" role="group" aria-label="Work mode"><p class="loading-copy">Building modes...</p></div>

      <div class="config-grid">
        <section class="matrix-panel" aria-labelledby="matrixTitle">
          <div class="main-heading">
            <div><h2 id="matrixTitle">Identities</h2><span id="identityCount">3 configured</span><p>Codex models are read from your local account, Claude models use the official CLI aliases, Copilot models come from your entitlement, and Cursor models from <code>cursor-agent models</code>. A Cursor model carries its own effort.</p></div>
            <span class="current-mode" id="currentMode">Current mode: loading</span>
          </div>
          <div class="matrix" id="identities"><p class="loading-copy">Reading available models...</p></div>
          <details id="e2eAddOn" class="e2e-addon">
            <summary>E2E acceptance testing (optional)</summary>
            <p>Adds two identities that write and run acceptance tests for
               user-observable changes. Leave off if you do not run end-to-end tests.</p>
            <label class="e2e-toggle"><input type="checkbox" id="withE2e"> Configure e2e identities</label>
            <div class="matrix" id="e2eCards"></div>
          </details>
          <p class="permission-note">Permission applies to writing jobs. Review and other read-only jobs always run read-only.</p>
          <div class="review-gates">
            <h3>Review gates</h3>
            <p>Every plan gets one read from deep_reasoner before you see it, and every delegated diff gets the driver's review. When a gate runs out of passes without agreement, the arbiter rules.</p>
            <div class="review-caps">
              <div class="field"><label for="specMaxRounds">Spec review passes</label><input id="specMaxRounds" type="number" min="1" step="1" inputmode="numeric" aria-describedby="specMaxRoundsHint specMaxRoundsError"><p class="source" id="specMaxRoundsHint">Reads of the plan before the arbiter rules. Default 1.</p><p class="field-error" id="specMaxRoundsError" hidden></p></div>
              <div class="field"><label for="implementationMaxRounds">Implementation review passes</label><input id="implementationMaxRounds" type="number" min="1" step="1" inputmode="numeric" aria-describedby="implementationMaxRoundsHint implementationMaxRoundsError"><p class="source" id="implementationMaxRoundsHint">Reads of each diff, fix rounds included. Default 3.</p><p class="field-error" id="implementationMaxRoundsError" hidden></p></div>
            </div>
          </div>
        </section>

        <aside class="settings-panel" aria-label="Pre-install confirmation">
          <div class="settings-head"><h2 id="settingsTitle">Ready to install</h2><p>Project scope, kept out of Git, checked after install.</p></div>
          <div class="warning-banner" id="permissionWarning" hidden></div>

          <div class="settings-body">
            <ul class="setup-summary">
              <li class="setup-item"><span><strong>Configures this project only</strong><small>No other project on your machine is touched.</small></span></li>
              <li class="setup-item"><span><strong>Config stays on this machine</strong><small>Your personal model settings are never committed to Git.</small></span></li>
              <li class="setup-item"><span><strong>Automatic check after install</strong><small>Confirms Handoff can read the new config; failures show the reason.</small></span></li>
            </ul>
          </div>
          <div class="actions">
            <span class="status" id="status" role="status" aria-live="polite">No files changed yet</span>
            <button class="primary" id="previewBtn">Preview install</button>
          </div>
        </aside>
      </div>

      <div class="output-section" id="previewWrap" aria-live="polite">
        <h2 id="previewTitle" tabindex="-1">Files that will change</h2>
        <div class="warning-banner" id="previewError" hidden></div>
        <ul class="preview-plan" id="previewPlan"></ul>
        <p class="preview-explainer" id="previewExplainer" hidden>Install backs up each file it changes under .handoff/backups/, then runs a check that records each identity's verification in .handoff/config.toml.</p>
        <details class="technical-details" id="technicalDetails">
          <summary id="technicalSummary">Show the full diff</summary>
          <pre id="preview"></pre>
        </details>
        <div class="confirm" id="confirm">
          <label class="choice"><input type="checkbox" id="confirmed"> I confirm installing into this project</label>
          <div class="install">
            <span class="install-reason" id="installReason">Tick the confirmation to install.</span>
            <button class="apply" id="apply" aria-describedby="installReason" disabled>Install and check</button>
          </div>
        </div>
      </div>

      <div class="output-section result" id="resultWrap" aria-live="polite">
        <h2 id="resultTitle" tabindex="-1">Result</h2>
        <p class="verdict" id="resultVerdict" hidden></p>
        <pre id="result"></pre>
      </div>
    </section>
  </main>
  <script>
    const token = new URLSearchParams(location.search).get('token');
    let state;
    let mode = 'balanced';
    let matrix = {};
    let previewValid = false;
    let previewRequest = 0;
    let previewLoading = false;
    let undoPreset = null;
    const MODE_LABELS = {balanced:'Balanced',quality:'Quality',cost:'Cost',custom:'Custom'};
    const PRESET_MODES = ['balanced','quality','cost','custom'];
    const MODE_DESCRIPTIONS = {
      balanced:'Claude leads, Codex executes',
      quality:'More work goes to Claude',
      cost:'Codex runs most of it, Claude backs it up',
      custom:'Set each identity by hand',
    };
    const BACKEND_LABELS = {claude:'Claude Code', codex:'Codex', copilot:'GitHub Copilot', cursor:'Cursor'};
    const BACKEND_LABELS_ORDER = ['claude','codex','copilot','cursor'];
    const PERMISSION_HINTS = {
      claude: {
        default:'Runs in dontAsk mode with Read, Glob, Grep, Edit, Write and Bash only.',
        'allow-all':'Bypasses Claude Code permission checks entirely.',
      },
      codex: {
        default:'Uses your own Codex config. Handoff adds no sandbox flag.',
        'allow-all':'Runs with no approval prompts and no sandbox.',
      },
      copilot: {
        default:"Allows all tools but keeps Copilot's path and URL checks.",
        'allow-all':'Allows all tools, all paths and all URLs.',
      },
      cursor: {
        default:'Cursor runs in force mode either way. Only deny rules in .cursor/cli.json narrow it.',
        'allow-all':'Cursor runs in force mode either way. Only deny rules in .cursor/cli.json narrow it.',
      },
    };
    const EFFORT_LABELS = {
      none:'None',
      minimal:'Minimal',
      low:'Low',
      medium:'Medium',
      high:'High',
      xhigh:'Extra high',
      max:'Max',
      model:'Set by the model id',
    };
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const systemDark = window.matchMedia('(prefers-color-scheme: dark)');
    let themePreference = document.documentElement.dataset.themePreference || 'system';

    function applyTheme(preference) {
      themePreference = preference;
      const dark = preference === 'dark' || (preference === 'system' && systemDark.matches);
      document.documentElement.dataset.theme = dark ? 'dark' : 'light';
      document.documentElement.dataset.themePreference = preference;
      document.querySelectorAll('#themeSwitch button').forEach(el =>
        el.setAttribute('aria-pressed', String(el.dataset.themeChoice === preference)));
    }
    document.querySelectorAll('#themeSwitch button').forEach(el => el.addEventListener('click', () => {
      applyTheme(el.dataset.themeChoice);
      try { localStorage.setItem('handoff-theme', el.dataset.themeChoice); } catch (error) { /* private window: this session only */ }
    }));
    systemDark.addEventListener('change', () => { if (themePreference === 'system') applyTheme('system'); });
    applyTheme(themePreference);
    const $ = (id) => document.getElementById(id);
    const clone = (value) => JSON.parse(JSON.stringify(value));

    function esc(value) {
      return String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
    }
    function modeSummary(name) {
      return esc(MODE_DESCRIPTIONS[name]);
    }
    function sourceLabel(source) {
      return ({
        'detected':'Detected locally',
        'built-in alias':'Built-in alias',
        'existing config':'Existing config',
        'codex model/list':'Read from Codex CLI',
        'claude --help':'Official Claude CLI aliases',
        'local claude config':'Local Claude config',
        'copilot models':'Read from your Copilot entitlement',
        'cursor-agent models':'Read from cursor-agent models',
        'custom (required)':'Not detected yet',
        'built-in':'Built-in value',
      })[source] || source;
    }
    // A retained model fills the list, so a failed discovery is named beside
    // the source rather than only on an empty list.
    function sourceText(backend, source) {
      return `Source: ${sourceLabel(source)}`
        + (state.model_discovery_failed[backend] ? ` · ${state.model_discovery[backend] || ''}` : '');
    }
    function modelCatalog(backend, current, source) {
      const options = clone(state.model_options[backend] || []);
      if (current && !options.some(option => option.value === current)) {
        options.unshift({value:current,label:current,source:source || 'existing config'});
      }
      return options;
    }
    function modelOption(backend, value) {
      return modelCatalog(backend, value, 'existing config').find(option => option.value === value);
    }
    function modelOptionLabel(option) {
      return option.label === option.value ? option.value : `${option.value} · ${option.label}`;
    }
    function effortCatalog(backend, model) {
      const option = modelOption(backend, model);
      if (option && option.efforts && option.efforts.length) return option.efforts;
      return state.efforts_by_backend[backend] || [];
    }
    function syncEffort(values) {
      const efforts = effortCatalog(values.backend, values.model);
      if (!efforts.includes(values.effort)) {
        values.effort = efforts.includes('high') ? 'high' : (efforts[0] || '');
      }
      return efforts;
    }
    function syncReadiness() {
      const ready = activeIdentities().every(identity => {
        const values = matrix[identity];
        return values.model && values.effort && (values.backend !== 'cursor'
          || (state.model_options.cursor || []).some(option => option.value === values.model));
      }) && ['specMaxRounds', 'implementationMaxRounds'].every(id => capIsValid(id));
      $('previewBtn').disabled = !ready || previewLoading;
      if (!ready && !undoPreset && activeIdentities().some(identity => !matrix[identity].model))
        $('status').textContent = 'Every identity needs a model. Follow the instruction under each empty list, then start the wizard again; this page holds the list it was opened with.';
    }
    function capIsValid(id) {
      const input = $(id);
      return input.value.trim() !== '' && Number.isInteger(Number(input.value))
        && Number(input.value) >= 1 && input.getAttribute('aria-invalid') !== 'true';
    }
    function markCapError(id, message) {
      const error = $(`${id}Error`);
      $(id).setAttribute('aria-invalid', 'true');
      error.textContent = message;
      error.hidden = false;
    }
    function validateCap(id) {
      const input = $(id);
      const valid = input.value.trim() !== '' && Number.isInteger(Number(input.value)) && Number(input.value) >= 1;
      if (valid) {
        input.removeAttribute('aria-invalid');
        $(`${id}Error`).textContent = '';
        $(`${id}Error`).hidden = true;
      } else {
        markCapError(id, 'Enter a whole number, 1 or more.');
      }
    }
    function markServerCapErrors(message) {
      for (const [key, id] of [['spec_max_rounds', 'specMaxRounds'], ['implementation_max_rounds', 'implementationMaxRounds']]) {
        if (message.includes(key)) markCapError(id, message);
      }
    }
    function syncModeControls() {
      document.querySelectorAll('.mode').forEach(el => {
        const active = el.dataset.mode === mode;
        el.classList.toggle('active', active);
        el.setAttribute('aria-pressed', String(active));
      });
      $('currentMode').textContent = `Current mode: ${MODE_LABELS[mode]}`;
    }
    function activeIdentities() {
      return $('withE2e').checked
        ? state.core_identities.concat(state.optional_identities)
        : state.core_identities;
    }
    function syncPermissionWarning() {
      const active = activeIdentities();
      $('identityCount').textContent = `${active.length} configured`;
      // Cursor identities are named in the Cursor sentence below: Cursor runs writing jobs in
      // force mode whatever the Allow all switch says, so listing them here would name them twice.
      const allowAll = active.filter(identity => matrix[identity].permission_mode === 'allow-all'
        && matrix[identity].backend !== 'cursor');
      const cursor = active.filter(identity => matrix[identity].backend === 'cursor');
      const sentences = [];
      if (allowAll.length) {
        const names = allowAll.map(identity => `${identity} (${BACKEND_LABELS[matrix[identity].backend]})`).join(', ');
        sentences.push(`${allowAll.length} ${allowAll.length === 1 ? 'identity runs' : 'identities run'} with Allow all: ${names}. ${allowAll.length === 1 ? "Its writing jobs skip that backend's permission checks." : "Their writing jobs skip those backends' permission checks."}`);
      }
      if (cursor.length) {
        sentences.push(`${cursor.length} ${cursor.length === 1 ? 'identity runs' : 'identities run'} on Cursor: ${cursor.join(', ')}. Cursor runs writing jobs in force mode either way.`);
      }
      $('permissionWarning').innerHTML = sentences.map(sentence => `<p>${esc(sentence)}</p>`).join('');
      $('permissionWarning').hidden = !sentences.length;
      $('settingsTitle').textContent = sentences.length ? 'Review before install' : 'Ready to install';
    }
    // The reason is true only while the box is unticked. aria-describedby leaves with the
    // hidden line, since a screen reader still reads a hidden element it is pointed at.
    function setInstallEnabled(enabled) {
      $('apply').disabled = !enabled;
      const reason = !enabled && !$('confirmed').checked;
      $('installReason').hidden = !reason;
      if (reason) $('apply').setAttribute('aria-describedby', 'installReason');
      else $('apply').removeAttribute('aria-describedby');
    }
    function invalidate() {
      undoPreset = null;
      previewRequest++;
      previewValid = false;
      $('confirmed').checked = false;
      setInstallEnabled(false);
      $('confirm').style.display = 'none';
      if ($('previewWrap').style.display === 'block') $('previewWrap').classList.add('stale');
      $('technicalDetails').open = false;
      $('status').textContent = 'Selection changed. Generate a new preview.';
      syncReadiness();
    }
    function selectMode(next) {
      // Only a custom matrix holds hand edits (or the config the page opened with);
      // a preset-to-preset switch overwrites nothing the user wrote, so it offers no Undo.
      const previous = next !== 'custom' && mode === 'custom' && Object.keys(matrix).some(identity =>
        ['backend', 'model', 'effort', 'permission_mode'].some(field =>
          matrix[identity][field] !== state.presets[next][identity][field]))
        ? {mode, matrix:clone(matrix)} : null;
      mode = next;
      if (next !== 'custom') matrix = clone(state.presets[next]);
      syncModeControls();
      renderIdentities();
      invalidate();
      if (previous) {
        undoPreset = previous;
        $('status').innerHTML = `${MODE_LABELS[next]} preset replaced your custom settings. <button type="button" class="link-button" id="undoPreset">Undo</button>`;
        $('undoPreset').addEventListener('click', () => {
          mode = undoPreset.mode;
          matrix = undoPreset.matrix;
          syncModeControls();
          renderIdentities();
          invalidate();
        });
      }
    }
    function renderIdentities() {
      renderCards('identities', state.core_identities);
      renderCards('e2eCards', $('withE2e').checked ? state.optional_identities : []);
      bindIdentityInputs();
      syncPermissionWarning();
    }
    function effortOptionsHtml(efforts, current) {
      return efforts.map(e => `<option value="${e}" ${current === e ? 'selected' : ''}>${esc(EFFORT_LABELS[e] || e)}</option>`).join('');
    }
    function renderEffortField(card, identity, efforts) {
      const select = card.querySelector('select[data-field="effort"]');
      if (select) select.innerHTML = effortOptionsHtml(efforts, matrix[identity].effort);
    }
    function renderBackendFields(card, identity) {
      const values = matrix[identity];
      card.querySelector('.model-field').innerHTML = modelFieldHtml(identity, values);
      bindIdentityInput(card.querySelector('[data-field="model"]'));
      validateModelField(card, identity);
      renderEffortField(card, identity, syncEffort(values));
      card.querySelector('select[data-field="effort"]').disabled = values.backend === 'cursor' || !effortCatalog(values.backend, values.model).length;
      card.querySelector('.permission-hint').textContent = PERMISSION_HINTS[values.backend][values.permission_mode || 'default'];
    }
    function modelFieldHtml(identity, values) {
      const models = values.backend === 'cursor'
        ? state.model_options.cursor || []
        : modelCatalog(values.backend, values.model, values.model_source);
      const verified = models.find(option => option.value === values.model);
      const source = verified ? verified.source : (values.model_source || 'existing config');
      const sourceLine = models.length
        ? esc(sourceText(values.backend, source))
        : esc(state.model_discovery[values.backend] || 'No models available');
      const control = values.backend === 'cursor'
        ? `<input type="text" id="${identity}-model" data-field="model" list="${identity}-models" value="${esc(values.model)}" aria-describedby="${identity}-source" autocomplete="off" spellcheck="false" autocapitalize="off" ${models.length ? '' : 'disabled'}><datalist id="${identity}-models">${models.map(option => `<option value="${esc(option.value)}" label="${esc(option.label)}"></option>`).join('')}</datalist>`
        : `<select id="${identity}-model" data-field="model" aria-describedby="${identity}-source" ${models.length ? '' : 'disabled'}>${models.length ? models.map(option => `<option value="${esc(option.value)}" ${values.model === option.value ? 'selected' : ''}>${esc(modelOptionLabel(option))}</option>`).join('') : '<option value="">No models available</option>'}</select>`;
      return `<label for="${identity}-model">Model</label>${control}<div class="source" id="${identity}-source">${sourceLine}</div><p class="field-error" id="${identity}-model-error" hidden></p>`;
    }
    function validateModelField(card, identity) {
      const control = card.querySelector('[data-field="model"]');
      const error = card.querySelector('.field-error');
      const invalid = matrix[identity].backend === 'cursor' && !control.disabled
        && !(state.model_options.cursor || []).some(option => option.value === matrix[identity].model);
      if (invalid) {
        control.setAttribute('aria-invalid', 'true');
        control.setAttribute('aria-describedby', `${identity}-source ${identity}-model-error`);
        error.textContent = 'Not in `cursor-agent models`. Pick a slug from the list.';
      } else {
        control.removeAttribute('aria-invalid');
        control.setAttribute('aria-describedby', `${identity}-source`);
        error.textContent = '';
      }
      error.hidden = !invalid;
    }
    function renderCards(container, names) {
      $(container).innerHTML = names.map(identity => {
        const meta = state.identity_meta[identity];
        const values = matrix[identity];
        const efforts = syncEffort(values);
        return `<article class="identity" data-identity="${identity}">
          <div class="identity-head"><h3>${esc(meta.label)}</h3><small>${esc(meta.hint)}</small><code class="identity-code">${identity}</code></div>
          <div class="field"><label for="${identity}-backend">Runs on</label><select id="${identity}-backend" data-field="backend">${BACKEND_LABELS_ORDER.map(b => `<option value="${b}" ${values.backend === b ? 'selected' : ''}>${BACKEND_LABELS[b]}</option>`).join('')}</select></div>
          <div class="field model-field">${modelFieldHtml(identity, values)}</div>
          <div class="field"><label for="${identity}-effort">Effort</label><select id="${identity}-effort" data-field="effort" ${efforts.length && values.backend !== 'cursor' ? '' : 'disabled'}>${effortOptionsHtml(efforts, values.effort)}</select></div>
          <div class="field"><label for="${identity}-permission">Permission</label><label class="perm-switch"><input type="checkbox" role="switch" id="${identity}-permission" data-field="permission_mode" aria-describedby="${identity}-permission-hint" ${(values.permission_mode || 'default') === 'allow-all' ? 'checked' : ''}><span class="perm-track">${state.permission_modes.map(p => `<span data-value="${p}">${esc(state.permission_labels[p])}</span>`).join('')}</span></label><p class="permission-hint" id="${identity}-permission-hint">${esc(PERMISSION_HINTS[values.backend][values.permission_mode || 'default'])}</p></div>
        </article>`;
      }).join('');
    }
    function bindIdentityInputs() {
      document.querySelectorAll('.identity select, .identity input[data-field]').forEach(bindIdentityInput);
      document.querySelectorAll('.identity').forEach(card => validateModelField(card, card.dataset.identity));
    }
    function bindIdentityInput(control) {
      control.addEventListener('input', event => {
        const card = event.target.closest('.identity');
        const identity = card.dataset.identity;
        const field = event.target.dataset.field;
        matrix[identity][field] = field === 'permission_mode'
          ? (event.target.checked ? 'allow-all' : 'default')
          : event.target.value;
        if (field === 'backend') {
          const options = modelCatalog(event.target.value, '', '');
          const selected = options.find(option => option.is_default) || options[0];
          matrix[identity].model = selected ? selected.value : '';
          matrix[identity].model_source = selected ? selected.source : 'custom (required)';
          syncEffort(matrix[identity]);
        } else if (field === 'model') {
          const selected = matrix[identity].backend === 'cursor'
            ? (state.model_options.cursor || []).find(option => option.value === event.target.value)
            : modelOption(matrix[identity].backend, event.target.value);
          matrix[identity].model_source = selected ? selected.source : 'custom (required)';
          // syncEffort may move the effort onto one this model accepts, so the
          // control is rebuilt rather than left showing the previous list.
          renderEffortField(card, identity, syncEffort(matrix[identity]));
        }
        mode = 'custom';
        syncModeControls();
        if (field === 'backend') renderBackendFields(card, identity);
        if (field === 'model') {
          card.querySelector('.source').textContent = sourceText(matrix[identity].backend, matrix[identity].model_source);
          validateModelField(card, identity);
        }
        if (field === 'permission_mode') card.querySelector('.permission-hint').textContent = PERMISSION_HINTS[matrix[identity].backend][matrix[identity].permission_mode];
        syncPermissionWarning();
        invalidate();
      });
    }
    function payload() {
      const withE2e = $('withE2e').checked;
      const identities = {};
      for (const identity of activeIdentities()) {
        identities[identity] = {
          backend: matrix[identity].backend,
          model: matrix[identity].model,
          effort: matrix[identity].effort,
          permission_mode: matrix[identity].permission_mode || 'default',
        };
      }
      return {
        mode,
        identities,
        with_e2e: withE2e,
        review: {
          spec_max_rounds: Number($('specMaxRounds').value),
          implementation_max_rounds: Number($('implementationMaxRounds').value),
        },
        scope: 'project',
        exclude_choice: 'git-exclude',
        write_agents: state.write_agents_available,
        smoke: true,
        routing_action: 'none',
      };
    }
    function renderPreviewPlan(files, submitted) {
      const descriptions = [
        ['.handoff/config.toml', 'Identity and review settings'],
        ['.handoff/.generated-manifest', 'Records which agent files Handoff generated'],
        ['.git/info/exclude', 'Keeps the config out of Git'],
      ];
      const fileRows = files.map(file => {
        const description = descriptions.find(([suffix]) => file.path.endsWith(suffix))?.[1]
          || (/\.claude\/agents\/handoff-[^/]+\.md$/.test(file.path) ? 'Claude Code subagent definition' : '');
        return `<li class="setup-item file-row"><span class="state-pill ${esc(file.state.toLowerCase())}">${esc(file.state)}</span><span><code>${esc(file.display)}</code>${description ? `<small>${esc(description)}</small>` : ''}</span></li>`;
      });
      const identityRows = Object.entries(submitted.identities).map(([identity, values]) =>
        `<li class="setup-item"><span><code>${esc(identity)}</code>${values.permission_mode === 'allow-all' ? '<span class="state-pill refused">Allow all</span>' : ''}<small>${esc(values.backend)} · ${esc(values.model)} · ${esc(values.effort)}</small></span></li>`);
      $('previewPlan').innerHTML = fileRows.concat(identityRows).join('');
      $('previewPlan').hidden = !files.length && !identityRows.length;
      $('previewExplainer').hidden = false;
      const changes = files.filter(file => file.state === 'WRITE' || file.state === 'DELETE').length;
      return changes === 0 ? 'No planned file changes' : `${changes} planned file ${changes === 1 ? 'change' : 'changes'}`;
    }
    async function api(path, body) {
      const response = await fetch(path, {
        method: body ? 'POST' : 'GET',
        headers: {'Content-Type':'application/json','X-Handoff-Token':token},
        body: body ? JSON.stringify(body) : undefined,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      return data;
    }
    function detectItem(key, word, tone, detail, extra = '') {
      return `<div class="item${extra}"><div class="k">${key}</div><span class="pill ${tone}">${esc(word)}</span><div class="detail" title="${esc(detail)}">${esc(detail)}</div></div>`;
    }
    async function load() {
      state = await api('/api/state');
      mode = state.initial_mode;
      matrix = clone(state.initial_matrix);
      $('repo').textContent = state.repo;
      const codex = state.detected.codex_model ? ` · ${state.detected.codex_model} / ${state.detected.codex_effort || 'not set'}` : '';
      const config = state.config_source === 'default' ? 'No config yet' : 'Existing config';
      const models = backend => state.model_options[backend].filter(option => option.source === (backend === 'copilot' ? 'copilot models' : 'cursor-agent models')).length;
      $('detect').innerHTML = `
        ${detectItem('Project config', state.config_source.toUpperCase(), 'cobalt', config)}
        ${detectItem('Claude Code', state.clis.claude.available ? 'FOUND' : 'MISSING', state.clis.claude.available ? 'ok' : 'bad', state.clis.claude.version || 'Not installed')}
        ${detectItem('Codex', state.clis.codex.available ? 'FOUND' : 'MISSING', state.clis.codex.available ? 'ok' : 'bad', `${state.clis.codex.version || 'Not installed'}${codex}`)}
        ${detectItem('GitHub Copilot', state.model_discovery_failed.copilot ? 'NO MODELS' : 'LISTED', state.model_discovery_failed.copilot ? 'bad' : 'ok', state.model_discovery_failed.copilot ? state.model_discovery.copilot : `${models('copilot')} models from your Copilot entitlement · CLI checked at smoke`)}
        ${detectItem('Cursor', state.model_discovery_failed.cursor ? 'NO MODELS' : 'LISTED', state.model_discovery_failed.cursor ? 'bad' : 'ok', state.model_discovery_failed.cursor ? state.model_discovery.cursor : `${models('cursor')} models from cursor-agent models`)}`;
      $('modes').innerHTML = PRESET_MODES.map(name => `<button type="button" class="mode ${name === mode ? 'active':''}" data-mode="${name}" aria-pressed="${name === mode}"><strong>${MODE_LABELS[name]}</strong><small>${modeSummary(name)}</small></button>`).join('');
      document.querySelectorAll('.mode').forEach(el => el.addEventListener('click', () => selectMode(el.dataset.mode)));
      renderIdentities();
      syncModeControls();
      $('specMaxRounds').value = state.initial_review.spec_max_rounds;
      $('implementationMaxRounds').value = state.initial_review.implementation_max_rounds;
      syncReadiness();
      $('configWorkspace').setAttribute('aria-busy', 'false');
    }
    $('withE2e').addEventListener('change', () => {
      renderIdentities();
      invalidate();
    });
    ['specMaxRounds', 'implementationMaxRounds'].forEach(id => $(id).addEventListener('input', () => {
      validateCap(id);
      invalidate();
    }));
    $('confirmed').addEventListener('change', () => setInstallEnabled($('confirmed').checked && previewValid));
    $('previewBtn').addEventListener('click', async () => {
      undoPreset = null;
      const submitted = payload();
      const requestId = ++previewRequest;
      previewLoading = true;
      previewValid = false;
      $('confirmed').checked = false;
      setInstallEnabled(false);
      $('previewBtn').disabled = true;
      $('previewBtn').textContent = 'Preparing...';
      $('configWorkspace').setAttribute('aria-busy', 'true');
      $('previewWrap').classList.remove('stale');
      $('status').textContent = 'Checking which files would change';
      try {
        const data = await api('/api/preview', submitted);
        if (requestId !== previewRequest || JSON.stringify(submitted) !== JSON.stringify(payload())) {
          $('status').textContent = 'Selection changed. Generate a new preview.';
          return;
        }
        $('preview').textContent = [data.output, data.error].filter(Boolean).join('\n');
        $('previewWrap').style.display = 'block';
        $('previewWrap').classList.toggle('has-error', !data.ok);
        const files = data.files || [];
        const countTitle = renderPreviewPlan(files, submitted);
        $('previewTitle').textContent = data.ok ? countTitle : 'Preview failed';
        const refusals = (data.output || '').split('\n').filter(line => line.startsWith('REFUSED: '));
        $('previewError').textContent = [data.error, ...refusals].filter(Boolean).join('\n') || 'Preview failed.';
        $('previewError').hidden = data.ok;
        if (!data.ok) markServerCapErrors($('previewError').textContent);
        $('technicalDetails').open = false;
        previewValid = data.ok;
        $('confirm').style.display = data.ok ? 'flex' : 'none';
        $('status').textContent = data.ok ? 'Preview done. No files changed.' : 'Preview failed. No files changed.';
        $('previewWrap').scrollIntoView({behavior:reduceMotion ? 'auto' : 'smooth',block:'start'});
        $('previewTitle').focus({preventScroll:true});
      } catch (error) {
        if (requestId !== previewRequest || JSON.stringify(submitted) !== JSON.stringify(payload())) {
          $('status').textContent = 'Selection changed. Generate a new preview.';
          return;
        }
        $('preview').textContent = error.message;
        $('previewWrap').style.display = 'block';
        $('previewWrap').classList.add('has-error');
        $('previewTitle').textContent = 'Preview failed';
        $('previewError').textContent = error.message;
        $('previewError').hidden = false;
        $('previewPlan').innerHTML = '';
        $('previewPlan').hidden = true;
        $('previewExplainer').hidden = true;
        $('technicalDetails').open = false;
        $('confirm').style.display = 'none';
        $('status').textContent = 'Preview failed. No files changed.';
        markServerCapErrors(error.message);
        $('previewWrap').scrollIntoView({behavior:reduceMotion ? 'auto' : 'smooth',block:'start'});
        $('previewTitle').focus({preventScroll:true});
      } finally {
        previewLoading = false;
        $('previewBtn').textContent = 'Preview install';
        syncReadiness();
        if (requestId !== previewRequest || JSON.stringify(submitted) !== JSON.stringify(payload())) $('status').textContent = 'Selection changed. Generate a new preview.';
        $('configWorkspace').setAttribute('aria-busy', 'false');
      }
    });
    // One pill per step that ran, each with its word, then the way back out. Rebuilt on
    // every install so the stamp lands again for the result it describes.
    function renderVerdict(written, checked) {
      const line = $('resultVerdict');
      line.hidden = !written;
      if (!written) return;
      const pills = [['Written', 'ok']];
      if (checked !== null) pills.push(checked ? ['Checked', 'ok'] : ['Check failed', 'bad']);
      line.innerHTML = pills.map(([word, tone]) => `<span class="pill ${tone}">${word}</span>`).join('') +
        `<span>Changed files are backed up under <code>.handoff/backups/</code>; <code>handoff-setup.py --rollback</code> puts them back.</span>`;
    }
    $('apply').addEventListener('click', async () => {
      setInstallEnabled(false);
      $('apply').textContent = 'Installing...';
      $('previewBtn').disabled = true;
      $('configWorkspace').setAttribute('aria-busy', 'true');
      $('status').textContent = 'Installing and checking';
      try {
        const data = await api('/api/apply', payload());
        const smoke = data.smoke ? `\nSmoke test:\n${data.smoke.output}${data.smoke.error}` : '';
        $('result').textContent = `${data.output}${data.error}${smoke}`;
        $('resultWrap').style.display = 'block';
        const checksOk = !data.smoke || data.smoke.ok;
        $('resultWrap').classList.toggle('has-error', !data.ok || !checksOk);
        $('resultTitle').textContent = !data.ok ? 'Install failed' : (checksOk ? 'Installed' : 'Installed, but the check failed');
        $('status').textContent = !data.ok ? 'Install failed' : (checksOk ? 'Handoff installed' : 'Installed, but the automatic check did not pass');
        renderVerdict(data.ok, data.smoke ? data.smoke.ok : null);
        previewValid = false;
        $('resultWrap').scrollIntoView({behavior:reduceMotion ? 'auto' : 'smooth',block:'start'});
        $('resultTitle').focus({preventScroll:true});
      } catch (error) {
        $('result').textContent = error.message;
        $('resultVerdict').hidden = true;
        $('resultWrap').style.display = 'block';
        $('resultWrap').classList.add('has-error');
        $('resultTitle').textContent = 'Install failed';
        $('status').textContent = 'Install failed';
        $('resultWrap').scrollIntoView({behavior:reduceMotion ? 'auto' : 'smooth',block:'start'});
        $('resultTitle').focus({preventScroll:true});
      } finally {
        $('apply').textContent = 'Install and check';
        syncReadiness();
        $('configWorkspace').setAttribute('aria-busy', 'false');
      }
    });
    load().catch(error => {
      $('configWorkspace').setAttribute('aria-busy', 'false');
      $('detect').innerHTML = detectItem('Environment read failed', 'FAILED', 'bad', error.message, ' failed');
      $('status').textContent = error.message;
    });
  </script>
</body>
</html>
'''


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
