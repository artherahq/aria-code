import pytest

from apps.cli.runtime_consumer import TerminalApprovalEventConsumer, TerminalRuntimeEventConsumer
from runtime import AgentEventStatus, AgentEventToken, AgentEventToolCall, AgentEventToolResult, ApprovalDecision


class _Console:
    is_terminal = False
    is_dumb_terminal = True

    def __init__(self):
        self.lines = []

    def print(self, *args, **kwargs):
        self.lines.append(" ".join(str(a) for a in args))


class _Terminal:
    def __init__(self):
        self.config = {"command_policy": "safe"}
        self._last_thinking = ""
        self._transcript_log = []
        self._task_list = []
        self.feedback = []

    def _record_feedback(self, kind, tool):
        self.feedback.append((kind, tool))


def test_terminal_runtime_consumer_handles_runtime_events(capsys):
    terminal = _Terminal()
    tool_calls = []
    tool_done = []
    states = []
    consumer = TerminalRuntimeEventConsumer(
        terminal=terminal,
        console=_Console(),
        has_rich=False,
        markdown_cls=None,
        live_cls=None,
        strip_latex=lambda text: text,
        set_robot_state=lambda state: states.append(state),
        streaming_state="streaming",
        print_tool_call=lambda tool, params: tool_calls.append((tool, params)),
        print_tool_done=lambda tool, elapsed, success: tool_done.append((tool, success)),
    )

    consumer.handle_runtime_event(AgentEventToken("hi"))
    consumer.handle_runtime_event(AgentEventToolCall("TaskCreate", {"title": "ship"}))
    consumer.handle_runtime_event(
        AgentEventToolResult("TaskCreate", {"success": True, "id": "t1", "title": "ship", "status": "done"}, 0.1)
    )
    consumer.handle_runtime_event(AgentEventStatus("noop", "ignored"))

    assert capsys.readouterr().out == "hi"
    assert consumer.response_text == "hi"
    assert consumer.token_count == 1
    assert states == ["streaming"]
    assert tool_calls == [("TaskCreate", {"title": "ship"})]
    assert tool_done == [("TaskCreate", True)]
    assert terminal._task_list == [{"id": "t1", "title": "ship", "status": "done"}]
    assert terminal._transcript_log


def test_terminal_runtime_consumer_hides_repetition_marker(capsys):
    consumer = TerminalRuntimeEventConsumer(
        terminal=_Terminal(),
        console=_Console(),
        has_rich=False,
        markdown_cls=None,
        live_cls=None,
        strip_latex=lambda text: text,
    )

    consumer.on_token("已经生成文件。")
    consumer.on_token("\n\n*[model stopped — repetition detected]*")

    out = capsys.readouterr().out
    assert "*[model stopped" not in out
    assert "已检测到模型开始重复输出" in out
    assert consumer.repetition_stopped is True
    assert "*[model stopped" in consumer.response_text


@pytest.mark.asyncio
async def test_terminal_approval_consumer_records_feedback_and_upgrade():
    terminal = _Terminal()
    saved = []

    def confirm(tool, params, *, config_policy):
        assert config_policy == "safe"
        return ApprovalDecision.allow(policy="balanced", user_approved=True, upgrade_policy=True)

    def apply(params, decision):
        params["policy"] = decision.policy
        params["_upgrade_policy"] = decision.upgrade_policy
        return params

    consumer = TerminalApprovalEventConsumer(
        terminal=terminal,
        console=_Console(),
        has_rich=False,
        confirm_decision=confirm,
        apply_decision=apply,
        save_config=lambda config: saved.append(dict(config)),
    )

    stopped = []
    decision = await consumer.approve("run_command", {"command": "pytest"}, stop_before_prompt=lambda: stopped.append(True))
    params = consumer.apply({"command": "pytest"}, decision)

    assert stopped == [True]
    assert terminal.feedback == [("tool_accept", "run_command")]
    assert params == {"command": "pytest", "policy": "balanced"}
    assert terminal.config["command_policy"] == "balanced"
    assert saved and saved[-1]["command_policy"] == "balanced"
