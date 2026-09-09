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


def _ensure_model_option(
    options: List[Dict[str, Any]], value: str, source: str, backend: str
) -> None:
    if not value or any(option["value"] == value for option in options):
        return
    options.append(
        {
            "value": value,
            "label": value,
            "description": "Already used in the current config",
            "source": source,
            "efforts": list(engine.BACKEND_EFFORTS[backend]),
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
    option_sets = {"claude": claude_options, "codex": codex_options}
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
                )
    initial_mode = "custom" if current else "balanced"
    initial_matrix = current or presets["balanced"]
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
        },
        "presets": presets,
        "initial_mode": initial_mode,
        "initial_matrix": initial_matrix,
        "efforts_by_backend": {
            backend: list(efforts) for backend, efforts in engine.BACKEND_EFFORTS.items()
        },
        "identity_meta": IDENTITY_META,
        "initial_spec_review": bool(
            identities.get(engine.SPEC_REVIEW_IDENTITY, {}).get("auto_review_spec")
        ),
        "spec_review_identity": engine.SPEC_REVIEW_IDENTITY,
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
        if effort not in supported_efforts:
            raise UIError(
                f"effort {effort} for {identity} is not supported by {backend}/{model}; "
                f"allowed values: {', '.join(supported_efforts)}"
            )
        identities[identity] = {
            "backend": backend,
            "model": model,
            "effort": effort,
        }
    if mode != "custom":
        expected = _preset_matrices(env)[mode]
        comparable = {
            identity: {
                field: expected[identity][field]
                for field in ("backend", "model", "effort")
            }
            for identity in required
        }
        if identities != comparable:
            raise UIError("Identity settings changed. Switch to custom mode and preview again.")
    return {
        "repo": str(repo.resolve()),
        "mode": mode,
        "scope": scope,
        "exclude_choice": exclude_choice,
        "routing_action": routing_action,
        "write_agents": bool(raw.get("write_agents")),
        "smoke": bool(raw.get("smoke", True)),
        "with_e2e": with_e2e,
        "spec_review": bool(raw.get("spec_review")),
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
    args.append("--with-e2e" if payload["with_e2e"] else "--no-with-e2e")
    args.append("--spec-review" if payload["spec_review"] else "--no-spec-review")
    args.append("--write-agents" if payload["write_agents"] else "--no-write-agents")
    if payload["routing_action"] == "write":
        args.append("--routing-block")
    elif payload["routing_action"] == "remove":
        args.append("--remove-routing-block")
    return args


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
    /* Calm local Agent installer, read for first-time users. Variance 4, motion 5, density 4. */
    :root {
      color-scheme:light;
      /* Light palette. The dark block below re-declares every color that changes. */
      /* data-theme is set before first paint by the head script, so it is always one of light or dark. */
      --canvas:#f0f1ec;
      --panel:rgba(250,250,247,.84);
      --card:#ffffff;
      --field:#f3f4ef;
      --ink:#171915;
      --ink-soft:#343730;
      --muted-v2:#5f6359;
      --line-v2:#d3d6cc;
      --line-strong:#8f9488;
      --rule:rgba(23,25,21,.18);
      --noise:rgba(23,25,21,.42);
      --accent-v2:#e75b38;
      --accent-text:#8f2f19;
      --accent-strong:#8f2f19;
      --accent-strong-hover:#6f2312;
      --accent-soft:#f7d9d0;
      --accent-soft-ink:#6b4c43;
      /* The hero map and code blocks stay dark slabs in both themes. */
      --slab:#171915;
      --slab-raised:#242720;
      --on-slab:#fafaf7;
      --on-slab-muted:#b9bcb2;
      --on-accent:#171915;
      /* Segmented trays (work mode, theme) are light in light and dark in dark. */
      --switch-bg:#e6e8df;
      --switch-hover:#dadcd1;
      --switch-fg:#343730;
      --invert-bg:#171915;
      --invert-fg:#fafaf7;
      --invert-bg-hover:#8f2f19;
      --invert-fg-hover:#fafaf7;
      --sheen:rgba(255,255,255,.22);
      --shadow-lg:0 28px 70px rgba(66,54,43,.12);
      --shadow-map:0 35px 80px rgba(63,44,32,.22);
      --shadow-switch:0 12px 28px rgba(66,54,43,.08);
      --shadow-xs:0 14px 35px rgba(66,54,43,.07);
      --shadow-card:0 10px 26px rgba(66,54,43,.06);
      --shadow-card-hover:0 18px 38px rgba(66,54,43,.12);
      --display:"Avenir Next","SF Pro Display","PingFang SC",sans-serif;
      --body:"Avenir Next","SF Pro Text","PingFang SC",sans-serif;
      --mono-v2:"SFMono-Regular","JetBrains Mono",Consolas,monospace;
      --ease-out:cubic-bezier(.16,1,.3,1);
    }
    :root[data-theme="dark"] {
      color-scheme:dark;
      --canvas:#191c15;
      --panel:rgba(36,40,31,.78);
      --card:#22261e;
      --field:#1c2017;
      --ink:#eceee4;
      --ink-soft:#c4c8ba;
      --muted-v2:#9ba193;
      --line-v2:#353a2d;
      --line-strong:#4d5442;
      --rule:rgba(236,238,228,.14);
      --noise:rgba(236,238,228,.4);
      --accent-v2:#f06a44;
      --accent-text:#f4a68d;
      --accent-strong:#b03a1f;
      --accent-strong-hover:#8f2f19;
      --accent-soft:#38201a;
      --accent-soft-ink:#f0c7b8;
      --slab:#0f110c;
      --slab-raised:#23271e;
      --switch-bg:#0f110c;
      --switch-hover:#1b1f16;
      --switch-fg:#d3d6cc;
      --invert-bg:#e9ebe2;
      --invert-fg:#191c15;
      --invert-bg-hover:#d5d8cb;
      --invert-fg-hover:#191c15;
      --sheen:rgba(23,25,21,.2);
      --shadow-lg:0 28px 70px rgba(0,0,0,.45);
      --shadow-map:0 35px 80px rgba(0,0,0,.5);
      --shadow-switch:0 20px 45px rgba(0,0,0,.4);
      --shadow-xs:0 14px 35px rgba(0,0,0,.3);
      --shadow-card:0 10px 26px rgba(0,0,0,.28);
      --shadow-card-hover:0 18px 38px rgba(0,0,0,.42);
    }
    * { box-sizing:border-box; }
    html {
      background:var(--canvas);
      scroll-behavior:smooth;
      -webkit-font-smoothing:antialiased;
      -moz-osx-font-smoothing:grayscale;
      text-rendering:optimizeLegibility;
    }
    body { margin:0; min-width:320px; overflow-x:hidden; background:
      radial-gradient(circle at 82% 4%,rgba(231,91,56,.15),transparent 30rem),
      var(--canvas); color:var(--ink); font:15px/1.5 var(--body); }
    body::before { content:""; position:fixed; inset:0; pointer-events:none; z-index:3; opacity:.13; background-image:radial-gradient(var(--noise) .45px,transparent .55px); background-size:4px 4px; }
    h1,h2,h3,p { margin:0; }
    h1,h2,h3 { text-wrap:balance; }
    p { text-wrap:pretty; }
    button,select,input { font:inherit; }
    button { cursor:pointer; -webkit-tap-highlight-color:transparent; }
    button:disabled { opacity:.38; cursor:not-allowed; transform:none; }
    select:focus-visible,input:focus-visible,button:focus-visible,summary:focus-visible { outline:3px solid rgba(231,91,56,.55); outline-offset:2px; border-color:var(--accent-v2); }
    .shell { width:min(1320px,calc(100% - 48px)); margin:0 auto 80px; }

    .masthead { min-height:68px; display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:12px; padding:10px 0; border-bottom:1px solid var(--rule); }
    .brand { display:flex; align-items:center; gap:11px; font-weight:680; letter-spacing:-.025em; }
    .brand-mark { width:32px; height:32px; display:grid; place-items:center; border-radius:10px; background:var(--invert-bg); color:var(--invert-fg); font-weight:760; }
    .brand small { display:block; color:var(--muted-v2); font:11px/1.2 var(--mono-v2); letter-spacing:.03em; }
    .masthead-tools { display:flex; align-items:center; gap:16px; }
    .local-state { color:var(--muted-v2); font:12px var(--mono-v2); }
    .segmented { display:flex; gap:2px; padding:3px; border:1px solid var(--line-v2); border-radius:12px; background:var(--switch-bg); }
    .segmented button { position:relative; min-height:36px; padding:8px 12px; border:0; border-radius:9px; background:transparent; color:var(--switch-fg); font:12px var(--mono-v2); }
    .segmented button::before { content:""; position:absolute; inset:-4px 0; }
    .segmented button[aria-pressed="true"] { background:var(--accent-v2); color:var(--on-accent); }

    .hero { min-height:430px; display:grid; grid-template-columns:minmax(0,1fr) minmax(440px,.85fr); gap:clamp(36px,7vw,100px); align-items:center; padding:42px 0 54px; }
    .hero-copy { align-self:center; }
    .hero h1 { max-width:760px; color:var(--ink); font:760 clamp(46px,6vw,78px)/.98 var(--display); letter-spacing:-.065em; }
    .hero .subtitle { max-width:520px; margin-top:22px; color:var(--ink-soft); font-size:17px; line-height:1.65; }
    .repo-block { margin-top:30px; }
    .repo-block span { display:block; margin-bottom:6px; color:var(--muted-v2); font-size:12px; }
    .repo-block code { display:inline-block; max-width:100%; padding:9px 12px; border:1px solid var(--line-v2); border-radius:10px; background:var(--field); color:var(--ink-soft); font:12px/1.4 var(--mono-v2); overflow-wrap:anywhere; }
    .hero-map { position:relative; min-height:340px; overflow:hidden; border-radius:30px; background:var(--slab); color:var(--on-slab); box-shadow:var(--shadow-map); isolation:isolate; }
    .hero-map::before { content:""; position:absolute; width:320px; height:320px; right:-100px; top:-130px; border-radius:50%; background:radial-gradient(circle,rgba(231,91,56,.72),rgba(231,91,56,0) 68%); opacity:.7; }
    .hero-map::after { content:""; position:absolute; inset:0; z-index:-1; opacity:.12; background-image:radial-gradient(rgba(250,250,247,.75) .55px,transparent .7px); background-size:7px 7px; }
    .map-caption { position:absolute; left:24px; top:22px; color:var(--on-slab-muted); font:11px var(--mono-v2); }
    .agent-core { position:absolute; left:7%; top:50%; width:126px; height:126px; transform:translateY(-50%); display:grid; place-content:center; text-align:center; border:1px solid rgba(250,250,247,.28); border-radius:28px; background:var(--slab-raised); box-shadow:inset 0 1px rgba(255,255,255,.09),0 18px 42px rgba(0,0,0,.28); }
    .agent-core strong { font:720 28px/1 var(--display); letter-spacing:-.05em; }
    .agent-core small { margin-top:8px; color:var(--on-slab-muted); font:10px var(--mono-v2); }
    .agent-core::after { content:""; position:absolute; inset:-10px; border:1px solid rgba(231,91,56,.32); border-radius:36px; }
    .role-node { position:absolute; left:62%; width:31%; min-width:140px; padding:13px 15px; border:1px solid rgba(250,250,247,.18); border-radius:16px; background:rgba(41,44,36,.92); box-shadow:inset 0 1px rgba(255,255,255,.06); }
    .role-node.deep { top:3%; }
    .role-node.fast { top:22%; }
    .role-node.arbiter { top:41%; }
    .role-node.e2e-spec { top:60%; }
    .role-node.e2e-verify { top:79%; }
    .role-node span { display:block; margin-bottom:3px; color:var(--on-slab-muted); font-size:11px; }
    .role-node strong { display:block; overflow:hidden; color:var(--on-slab); font:11px/1.45 var(--mono-v2); font-variant-numeric:tabular-nums; text-overflow:ellipsis; white-space:nowrap; }
    .route-line { position:absolute; left:31%; width:34%; height:1px; transform-origin:left center; background:linear-gradient(90deg,rgba(231,91,56,.18),rgba(231,91,56,.78)); }
    .route-line.deep { top:48%; transform:rotate(-28deg); }
    .route-line.fast { top:50%; }
    .route-line.arbiter { top:52%; transform:rotate(28deg); }
    .route-line i { position:absolute; left:0; top:-4px; width:9px; height:9px; border-radius:3px; background:var(--accent-v2); box-shadow:0 0 16px rgba(231,91,56,.7); }

    .detect { display:grid; grid-template-columns:.72fr .92fr 1.05fr 1.2fr; overflow:hidden; border:1px solid var(--line-v2); border-radius:18px; background:var(--panel); box-shadow:var(--shadow-xs); }
    .detect .item { min-width:0; padding:17px 18px; border-left:1px solid var(--line-v2); }
    .detect .item:first-child { border-left:0; }
    .detect .item:nth-child(1) { --i:0; }
    .detect .item:nth-child(2) { --i:1; }
    .detect .item:nth-child(3) { --i:2; }
    .detect .item:nth-child(4) { --i:3; }
    .k { margin-bottom:4px; color:var(--muted-v2); font-size:11px; letter-spacing:.02em; }
    .v { overflow:hidden; color:var(--ink); font:650 13px/1.4 var(--body); font-variant-numeric:tabular-nums; text-overflow:ellipsis; white-space:nowrap; }
    .ok,.warn,.bad { color:var(--accent-text); }

    .config-section { margin-top:70px; }
    .config-heading { max-width:680px; margin-bottom:24px; }
    .config-heading h2 { color:var(--ink); font:720 clamp(30px,4vw,48px)/1.05 var(--display); letter-spacing:-.045em; }
    .config-heading p { max-width:570px; margin-top:10px; color:var(--muted-v2); font-size:14px; }
    .modes { display:grid; grid-template-columns:repeat(3,1fr); gap:5px; padding:5px; border:1px solid var(--line-v2); border-radius:18px; background:var(--switch-bg); box-shadow:var(--shadow-switch); }
    .mode { appearance:none; width:100%; min-height:86px; display:block; position:relative; overflow:hidden; padding:15px 16px; border:0; border-radius:13px; background:transparent; color:var(--switch-fg); text-align:left; }
    .mode strong { position:relative; z-index:1; display:block; margin:0 0 6px; color:inherit; font:680 15px var(--body); }
    .mode small { position:relative; z-index:1; display:block; color:inherit; font:11px/1.45 var(--mono-v2); }
    .mode::before { content:""; position:absolute; inset:0; border-radius:inherit; background:var(--accent-v2); transform:scale(.86); opacity:0; }
    .mode.active { color:var(--on-accent); background:transparent; }
    .mode.active::before { transform:scale(1); opacity:1; }

    .config-grid { display:grid; grid-template-columns:minmax(0,1.55fr) minmax(300px,.7fr); gap:22px; align-items:start; margin-top:22px; }
    .matrix-panel,.settings-panel { overflow:hidden; border:1px solid var(--line-v2); border-radius:24px; background:var(--panel); box-shadow:var(--shadow-lg); }
    .matrix-panel { min-width:0; }
    .main-heading,.settings-head { box-sizing:border-box; min-height:81px; padding:20px 22px 16px; border-bottom:1px solid var(--line-v2); }
    .main-heading { display:flex; align-items:flex-start; justify-content:space-between; gap:20px; }
    .main-heading h2 { color:var(--ink); font:700 23px/1.15 var(--display); letter-spacing:-.035em; }
    .main-heading p { max-width:530px; margin-top:5px; color:var(--muted-v2); font-size:13px; }
    .current-mode { flex:0 0 auto; color:var(--accent-text); font:11px var(--mono-v2); }
    .matrix { display:grid; gap:12px; padding:18px 20px 20px; }
    .e2e-addon { margin:0 20px 20px; padding:16px 0 0; border-top:1px solid var(--line-v2); }
    .e2e-addon > summary { width:max-content; max-width:100%; color:var(--ink); font:680 15px var(--body); cursor:pointer; }
    .e2e-addon > p { max-width:530px; margin-top:6px; color:var(--muted-v2); font-size:13px; line-height:1.5; }
    .e2e-toggle { display:flex; align-items:center; gap:8px; width:max-content; margin:12px 0 0; color:var(--ink); font-size:13px; letter-spacing:0; cursor:pointer; }
    .e2e-toggle input { flex:0 0 auto; width:16px; height:16px; margin:0; accent-color:var(--accent-v2); }
    .e2e-addon .matrix { padding:14px 0 0; }
    .review-addon { grid-column:1/-1; margin:3px 0 0; padding:14px 0 0; border-top:1px solid var(--line-v2); }
    .review-addon > p { max-width:530px; margin:6px 0 0; color:var(--muted-v2); font-size:13px; line-height:1.5; }
    .review-addon strong { color:var(--ink); font:680 15px var(--body); }
    .identity { display:grid; grid-template-columns:minmax(155px,.82fr) minmax(130px,.7fr) minmax(220px,1.2fr) minmax(120px,.62fr); gap:12px; align-items:start; position:relative; min-height:110px; overflow:hidden; padding:17px; border:1px solid var(--line-v2); border-radius:17px; background:var(--card); box-shadow:var(--shadow-card); }
    .identity:nth-child(1) { --row:0; }
    .identity:nth-child(2) { --row:1; }
    .identity:nth-child(3) { --row:2; }
    .identity::before { content:""; position:absolute; left:0; top:13px; bottom:13px; width:3px; border-radius:3px; background:var(--accent-v2); transform:scaleY(0); }
    .identity:focus-within::before { transform:scaleY(1); }
    .identity-head { min-width:0; }
    .identity h3 { margin-bottom:4px; color:var(--ink); font:680 15px var(--body); }
    .identity-head small { display:block; color:var(--muted-v2); font-size:12px; line-height:1.4; }
    .identity-code { display:block; margin-top:8px; color:var(--muted-v2); font:11px var(--mono-v2); overflow-wrap:anywhere; }
    .field { min-width:0; }
    label,.field-label { display:block; margin:0 0 6px; color:var(--muted-v2); font-size:11px; letter-spacing:.02em; }
    select,input[type=text] { width:100%; min-height:44px; padding:9px 10px; border:1px solid var(--line-v2); border-radius:11px; background:var(--field); color:var(--ink); font:13px var(--mono-v2); outline:none; }
    .source { margin-top:5px; color:var(--muted-v2); font-size:11px; overflow-wrap:anywhere; }

    .settings-panel { position:sticky; top:18px; }
    .settings-head { background:var(--accent-soft); }
    .settings-head h2 { color:var(--ink); font:700 21px/1.15 var(--display); letter-spacing:-.03em; }
    .settings-head p { margin-top:7px; color:var(--accent-soft-ink); font-size:13px; }
    .settings-body { padding:8px 22px 4px; }
    .setup-summary { list-style:none; margin:0; padding:0; }
    .setup-item { display:grid; grid-template-columns:28px 1fr; gap:11px; padding:15px 0; border-bottom:1px solid var(--line-v2); }
    .setup-item:last-child { border-bottom:0; }
    .setup-check { width:28px; height:28px; display:grid; place-items:center; border-radius:9px; background:var(--invert-bg); color:var(--invert-fg); font:700 13px var(--body); }
    .setup-item strong { display:block; color:var(--ink); font-size:13px; }
    .setup-item small { display:block; margin-top:3px; color:var(--muted-v2); font-size:12px; line-height:1.45; }
    .choice { display:flex; align-items:flex-start; gap:8px; color:var(--ink-soft); font-size:13px; line-height:1.35; cursor:pointer; }
    .choice input { margin:2px 0 0; accent-color:var(--accent-v2); }
    .choice:has(input:disabled) { color:var(--muted-v2); cursor:not-allowed; }

    .actions { display:grid; gap:10px; margin:12px 22px 18px; padding:14px 0 0; border-top:1px solid var(--line-v2); }
    .status { width:100%; margin:0; color:var(--muted-v2); font-size:12px; text-align:left; }
    button.primary,button.apply { min-height:48px; padding:11px 17px; border-radius:13px; border:1px solid var(--invert-bg); background:var(--invert-bg); color:var(--invert-fg); font-weight:700; }
    button.primary { width:100%; position:relative; overflow:hidden; }
    button.primary::after { content:""; position:absolute; inset:-80% -35%; background:linear-gradient(90deg,transparent,var(--sheen),transparent); transform:translateX(-70%) rotate(12deg); }
    button.apply { background:var(--accent-strong); border-color:var(--accent-strong); color:#fafaf7; }

    .output-section { display:none; margin-top:22px; padding:24px; border:1px solid var(--line-v2); border-radius:24px; background:var(--panel); box-shadow:var(--shadow-lg); }
    .output-section h2 { margin-bottom:10px; color:var(--ink); font:700 22px var(--display); }
    .output-section.stale { opacity:.5; }
    .output-section.has-error { border-left:4px solid var(--accent-v2); padding-left:24px; }
    .preview-plan { margin:15px 0; padding:4px 18px; border:1px solid var(--line-v2); border-radius:16px; background:var(--card); }
    .preview-plan .setup-item { grid-template-columns:24px 1fr; padding:12px 0; }
    .preview-plan .setup-check { width:24px; height:24px; border-radius:8px; font-size:12px; }
    .technical-details { margin-top:12px; }
    .technical-details summary { width:max-content; max-width:100%; color:var(--accent-text); font-size:13px; font-weight:650; cursor:pointer; }
    .technical-details pre { margin-top:12px; }
    pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:430px; overflow:auto; padding:15px; border-radius:16px; background:var(--slab); color:var(--on-slab); box-shadow:inset 0 1px rgba(255,255,255,.08); font:12px/1.55 var(--mono-v2); }
    .confirm { display:none; align-items:center; justify-content:flex-end; gap:14px; margin-top:12px; }
    .confirm label { margin:0; color:var(--ink); font-size:13px; }
    .result { border-left:4px solid var(--accent-v2); padding-left:24px; }
    .loading-copy { padding:12px 0; color:var(--muted-v2); font-size:13px; }

    @media (hover:hover) and (pointer:fine) {
      .mode:hover { color:var(--ink); background:var(--switch-hover); }
      .segmented button:hover { background:var(--switch-hover); color:var(--ink); }
      .segmented button[aria-pressed="true"]:hover { background:var(--accent-v2); color:var(--on-accent); }
      .mode.active:hover { color:var(--on-accent); background:transparent; }
      .identity:hover { box-shadow:var(--shadow-card-hover); }
      .identity:hover::before { transform:scaleY(1); }
      select:hover,input[type=text]:hover { border-color:var(--line-strong); }
      button.primary:hover { background:var(--invert-bg-hover); border-color:var(--invert-bg-hover); color:var(--invert-fg-hover); }
      button.apply:hover { background:var(--accent-strong-hover); border-color:var(--accent-strong-hover); }
      button:disabled:hover { background:var(--invert-bg); border-color:var(--invert-bg); color:var(--invert-fg); }
      button.apply:disabled:hover { background:var(--accent-strong); border-color:var(--accent-strong); color:#fafaf7; }
    }
    @media (prefers-reduced-motion:no-preference) {
      .hero-copy { animation:rise-in .72s var(--ease-out) both; }
      .hero-map { animation:map-in .82s .08s var(--ease-out) both; }
      .detect { animation:rise-in .65s .18s var(--ease-out) both; }
      .detect .item { animation:rise-in .48s var(--ease-out) both; animation-delay:calc(.22s + var(--i,0) * .055s); }
      .route-line i { animation:signal-run 2.2s cubic-bezier(.4,0,.2,1) infinite; }
      .route-line.fast i { animation-delay:.55s; }
      .route-line.arbiter i { animation-delay:1.1s; }
      .mode,.mode::before,.segmented button { transition:transform .16s var(--ease-out),background-color .16s ease,color .16s ease,opacity .16s ease; }
      .identity,.identity::before { transition:transform .2s var(--ease-out),box-shadow .2s ease; }
      button,select,input { transition:background-color .16s ease,border-color .16s ease,color .16s ease,transform .1s var(--ease-out); }
      .mode:active,button:active { transform:scale(.98); }
      .identity { animation:row-enter .48s var(--ease-out) both; animation-delay:calc(var(--row,0) * .07s); }
      .output-section[style*="block"] { animation:output-enter .5s var(--ease-out) both; }
      [aria-busy="true"] button.primary::after { animation:button-scan 1.3s ease-in-out infinite; }
    }
    @keyframes rise-in { from { opacity:0; transform:translateY(22px); } to { opacity:1; transform:translateY(0); } }
    @keyframes map-in { from { opacity:0; transform:translateY(28px) rotate(1.5deg) scale(.96); } to { opacity:1; transform:none; } }
    @keyframes row-enter { from { opacity:0; transform:translateX(18px); } to { opacity:1; transform:translateX(0); } }
    @keyframes output-enter { from { opacity:0; transform:translateY(18px) scale(.985); } to { opacity:1; transform:none; } }
    @keyframes signal-run { 0% { opacity:0; transform:translateX(0) scale(.75); } 18% { opacity:1; } 80% { opacity:1; } 100% { opacity:0; transform:translateX(150px) scale(1); } }
    @keyframes button-scan { from { transform:translateX(-70%) rotate(12deg); } to { transform:translateX(70%) rotate(12deg); } }

    @media (max-width:1040px) {
      .hero { grid-template-columns:1fr 1fr; gap:34px; }
      .config-grid { grid-template-columns:1fr; }
      .settings-panel { position:static; }
      .actions { grid-template-columns:1fr auto; align-items:center; }
      .status { width:auto; }
      button.primary { width:auto; }
    }
    @media (max-width:780px) {
      .shell { width:min(100% - 28px,1320px); margin-bottom:48px; }
      .hero { min-height:auto; grid-template-columns:1fr; padding:38px 0 42px; }
      .hero h1 { font-size:clamp(44px,13vw,66px); }
      .hero-map { min-height:320px; }
      .detect { grid-template-columns:1fr 1fr; }
      .detect .item { padding:13px 12px; border-top:1px solid var(--line-v2); }
      .detect .item:nth-child(odd) { border-left:0; }
      .detect .item:nth-child(1),.detect .item:nth-child(2) { border-top:0; }
      .modes { grid-template-columns:1fr 1fr; }
      .identity { grid-template-columns:1fr 1fr; }
      .identity-head,.field.model-field { grid-column:1 / -1; }
      .actions { grid-template-columns:1fr; }
      button.primary { width:100%; }
      .confirm { align-items:stretch; flex-direction:column; }
    }
    @media (max-width:500px) {
      .masthead { padding:12px 0; }
      .brand small,.local-state { display:none; }
      .masthead-tools { width:100%; }
      .segmented { width:100%; }
      .segmented button { flex:1; }
      .hero-map { min-height:380px; }
      .agent-core { left:50%; top:43%; transform:translate(-50%,-50%); }
      .role-node { left:7%; width:86%; display:grid; grid-template-columns:90px 1fr; gap:8px; align-items:center; }
      .role-node.deep { top:52%; }
      .role-node.fast { top:62%; }
      .role-node.arbiter { top:72%; }
      .role-node.e2e-spec { top:82%; }
      .role-node.e2e-verify { top:92%; }
      .route-line { display:none; }
      .detect { grid-template-columns:1fr; }
      .detect .item { border-left:0; }
      .detect .item:nth-child(2) { border-top:1px solid var(--line-v2); }
      .modes { grid-template-columns:1fr; }
      .mode { min-height:70px; }
      .identity { grid-template-columns:1fr; }
      .identity-head,.field.model-field { grid-column:1; }
      .main-heading { display:block; min-height:auto; padding:18px; }
      .current-mode { display:block; margin-top:10px; }
      .matrix { padding:14px; }
      .output-section { padding:18px; }
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
    <nav class="masthead" aria-label="Handoff setup">
      <div class="brand"><span class="brand-mark">H</span><span>Agent Handoff Setup<small>Local agent setup</small></span></div>
      <div class="masthead-tools">
        <span class="local-state">Runs on this machine only</span>
        <div class="segmented" id="themeSwitch" role="group" aria-label="Theme">
          <button type="button" data-theme-choice="system">System</button>
          <button type="button" data-theme-choice="light">Light</button>
          <button type="button" data-theme-choice="dark">Dark</button>
        </div>
      </div>
    </nav>

    <header class="hero">
      <div class="hero-copy">
        <h1>Set up Agent Handoff</h1>
        <p class="subtitle">Assign reasoning, execution, and arbitration in one pass. See the real models and the exact diff before anything is written.</p>
        <div class="repo-block"><span>Current project</span><code id="repo"></code></div>
      </div>
      <div class="hero-map" aria-label="Current role routing preview">
        <span class="map-caption">Live config preview</span>
        <div class="agent-core"><strong>Handoff</strong><small>orchestrator</small></div>
        <div class="route-line deep" aria-hidden="true"><i></i></div>
        <div class="route-line fast" aria-hidden="true"><i></i></div>
        <div class="route-line arbiter" aria-hidden="true"><i></i></div>
        <div class="role-node deep"><span>Deep reasoning</span><strong id="hero-deep_reasoner">Detecting</strong></div>
        <div class="role-node fast"><span>Fast execution</span><strong id="hero-fast_worker">Detecting</strong></div>
        <div class="role-node arbiter"><span>Independent arbitration</span><strong id="hero-arbiter">Detecting</strong></div>
        <div class="role-node e2e-spec"><span>Acceptance authoring</span><strong id="hero-e2e_specifier">Off</strong></div>
        <div class="role-node e2e-verify"><span>Acceptance execution</span><strong id="hero-e2e_verifier">Off</strong></div>
      </div>
    </header>

    <section class="detect" id="detect" aria-label="Local environment detection">
      <p class="loading-copy">Reading the local environment...</p>
    </section>

    <section class="config-section" id="configWorkspace" aria-busy="true">
      <div class="config-heading"><h2>Pick a work mode</h2><p>Start from a recommended combination, or set each role's CLI, model, and effort directly.</p></div>
      <div class="modes" id="modes" role="group" aria-label="Work mode"><p class="loading-copy">Building modes...</p></div>

      <div class="config-grid">
        <section class="matrix-panel" aria-labelledby="matrixTitle">
          <div class="main-heading">
            <div><h2 id="matrixTitle">The three Agent Handoff roles</h2><p>Codex models are read from your local account and Claude models use the official CLI aliases. Copilot exposes no model catalog, so you type its model name and the CLI checks it when you install.</p></div>
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
        </section>

        <aside class="settings-panel" aria-label="Pre-install confirmation">
          <div class="settings-head"><h2>Ready to install</h2><p>Nothing to choose here. Handoff uses safe defaults.</p></div>

          <div class="settings-body">
            <ul class="setup-summary">
              <li class="setup-item"><span class="setup-check" aria-hidden="true">✓</span><span><strong>Configures this project only</strong><small>No other project on your machine is touched.</small></span></li>
              <li class="setup-item"><span class="setup-check" aria-hidden="true">✓</span><span><strong>Config stays on this machine</strong><small>Your personal model settings are never committed to Git.</small></span></li>
              <li class="setup-item"><span class="setup-check" aria-hidden="true">✓</span><span><strong>Automatic check after install</strong><small>Confirms Handoff can read the new config; failures show the reason.</small></span></li>
            </ul>
          </div>
          <div class="actions">
            <span class="status" id="status" role="status" aria-live="polite">No files changed yet</span>
            <button class="primary" id="previewBtn">Preview install</button>
          </div>
        </aside>
      </div>

      <div class="output-section" id="previewWrap" aria-live="polite">
        <h2>Files that will change</h2>
        <ul class="preview-plan" id="previewPlan">
          <li class="setup-item"><span class="setup-check" aria-hidden="true">1</span><span><strong>Save the three roles' model settings</strong><small>Written to this project's .handoff/config.toml.</small></span></li>
          <li class="setup-item"><span class="setup-check" aria-hidden="true">2</span><span><strong>Keep the config local</strong><small>Adds it to this repository's local Git ignore list.</small></span></li>
          <li class="setup-item"><span class="setup-check" aria-hidden="true">3</span><span><strong>Check after install</strong><small>Confirms Handoff can read the settings just written.</small></span></li>
        </ul>
        <details class="technical-details" id="technicalDetails">
          <summary id="technicalSummary">Show full paths and the technical diff</summary>
          <pre id="preview"></pre>
        </details>
        <div class="confirm" id="confirm">
          <label class="choice"><input type="checkbox" id="confirmed"> I confirm installing into this project</label>
          <button class="apply" id="apply" disabled>Install and check</button>
        </div>
      </div>

      <div class="output-section result" id="resultWrap" aria-live="polite">
        <h2>Result</h2>
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
    const MODE_LABELS = {balanced:'Balanced',quality:'Quality',cost:'Cost',custom:'Custom'};
    const PRESET_MODES = ['balanced','quality','cost'];
    const MODE_DESCRIPTIONS = {
      balanced:'Claude leads, Codex executes',
      quality:'More work goes to Claude',
      cost:'Codex runs most of it, Claude backs it up',
      custom:'Set each role by hand',
    };
    const BACKEND_LABELS = {claude:'Claude Code', codex:'Codex', copilot:'GitHub Copilot'};
    const BACKEND_LABELS_ORDER = ['claude','codex','copilot'];
    // Copilot publishes no model catalog, so its model is typed rather than picked.
    const TYPED_MODEL_BACKENDS = ['copilot'];
    const EFFORT_LABELS = {
      none:'None',
      minimal:'Minimal',
      low:'Low',
      medium:'Medium',
      high:'High',
      xhigh:'Extra high',
      max:'Max',
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
        'custom (required)':'Not detected yet',
        'built-in':'Built-in value',
        'typed':'Typed; validated against the CLI',
      })[source] || source;
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
      return option.label === option.value ? option.label : `${option.label} (${option.value})`;
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
      const ready = Object.values(matrix).every(values => values.model);
      $('previewBtn').disabled = !ready;
      if (!ready) $('status').textContent = 'Every role needs a model. Type one for Copilot, or check your CLI login and refresh for Claude and Codex.';
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
    function syncHeroMap() {
      const active = activeIdentities();
      for (const identity of state.core_identities.concat(state.optional_identities)) {
        const node = $(`hero-${identity}`);
        const values = matrix[identity];
        if (!node || !values) continue;
        if (!active.includes(identity)) {
          node.textContent = 'Off';
          node.title = 'Not configured';
          continue;
        }
        const backend = BACKEND_LABELS[values.backend] || values.backend;
        const summary = `${backend} / ${values.model || 'not set'} / ${values.effort}`;
        node.textContent = summary;
        node.title = summary;
      }
    }
    function invalidate() {
      previewValid = false;
      $('confirmed').checked = false;
      $('apply').disabled = true;
      $('confirm').style.display = 'none';
      if ($('previewWrap').style.display === 'block') $('previewWrap').classList.add('stale');
      $('technicalDetails').open = false;
      $('status').textContent = 'Selection changed. Generate a new preview.';
      syncReadiness();
    }
    function selectMode(next) {
      mode = next;
      if (next !== 'custom') matrix = clone(state.presets[next]);
      syncModeControls();
      renderIdentities();
      syncHeroMap();
      invalidate();
    }
    function renderIdentities() {
      renderCards('identities', state.core_identities);
      renderCards('e2eCards', $('withE2e').checked ? state.optional_identities : []);
      bindIdentityInputs();
    }
    function renderCards(container, names) {
      $(container).innerHTML = names.map(identity => {
        const meta = state.identity_meta[identity];
        const values = matrix[identity];
        const efforts = syncEffort(values);
        const verified = modelOption(values.backend, values.model);
        const source = verified ? verified.source : (values.model_source || 'existing config');
        const models = modelCatalog(values.backend, values.model, source);
        const typed = TYPED_MODEL_BACKENDS.includes(values.backend);
        const modelOptions = models.length
          ? models.map(option => `<option value="${esc(option.value)}" ${values.model === option.value ? 'selected' : ''}>${esc(modelOptionLabel(option))}</option>`).join('')
          : '<option value="">No models available</option>';
        return `<article class="identity" data-identity="${identity}">
          <div class="identity-head"><h3>${esc(meta.label)}</h3><small>${esc(meta.hint)}</small><code class="identity-code">${identity}</code></div>
          <div class="field"><label for="${identity}-backend">Runs on</label><select id="${identity}-backend" data-field="backend">${BACKEND_LABELS_ORDER.map(b => `<option value="${b}" ${values.backend === b ? 'selected' : ''}>${BACKEND_LABELS[b]}</option>`).join('')}</select></div>
          <div class="field model-field"><label for="${identity}-model">Model</label>${typed
            ? `<input id="${identity}-model" data-field="model" type="text" spellcheck="false" autocomplete="off" placeholder="Model name, exactly as Copilot names it" value="${esc(values.model || '')}" aria-describedby="${identity}-source">`
            : `<select id="${identity}-model" data-field="model" aria-describedby="${identity}-source" ${models.length ? '' : 'disabled'}>${modelOptions}</select>`}<div class="source" id="${identity}-source">Source: ${esc(sourceLabel(typed ? 'typed' : source))}</div></div>
          <div class="field"><label for="${identity}-effort">Effort</label><select id="${identity}-effort" data-field="effort">${efforts.map(e => `<option value="${e}" ${values.effort === e ? 'selected' : ''}>${esc(EFFORT_LABELS[e] || e)}</option>`).join('')}</select></div>
          ${identity === state.spec_review_identity ? reviewAddon() : ''}
        </article>`;
      }).join('');
    }
    function reviewAddon() {
      const box = $('specReview');
      const checked = box ? box.checked : state.initial_spec_review;
      return `<div class="review-addon">
            <strong>Second pair of eyes on the plan (optional)</strong>
            <p>Before a plan reaches you, deep_reasoner reads it once on its own model and
               reports what it would change. It happens once per run and never edits anything.</p>
            <label class="e2e-toggle"><input type="checkbox" id="specReview" ${checked ? 'checked' : ''}> Review the plan before I see it</label>
          </div>`;
    }
    function bindIdentityInputs() {
      $('specReview').addEventListener('change', invalidate);
      document.querySelectorAll('.identity select, .identity input[type=text]').forEach(control => control.addEventListener('input', event => {
        const card = event.target.closest('.identity');
        const identity = card.dataset.identity;
        const field = event.target.dataset.field;
        matrix[identity][field] = event.target.value;
        if (field === 'backend') {
          if (TYPED_MODEL_BACKENDS.includes(event.target.value)) {
            matrix[identity].model = '';
            matrix[identity].model_source = 'typed';
          } else {
            const options = modelCatalog(event.target.value, '', '');
            const selected = options.find(option => option.is_default) || options[0];
            matrix[identity].model = selected ? selected.value : '';
            matrix[identity].model_source = selected ? selected.source : 'custom (required)';
          }
          syncEffort(matrix[identity]);
        } else if (field === 'model') {
          if (TYPED_MODEL_BACKENDS.includes(matrix[identity].backend)) {
            matrix[identity].model_source = 'typed';
          } else {
            const selected = modelOption(matrix[identity].backend, event.target.value);
            matrix[identity].model_source = selected ? selected.source : 'existing config';
            syncEffort(matrix[identity]);
          }
        }
        mode = 'custom';
        syncModeControls();
        if (field === 'backend') renderIdentities();
        syncHeroMap();
        if (field === 'model') card.querySelector('.source').textContent = `Source: ${sourceLabel(matrix[identity].model_source)}`;
        invalidate();
      }));
    }
    function payload() {
      const withE2e = $('withE2e').checked;
      const identities = {};
      for (const identity of activeIdentities()) {
        identities[identity] = {
          backend: matrix[identity].backend,
          model: matrix[identity].model,
          effort: matrix[identity].effort,
        };
      }
      return {
        mode,
        identities,
        with_e2e: withE2e,
        spec_review: $('specReview').checked,
        scope: 'project',
        exclude_choice: 'git-exclude',
        write_agents: state.write_agents_available,
        smoke: true,
        routing_action: 'none',
      };
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
    async function load() {
      state = await api('/api/state');
      mode = state.initial_mode;
      matrix = clone(state.initial_matrix);
      $('repo').textContent = state.repo;
      const codex = state.detected.codex_model ? `${state.detected.codex_model} / ${state.detected.codex_effort || 'not set'}` : 'No model detected';
      $('detect').innerHTML = `
        <div class="item"><div class="k">Project config</div><div class="v">${esc(state.config_source)}</div></div>
        <div class="item"><div class="k">Claude CLI</div><div class="v ${state.clis.claude.available ? 'ok':'bad'}">${esc(state.clis.claude.version || 'Not installed')}</div></div>
        <div class="item" title="${esc(state.clis.codex.path || '')}"><div class="k">Codex CLI (${esc(state.clis.codex.source)})</div><div class="v ${state.clis.codex.available ? 'ok':'bad'}">${esc(state.clis.codex.version || 'Not installed')}</div></div>
        <div class="item"><div class="k">Codex detected</div><div class="v ${state.detected.codex_model ? 'ok':'warn'}">${esc(codex)}</div></div>`;
      $('modes').innerHTML = PRESET_MODES.map(name => `<button type="button" class="mode ${name === mode ? 'active':''}" data-mode="${name}" aria-pressed="${name === mode}"><strong>${MODE_LABELS[name]}</strong><small>${modeSummary(name)}</small></button>`).join('');
      document.querySelectorAll('.mode').forEach(el => el.addEventListener('click', () => selectMode(el.dataset.mode)));
      renderIdentities();
      syncModeControls();
      syncHeroMap();
      syncReadiness();
      $('configWorkspace').setAttribute('aria-busy', 'false');
    }
    $('withE2e').addEventListener('change', () => {
      renderIdentities();
      syncHeroMap();
      invalidate();
    });
    $('confirmed').addEventListener('change', () => $('apply').disabled = !$('confirmed').checked || !previewValid);
    $('previewBtn').addEventListener('click', async () => {
      $('previewBtn').disabled = true;
      $('previewBtn').textContent = 'Preparing...';
      $('configWorkspace').setAttribute('aria-busy', 'true');
      $('previewWrap').classList.remove('stale');
      $('status').textContent = 'Checking which files would change';
      try {
        const data = await api('/api/preview', payload());
        $('preview').textContent = [data.output, data.error].filter(Boolean).join('\n');
        $('previewWrap').style.display = 'block';
        $('previewWrap').classList.toggle('has-error', !data.ok);
        $('previewPlan').style.display = data.ok ? 'block' : 'none';
        $('technicalDetails').open = !data.ok;
        $('technicalSummary').textContent = data.ok ? 'Show full paths and the technical diff' : 'Show the failure reason';
        previewValid = data.ok;
        $('confirm').style.display = data.ok ? 'flex' : 'none';
        $('status').textContent = data.ok ? 'Preview done. No files changed.' : 'Preview failed. No files changed.';
        $('previewWrap').scrollIntoView({behavior:reduceMotion ? 'auto' : 'smooth',block:'start'});
      } catch (error) {
        $('preview').textContent = error.message;
        $('previewWrap').style.display = 'block';
        $('previewWrap').classList.add('has-error');
        $('previewPlan').style.display = 'none';
        $('technicalDetails').open = true;
        $('technicalSummary').textContent = 'Show the failure reason';
        $('confirm').style.display = 'none';
        $('status').textContent = 'Preview failed. No files changed.';
        $('previewWrap').scrollIntoView({behavior:reduceMotion ? 'auto' : 'smooth',block:'start'});
      } finally {
        $('previewBtn').textContent = 'Preview install';
        syncReadiness();
        $('configWorkspace').setAttribute('aria-busy', 'false');
      }
    });
    $('apply').addEventListener('click', async () => {
      $('apply').disabled = true;
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
        $('status').textContent = !data.ok ? 'Install failed' : (checksOk ? 'Handoff installed' : 'Installed, but the automatic check did not pass');
        previewValid = false;
        $('resultWrap').scrollIntoView({behavior:reduceMotion ? 'auto' : 'smooth',block:'start'});
      } catch (error) {
        $('result').textContent = error.message;
        $('resultWrap').style.display = 'block';
        $('resultWrap').classList.add('has-error');
        $('status').textContent = 'Install failed';
        $('resultWrap').scrollIntoView({behavior:reduceMotion ? 'auto' : 'smooth',block:'start'});
      } finally {
        $('apply').textContent = 'Install and check';
        $('previewBtn').disabled = false;
        $('configWorkspace').setAttribute('aria-busy', 'false');
      }
    });
    load().catch(error => {
      $('configWorkspace').setAttribute('aria-busy', 'false');
      $('detect').innerHTML = `<div class="item"><div class="k">Environment read failed</div><div class="v bad">${esc(error.message)}</div></div>`;
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
