from apps.cli.project_aria import build_project_aria_md


def test_build_project_aria_md_contains_required_sections():
    text = build_project_aria_md(
        project_name="Demo",
        stack="Python",
        entry="app.py",
        purpose="demo",
        notes=["note"],
    )

    assert text.startswith("# Memory")
    assert "- **Project**: Demo" in text
    assert "- **Stack**: Python" in text
    assert "- **Entry**: app.py" in text
    assert "## Memory Layers" in text
    assert "## Operational Rules" in text
    assert "## Workflow Notes" in text
