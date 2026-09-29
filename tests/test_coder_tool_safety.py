"""Regression checks for model-facing code tools."""

import asyncio
import sys

from aria_code.agents.engineering.coder import CoderAgent


def call(agent, name, args):
    return asyncio.run(agent._execute_tool(name, args))


def test_command_requires_real_approval(tmp_path):
    agent = CoderAgent(output_dir=tmp_path)
    marker = tmp_path / "executed"
    command = f'{sys.executable} -c "open(\'executed\', \'w\').write(\'yes\')"'
    assert "approval is unavailable" in call(agent, "run_command", {"command": command})
    assert not marker.exists()
    agent.command_approval = lambda argv, cwd: True
    assert "ReturnCode: 0" in call(agent, "run_command", {"command": command})
    assert marker.read_text() == "yes"


def test_paths_cannot_escape_workspace(tmp_path):
    agent = CoderAgent(output_dir=tmp_path / "workspace")
    assert "Successfully" in call(agent, "write_file", {"filename": "ok.txt", "content": "ok"})
    assert "Path escapes" in call(agent, "write_file", {"filename": "../outside.txt", "content": "bad"})
    assert "Absolute paths" in call(agent, "read_file", {"filename": str(tmp_path / "outside.txt")})
    outside = tmp_path / "outside"
    outside.mkdir()
    (agent.output_dir / "link").symlink_to(outside, target_is_directory=True)
    assert "Path escapes" in call(agent, "write_file", {"filename": "link/escape.txt", "content": "bad"})
    assert not (outside / "escape.txt").exists()


def test_no_fabricated_user_reply_or_screenshot(tmp_path):
    agent = CoderAgent(output_dir=tmp_path)
    assert "no user response" in call(agent, "ask_user", {"question": "May I proceed?"})
    assert "no image was captured" in call(agent, "take_screenshot", {"url": "https://example.com"})
    agent.on_user_question = lambda question: "No"
    assert call(agent, "ask_user", {"question": "May I proceed?"}) == "USER REPLIED: No"


def test_github_read_validates_arguments(tmp_path):
    agent = CoderAgent(output_dir=tmp_path)
    result = call(agent, "github_api", {"action": "read_issue", "repo": "owner/repo;echo", "issue_number": "1"})
    assert "valid repository" in result
