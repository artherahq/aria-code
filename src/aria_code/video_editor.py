"""Layer 1 of the video architecture: deterministic ffmpeg-based editing —
trim, concat, overlay text, overlay audio, convert format/aspect, change
speed. No AI, no model weights, no API key — just a subprocess wrapper
around the `ffmpeg`/`ffprobe` CLIs, same pattern as project_tools.py's
`_git()` helper (timeout-bounded, capture output, never raise — return an
error dict instead).

Layer 2 (video_analysis.py, local Whisper transcription + opencv scene
detection) and Layer 3 (cloud AI generation, not built yet — needs a
provider/budget decision first) are separate modules.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_TIMEOUT = 300.0


def _require_ffmpeg() -> Optional[str]:
    if shutil.which("ffmpeg") is None:
        return "ffmpeg not found on PATH. Install it: brew install ffmpeg"
    return None


def _run_ffmpeg(args: List[str], *, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Run an ffmpeg command. Returns {"success": bool, "error"?: str}."""
    missing = _require_ffmpeg()
    if missing:
        return {"success": False, "error": missing}
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"ffmpeg timed out after {timeout:.0f}s"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    if result.returncode != 0:
        return {"success": False, "error": result.stderr[-800:]}
    return {"success": True}


def _default_output(input_path: str, suffix: str, ext: Optional[str] = None) -> Path:
    src = Path(input_path)
    from artifacts import create_user_artifact

    artifact = create_user_artifact("video", src.stem, f"{src.stem}_{suffix}", ext or src.suffix)
    return artifact.path


def probe_video(input_path: str) -> Dict[str, Any]:
    """Return duration/resolution/codec info via ffprobe."""
    if shutil.which("ffprobe") is None:
        return {"success": False, "error": "ffprobe not found on PATH. Install it: brew install ffmpeg"}
    if not Path(input_path).exists():
        return {"success": False, "error": f"File not found: {input_path}"}
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=width,height,codec_type,codec_name,r_frame_rate:format=duration,size",
             "-of", "json", input_path],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    if result.returncode != 0:
        return {"success": False, "error": result.stderr[-500:]}
    import json
    return {"success": True, **json.loads(result.stdout)}


def trim_video(input_path: str, start: float, end: float, *, output_path: Optional[str] = None) -> Dict[str, Any]:
    """Cut [start, end] seconds out of a video (re-encodes for frame-accurate cuts)."""
    if not Path(input_path).exists():
        return {"success": False, "error": f"File not found: {input_path}"}
    if end <= start:
        return {"success": False, "error": "end must be greater than start"}
    out = Path(output_path) if output_path else _default_output(input_path, "trimmed")
    result = _run_ffmpeg([
        "-ss", str(start), "-to", str(end), "-i", input_path,
        "-c:v", "libx264", "-c:a", "aac", str(out),
    ])
    if not result["success"]:
        return result
    return {"success": True, "path": str(out)}


def concat_videos(input_paths: List[str], *, output_path: Optional[str] = None) -> Dict[str, Any]:
    """Concatenate multiple videos in order. Re-encodes each to a common
    codec first so mismatched source formats (different codec/resolution)
    don't silently produce a broken concat — the naive ffmpeg concat demuxer
    only works when all inputs already share a codec."""
    for p in input_paths:
        if not Path(p).exists():
            return {"success": False, "error": f"File not found: {p}"}
    if len(input_paths) < 2:
        return {"success": False, "error": "Need at least 2 videos to concatenate"}

    out = Path(output_path) if output_path else _default_output(input_paths[0], "concat")
    filter_inputs = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(len(input_paths)))
    args = []
    for p in input_paths:
        args += ["-i", p]
    args += [
        "-filter_complex", f"{filter_inputs}concat=n={len(input_paths)}:v=1:a=1[outv][outa]",
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-c:a", "aac", str(out),
    ]
    result = _run_ffmpeg(args)
    if not result["success"]:
        return result
    return {"success": True, "path": str(out)}


def overlay_text(
    input_path: str,
    text: str,
    *,
    position: str = "bottom",
    font_size: int = 36,
    font_color: str = "white",
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Burn a text overlay onto the video.

    Renders the text as a transparent PNG via Pillow and composites it with
    ffmpeg's `overlay` filter, rather than ffmpeg's own `drawtext` filter —
    `drawtext` needs ffmpeg built with libfreetype, which isn't guaranteed
    (confirmed absent on a stock Homebrew ffmpeg install: `No such filter:
    'drawtext'`). Rendering the text ourselves works on any ffmpeg build.
    """
    if not Path(input_path).exists():
        return {"success": False, "error": f"File not found: {input_path}"}

    probe = probe_video(input_path)
    if not probe["success"]:
        return probe
    video_stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video_stream:
        return {"success": False, "error": "Could not determine video dimensions"}
    width, height = int(video_stream["width"]), int(video_stream["height"])

    from PIL import Image, ImageDraw, ImageFont

    overlay_img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay_img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
    except Exception:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    margin = 40
    positions = {
        "top": ((width - text_w) // 2, margin),
        "bottom": ((width - text_w) // 2, height - text_h - margin),
        "center": ((width - text_w) // 2, (height - text_h) // 2),
    }
    x, y = positions.get(position, positions["bottom"])
    pad = 10
    draw.rectangle([x - pad, y - pad, x + text_w + pad, y + text_h + pad], fill=(0, 0, 0, 128))
    draw.text((x, y), text, font=font, fill=font_color)

    out = Path(output_path) if output_path else _default_output(input_path, "text")
    with tempfile.TemporaryDirectory() as tmpdir:
        overlay_path = str(Path(tmpdir) / "overlay.png")
        overlay_img.save(overlay_path)
        result = _run_ffmpeg([
            "-i", input_path, "-i", overlay_path,
            "-filter_complex", "[0:v][1:v]overlay=0:0",
            "-c:a", "copy", str(out),
        ])
    if not result["success"]:
        return result
    return {"success": True, "path": str(out)}


def overlay_audio(input_path: str, audio_path: str, *, replace: bool = False, output_path: Optional[str] = None) -> Dict[str, Any]:
    """Add or replace the audio track. replace=True drops the original audio
    entirely; replace=False mixes the new track under the original."""
    if not Path(input_path).exists():
        return {"success": False, "error": f"File not found: {input_path}"}
    if not Path(audio_path).exists():
        return {"success": False, "error": f"File not found: {audio_path}"}

    out = Path(output_path) if output_path else _default_output(input_path, "audio")
    if replace:
        args = ["-i", input_path, "-i", audio_path, "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-shortest", str(out)]
    else:
        args = ["-i", input_path, "-i", audio_path,
                "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first[a]",
                "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-shortest", str(out)]
    result = _run_ffmpeg(args)
    if not result["success"]:
        return result
    return {"success": True, "path": str(out)}


def convert_video(
    input_path: str,
    *,
    output_format: Optional[str] = None,
    aspect: Optional[str] = None,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Convert container/codec format and/or reframe to a target aspect
    ratio (e.g. "9:16" for vertical social, "1:1" for square) via
    crop-to-fill — center-crops rather than letterboxing, since a cropped
    fill is what "reframe for a platform" almost always means in practice."""
    if not Path(input_path).exists():
        return {"success": False, "error": f"File not found: {input_path}"}

    ext = f".{output_format}" if output_format else None
    out = Path(output_path) if output_path else _default_output(input_path, "converted", ext)

    vf = None
    if aspect:
        try:
            aw, ah = (int(x) for x in aspect.split(":"))
        except ValueError:
            return {"success": False, "error": "aspect must be like '9:16'"}
        vf = f"crop='min(iw,ih*{aw}/{ah})':'min(ih,iw*{ah}/{aw})',scale=trunc(iw/2)*2:trunc(ih/2)*2"

    args = ["-i", input_path]
    if vf:
        args += ["-vf", vf]
    args += ["-c:v", "libx264", "-c:a", "aac", str(out)]
    result = _run_ffmpeg(args)
    if not result["success"]:
        return result
    return {"success": True, "path": str(out)}


def change_speed(input_path: str, factor: float, *, output_path: Optional[str] = None) -> Dict[str, Any]:
    """Speed up (factor > 1) or slow down (factor < 1) a video, audio pitch-
    corrected. atempo only accepts 0.5-2.0 per filter pass, so factors
    outside that range are chained across multiple atempo stages."""
    if not Path(input_path).exists():
        return {"success": False, "error": f"File not found: {input_path}"}
    if factor <= 0:
        return {"success": False, "error": "factor must be positive"}

    out = Path(output_path) if output_path else _default_output(input_path, "speed")
    atempo_stages = []
    remaining = factor
    while remaining > 2.0:
        atempo_stages.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        atempo_stages.append(0.5)
        remaining /= 0.5
    atempo_stages.append(remaining)
    atempo_filter = ",".join(f"atempo={s:.4f}" for s in atempo_stages)

    result = _run_ffmpeg([
        "-i", input_path,
        "-filter_complex", f"[0:v]setpts={1/factor:.6f}*PTS[v];[0:a]{atempo_filter}[a]",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-c:a", "aac", str(out),
    ])
    if not result["success"]:
        return result
    return {"success": True, "path": str(out)}
