from runtime.task_orchestrator import build_task_graph


def test_financial_graph_has_evidence_gate_before_analysis():
    graph = build_task_graph("分析四环生物 000518 明日走势和新闻")

    assert graph.kind == "financial_research"
    stages = {stage.key: stage for stage in graph.stages}
    assert stages["analysis"].depends_on == ("evidence", "research")
    assert stages["prediction"].depends_on == ("evidence",)
    assert set(stage.key for stage in graph.ready(())) == {"evidence"}
    assert "覆盖率" in stages["prediction"].verification


def test_engineering_graph_requires_verification_before_review():
    graph = build_task_graph("写一个 Python 脚本分析 AAPL 并保存为 aapl.py")

    assert graph.kind == "engineering"
    stages = {stage.key: stage for stage in graph.stages}
    assert stages["implement"].mode == "workspace-write"
    assert stages["review"].depends_on == ("verify",)


def test_general_graph_starts_with_scope_clarification():
    graph = build_task_graph("帮我整理这个问题")

    assert graph.kind == "general"
    assert [stage.key for stage in graph.ready(())] == ["clarify"]
