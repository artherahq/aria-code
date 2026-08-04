"""OpenAI image generation/editing client — the execution backend for prompts
compiled by the `minimal-editorial-poster` / `minimal-editorial-exports`
skills.

Two capabilities, matching OpenAI's two endpoints (verified against the
official openai-python SDK source, since the public docs page 403s to
scrapers):
    generate_image() -> POST /v1/images/generations (text -> new image)
    edit_image()     -> POST /v1/images/edits       (existing photo + prompt
                          -> transformed image; multipart, not JSON)

Both use gpt-image-1, which always returns base64 (no response_format /
url option for GPT image models, only for dall-e-2/3).

Key resolution mirrors aria_cli.py's _get_provider_key(): env var
OPENAI_API_KEY first, then providers.json's "llm" then "data" sections
under "openai" — so a key already set via `/apikey set openai sk-...` for
chat is reused here, no separate setup needed.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

API_BASE = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-image-1"


def _providers_path() -> Path:
    from apps.cli.config_paths import resolve_paths
    return resolve_paths().providers_file


def _api_key() -> str:
    env = os.getenv("OPENAI_API_KEY", "").strip()
    if env:
        return env
    path = _providers_path()
    if not path.exists():
        return ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    for section in ("llm", "data"):
        entry = raw.get(section, {}).get("openai", {}) or {}
        if entry.get("api_key"):
            return entry["api_key"]
    return ""


def _require_key() -> str:
    key = _api_key()
    if not key:
        raise RuntimeError(
            "No OpenAI API key found. Set one with `/apikey set openai sk-...` "
            "or export OPENAI_API_KEY."
        )
    return key


def _save_b64_image(b64_data: str, dest: Path) -> Path:
    dest.write_bytes(base64.b64decode(b64_data))
    return dest


def generate_image(
    prompt: str,
    *,
    size: str = "1024x1536",
    quality: str = "high",
) -> Dict[str, Any]:
    """Generate a new image from a text prompt (e.g. minimal-editorial-poster's
    compiled prompt) via POST /v1/images/generations.

    Blocking (uses requests) — callers on an event loop should run this in
    an executor, same as every other client in this codebase.
    """
    import requests

    from artifacts import create_user_artifact

    key = _require_key()
    resp = requests.post(
        f"{API_BASE}/images/generations",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": DEFAULT_MODEL, "prompt": prompt, "size": size, "quality": quality},
        timeout=120,
    )
    if resp.status_code != 200:
        return {"success": False, "error": f"OpenAI image generation failed: {resp.status_code} {resp.text[:300]}"}
    data = (resp.json().get("data") or [{}])[0]
    b64 = data.get("b64_json")
    if not b64:
        return {"success": False, "error": "OpenAI response had no b64_json image data"}

    artifact = create_user_artifact("image", "generated", "poster", ".png")
    _save_b64_image(b64, artifact.path)
    return {"success": True, "path": str(artifact.path)}


def edit_image(
    image_path: str,
    prompt: str,
    *,
    size: str = "1024x1536",
    quality: str = "high",
    mask_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Transform an existing local photo per `prompt` (e.g. "convert to duotone,
    simplify background to negative space, add scan-noise texture...") via
    POST /v1/images/edits — the execution path for a real user-provided photo
    run through the minimal-editorial-poster skill.

    Blocking (uses requests) — same threading note as generate_image.
    """
    import requests

    from artifacts import create_user_artifact

    src = Path(image_path)
    if not src.exists():
        return {"success": False, "error": f"File not found: {image_path}"}

    key = _require_key()
    files = {"image": (src.name, src.read_bytes())}
    if mask_path:
        mask = Path(mask_path)
        if not mask.exists():
            return {"success": False, "error": f"Mask file not found: {mask_path}"}
        files["mask"] = (mask.name, mask.read_bytes())

    resp = requests.post(
        f"{API_BASE}/images/edits",
        headers={"Authorization": f"Bearer {key}"},
        data={"model": DEFAULT_MODEL, "prompt": prompt, "size": size, "quality": quality},
        files=files,
        timeout=180,
    )
    if resp.status_code != 200:
        return {"success": False, "error": f"OpenAI image edit failed: {resp.status_code} {resp.text[:300]}"}
    data = (resp.json().get("data") or [{}])[0]
    b64 = data.get("b64_json")
    if not b64:
        return {"success": False, "error": "OpenAI response had no b64_json image data"}

    artifact = create_user_artifact("image", "edited", f"{src.stem}_edited", ".png")
    _save_b64_image(b64, artifact.path)
    return {"success": True, "path": str(artifact.path)}
