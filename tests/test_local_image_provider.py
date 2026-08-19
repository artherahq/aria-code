"""Tests for local_image_provider.py — the graceful-degradation and
fp16-variant-selection logic; not the real diffusion pipeline (that's
exercised manually against real hardware, not in CI)."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from providers import local_image_provider as lip

# 可选依赖 guard：这些用例调用的代码路径需要 PIL（files extra）。
# CI 的 test workflow 只装 .[cn,dev]，其注释明确写着"没装 extra 的可选功能
# 会优雅跳过"——但这几个用例此前没有 guard，缺依赖时直接 FAILED 而不是
# SKIPPED，让 pytest (Python 3.12) 长期红灯。改成 importorskip 以符合该契约。
pytest.importorskip("PIL", reason="需要 files extra（pip install 'aria-code[files]'）")


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


def test_resize_for_img2img_downscales_12mp_photo_to_budget():
    from PIL import Image

    # Real-world case that triggered an MPS OOM before this existed: a
    # 4032x3024 phone photo fed straight into SDXL img2img.
    img = Image.new("RGB", (4032, 3024))
    resized = lip._resize_for_img2img(img)
    assert max(resized.size) <= lip._MAX_IMG2IMG_DIMENSION
    # aspect ratio preserved (within multiple-of-8 rounding)
    assert abs(resized.size[0] / resized.size[1] - 4032 / 3024) < 0.01


def test_resize_for_img2img_rounds_to_multiple_of_8():
    from PIL import Image

    img = Image.new("RGB", (4032, 3024))
    resized = lip._resize_for_img2img(img)
    assert resized.size[0] % 8 == 0
    assert resized.size[1] % 8 == 0


def test_resize_for_img2img_leaves_small_image_unchanged():
    from PIL import Image

    img = Image.new("RGB", (512, 384))
    resized = lip._resize_for_img2img(img, max_dim=1024)
    assert resized.size == (512, 384)


def test_edit_image_local_resizes_before_calling_pipeline(tmp_path):
    from PIL import Image

    src = tmp_path / "big_photo.jpg"
    Image.new("RGB", (4032, 3024)).save(src)

    fake_pipe = MagicMock()
    fake_result = MagicMock()
    fake_output_image = MagicMock()
    fake_result.images = [fake_output_image]
    fake_pipe.return_value = fake_result

    with patch.object(lip, "_get_img2img_pipeline", return_value=fake_pipe), \
         patch("artifacts.create_user_artifact") as mock_artifact:
        mock_artifact.return_value.path = tmp_path / "out.png"
        result = lip.edit_image_local(str(src), "restyle it")

    assert result["success"] is True
    _, kwargs = fake_pipe.call_args
    passed_image = kwargs["image"]
    assert max(passed_image.size) <= lip._MAX_IMG2IMG_DIMENSION


def test_edit_image_local_corrects_exif_rotation_before_pipeline(tmp_path):
    from PIL import Image

    # orientation=6 ("rotate 90 CW to display correctly") on a 400x200
    # source — without exif_transpose, the pipeline would receive a
    # genuinely sideways 400x200 image instead of the correct 200x400.
    img = Image.new("RGB", (400, 200), color="blue")
    exif = img.getexif()
    exif[274] = 6
    src = tmp_path / "sideways.jpg"
    img.save(src, exif=exif)

    fake_pipe = MagicMock()
    fake_result = MagicMock()
    fake_result.images = [MagicMock()]
    fake_pipe.return_value = fake_result

    with patch.object(lip, "_get_img2img_pipeline", return_value=fake_pipe), \
         patch("artifacts.create_user_artifact") as mock_artifact:
        mock_artifact.return_value.path = tmp_path / "out.png"
        lip.edit_image_local(str(src), "restyle it")

    _, kwargs = fake_pipe.call_args
    passed_image = kwargs["image"]
    # corrected orientation: taller than wide (200x400 pre-resize)
    assert passed_image.size[1] > passed_image.size[0]


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
