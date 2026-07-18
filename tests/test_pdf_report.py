from apps.cli.pdf_report import (
    Document,
    Section,
    THEMES,
    parse_shortterm_report,
    render_document,
    strip_md_bold,
)

SAMPLE_MD = """# 测试报告

> **生成时间**: 2026-07-17 16:08  |  数据截止: **2026-07-17**  |  **策略**: 短线持仓 3–15 交易日  |  数据源: Tushare

> ⚠️ 本报告仅供参考，不构成投资建议。

---

## 📊 今日概览

| 项目 | 数值 |
|------|------|
| 分析标的 | 2 只 |
| 🟢 买入信号 | 1 只 |
| ⬜ 持有观望 | 0 只 |
| 🔴 减仓/离场 | 1 只 |

---

## 🧭 综合研判 · 一眼看清买卖

> 说明文字

| 标的 | 🚦 综合研判 | 技术信号 | 新闻面 | 印证 | 一句话操作 |
|------|:----------:|:--------:|:------:|:----:|-----------|
| **示例股份** (600000) | 🟢🟢 **强力买入** | 🟢🟢 STRONG_BUY | 📈 利好 (+0.69) | ✅ 共振 | 技术 **买入** + 新闻面利好 → **买入** |
| **另一股份** (000001) | 🔴 **卖出** | 🔴 SELL | — 无 | — 无 | 技术信号 **卖出** |

---

## 💡 今日特殊行情 & 风险提示

- **示例股份** (600000) 🟢🟢 评分80
  - 🚀 今日急涨 +7.9%，追高风险高

## 📌 短线交易纪律

| 规则 | 要求 |
|------|------|
| 止损 | 亏损 **5%** 无条件离场 |

---

*页脚文字*
"""


def test_strip_md_bold():
    assert strip_md_bold("**买入信号**") == "买入信号"
    assert strip_md_bold("no bold here") == "no bold here"
    assert strip_md_bold("**a** and **b**") == "a and b"


def test_parse_shortterm_report_extracts_expected_sections():
    doc = parse_shortterm_report(SAMPLE_MD)

    assert doc.title == "测试报告"
    assert "2026-07-17" in doc.meta
    assert "仅供参考" in doc.warning

    kinds = [(s.kind, s.title) for s in doc.sections]
    assert ("stats", "今日概览") in kinds
    assert ("card_grid", "今日特殊行情 & 风险提示") in kinds
    titles = [s.title for s in doc.sections]
    assert any("综合研判" in t for t in titles)
    assert any("交易纪律" in t for t in titles)


def test_parse_shortterm_report_strips_bold_markdown_from_tables():
    # 回归测试：优选标的表/交易纪律表里的 **加粗** markdown 曾经原样打印星号
    doc = parse_shortterm_report(SAMPLE_MD)
    rules = next(s for s in doc.sections if "交易纪律" in s.title)
    assert rules.content["rows"][0]["要求"] == "亏损 5% 无条件离场"
    assert "**" not in rules.content["rows"][0]["要求"]


def test_parse_shortterm_report_badge_table_signal_and_news():
    doc = parse_shortterm_report(SAMPLE_MD)
    verdict = next(s for s in doc.sections if s.kind == "badge_table")
    rows = verdict.content["rows"]

    assert rows[0]["name"] == "示例股份"
    assert rows[0]["code"] == "600000"
    assert rows[0]["signal"]["tone"] == "buy2"
    assert rows[0]["signal"]["label"] == "强力买入"
    # 回归测试：新闻面情绪分曾经因为正则里多打了一个反斜杠而丢失数字
    assert rows[0]["news"]["label"] == "利好 +0.69"

    assert rows[1]["signal"]["tone"] == "sell"
    assert rows[1]["news"]["label"] == "无"


def test_parse_shortterm_report_card_grid_alerts():
    # 回归测试：今日特殊行情卡片的详情行(alerts)曾经被一个失效的守卫条件吞掉
    doc = parse_shortterm_report(SAMPLE_MD)
    special = next(s for s in doc.sections if s.kind == "card_grid")
    card = special.content["cards"][0]
    assert card["title"] == "示例股份"
    assert card["lines"] == ["🚀 今日急涨 +7.9%，追高风险高"]


def test_document_select_include_exclude():
    doc = parse_shortterm_report(SAMPLE_MD)

    only_verdict = doc.select(include=["综合研判"])
    assert len(only_verdict.sections) == 1
    assert "综合研判" in only_verdict.sections[0].title

    without_rules = doc.select(exclude=["交易纪律"])
    assert not any("交易纪律" in s.title for s in without_rules.sections)
    assert len(without_rules.sections) == len(doc.sections) - 1


def test_render_document_produces_html_for_every_theme():
    doc = parse_shortterm_report(SAMPLE_MD)
    for theme in THEMES.values():
        html_doc = render_document(doc, theme)
        assert html_doc.startswith("<!doctype html>")
        assert "示例股份" in html_doc
        assert "**" not in html_doc  # no leaked markdown anywhere in final output
        assert theme.bg in html_doc  # theme actually applied


def test_render_document_handles_empty_sections_gracefully():
    doc = Document(title="空报告", sections=[])
    html_doc = render_document(doc, THEMES["institutional"])
    assert "空报告" in html_doc


def test_unknown_section_kind_falls_back_to_text_renderer():
    doc = Document(title="t", sections=[Section(kind="nonexistent", title="x", content={"text": "hello"})])
    html_doc = render_document(doc, THEMES["institutional"])
    assert "hello" in html_doc
