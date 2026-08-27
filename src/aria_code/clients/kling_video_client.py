"""Kling (Kuaishou) video generation client — Layer 3, the other provider
behind the same submit/poll shape as runway_video_client.py.

Verified against Kling's own auth documentation (kling.ai/document-api/
apiReference/commonInfo, titled "Authentication - KlingAI Open Platform")
plus a detailed third-party API reference mirroring the same official
`api.klingai.com` endpoints (github.com/betasecond/KlingDemo) for the
request/response shapes — the official docs page itself wasn't fetchable
(same limitation hit on OpenAI's docs page), so this is corroborated across
two independent sources rather than a single primary one; treat exact
text-to-video field names as slightly less certain than Runway's (verified
straight from Runway's generated SDK) until confirmed against a real
account.

    base_url: https://api.klingai.com
    auth:     JWT, HS256, signed with an Access Key (AK) / Secret Key (SK)
              pair — NOT a static bearer token. Header: iss=AK, exp=now+1800s,
              nbf=now-5s, sent as `Authorization: Bearer <jwt>`. Tokens expire
              in 30 minutes, so this module mints a fresh one per call rather
              than caching (video generation calls are infrequent and slow
              enough that this isn't a meaningful cost).
    submit:   POST /v1/videos/text2video  {model_name, prompt, duration, ...}
    poll:     GET  /v1/videos/text2video/{task_id}
              -> data.task_status: submitted|processing|succeed|failed
              (succeed carries data.task_result.videos[].url)

Real per-request cost, same as Runway — see that module's docstring for the
confirm-after-estimate reasoning; the MCP server layer enforces it, not this
module.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

API_BASE = "https://api.klingai.com"

# Published at $0.07/s for Kling Standard (per public pricing coverage as of
# 2026) — more concrete than Runway's estimate, but still verify current
# pricing on your account before relying on it for a real cost decision.
APPROX_COST_PER_SECOND_USD = 0.07


def _providers_path() -> Path:
    from aria_code.apps.cli.config_paths import resolve_paths
    return resolve_paths().providers_file


def _keys() -> tuple[str, str]:
    """Return (access_key, secret_key), env vars first."""
    ak = os.getenv("KLING_ACCESS_KEY", "").strip()
    sk = os.getenv("KLING_SECRET_KEY", "").strip()
    if ak and sk:
        return ak, sk
    path = _providers_path()
    if not path.exists():
        return "", ""
    try:
        import json
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return "", ""
    entry = raw.get("data", {}).get("kling", {}) or {}
    return entry.get("access_key", ""), entry.get("secret_key", "")


def _require_keys() -> tuple[str, str]:
    ak, sk = _keys()
    if not ak or not sk:
        raise RuntimeError(
            "No Kling access_key/secret_key found. Set both with "
            "`/apikey set kling <access_key>:<secret_key>` or export "
            "KLING_ACCESS_KEY / KLING_SECRET_KEY."
        )
    return ak, sk


def _mint_jwt() -> str:
    import jwt

    ak, sk = _require_keys()
    now = int(time.time())
    payload = {"iss": ak, "exp": now + 1800, "nbf": now - 5}
    return jwt.encode(payload, sk, algorithm="HS256", headers={"typ": "JWT"})


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_mint_jwt()}", "Content-Type": "application/json"}


def estimate_cost(duration: int) -> Dict[str, Any]:
    """Rough cost estimate — call before submit_video(), not a live quote."""
    return {
        "provider": "kling",
        "duration_sec": duration,
        "estimated_cost_usd": round(duration * APPROX_COST_PER_SECOND_USD, 2),
        "note": "Illustrative estimate — verify current pricing on your Kling account dashboard.",
    }


def submit_video(
    prompt: str,
    *,
    model_name: str = "kling-v1",
    duration: int = 5,
    aspect_ratio: str = "16:9",
    mode: str = "std",
    negative_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Submit a text-to-video generation job. Returns immediately with a
    task id — poll with poll_video().

    duration must be "5" or "10" (Kling takes this as a string on the wire,
    handled here). mode: "std" (cheaper) or "pro" (higher quality).
    """
    import requests

    body: Dict[str, Any] = {
        "model_name": model_name,
        "prompt": prompt,
        "duration": str(duration),
        "aspect_ratio": aspect_ratio,
        "mode": mode,
    }
    if negative_prompt:
        body["negative_prompt"] = negative_prompt

    try:
        resp = requests.post(f"{API_BASE}/v1/videos/text2video", headers=_headers(), json=body, timeout=30)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    if resp.status_code != 200:
        return {"success": False, "error": f"Kling submission failed: {resp.status_code} {resp.text[:400]}"}
    data = resp.json()
    if data.get("code", 0) != 0:
        return {"success": False, "error": f"Kling error: {data.get('message', 'unknown')}"}
    task_id = (data.get("data") or {}).get("task_id", "")
    if not task_id:
        return {"success": False, "error": "Kling did not return a task_id"}
    return {"success": True, "task_id": task_id, "provider": "kling"}


def poll_video(task_id: str, *, download: bool = True) -> Dict[str, Any]:
    """Check a submitted task's status. On succeed, optionally downloads the
    first output video to the artifacts folder."""
    import requests

    try:
        resp = requests.get(f"{API_BASE}/v1/videos/text2video/{task_id}", headers=_headers(), timeout=30)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    if resp.status_code != 200:
        return {"success": False, "error": f"Kling task lookup failed: {resp.status_code} {resp.text[:400]}"}
    body = resp.json()
    if body.get("code", 0) != 0:
        return {"success": False, "error": f"Kling error: {body.get('message', 'unknown')}"}
    data = body.get("data") or {}
    status = data.get("task_status", "")

    if status == "succeed":
        videos = ((data.get("task_result") or {}).get("videos")) or []
        if not videos:
            return {"success": False, "status": status, "error": "succeed but no videos present"}
        video_url = videos[0].get("url", "")
        result = {"success": True, "status": status, "video_url": video_url}
        if download and video_url:
            from aria_code.artifacts import create_user_artifact

            video_resp = requests.get(video_url, timeout=120)
            if video_resp.status_code == 200:
                artifact = create_user_artifact("video", "kling", "kling_video", ".mp4")
                artifact.path.write_bytes(video_resp.content)
                result["path"] = str(artifact.path)
        return result

    if status == "failed":
        return {"success": False, "status": status, "error": data.get("task_status_msg", "generation failed")}

    return {"success": True, "status": status}
