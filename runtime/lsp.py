"""Minimal Language Server Protocol (LSP) client for on-demand diagnostics.

This is the "biggest gap" vs Claude Code's architecture: real diagnostics from
a language server (type errors, undefined names, unused imports) rather than
just a syntax compile check.

Design — deliberately one-shot, not a persistent server:
  spawn language server → initialize → didOpen → collect publishDiagnostics →
  shutdown. Each call is self-contained with strict timeouts so it never hangs
  the REPL, and degrades gracefully (returns []) when no server is installed.

Supported servers (auto-detected on PATH):
  Python      → pylsp            (pip install python-lsp-server)
  TS / JS     → typescript-language-server --stdio  (npm i -g …)

Exposed to the LLM as the `lsp_diagnostics` tool and to users via /lsp.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

# ── Server registry ──────────────────────────────────────────────────────────
# suffix → (command argv, LSP languageId)
_SERVERS: dict[str, tuple[list[str], str]] = {
    ".py":  (["pylsp"], "python"),
    ".ts":  (["typescript-language-server", "--stdio"], "typescript"),
    ".tsx": (["typescript-language-server", "--stdio"], "typescriptreact"),
    ".js":  (["typescript-language-server", "--stdio"], "javascript"),
    ".jsx": (["typescript-language-server", "--stdio"], "javascriptreact"),
    ".mjs": (["typescript-language-server", "--stdio"], "javascript"),
    ".cjs": (["typescript-language-server", "--stdio"], "javascript"),
}

_SEVERITY = {1: "error", 2: "warning", 3: "info", 4: "hint"}

# Cache PATH lookups so repeated edits don't re-stat the filesystem each time.
_AVAILABILITY: dict[str, bool] = {}


def server_for(path) -> Optional[tuple[list[str], str]]:
    """Return (argv, languageId) if a language server is installed for this
    file's type, else None."""
    suffix = Path(path).suffix.lower()
    entry = _SERVERS.get(suffix)
    if not entry:
        return None
    cmd, lang = entry
    exe = cmd[0]
    if exe not in _AVAILABILITY:
        _AVAILABILITY[exe] = shutil.which(exe) is not None
    if not _AVAILABILITY[exe]:
        return None
    return cmd, lang


def available_servers() -> dict[str, bool]:
    """Report which known language servers are installed (for /lsp status)."""
    out: dict[str, bool] = {}
    for cmd, _ in _SERVERS.values():
        exe = cmd[0]
        if exe not in out:
            out[exe] = shutil.which(exe) is not None
    return out


# ── JSON-RPC framing ──────────────────────────────────────────────────────────

def _encode(msg: dict) -> bytes:
    body = json.dumps(msg).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def _read_message(stdout) -> Optional[dict]:
    """Read one LSP message (headers + body) from a blocking stream."""
    headers: dict[bytes, bytes] = {}
    while True:
        line = stdout.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break  # blank line terminates headers
        if b":" in line:
            k, v = line.split(b":", 1)
            headers[k.strip().lower()] = v.strip()
    length = int(headers.get(b"content-length", b"0") or 0)
    if length <= 0:
        return None
    body = stdout.read(length)
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


def _format_diagnostics(raw: list) -> list[dict]:
    out = []
    for d in raw or []:
        start = (d.get("range") or {}).get("start") or {}
        code = d.get("code", "")
        out.append({
            "line": int(start.get("line", 0)) + 1,        # LSP is 0-based
            "col": int(start.get("character", 0)) + 1,
            "severity": _SEVERITY.get(d.get("severity", 1), "info"),
            "message": str(d.get("message", "")).strip(),
            "source": d.get("source", "") or "",
            "code": str(code) if code != "" else "",
        })
    out.sort(key=lambda x: (x["line"], x["col"]))
    return out


# ── Core: one-shot diagnostics ────────────────────────────────────────────────

def get_diagnostics(path, timeout: float = 8.0) -> list[dict]:
    """Spawn a language server, open `path`, and return its diagnostics.

    Returns [] if no server is available, the file can't be read, or the
    handshake times out. Never raises.
    """
    resolved = server_for(path)
    if not resolved:
        return []
    cmd, lang_id = resolved

    p = Path(path).expanduser().resolve()
    try:
        text = p.read_text(errors="replace")
    except Exception:
        return []

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(p.parent),
        )
    except Exception:
        return []

    msg_queue: "queue.Queue[dict]" = queue.Queue()

    def _reader():
        try:
            while True:
                msg = _read_message(proc.stdout)
                if msg is None:
                    break
                msg_queue.put(msg)
        except Exception:
            pass

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()

    def _send(msg: dict) -> None:
        try:
            proc.stdin.write(_encode(msg))
            proc.stdin.flush()
        except Exception:
            pass

    def _cleanup() -> None:
        try:
            _send({"jsonrpc": "2.0", "id": 9999, "method": "shutdown", "params": None})
            _send({"jsonrpc": "2.0", "method": "exit"})
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    file_uri = p.as_uri()
    deadline = time.time() + timeout

    # 1) initialize
    _send({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "processId": os.getpid(),
            "rootUri": p.parent.as_uri(),
            "workspaceFolders": [{"uri": p.parent.as_uri(), "name": p.parent.name}],
            "capabilities": {
                "textDocument": {
                    "publishDiagnostics": {"relatedInformation": True},
                    "synchronization": {"didSave": True},
                },
            },
        },
    })

    initialized = False
    while time.time() < deadline:
        try:
            msg = msg_queue.get(timeout=0.2)
        except queue.Empty:
            if proc.poll() is not None:
                _cleanup()
                return []
            continue
        if msg.get("id") == 1 and "result" in msg:
            initialized = True
            break
    if not initialized:
        _cleanup()
        return []

    # 2) initialized + didOpen
    _send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
    _send({
        "jsonrpc": "2.0", "method": "textDocument/didOpen",
        "params": {
            "textDocument": {
                "uri": file_uri, "languageId": lang_id,
                "version": 1, "text": text,
            },
        },
    })

    # 3) collect publishDiagnostics for our file. Some servers emit an empty
    #    set first, then a populated one after analysis — so once we see a
    #    matching notification we keep a short grace window for a better one.
    diagnostics: list = []
    got_one = False
    grace_deadline = None
    while time.time() < deadline:
        remaining = deadline - time.time()
        if grace_deadline is not None:
            remaining = min(remaining, grace_deadline - time.time())
            if remaining <= 0:
                break
        try:
            msg = msg_queue.get(timeout=max(0.05, min(0.2, remaining)))
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue
        if msg.get("method") == "textDocument/publishDiagnostics":
            params = msg.get("params", {})
            if _same_uri(params.get("uri", ""), file_uri, p):
                diags = params.get("diagnostics", [])
                if diags:
                    diagnostics = diags
                    got_one = True
                    break  # populated result — done
                if not got_one:
                    diagnostics = diags  # remember the empty result
                    got_one = True
                    grace_deadline = time.time() + 1.5  # wait briefly for more

    _cleanup()
    return _format_diagnostics(diagnostics)


def _same_uri(a: str, b: str, path: Path) -> bool:
    if not a:
        return False
    if a == b:
        return True
    # Tolerate trailing-slash / encoding differences by comparing resolved paths
    try:
        return Path(a.replace("file://", "")).resolve() == path
    except Exception:
        return False


# ── Tool wrapper ──────────────────────────────────────────────────────────────

def tool_lsp_diagnostics(params: dict) -> dict:
    """LLM-callable: run language-server diagnostics on a single file."""
    path = params.get("path", "")
    if not path:
        return {"success": False, "error": "Missing 'path' parameter"}
    p = Path(path).expanduser()
    if not p.exists():
        return {"success": False, "error": f"File not found: {p}"}

    resolved = server_for(p)
    if not resolved:
        return {"success": True, "data": {
            "path": str(p), "diagnostics": [], "available": False,
            "note": f"No language server installed for '{p.suffix}' files. "
                    f"Python: pip install 'python-lsp-server[all]'  (the [all] extra "
                    f"pulls in pyflakes/pycodestyle — without it pylsp reports nothing) · "
                    f"TS/JS: npm i -g typescript-language-server typescript",
        }}

    cmd, _ = resolved
    diags = get_diagnostics(p)
    errors = sum(1 for d in diags if d["severity"] == "error")
    warnings = sum(1 for d in diags if d["severity"] == "warning")
    return {"success": True, "data": {
        "path": str(p),
        "server": cmd[0],
        "available": True,
        "diagnostics": diags,
        "errors": errors,
        "warnings": warnings,
        "total": len(diags),
    }}


# ── Registry (merged into LOCAL_TOOLS in aria_cli.py) ─────────────────────────

LSP_TOOLS = {
    "lsp_diagnostics": (tool_lsp_diagnostics,
                        "Run language-server diagnostics (errors/warnings) on a code file"),
}

LSP_SCHEMAS = [
    {
        "name": "lsp_diagnostics",
        "description": (
            "Run a language server (pylsp / typescript-language-server) on a single "
            "file and return its diagnostics: type errors, undefined names, unused "
            "imports, lint warnings. Use this after editing code to catch problems a "
            "plain syntax check misses. Returns [] if no server is installed for the "
            "file type."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the code file to analyze"},
            },
            "required": ["path"],
        },
    },
]
