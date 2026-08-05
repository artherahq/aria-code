"""Tests for video_analysis.py (Layer 2: local AI-assisted analysis).

detect_scenes runs against a real synthetic video built with opencv itself
(fast, no ffmpeg/model download needed) rather than mocking cv2, since the
histogram-diff logic is the actual thing worth verifying. transcribe_video's
model loading is mocked — downloading/running a real Whisper model is
exactly what the local_image_provider precedent avoids in unit tests.
"""
from __future__ import annotations

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

import video_analysis


def _write_two_color_video(path: str, fps: float = 10.0, seconds_per_color: int = 1) -> None:
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(path, fourcc, fps, (64, 64))
    frames_per_color = int(fps * seconds_per_color)
    blue = np.full((64, 64, 3), (255, 0, 0), dtype=np.uint8)
    red = np.full((64, 64, 3), (0, 0, 255), dtype=np.uint8)
    for _ in range(frames_per_color):
        writer.write(blue)
    for _ in range(frames_per_color):
        writer.write(red)
    writer.release()


def test_detect_scenes_finds_the_real_cut(tmp_path):
    video_path = str(tmp_path / "two_color.mp4")
    _write_two_color_video(video_path, fps=10.0, seconds_per_color=1)

    result = video_analysis.detect_scenes(video_path, threshold=20.0, sample_every_n_frames=1)
    assert result["success"] is True
    assert result["total_frames"] == 20
    # The cut is at frame 10 (1s in at 10fps) -> 1.0s
    assert any(abs(t - 1.0) < 0.2 for t in result["scene_cuts_sec"])


def test_detect_scenes_missing_file():
    result = video_analysis.detect_scenes("/nonexistent/video.mp4")
    assert result["success"] is False
    assert "not found" in result["error"]


def test_detect_scenes_no_cuts_in_uniform_video(tmp_path):
    video_path = str(tmp_path / "uniform.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(video_path, fourcc, 10.0, (64, 64))
    frame = np.full((64, 64, 3), (100, 100, 100), dtype=np.uint8)
    for _ in range(20):
        writer.write(frame)
    writer.release()

    result = video_analysis.detect_scenes(video_path, threshold=20.0)
    assert result["success"] is True
    assert result["scene_cuts_sec"] == []


def test_transcribe_video_missing_file():
    result = video_analysis.transcribe_video("/nonexistent/video.mp4")
    assert result["success"] is False
    assert "not found" in result["error"]


def test_get_whisper_model_raises_actionable_error_when_missing(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ImportError("no module named faster_whisper")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    video_analysis._whisper_model_cache.clear()
    with pytest.raises(RuntimeError, match="video_analysis' extra"):
        video_analysis._get_whisper_model("base")
