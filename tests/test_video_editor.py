"""Tests for video_editor.py (Layer 1: deterministic ffmpeg editing).

Mocks the ffmpeg subprocess boundary (_run_ffmpeg) rather than requiring a
real ffmpeg binary in CI — the pure-Python logic (arg construction, atempo
chaining math, input validation) is what these tests actually cover.
"""
from __future__ import annotations

from pathlib import Path

from aria_code import video_editor


def test_require_ffmpeg_absent(monkeypatch):
    monkeypatch.setattr(video_editor.shutil, "which", lambda _name: None)
    assert "ffmpeg not found" in video_editor._require_ffmpeg()


def test_require_ffmpeg_present(monkeypatch):
    monkeypatch.setattr(video_editor.shutil, "which", lambda _name: "/opt/homebrew/bin/ffmpeg")
    assert video_editor._require_ffmpeg() is None


def test_trim_video_rejects_missing_file(tmp_path):
    result = video_editor.trim_video(str(tmp_path / "nope.mp4"), 0, 5)
    assert result["success"] is False
    assert "not found" in result["error"]


def test_trim_video_rejects_bad_range(tmp_path, monkeypatch):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"fake")
    result = video_editor.trim_video(str(src), 5, 2)
    assert result["success"] is False
    assert "end must be greater than start" in result["error"]


def test_concat_videos_requires_at_least_two(tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"fake")
    result = video_editor.concat_videos([str(src)])
    assert result["success"] is False
    assert "at least 2" in result["error"]


def test_concat_videos_builds_filter_complex_for_all_inputs(tmp_path, monkeypatch):
    paths = []
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        p = tmp_path / name
        p.write_bytes(b"fake")
        paths.append(str(p))

    captured = {}

    def fake_run_ffmpeg(args, **kwargs):
        captured["args"] = args
        return {"success": True}

    monkeypatch.setattr(video_editor, "_run_ffmpeg", fake_run_ffmpeg)
    result = video_editor.concat_videos(paths, output_path=str(tmp_path / "out.mp4"))
    assert result["success"] is True
    # 3 inputs -> concat=n=3, and each input appears via -i
    assert captured["args"].count("-i") == 3
    assert "concat=n=3:v=1:a=1" in captured["args"][captured["args"].index("-filter_complex") + 1]


def test_change_speed_chains_atempo_stages_outside_0_5_to_2_range(tmp_path, monkeypatch):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"fake")
    captured = {}

    def fake_run_ffmpeg(args, **kwargs):
        captured["args"] = args
        return {"success": True}

    monkeypatch.setattr(video_editor, "_run_ffmpeg", fake_run_ffmpeg)
    # factor=4.0 is outside atempo's single-stage 0.5-2.0 range, must chain
    result = video_editor.change_speed(str(src), 4.0, output_path=str(tmp_path / "out.mp4"))
    assert result["success"] is True
    filter_complex = captured["args"][captured["args"].index("-filter_complex") + 1]
    # Two atempo=2.0000 stages chained (2.0 * 2.0 = 4.0)
    assert filter_complex.count("atempo=2.0000") == 2


def test_change_speed_rejects_non_positive_factor(tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"fake")
    result = video_editor.change_speed(str(src), 0)
    assert result["success"] is False
    assert "positive" in result["error"]


def test_convert_video_builds_crop_filter_for_aspect(tmp_path, monkeypatch):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"fake")
    captured = {}

    def fake_run_ffmpeg(args, **kwargs):
        captured["args"] = args
        return {"success": True}

    monkeypatch.setattr(video_editor, "_run_ffmpeg", fake_run_ffmpeg)
    result = video_editor.convert_video(str(src), aspect="9:16", output_path=str(tmp_path / "out.mp4"))
    assert result["success"] is True
    vf = captured["args"][captured["args"].index("-vf") + 1]
    assert "crop=" in vf and "9/16" in vf


def test_convert_video_rejects_malformed_aspect(tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"fake")
    result = video_editor.convert_video(str(src), aspect="not-a-ratio")
    assert result["success"] is False
    assert "aspect" in result["error"]


def test_overlay_audio_replace_maps_only_new_track(tmp_path, monkeypatch):
    src = tmp_path / "clip.mp4"
    audio = tmp_path / "music.mp3"
    src.write_bytes(b"fake")
    audio.write_bytes(b"fake")
    captured = {}

    def fake_run_ffmpeg(args, **kwargs):
        captured["args"] = args
        return {"success": True}

    monkeypatch.setattr(video_editor, "_run_ffmpeg", fake_run_ffmpeg)
    result = video_editor.overlay_audio(str(src), str(audio), replace=True, output_path=str(tmp_path / "out.mp4"))
    assert result["success"] is True
    assert "amix" not in " ".join(captured["args"])


def test_overlay_audio_mix_uses_amix_filter(tmp_path, monkeypatch):
    src = tmp_path / "clip.mp4"
    audio = tmp_path / "music.mp3"
    src.write_bytes(b"fake")
    audio.write_bytes(b"fake")
    captured = {}

    def fake_run_ffmpeg(args, **kwargs):
        captured["args"] = args
        return {"success": True}

    monkeypatch.setattr(video_editor, "_run_ffmpeg", fake_run_ffmpeg)
    result = video_editor.overlay_audio(str(src), str(audio), replace=False, output_path=str(tmp_path / "out.mp4"))
    assert result["success"] is True
    assert "amix" in " ".join(captured["args"])
