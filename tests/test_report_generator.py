"""2026-07-20 回归测试：report_generator.py（单股票分析报告，Bloomberg 风格）
跟 apps/cli/pdf_report.py 是完全独立的第二套渲染系统（同一个代码库里三套
互不共享的 HTML/CSS 栈之一），但都走 weasyprint 转 PDF，所以有同一类 bug：
weasyprint 没有彩色 emoji 字形，raw emoji 漏进纯文本渲染会飘在文字外面。

pdf_report.py 那边的风险点是结构化的信号列（🟢/🔴等，有清晰的徽章替代），
这边的风险点不一样：_md_to_html() 转换的是 LLM 现写的分析原文
（_agent_card 的 analysis/key_points、synthesis），模型写报告时经常会
自己夹带 emoji，没有任何"结构化替代"可言，直接在这唯一的出口处剥掉即可。
"""
from aria_code.report_generator import _md_to_html, _strip_emoji


def test_strip_emoji_removes_common_llm_decoration():
    assert _strip_emoji("📈 该股票近期表现强劲") == "该股票近期表现强劲"
    assert _strip_emoji("⚠️ 注意回调风险") == "注意回调风险"
    assert _strip_emoji("✅ 已确认") == "已确认"


def test_strip_emoji_covers_misc_symbols_and_arrows_block():
    # ⭐(U+2B50) 属于跟 U+1F300-1FAFF / U+2600-27BF 都不重叠的另一个区块
    # (U+2B00-2BFF)——这是从 pdf_report.py 那次修复里学到的教训，第一版
    # 实现漏了这个区段，这里直接把回归测试也搬过来，防止两边分叉。
    assert _strip_emoji("重点关注⭐") == "重点关注"


def test_strip_emoji_does_not_touch_plain_text_or_punctuation():
    assert _strip_emoji("止损 5%，严格执行") == "止损 5%，严格执行"
    assert _strip_emoji("") == ""
    assert _strip_emoji(None) == ""


def test_strip_emoji_covers_enclosed_alphanumeric_supplement():
    # 🆕(U+1F195) 属于"Enclosed Alphanumeric Supplement"(U+1F100-1F1FF)，跟
    # U+1F300 起的 pictographs 不是同一区块——同样是从 pdf_report.py 那次
    # 修复里搬过来的教训（第一版只盖了这个区块尾巴的旗帜部分），两边独立
    # 维护同一份正则，得两边都补。
    assert _strip_emoji("🆕 新增关注") == "新增关注"


def test_md_to_html_strips_emoji_from_llm_authored_analysis():
    html_out = _md_to_html("📈 近期强势上涨，⚠️ 但估值已偏高，🎯 目标价 120 元")
    assert "📈" not in html_out
    assert "⚠️" not in html_out
    assert "🎯" not in html_out
    assert "近期强势上涨" in html_out
    assert "目标价 120 元" in html_out


def test_md_to_html_still_escapes_html_after_emoji_strip():
    # emoji 剥离不能绕过原有的 XSS 转义——顺序上先剥 emoji 再转义，
    # 两步互不干扰。
    html_out = _md_to_html("📈 <script>alert(1)</script>")
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_md_to_html_preserves_markdown_table_and_bold_with_emoji_stripped():
    md = "**重点** ✅\n\n| 指标 | 值 |\n|---|---|\n| PE | 15📊 |"
    html_out = _md_to_html(md)
    assert "<strong>重点</strong>" in html_out
    assert "✅" not in html_out
    assert "📊" not in html_out
    assert "<table" in html_out
    assert "<td>PE</td>" in html_out
