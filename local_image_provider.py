"""Local, self-hosted image generation — the non-OpenAI alternative to
openai_image_client.py, same relationship local_llm_provider.py (Ollama) has
to a cloud chat API: no per-call cost, no API key, runs entirely on this
machine via the open-weight `diffusers` pipeline.

Heavy dependencies (torch, diffusers, transformers, accelerate) are lazy-
imported and entirely optional — aria-code's core install stays lean, same
pattern as akshare/ccxt/playwright elsewhere in this codebase. Nothing here
downloads model weights or installs packages on import; that only happens
the first time generate_image_local()/edit_image_local() actually runs, and
raises a clear, actionable error if the extras aren't installed rather than
failing on a confusing internal import error.

Default model is SDXL-Turbo (~7GB, 1-4 step generation) rather than a full
SDXL/FLUX checkpoint (15-24GB) — chosen for feasibility on 16GB-class
unified-memory Macs and to leave room on disk, not because it's the best
possible quality. Override via ARIA_LOCAL_IMAGE_MODEL if you have the RAM
and disk for something larger.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_MODEL = os.getenv("ARIA_LOCAL_IMAGE_MODEL", "stabilityai/sdxl-turbo")

_pipeline_cache: Dict[str, Any] = {}
_img2img_cache: Dict[str, Any] = {}


def _select_device() -> str:
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _require_diffusers():
    try:
        import torch  # noqa: F401
        import diffusers  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "Local image generation needs the optional 'image_gen' extra: "
            "pip install -e '.[image_gen]'  (adds torch + diffusers, ~a few GB). "
            f"Missing: {exc}"
        ) from exc


def _get_pipeline(model: str):
    if model in _pipeline_cache:
        return _pipeline_cache[model]
    _require_diffusers()
    import torch
    from diffusers import AutoPipelineForText2Image

    device = _select_device()
    dtype = torch.float16 if device in ("mps", "cuda") else torch.float32
    pipe = AutoPipelineForText2Image.from_pretrained(model, torch_dtype=dtype)
    pipe = pipe.to(device)
    _pipeline_cache[model] = pipe
    return pipe


def _get_img2img_pipeline(model: str):
    if model in _img2img_cache:
        return _img2img_cache[model]
    _require_diffusers()
    import torch
    from diffusers import AutoPipelineForImage2Image

    device = _select_device()
    dtype = torch.float16 if device in ("mps", "cuda") else torch.float32
    pipe = AutoPipelineForImage2Image.from_pretrained(model, torch_dtype=dtype)
    pipe = pipe.to(device)
    _img2img_cache[model] = pipe
    return pipe


def generate_image_local(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    width: int = 1024,
    height: int = 1024,
    steps: int = 4,
    guidance_scale: float = 0.0,
) -> Dict[str, Any]:
    """Generate a new image from a text prompt entirely locally.

    guidance_scale=0.0 matches SDXL-Turbo's distilled few-step training —
    raise it (e.g. 7.5) if you switch `model` to a non-turbo checkpoint that
    expects classifier-free guidance.

    Blocking, CPU/GPU-bound — callers on an event loop must run this in an
    executor, same as every other client in this codebase. First call for a
    given `model` loads weights from disk (or downloads them once) into
    memory and is much slower than subsequent calls.
    """
    from artifacts import create_user_artifact

    try:
        pipe = _get_pipeline(model)
        result = pipe(
            prompt=prompt,
            width=width,
            height=height,
            num_inference_steps=steps,
            guidance_scale=guidance_scale,
        )
        image = result.images[0]
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    artifact = create_user_artifact("image", "generated", "poster_local", ".png")
    image.save(artifact.path)
    return {"success": True, "path": str(artifact.path), "model": model}


def edit_image_local(
    image_path: str,
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    strength: float = 0.6,
    steps: int = 4,
    guidance_scale: float = 0.0,
) -> Dict[str, Any]:
    """Transform an existing local photo per `prompt`, entirely locally
    (image-to-image, not the mask-based inpainting openai_image_client uses).

    `strength` (0-1) controls how much the output is allowed to diverge from
    the input — low keeps it close to the original photo, high lets the
    prompt dominate. 0.6 is a reasonable "restyle but keep composition"
    default for the minimal-editorial-poster use case.
    """
    from artifacts import create_user_artifact

    src = Path(image_path)
    if not src.exists():
        return {"success": False, "error": f"File not found: {image_path}"}

    try:
        from PIL import Image

        init_image = Image.open(src).convert("RGB")
        pipe = _get_img2img_pipeline(model)
        result = pipe(
            prompt=prompt,
            image=init_image,
            strength=strength,
            num_inference_steps=steps,
            guidance_scale=guidance_scale,
        )
        image = result.images[0]
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    artifact = create_user_artifact("image", "edited", f"{src.stem}_edited_local", ".png")
    image.save(artifact.path)
    return {"success": True, "path": str(artifact.path), "model": model}
