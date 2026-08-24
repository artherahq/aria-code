"""Runway video generation client — Layer 3 (cloud AI generation) of the
video architecture, one of two provider clients (see also
kling_video_client.py) behind the same submit/poll shape so
packages/aria_mcp/server.py can route to either without provider-specific
logic outside these two files.

Verified against Runway's own generated SDK source
(github.com/runwayml/sdk-python), not the public docs page (which returned
an unfetchable error) — same discipline as openai_image_client.py and
canva_client.py: primary-source field names, not guessed ones.

    base_url: https://api.dev.runwayml.com
    auth:     Authorization: Bearer {api_key}, X-Runway-Version: 2024-11-06
    submit:   POST /v1/text_to_video  {model, promptText, ratio, duration?, ...}
    poll:     GET  /v1/tasks/{id}  -> status PENDING|THROTTLED|RUNNING|FAILED|SUCCEEDED
              (SUCCEEDED carries `output`: a list of video URLs, valid 24-48h)

Real per-request cost — every submission is a genuine payment. This module
never gates that itself (no local "confirmed" concept); the MCP server layer
enforces the confirm-after-estimate gate, matching the broker order-preview
pattern's reasoning: submission is the moment money is spent, so the caller
must have deliberately opted in.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

API_BASE = "https://api.dev.runwayml.com"
API_VERSION = "2024-11-06"

# Rough, illustrative-only pricing — Runway doesn't publish a stable public
# per-second API rate the way Kling does; verify current pricing on your
# account dashboard before relying on this for a real cost decision.
APPROX_COST_PER_SECOND_USD = 0.25


def _providers_path() -> Path:
    from apps.cli.config_paths import resolve_paths
    return resolve_paths().providers_file


def _api_key() -> str:
    env = os.getenv("RUNWAYML_API_SECRET", "").strip()
    if env:
        return env
    path = _providers_path()
    if not path.exists():
        return ""
    try:
        import json
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    for section in ("llm", "data"):
        entry = raw.get(section, {}).get("runway", {}) or {}
        if entry.get("api_key"):
            return entry["api_key"]
    return ""


def _require_key() -> str:
    key = _api_key()
    if not key:
        raise RuntimeError(
            "No Runway API key found. Set one with `/apikey set runway <key>` "
            "or export RUNWAYML_API_SECRET."
        )
    return key


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_require_key()}",
        "X-Runway-Version": API_VERSION,
        "Content-Type": "application/json",
    }


def estimate_cost(duration: int) -> Dict[str, Any]:
    """Rough cost estimate — call before submit_video(), not a live quote."""
    return {
        "provider": "runway",
        "duration_sec": duration,
        "estimated_cost_usd": round(duration * APPROX_COST_PER_SECOND_USD, 2),
        "note": "Illustrative estimate — verify current pricing on your Runway account dashboard.",
    }


def submit_video(
    prompt: str,
    *,
    model: str = "gen4.5",
    duration: int = 5,
    ratio: str = "1280:720",
) -> Dict[str, Any]:
    """Submit a text-to-video generation job. Returns immediately with a
    task id — this does NOT wait for the video, since generation takes
    minutes; poll with poll_video().

    duration must be 2-10 for gen4.5; ratio must be one of the values that
    model supports ("1280:720"/"720:1280" for gen4.5; veo3.1/veo3.1_fast
    additionally support "1080:1920"/"1920:1080" — see Runway's docs for the
    full per-model matrix, verified in this repo only for gen4.5's basic
    shape).
    """
    import requests

    body = {"model": model, "promptText": prompt, "ratio": ratio, "duration": duration}
    try:
        resp = requests.post(f"{API_BASE}/v1/text_to_video", headers=_headers(), json=body, timeout=30)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    if resp.status_code not in (200, 201, 202):
        return {"success": False, "error": f"Runway submission failed: {resp.status_code} {resp.text[:400]}"}
    data = resp.json()
    task_id = data.get("id", "")
    if not task_id:
        return {"success": False, "error": "Runway did not return a task id"}
    return {"success": True, "task_id": task_id, "provider": "runway"}


def poll_video(task_id: str, *, download: bool = True) -> Dict[str, Any]:
    """Check a submitted task's status. On SUCCEEDED, optionally downloads
    the first output video to the artifacts folder."""
    import requests

    try:
        resp = requests.get(f"{API_BASE}/v1/tasks/{task_id}", headers=_headers(), timeout=30)
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    if resp.status_code != 200:
        return {"success": False, "error": f"Runway task lookup failed: {resp.status_code} {resp.text[:400]}"}
    data = resp.json()
    status = data.get("status", "")

    if status == "SUCCEEDED":
        outputs = data.get("output") or []
        if not outputs:
            return {"success": False, "status": status, "error": "SUCCEEDED but no output URLs present"}
        result = {"success": True, "status": status, "video_url": outputs[0]}
        if download:
            from artifacts import create_user_artifact

            video_resp = requests.get(outputs[0], timeout=120)
            if video_resp.status_code == 200:
                artifact = create_user_artifact("video", "runway", "runway_video", ".mp4")
                artifact.path.write_bytes(video_resp.content)
                result["path"] = str(artifact.path)
        return result

    if status == "FAILED":
        return {"success": False, "status": status, "error": data.get("failure", "generation failed")}

    return {"success": True, "status": status, "progress": data.get("progress")}
