from apps.cli.pdf_report import (
    Document,
    Section,
    THEMES,
    parse_shortterm_report,
    render_document,
    strip_emoji,
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
    # 2026-07-20 emoji 渲染 bug 修复后：alerts 里不该再带原始 🚀——weasyprint 没有
    # emoji 字形，直接渲染会飘在文字外面（见 test_strip_emoji* 和下面的回归测试）。
    assert card["lines"] == ["今日急涨 +7.9%，追高风险高"]


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


# ─────────────────────────────────────────────────────────────────────────────
# 2026-07-20 回归测试：weasyprint 没有彩色 emoji 字形，任何原始 emoji 字符漏进
# 纯文本渲染都会在 PDF 里变成跟文字基线脱节、飘在外面的图标碎片（不是布局 bug，
# 是渲染引擎读不懂这些字符）。修法是 strip_emoji()——按 Unicode 区段清，不是
# 枚举字符集，防止以后加新 emoji 又漏网；真正的信号色块（🟢/⬜/🔴等）该走
# _signal_tone()/_badge_html() 转成纯文字徽章，不是直接删空。
# ─────────────────────────────────────────────────────────────────────────────

def test_strip_emoji_removes_common_signal_emoji():
    assert strip_emoji("🟢🟢STRONG_BUY") == "STRONG_BUY"
    assert strip_emoji("⚠️ RSI接近超买") == "RSI接近超买"
    assert strip_emoji("🚀急涨") == "急涨"
    assert strip_emoji("📉 今日急跌 -10.0%") == "今日急跌 -10.0%"


def test_strip_emoji_covers_misc_symbols_and_arrows_block():
    # ⭐(U+2B50)/⬜(U+2B1C) 属于"Misc Symbols and Arrows"(U+2B00-2BFF)，
    # 这个区段曾经被漏掉——第一版 strip_emoji 只盖了 U+1F300-1FAFF 和
    # U+2600-27BF，⭐⬜ 完全没在里面，导致"名称"列的 ⭐ 后缀在实测 PDF 里
    # 还是飘着的，是重新生成、肉眼核对后才补上这个区段的。
    assert strip_emoji("建设银行⭐") == "建设银行"
    assert strip_emoji("77⬜") == "77"


def test_strip_emoji_does_not_touch_trend_arrows():
    # 均线列用的 ↑/↓/→(U+2190-21FF，"Arrows"区块)是有意义的数据，不是装饰性
    # emoji，跟 U+2B00-2BFF("Misc Symbols and Arrows")是完全不同的 Unicode
    # 区块——这条测试锁死两者不能混在一起清，否则会把趋势箭头也删掉。
    assert strip_emoji("↑↑") == "↑↑"
    assert strip_emoji("↓↓") == "↓↓"
    assert strip_emoji("→") == "→"


def test_strip_emoji_leaves_plain_cjk_and_numbers_untouched():
    assert strip_emoji("平安银行") == "平安银行"
    assert strip_emoji("止损 5% 无条件离场") == "止损 5% 无条件离场"
    assert strip_emoji("") == ""


def test_strip_emoji_covers_enclosed_alphanumeric_supplement():
    # 🆕(U+1F195) 属于"Enclosed Alphanumeric Supplement"(U+1F100-1F1FF)，跟
    # U+1F300 起的 pictographs 是不同区块——第一版只盖了这个区块的尾巴
    # (U+1F1E6-1F1FF，旗帜用的 regional indicators)，🆕 本身漏网，2026-07-21
    # 生成的 shortterm PDF 里"信号变化"这一节的"🆕 新增买入"标题实测仍然
    # 拖出一个 Apple-Color-Emoji-Bold 字体嵌入，才发现这个区块只盖了一半。
    assert strip_emoji("🆕 新增买入") == "新增买入"
    assert strip_emoji("🆓🆙🆒🆗") == ""


EMOJI_LEAK_MD = """# emoji 泄漏回归测试

## 今日概览

| 项目 | 数值 |
|------|------|
| 分析标的 | 2 只 |
| ⚠️ RSI接近超买(BUY) | 1 只: 工商银行 |
| ⭐ 全市场共振 | 1 只: 示例股份 |
| 🔥 次日涨停候选 | 1 只（见下方详情） |

## 今日优选短线标的

| 代码 | 名称 | 分 | 信号 | RSI |
|------|------|----|------|-----|
| 600000 | **示例股份**⭐ | 80 | 🟢🟢 | 77🔴 |
| 000001 | **另一股份** | 50 | ⬜ | 40 |

## 短线交易纪律

| 规则 | 要求 |
|------|------|
| 🚀急涨 | 次日等回踩 MA5 确认后再入 |
| ⚠️超买 | 建议分批建仓 |
"""


def test_parse_shortterm_report_strips_emoji_from_overview_meta_bullets():
    # ⚠️/⭐/🔥 曾经不在旧版剥离正则 [🚀📉🛡️] 覆盖的字符集里，原样漏进 meta 标签。
    doc = parse_shortterm_report(EMOJI_LEAK_MD)
    overview = next(s for s in doc.sections if s.kind == "stats")
    meta_labels = {m["label"] for m in overview.content["meta"]}
    assert "RSI接近超买(BUY)" in meta_labels
    assert "全市场共振" in meta_labels
    assert "次日涨停候选" in meta_labels
    assert not any(strip_emoji(lbl) != lbl for lbl in meta_labels)


def test_parse_shortterm_report_data_table_signal_column_is_badge_not_raw_emoji():
    doc = parse_shortterm_report(EMOJI_LEAK_MD)
    table = next(s for s in doc.sections if s.kind == "data_table" and "优选" in s.title)
    signal_col = next(c for c in table.content["columns"] if c["key"] == "信号")
    assert signal_col.get("badge") is True

    rows = table.content["rows"]
    # "信号" 保留原始 emoji 供 _r_data_table 走 _signal_tone()/_badge_html()；
    # 其它列（名称/RSI）在解析阶段就该清掉装饰性后缀 emoji。
    assert rows[0]["名称"] == "示例股份"
    assert rows[0]["RSI"] == "77"

    html_doc = render_document(doc, THEMES["institutional"])
    assert '<span class="badge buy2">' in html_doc  # 🟢🟢 -> 强力买入徽章
    assert '<span class="badge hold">' in html_doc   # ⬜ -> 持有观望徽章
    assert "🟢" not in html_doc
    assert "⬜" not in html_doc
    assert "⭐" not in html_doc


def test_parse_shortterm_report_trading_rules_strips_emoji_prefixes():
    doc = parse_shortterm_report(EMOJI_LEAK_MD)
    rules = next(s for s in doc.sections if "交易纪律" in s.title)
    rule_labels = [r["规则"] for r in rules.content["rows"]]
    assert "急涨" in rule_labels
    assert "超买" in rule_labels


def test_render_document_institutional_theme_has_no_raw_emoji_leakage():
    # 端到端守卫：整份报告过一遍 institutional 主题渲染，输出里不该出现任何
    # 已知的原始信号/装饰 emoji——新加 section 时如果又直接把 emoji 塞进纯文本，
    # 这条测试会先炸，不用等人肉眼看 PDF 才发现飘字。
    doc = parse_shortterm_report(EMOJI_LEAK_MD)
    html_doc = render_document(doc, THEMES["institutional"])
    leaked = [ch for ch in "⚠️⭐🔥🚀📉🟢🟡🔴⬜⬛" if ch in html_doc]
    assert leaked == [], f"raw emoji leaked into rendered HTML: {leaked!r}"
