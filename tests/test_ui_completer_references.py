from pathlib import Path

from prompt_toolkit.document import Document

from ui.completer import AriaPTCompleter


def _values(completer: AriaPTCompleter, text: str) -> list[str]:
    return [item.text for item in completer.get_completions(Document(text), None)]


def _completer(tmp_path: Path, **kwargs) -> AriaPTCompleter:
    return AriaPTCompleter(
        {
            "/risk": (lambda _args: None, "Risk metrics"),
            "/review": (lambda _args: None, "Review code"),
        },
        [],
        ["BABA"],
        workspace=tmp_path,
        lang="en",
        **kwargs,
    )


def test_at_opens_typed_context_namespace_palette(tmp_path):
    (tmp_path / "README.md").write_text("not listed at first level", encoding="utf-8")
    values = _values(_completer(tmp_path), "@")

    assert "file:" in values
    assert "folder:" in values
    assert "asset:" in values
    assert "portfolio:" in values
    assert "strategy:" in values
    assert "README.md" not in values


def test_reference_completion_works_inside_natural_language(tmp_path):
    values = _values(_completer(tmp_path), "analyze @asset:AA")

    assert "asset:AAPL" in values


def test_reference_completion_works_in_slash_command_arguments(tmp_path):
    values = _values(_completer(tmp_path), "/risk @asset:BA")

    assert "asset:BABA" in values


def test_file_namespace_completes_workspace_relative_paths(tmp_path):
    (tmp_path / "README.md").write_text("read me", encoding="utf-8")

    values = _values(_completer(tmp_path), "review @file:REA")

    assert "file:README.md" in values


def test_named_report_completion_uses_output_root(tmp_path):
    output_root = tmp_path / "output"
    report = output_root / "reports" / "daily.md"
    report.parent.mkdir(parents=True)
    report.write_text("report", encoding="utf-8")

    values = _values(_completer(tmp_path, output_root=output_root), "@report:da")

    assert "report:daily" in values


def test_email_address_does_not_open_reference_completion(tmp_path):
    values = _values(_completer(tmp_path), "email dev@example.com")

    assert values == []


def test_slash_command_completion_stays_separate_from_context(tmp_path):
    completions = list(_completer(tmp_path).get_completions(Document("/ri"), None))

    assert completions[0].text == "/risk"
    assert "Research" in str(completions[0].display_meta)
