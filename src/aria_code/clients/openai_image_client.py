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

# Per-image USD cost by (quality, size), verified against OpenAI's published
# gpt-image-1 output-token pricing (developers.openai.com/api/docs/guides/
# image-generation) — output tokens only; input text/image tokens add a few
# thousandths of a cent and are ignored here as noise.
_COST_USD: Dict[str, Dict[str, float]] = {
    "low": {"1024x1024": 0.011, "1024x1536": 0.016, "1536x1024": 0.016},
    "medium": {"1024x1024": 0.042, "1024x1536": 0.063, "1536x1024": 0.063},
    "high": {"1024x1024": 0.167, "1024x1536": 0.25, "1536x1024": 0.25},
}


def estimate_cost(size: str = "1024x1536", quality: str = "high") -> Dict[str, Any]:
    """Rough cost estimate — call before generate_image()/edit_image(),
    not a live quote. "auto" quality/size can't be priced ahead of time
    (OpenAI picks the actual value), so it's reported as unknown rather
    than guessed."""
    per_image = (_COST_USD.get(quality) or {}).get(size)
    return {
        "provider": "openai",
        "model": DEFAULT_MODEL,
        "size": size,
        "quality": quality,
        "estimated_cost_usd": per_image,
        "note": (
            "Illustrative estimate from OpenAI's published per-image pricing; "
            "verify current pricing at platform.openai.com/docs/pricing."
            if per_image is not None
            else "'auto' size/quality is resolved by OpenAI at request time and can't be priced ahead of submission."
        ),
    }


def _providers_path() -> Path:
    from aria_code.apps.cli.config_paths import resolve_paths
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


def _normalize_image_for_upload(path: Path, *, keep_alpha: bool = False) -> tuple[str, bytes]:
    """Re-encode an arbitrary local image file into a clean single-frame PNG
    before uploading to OpenAI's edits endpoint.

    Two real problems this fixes, not just defensive coding — both
    reproduced directly against real iPhone photos:
      1. iPhones commonly save JPEGs as MPO (Multi-Picture Object — an
         embedded-stereo-frame container, not a plain single-frame JPEG)
         even with a plain .jpg extension. OpenAI's edit endpoint rejects
         these with a 400 "Invalid image file or mode" even though the
         extension and declared docs (png/webp/jpg) suggest it should work.
      2. A portrait photo is commonly stored as landscape pixels plus an
         EXIF orientation tag telling viewers to rotate on display — the
         raw bytes, uploaded as-is, are genuinely sideways. Reproduced: a
         raw PIL open() of a real orientation=6 photo, no exif_transpose,
         is visibly sideways.
    Re-saving through PIL with exif_transpose applied produces a single-
    frame PNG immune to both, regardless of the source container/orientation.
    """
    import io

    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(Image.open(path))
    img = img.convert("RGBA" if keep_alpha else "RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return f"{path.stem}.png", buf.getvalue()


def generate_image(
    prompt: str,
    *,
    size: str = "1024x1536",
    quality: str = "high",
    confirmed: bool = False,
) -> Dict[str, Any]:
    """Generate a new image from a text prompt (e.g. minimal-editorial-poster's
    compiled prompt) via POST /v1/images/generations — real per-call cost,
    billed by OpenAI the instant this succeeds (see estimate_cost()).

    Blocking (uses requests) — callers on an event loop should run this in
    an executor, same as every other client in this codebase.
    """
    if not confirmed:
        return {
            "success": False,
            "error": (
                "confirmed must be true to generate a real (paid) image. "
                "Call estimate_cost() first to see the cost, then resubmit with confirmed=true."
            ),
        }

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
    confirmed: bool = False,
) -> Dict[str, Any]:
    """Transform an existing local photo per `prompt` (e.g. "convert to duotone,
    simplify background to negative space, add scan-noise texture...") via
    POST /v1/images/edits — the execution path for a real user-provided photo
    run through the minimal-editorial-poster skill. Real per-call cost,
    billed by OpenAI the instant this succeeds (see estimate_cost()).

    Blocking (uses requests) — same threading note as generate_image.
    """
    if not confirmed:
        return {
            "success": False,
            "error": (
                "confirmed must be true to generate a real (paid) image edit. "
                "Call estimate_cost() first to see the cost, then resubmit with confirmed=true."
            ),
        }

    import requests

    from artifacts import create_user_artifact

    src = Path(image_path)
    if not src.exists():
        return {"success": False, "error": f"File not found: {image_path}"}

    key = _require_key()
    try:
        files = {"image": _normalize_image_for_upload(src)}
    except Exception as exc:
        return {"success": False, "error": f"Could not read image_path: {exc}"}
    if mask_path:
        mask = Path(mask_path)
        if not mask.exists():
            return {"success": False, "error": f"Mask file not found: {mask_path}"}
        try:
            files["mask"] = _normalize_image_for_upload(mask, keep_alpha=True)
        except Exception as exc:
            return {"success": False, "error": f"Could not read mask_path: {exc}"}

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
