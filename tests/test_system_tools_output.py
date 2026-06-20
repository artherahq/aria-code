import pathlib
import sys


_CLI_DIR = str(pathlib.Path(__file__).parents[1])
if _CLI_DIR not in sys.path:
    sys.path.insert(0, _CLI_DIR)


def test_run_command_persists_full_output_when_inline_stdout_is_truncated(monkeypatch, tmp_path):
    from apps.cli.tools.system_tools import tool_run_command

    monkeypatch.setenv("ARIA_ARTIFACT_ROOT", str(tmp_path))
    command = "python3 -c \"print('x' * 6001)\""

    result = tool_run_command({
        "command": command,
        "policy": "balanced",
        "permission_mode": "workspace-write",
        "network_enabled": False,
    }, has_rich=False)

    assert result["success"] is True
    data = result["data"]
    assert data["stdout_truncated"] is True
    assert len(data["stdout"]) <= 5001
    assert data["full_output_path"]
    full_output = pathlib.Path(data["full_output_path"]).read_text(encoding="utf-8")
    assert "$ python3 -c" in full_output
    assert "x" * 6001 in full_output


def test_run_command_persists_full_output_for_many_lines(monkeypatch, tmp_path):
    from apps.cli.tools.system_tools import tool_run_command

    monkeypatch.setenv("ARIA_ARTIFACT_ROOT", str(tmp_path))

    result = tool_run_command({
        "command": "python3 -c \"print('\\n'.join(str(i) for i in range(24)))\"",
        "policy": "balanced",
        "permission_mode": "workspace-write",
        "network_enabled": False,
    }, has_rich=False)

    assert result["success"] is True
    data = result["data"]
    assert data["full_output_path"]
    full_output = pathlib.Path(data["full_output_path"]).read_text(encoding="utf-8")
    assert "23" in full_output


def test_run_command_console_hides_full_output_path(monkeypatch, tmp_path):
    from apps.cli.tools.system_tools import tool_run_command

    class FakeConsole:
        def __init__(self):
            self.lines = []

        def print(self, *parts, **_kwargs):
            self.lines.append(" ".join(str(p) for p in parts))

    console = FakeConsole()
    monkeypatch.setenv("ARIA_ARTIFACT_ROOT", str(tmp_path))

    result = tool_run_command({
        "command": "python3 -c \"print('x' * 6001)\"",
        "policy": "balanced",
        "permission_mode": "workspace-write",
        "network_enabled": False,
    }, console=console, has_rich=True)

    rendered = "\n".join(console.lines)
    assert result["success"] is True
    assert "full output saved" in rendered
    assert "/Users" not in rendered
    assert str(tmp_path) not in rendered
