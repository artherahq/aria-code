"""Canva Connect API client — OAuth2 + Autofill (report design drafts).

Canva has no headless "create a design from scratch" API — Autofill is the
real capability: fill a brand template you already made in the Canva app
with data, and export the result. That's the only "generate a report design"
path Canva actually supports server-side, so that's all this module does.

One-time setup (outside this code, on Canva's side):
    1. Register a Connect app at https://www.canva.com/developers/ — get a
       client_id / client_secret.
    2. Create a brand template in Canva with the fields you want to autofill,
       note its template_id.
    3. Run `/canva connect` in aria-code once to do the OAuth handshake.

Token storage follows the same providers.json pattern as other data-service
keys (apps/cli/config_paths.py's providers_file, "data" section), extended
with refresh_token/expires_at — this is the first OAuth-based integration in
the codebase, so the refresh-before-expiry logic below is new, not reused
from elsewhere.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional

AUTH_URL = "https://www.canva.com/api/oauth/authorize"
TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"
API_BASE = "https://api.canva.com/rest/v1"
REDIRECT_PORT = 53219
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
SCOPES = "design:content:write design:content:read"


def _providers_path() -> Path:
    from apps.cli.config_paths import resolve_paths
    return resolve_paths().providers_file


def _load_canva_config() -> Dict[str, Any]:
    path = _providers_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw.get("data", {}).get("canva", {}) or {}


def _save_canva_config(entry: Dict[str, Any]) -> None:
    path = _providers_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: Dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    data_section = existing.get("data", {})
    data_section["canva"] = entry
    existing["data"] = data_section
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    code: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        _CallbackHandler.code = qs.get("code", [None])[0]
        _CallbackHandler.error = qs.get("error", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = "授权成功，可以关闭这个标签页了。" if _CallbackHandler.code else f"授权失败: {_CallbackHandler.error}"
        self.wfile.write(f"<html><body><p>{msg}</p></body></html>".encode("utf-8"))

    def log_message(self, *args):
        pass  # silence default stderr logging


def connect(client_id: str, client_secret: str, *, timeout: float = 120.0) -> Dict[str, Any]:
    """Run the OAuth2 authorization-code + PKCE flow, store tokens, return the saved entry."""
    verifier = secrets.token_urlsafe(64)[:64]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    _CallbackHandler.code = None
    _CallbackHandler.error = None
    server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), _CallbackHandler)
    server.timeout = timeout

    webbrowser.open(url)
    deadline = time.time() + timeout
    while _CallbackHandler.code is None and _CallbackHandler.error is None and time.time() < deadline:
        server.handle_request()
    server.server_close()

    if _CallbackHandler.error:
        return {"success": False, "error": _CallbackHandler.error}
    if not _CallbackHandler.code:
        return {"success": False, "error": f"Timed out waiting for Canva authorization (>{timeout:.0f}s)."}

    import requests

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": _CallbackHandler.code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
        auth=(client_id, client_secret),
        timeout=15,
    )
    if resp.status_code != 200:
        return {"success": False, "error": f"Token exchange failed: {resp.status_code} {resp.text[:300]}"}
    tok = resp.json()
    entry = {
        "client_id": client_id,
        "client_secret": client_secret,
        "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token", ""),
        "expires_at": time.time() + float(tok.get("expires_in", 3600)),
    }
    _save_canva_config(entry)
    return {"success": True}


def _refresh_if_needed(entry: Dict[str, Any]) -> Dict[str, Any]:
    if float(entry.get("expires_at", 0)) - time.time() > 60:
        return entry
    if not entry.get("refresh_token"):
        raise RuntimeError("Canva access token expired and no refresh_token stored — run /canva connect again.")
    import requests

    resp = requests.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": entry["refresh_token"]},
        auth=(entry["client_id"], entry["client_secret"]),
        timeout=15,
    )
    resp.raise_for_status()
    tok = resp.json()
    entry = {
        **entry,
        "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token", entry["refresh_token"]),
        "expires_at": time.time() + float(tok.get("expires_in", 3600)),
    }
    _save_canva_config(entry)
    return entry


def _access_token() -> str:
    entry = _load_canva_config()
    if not entry.get("access_token"):
        raise RuntimeError("Canva not connected — run /canva connect first.")
    entry = _refresh_if_needed(entry)
    return entry["access_token"]


def upload_asset(file_path: str, *, poll_timeout: float = 60.0) -> Dict[str, Any]:
    """Upload a local image/video file (e.g. a report chart PNG) to Canva and
    return its asset_id, for use as an {"type": "image", "asset_id": ...}
    value in autofill_design()'s `data`.

    Blocking (uses requests) — same threading note as autofill_design.
    """
    import base64
    import json
    import requests

    path = Path(file_path)
    if not path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}

    token = _access_token()
    name_b64 = base64.b64encode(path.name.encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/octet-stream",
        "Asset-Upload-Metadata": json.dumps({"name_base64": name_b64}),
    }

    create_resp = requests.post(
        f"{API_BASE}/asset-uploads",
        headers=headers,
        data=path.read_bytes(),
        timeout=30,
    )
    if create_resp.status_code not in (200, 201, 202):
        return {"success": False, "error": f"Asset upload failed: {create_resp.status_code} {create_resp.text[:300]}"}
    job = create_resp.json().get("job", {})
    job_id = job.get("id", "")
    if not job_id:
        return {"success": False, "error": "Canva did not return an asset-upload job id"}

    poll_headers = {"Authorization": f"Bearer {token}"}
    deadline = time.time() + poll_timeout
    while time.time() < deadline:
        poll_resp = requests.get(f"{API_BASE}/asset-uploads/{job_id}", headers=poll_headers, timeout=15)
        poll_resp.raise_for_status()
        job = poll_resp.json().get("job", {})
        status = job.get("status", "")
        if status == "success":
            asset = job.get("asset", {})
            return {"success": True, "asset_id": asset.get("id", ""), "name": asset.get("name", "")}
        if status == "failed":
            return {"success": False, "error": job.get("error", {}).get("message", "asset upload job failed")}
        time.sleep(2)
    return {"success": False, "error": f"Asset upload job timed out after {poll_timeout:.0f}s (job_id={job_id})"}


def autofill_design(template_id: str, data: Dict[str, Any], *, poll_timeout: float = 60.0) -> Dict[str, Any]:
    """Fill a Canva brand template with data and return the exported design.

    `data` values must be typed DatasetValue objects per the Canva Autofill
    API, not bare strings, e.g.:
        {"headline": {"type": "text", "text": "Q3 Report"},
         "chart_img": {"type": "image", "asset_id": "<uploaded-asset-id>"}}
    Supported value "type"s: text, image, video, chart, sheet.

    Blocking (uses requests) — callers on an event loop should run this in
    an executor, same as every other broker/report call in this codebase.
    """
    import requests

    token = _access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    create_resp = requests.post(
        f"{API_BASE}/autofills",
        headers=headers,
        json={"type": "create_from_brand_template", "brand_template_id": template_id, "data": data},
        timeout=15,
    )
    if create_resp.status_code not in (200, 201, 202):
        return {"success": False, "error": f"Autofill job creation failed: {create_resp.status_code} {create_resp.text[:300]}"}
    job = create_resp.json().get("job", {})
    job_id = job.get("id", "")
    if not job_id:
        return {"success": False, "error": "Canva did not return a job id"}

    deadline = time.time() + poll_timeout
    while time.time() < deadline:
        poll_resp = requests.get(f"{API_BASE}/autofills/{job_id}", headers=headers, timeout=15)
        poll_resp.raise_for_status()
        job = poll_resp.json().get("job", {})
        status = job.get("status", "")
        if status == "success":
            result = job.get("result", {})
            return {
                "success": True,
                "design_id": result.get("design", {}).get("id", ""),
                "design_url": result.get("design", {}).get("url", ""),
            }
        if status == "failed":
            return {"success": False, "error": job.get("error", {}).get("message", "autofill job failed")}
        time.sleep(2)
    return {"success": False, "error": f"Autofill job timed out after {poll_timeout:.0f}s (job_id={job_id})"}
