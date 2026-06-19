import pytest

from apps.cli.commands.business_workflow_cmds import BusinessWorkflowCommandsMixin


class _Terminal:
    def __init__(self):
        self.prompts = []

    async def handle_user_input(self, prompt):
        self.prompts.append(prompt)


class _WorkflowHarness(BusinessWorkflowCommandsMixin):
    def __init__(self):
        self.terminal = _Terminal()
        self.team_calls = []
        self.report_calls = []

    async def cmd_team(self, args):
        self.team_calls.append(args)

    async def cmd_report(self, args):
        self.report_calls.append(args)


@pytest.mark.asyncio
async def test_research_routes_to_full_team_workflow_without_prompt_recursion():
    harness = _WorkflowHarness()

    await harness.cmd_research("aapl")

    assert harness.team_calls == ["AAPL --full"]
    assert harness.terminal.prompts == []


@pytest.mark.asyncio
async def test_earnings_routes_to_markdown_report_workflow_without_prompt_recursion():
    harness = _WorkflowHarness()

    await harness.cmd_earnings_workflow("msft 最近一个季度")

    assert harness.report_calls == ["MSFT --format md --type standard"]
    assert harness.terminal.prompts == []


@pytest.mark.asyncio
async def test_earnings_deep_period_selects_deep_report_type():
    harness = _WorkflowHarness()

    await harness.cmd_earnings_workflow("nvda 年报")

    assert harness.report_calls == ["NVDA --format md --type deep"]
