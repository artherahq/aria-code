"""Tests for local_image_provider.py — the graceful-degradation and
fp16-variant-selection logic; not the real diffusion pipeline (that's
exercised manually against real hardware, not in CI)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import local_image_provider as lip


def test_generate_image_local_missing_deps_returns_actionable_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("no module named torch")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    lip._pipeline_cache.clear()
    result = lip.generate_image_local("a minimal poster")
    assert result["success"] is False
    assert "image_gen' extra" in result["error"]


def test_get_pipeline_requests_fp16_variant_on_mps():
    import sys

    fake_torch = MagicMock()
    fake_torch.backends.mps.is_available.return_value = True
    fake_torch.cuda.is_available.return_value = False
    fake_torch.float16 = "float16-sentinel"

    fake_pipeline_cls = MagicMock()
    fake_pipe_instance = MagicMock()
    fake_pipeline_cls.from_pretrained.return_value = fake_pipe_instance
    fake_pipe_instance.to.return_value = fake_pipe_instance
    fake_diffusers = MagicMock()
    fake_diffusers.AutoPipelineForText2Image = fake_pipeline_cls

    lip._pipeline_cache.clear()
    with patch.dict(sys.modules, {"torch": fake_torch, "diffusers": fake_diffusers}), \
         patch.object(lip, "_require_diffusers"):
        lip._get_pipeline("stabilityai/sdxl-turbo")

    _, kwargs = fake_pipeline_cls.from_pretrained.call_args
    assert kwargs["variant"] == "fp16"


def test_edit_image_local_missing_file():
    result = lip.edit_image_local("/nonexistent/photo.jpg", "restyle it")
    assert result["success"] is False
    assert "not found" in result["error"]


def test_select_device_prefers_mps_then_cuda_then_cpu():
    import sys

    fake_torch_mps = MagicMock()
    fake_torch_mps.backends.mps.is_available.return_value = True
    with patch.dict(sys.modules, {"torch": fake_torch_mps}):
        assert lip._select_device() == "mps"

    fake_torch_cuda = MagicMock()
    fake_torch_cuda.backends.mps.is_available.return_value = False
    fake_torch_cuda.cuda.is_available.return_value = True
    with patch.dict(sys.modules, {"torch": fake_torch_cuda}):
        assert lip._select_device() == "cuda"

    fake_torch_cpu = MagicMock()
    fake_torch_cpu.backends.mps.is_available.return_value = False
    fake_torch_cpu.cuda.is_available.return_value = False
    with patch.dict(sys.modules, {"torch": fake_torch_cpu}):
        assert lip._select_device() == "cpu"
