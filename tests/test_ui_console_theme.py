from rich.console import Console

from ui.console import _build_rich_theme, make_markdown


def test_dark_markdown_theme_avoids_black_inline_code_and_blue_purple_accents():
    themed_console = Console(theme=_build_rich_theme("dark"))
    code_style = themed_console.get_style("markdown.code")
    h2_style = themed_console.get_style("markdown.h2")
    link_style = themed_console.get_style("markdown.link")

    assert code_style.bgcolor is None
    assert code_style.color and code_style.color.name == "#c08050"
    assert h2_style.color and h2_style.color.name == "#e8e0d4"
    assert link_style.color and link_style.color.name == "#c08050"


def test_light_markdown_theme_uses_high_contrast_text():
    themed_console = Console(theme=_build_rich_theme("light"))
    code_style = themed_console.get_style("markdown.code")
    h2_style = themed_console.get_style("markdown.h2")
    quote_style = themed_console.get_style("markdown.block_quote")

    assert code_style.bgcolor is None
    assert code_style.color and code_style.color.name == "#8a5a00"
    assert h2_style.color and h2_style.color.name == "#24292f"
    assert quote_style.color and quote_style.color.name == "#6e7781"


def test_make_markdown_uses_neutral_code_theme():
    md = make_markdown("`AAPL` and ```python\nprint('ok')\n```")

    assert getattr(md, "code_theme", "") == "bw"
    assert getattr(md, "inline_code_theme", "") == "bw"
