"""Layer 2 of the video architecture: local, free AI-assisted analysis —
produces editing *decisions/suggestions* (transcript, scene-cut timestamps),
never new pixels. Both pieces run entirely on this machine:

    transcribe_video() — faster-whisper (CTranslate2-backed, much lighter
        than diffusion models: the "base" model is ~150MB, "small" ~500MB)
    detect_scenes()    — opencv (already an existing optional dependency,
        used elsewhere for video keyframe extraction) via a histogram-diff
        cut detector — no new heavy model, just frame comparison

Both are lazy-imported and optional, same pattern as local_image_provider.py.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

_whisper_model_cache: Dict[str, Any] = {}


def _require_ffmpeg() -> Optional[str]:
    if shutil.which("ffmpeg") is None:
        return "ffmpeg not found on PATH. Install it: brew install ffmpeg"
    return None


def _extract_audio(input_path: str, out_wav: str) -> Dict[str, Any]:
    missing = _require_ffmpeg()
    if missing:
        return {"success": False, "error": missing}
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-vn", "-ac", "1", "-ar", "16000", out_wav],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    if result.returncode != 0:
        return {"success": False, "error": result.stderr[-500:]}
    return {"success": True}


def _get_whisper_model(model_size: str):
    if model_size in _whisper_model_cache:
        return _whisper_model_cache[model_size]
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Transcription needs the optional 'video_analysis' extra: "
            "pip install -e '.[video_analysis]'  (adds faster-whisper). "
            f"Missing: {exc}"
        ) from exc
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    _whisper_model_cache[model_size] = model
    return model


def transcribe_video(input_path: str, *, model_size: str = "base", language: Optional[str] = None) -> Dict[str, Any]:
    """Transcribe a video's speech track locally via faster-whisper.

    Returns segments with timestamps (for captions/subtitles) plus the full
    text. Blocking, CPU-bound — callers on an event loop must run this in an
    executor, same as every other client in this codebase.
    """
    if not Path(input_path).exists():
        return {"success": False, "error": f"File not found: {input_path}"}

    with tempfile.TemporaryDirectory() as tmpdir:
        wav_path = str(Path(tmpdir) / "audio.wav")
        extracted = _extract_audio(input_path, wav_path)
        if not extracted["success"]:
            return extracted

        try:
            model = _get_whisper_model(model_size)
            segments, info = model.transcribe(wav_path, language=language)
            segment_list = [
                {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
                for s in segments
            ]
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    full_text = " ".join(s["text"] for s in segment_list)
    return {
        "success": True,
        "language": getattr(info, "language", language),
        "segments": segment_list,
        "text": full_text,
    }


def detect_scenes(input_path: str, *, threshold: float = 30.0, sample_every_n_frames: int = 5) -> Dict[str, Any]:
    """Detect scene-cut timestamps via consecutive-frame histogram
    difference — sampling every `sample_every_n_frames` rather than every
    frame keeps this fast on longer videos at the cost of cut-timing
    precision within that sampling window.

    `threshold` is the Bhattacharyya-distance-derived diff score above
    which two sampled frames are considered different scenes; higher =
    less sensitive (fewer, more confident cuts).
    """
    if not Path(input_path).exists():
        return {"success": False, "error": f"File not found: {input_path}"}
    try:
        import cv2
    except ImportError as exc:
        return {
            "success": False,
            "error": (
                "Scene detection needs the optional 'video' extra: "
                f"pip install -e '.[video]'  (adds opencv-python). Missing: {exc}"
            ),
        }

    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        return {"success": False, "error": f"Could not open video: {input_path}"}

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cuts: List[float] = []
    prev_hist = None
    frame_idx = 0

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_idx % sample_every_n_frames == 0:
                hist = cv2.calcHist([frame], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
                cv2.normalize(hist, hist)
                if prev_hist is not None:
                    diff = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA) * 100
                    if diff > threshold:
                        cuts.append(round(frame_idx / fps, 2))
                prev_hist = hist
            frame_idx += 1
    finally:
        cap.release()

    return {"success": True, "fps": fps, "total_frames": frame_idx, "scene_cuts_sec": cuts}
