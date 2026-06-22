"""Tests for runtime.subagent background task system."""

import pytest
from runtime.subagent import (
    _TASKS,
    SubagentTask,
    tool_spawn_task,
    tool_task_status,
    tool_task_result,
    tool_task_list,
    tool_task_cancel,
    register_runner,
)


@pytest.fixture(autouse=True)
def clear_tasks():
    """Clear the task registry and runner between tests."""
    import runtime.subagent as _sa
    _TASKS.clear()
    _orig_runner = _sa._RUNNER
    _sa._RUNNER = None  # ensure no runner is registered during tests
    yield
    _TASKS.clear()
    _sa._RUNNER = _orig_runner


class TestSpawnTask:
    def test_spawn_returns_task_id(self):
        result = tool_spawn_task({"prompt": "analyze AAPL"})
        assert result["success"] is True
        assert "task_id" in result
        assert len(result["task_id"]) == 8

    def test_missing_prompt_returns_error(self):
        result = tool_spawn_task({})
        assert result["success"] is False
        assert "prompt" in result["error"].lower()

    def test_task_stored_in_registry(self):
        result = tool_spawn_task({"prompt": "hello"})
        tid = result["task_id"]
        assert tid in _TASKS

    def test_status_is_pending_without_runner(self):
        result = tool_spawn_task({"prompt": "test"})
        assert result["status"] == "pending"


class TestTaskStatus:
    def test_status_of_existing_task(self):
        spawn = tool_spawn_task({"prompt": "test"})
        result = tool_task_status({"task_id": spawn["task_id"]})
        assert result["success"] is True
        assert result["status"] == "pending"

    def test_status_of_nonexistent_task(self):
        result = tool_task_status({"task_id": "deadbeef"})
        assert result["success"] is False

    def test_missing_task_id(self):
        result = tool_task_status({})
        assert result["success"] is False


class TestTaskResult:
    def test_result_of_pending_task_fails(self):
        spawn = tool_spawn_task({"prompt": "test"})
        result = tool_task_result({"task_id": spawn["task_id"]})
        assert result["success"] is False

    def test_result_of_completed_task(self):
        tid = "abc00001"
        task = SubagentTask(task_id=tid, prompt="test", status="done", result="done output")
        _TASKS[tid] = task
        result = tool_task_result({"task_id": tid})
        assert result["success"] is True
        assert result["result"] == "done output"

    def test_result_of_failed_task(self):
        tid = "abc00002"
        task = SubagentTask(task_id=tid, prompt="test", status="failed", error="boom")
        _TASKS[tid] = task
        result = tool_task_result({"task_id": tid})
        assert result["success"] is False
        assert "boom" in result["error"]


class TestTaskList:
    def test_empty_list(self):
        result = tool_task_list({})
        assert result["success"] is True
        assert result["tasks"] == []

    def test_list_with_tasks(self):
        tool_spawn_task({"prompt": "task1"})
        tool_spawn_task({"prompt": "task2"})
        result = tool_task_list({})
        assert result["total"] == 2
        assert len(result["tasks"]) == 2


class TestTaskCancel:
    def test_cancel_pending_task(self):
        spawn = tool_spawn_task({"prompt": "test"})
        result = tool_task_cancel({"task_id": spawn["task_id"]})
        assert result["success"] is True
        assert result["cancelled"] is True
        assert _TASKS[spawn["task_id"]].status == "cancelled"

    def test_cancel_already_done_task(self):
        tid = "done0001"
        task = SubagentTask(task_id=tid, prompt="test", status="done", result="ok")
        _TASKS[tid] = task
        result = tool_task_cancel({"task_id": tid})
        assert result["success"] is False

    def test_cancel_nonexistent(self):
        result = tool_task_cancel({"task_id": "deadbeef"})
        assert result["success"] is False
