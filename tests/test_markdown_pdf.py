"""markdown_pdf 回归测试：双语模板、徽章、元信息条、注册。"""

import pytest

pytest.importorskip("markdown")

from aria_code.markdown_pdf import (  # noqa: E402
    detect_lang,
    register_markdown_pdf_tools,
    render_markdown_html,
    tool_export_markdown_pdf,
)

_ZH = ("# 短线报告\n\n> **生成时间**: 2026-07-14 | 数据截止: 2026-07-13\n\n"
       "| 标的 | 信号 |\n|---|---|\n| 信科移动 | 🟢 **买入** |\n| 兆易创新 | 🔴 **卖出** |\n")
_EN = ("# Daily Brief\n\n> **Generated**: 2026-07-14\n\n"
       "| Ticker | Signal |\n|---|---|\n| AAPL | BUY |\n| TSLA | HOLD |\n")


def test_detect_lang():
    assert detect_lang(_ZH) == "zh"
    assert detect_lang(_EN) == "en"
    assert detect_lang("") == "en"


def test_render_bilingual_templates():
    h_zh = render_markdown_html(_ZH)          # auto → zh
    h_en = render_markdown_html(_EN)          # auto → en
    assert "仅供研究参考" in h_zh
    assert "not investment advice" in h_en
    # 首个引用块升级为元信息条；表格信号词包徽章
    assert 'class="meta"' in h_zh
    assert h_zh.count('class="badge"') >= 2
    assert h_en.count('class="badge"') >= 2
    # 强制语言覆盖自动检测
    assert "not investment advice" in render_markdown_html(_ZH, lang="en")


def test_tool_input_validation():
    assert tool_export_markdown_pdf({})["success"] is False


def test_register_never_overwrites():
    tools, schemas = {}, []
    assert register_markdown_pdf_tools(tools, schemas) == 1
    assert "export_markdown_pdf" in tools
    assert register_markdown_pdf_tools(tools, schemas) == 0
    assert len(schemas) == 1


def test_wide_table_autofit():
    """≥12 列的表格应打 t-xwide 类（按边界自适应缩字号）。"""
    headers = "|" + "|".join(f"c{i}" for i in range(13)) + "|"
    sep = "|" + "---|" * 13
    row = "|" + "|".join("x" for _ in range(13)) + "|"
    html = render_markdown_html(f"# t\n\n{headers}\n{sep}\n{row}\n")
    assert 'class="t-xwide"' in html
    # 少列表格不受影响（注意：CSS 文本里含 t-wide 字样，须查 class 属性）
    html2 = render_markdown_html("# t\n\n|a|b|\n|---|---|\n|1|2|\n")
    assert 'class="t-wide"' not in html2 and 'class="t-xwide"' not in html2
