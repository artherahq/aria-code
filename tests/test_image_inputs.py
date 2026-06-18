from __future__ import annotations

import base64
import pathlib
import sys

import pytest


_CLI_DIR = str(pathlib.Path(__file__).parents[1])
if _CLI_DIR not in sys.path:
    sys.path.insert(0, _CLI_DIR)


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4////fwAJ"
    "+wP+e6m1NwAAAABJRU5ErkJggg=="
)


def test_load_image_source_from_file(tmp_path):
    from apps.cli.commands.ui_cmds import UiCommandsMixin

    path = tmp_path / "chart.png"
    path.write_bytes(_PNG_1X1)

    payload = UiCommandsMixin._load_image_source(str(path))

    assert payload["label"] == "chart.png"
    assert payload["mime"] == "image/png"
    assert payload["size_kb"] >= 1
    assert payload["b64"]


def test_load_image_source_from_url(monkeypatch):
    from apps.cli.commands.ui_cmds import UiCommandsMixin

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "image/png"}
        content = _PNG_1X1

        def raise_for_status(self):
            return None

    class FakeRequests:
        def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setitem(sys.modules, "requests", FakeRequests())

    payload = UiCommandsMixin._load_image_source("https://example.com/chart.png")

    assert payload["label"] == "example.com/chart.png"
    assert payload["mime"] == "image/png"
    assert payload["size_kb"] >= 1


def test_visible_commands_include_upload_image():
    from apps.cli.commands.catalog import VISIBLE_SLASH_COMMANDS

    assert "/upload-image" in VISIBLE_SLASH_COMMANDS
