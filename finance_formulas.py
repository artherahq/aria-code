"""
finance_formulas.py — Canonical financial formula reference for Aria.

Two representations per formula:
  latex   : Standard LaTeX (rendered by KaTeX in web terminal)
  plain   : Clean Unicode plain-text (used in CLI terminal)
  note    : One-line explanation injected into system prompts

Usage in system prompts:
    from finance_formulas import FORMULA_PROMPT_BLOCK
    system_prompt += FORMULA_PROMPT_BLOCK

Usage for CLI rendering:
    from finance_formulas import formula_to_plaintext
    clean = formula_to_plaintext(raw_latex_string)
"""

from __future__ import annotations
from typing import NamedTuple


class Formula(NamedTuple):
    name: str
    latex: str        # $...$ or $$...$$
    plain: str        # clean Unicode, no backslashes
    note: str         # one-line description


# ── Canonical formula registry ────────────────────────────────────────────────

FORMULAS: dict[str, Formula] = {

    # ── Valuation ─────────────────────────────────────────────────────────────
    "pe": Formula(
        name="P/E Ratio (市盈率)",
        latex=r"$$P/E = \frac{\text{Stock Price}}{\text{EPS}}$$",
        plain="P/E = 股价 ÷ 每股收益(EPS)",
        note="市场愿意为每1元盈利支付的价格；越高代表市场期望越高",
    ),
    "pb": Formula(
        name="P/B Ratio (市净率)",
        latex=r"$$P/B = \frac{\text{Stock Price}}{\text{Book Value per Share}}$$",
        plain="P/B = 股价 ÷ 每股净资产",
        note="股价相对账面价值的溢价；<1 可能被低估",
    ),
    "ps": Formula(
        name="P/S Ratio (市销率)",
        latex=r"$$P/S = \frac{\text{Market Cap}}{\text{Annual Revenue}}$$",
        plain="P/S = 市值 ÷ 年营收",
        note="适合暂无盈利的成长型公司估值",
    ),
    "ev_ebitda": Formula(
        name="EV/EBITDA",
        latex=r"$$EV/EBITDA = \frac{\text{Enterprise Value}}{\text{EBITDA}}$$",
        plain="EV/EBITDA = 企业价值 ÷ 息税折旧摊销前利润",
        note="排除资本结构差异的估值指标；<10 通常被视为合理",
    ),
    "peg": Formula(
        name="PEG Ratio",
        latex=r"$$PEG = \frac{P/E}{\text{EPS Growth Rate (\%)}}$$",
        plain="PEG = 市盈率 ÷ 盈利增长率(%)",
        note="<1 通常被视为被低估；综合估值与成长性",
    ),
    "dcf": Formula(
        name="DCF 内在价值",
        latex=r"$$V = \sum_{t=1}^{n} \frac{FCF_t}{(1+r)^t} + \frac{TV}{(1+r)^n}$$",
        plain="DCF = Σ [自由现金流t ÷ (1+折现率)^t] + 终值 ÷ (1+折现率)^n",
        note="自由现金流折现；r 为 WACC，TV 为终值",
    ),
    "wacc": Formula(
        name="WACC (加权平均资本成本)",
        latex=r"$$WACC = \frac{E}{V} \cdot R_e + \frac{D}{V} \cdot R_d \cdot (1-T)$$",
        plain="WACC = 权益比 × 权益成本 + 债务比 × 债务成本 × (1-税率)",
        note="折现率的基础；E=权益，D=债务，V=E+D，T=税率",
    ),

    # ── Profitability ─────────────────────────────────────────────────────────
    "roe": Formula(
        name="ROE (净资产收益率)",
        latex=r"$$ROE = \frac{\text{Net Income}}{\text{Shareholders' Equity}} \times 100\%$$",
        plain="ROE = 净利润 ÷ 股东权益 × 100%",
        note="巴菲特首选指标；>15% 为优秀",
    ),
    "roa": Formula(
        name="ROA (总资产收益率)",
        latex=r"$$ROA = \frac{\text{Net Income}}{\text{Total Assets}} \times 100\%$$",
        plain="ROA = 净利润 ÷ 总资产 × 100%",
        note="衡量资产使用效率；>5% 较好",
    ),
    "gross_margin": Formula(
        name="毛利率",
        latex=r"$$\text{Gross Margin} = \frac{\text{Revenue} - \text{COGS}}{\text{Revenue}} \times 100\%$$",
        plain="毛利率 = (营收 - 营业成本) ÷ 营收 × 100%",
        note="产品定价能力的直接体现",
    ),
    "net_margin": Formula(
        name="净利率",
        latex=r"$$\text{Net Margin} = \frac{\text{Net Income}}{\text{Revenue}} \times 100\%$$",
        plain="净利率 = 净利润 ÷ 营收 × 100%",
        note="最终盈利能力；科技公司优秀水平 >20%",
    ),

    # ── Risk / Return ─────────────────────────────────────────────────────────
    "sharpe": Formula(
        name="Sharpe Ratio (夏普比率)",
        latex=r"$$Sharpe = \frac{R_p - R_f}{\sigma_p}$$",
        plain="Sharpe = (组合年化收益 - 无风险利率) ÷ 年化波动率",
        note="单位风险的超额收益；>1 良好，>2 优秀",
    ),
    "sortino": Formula(
        name="Sortino Ratio",
        latex=r"$$Sortino = \frac{R_p - R_f}{\sigma_d}$$",
        plain="Sortino = (组合年化收益 - 无风险利率) ÷ 下行波动率",
        note="只惩罚下行风险；比 Sharpe 更适合非对称收益",
    ),
    "max_drawdown": Formula(
        name="最大回撤",
        latex=r"$$MDD = \frac{\text{Trough} - \text{Peak}}{\text{Peak}} \times 100\%$$",
        plain="最大回撤 = (波谷 - 波峰) ÷ 波峰 × 100%",
        note="负值；衡量最坏情景下的损失幅度",
    ),
    "var": Formula(
        name="VaR (Value at Risk)",
        latex=r"$$VaR_{95\%} = \mu - 1.645\sigma$$",
        plain="VaR(95%) = 均值收益 - 1.645 × 波动率",
        note="95%置信度下单日最大可能亏损",
    ),
    "beta": Formula(
        name="Beta (贝塔系数)",
        latex=r"$$\beta = \frac{Cov(R_i, R_m)}{Var(R_m)}$$",
        plain="β = 个股与市场收益的协方差 ÷ 市场收益方差",
        note=">1 比市场波动大；<1 防御性；=1 跟随市场",
    ),
    "capm": Formula(
        name="CAPM (资本资产定价模型)",
        latex=r"$$E(R_i) = R_f + \beta_i \cdot (E(R_m) - R_f)$$",
        plain="期望收益 = 无风险利率 + β × 市场风险溢价",
        note="理论上的公允收益率；高于此值则α为正",
    ),

    # ── Technical indicators ──────────────────────────────────────────────────
    "rsi": Formula(
        name="RSI (相对强弱指数)",
        latex=r"$$RSI = 100 - \frac{100}{1 + \frac{\bar{U}}{\bar{D}}}$$",
        plain="RSI = 100 - 100 ÷ (1 + 平均涨幅 ÷ 平均跌幅)",
        note=">70 超买，<30 超卖；14日为标准周期",
    ),
    "macd_line": Formula(
        name="MACD 线",
        latex=r"$$MACD = EMA_{12} - EMA_{26}$$",
        plain="MACD = 12日EMA - 26日EMA",
        note="快慢线差值；正值为多头区间",
    ),
    "bollinger_upper": Formula(
        name="布林带上轨",
        latex=r"$$Upper = MA_{20} + 2\sigma_{20}$$",
        plain="上轨 = 20日均线 + 2 × 20日标准差",
        note="价格突破上轨为超买信号",
    ),
    "eps": Formula(
        name="EPS (每股收益)",
        latex=r"$$EPS = \frac{\text{Net Income} - \text{Preferred Dividends}}{\text{Weighted Average Shares}}$$",
        plain="EPS = (净利润 - 优先股股息) ÷ 加权平均流通股数",
        note="P/E 的分母；反映每股创造的盈利",
    ),
    "cagr": Formula(
        name="CAGR (复合年均增长率)",
        latex=r"$$CAGR = \left(\frac{V_f}{V_i}\right)^{\frac{1}{n}} - 1$$",
        plain="CAGR = (终值 ÷ 初值)^(1/年数) - 1",
        note="衡量长期增长的标准方式",
    ),
}


# ── Prompt injection block ────────────────────────────────────────────────────

def build_formula_prompt_block(keys: list[str] | None = None) -> str:
    """
    Build a compact formula reference block for injection into system prompts.

    keys: optional list of formula keys to include (default: all).
    Returns a string ready to append to a system prompt.
    """
    items = [FORMULAS[k] for k in (keys or FORMULAS.keys()) if k in FORMULAS]
    if not items:
        return ""

    lines = [
        "\n## 标准金融公式（必须使用以下格式，禁止自创公式或缩写）\n",
        "在 LaTeX 公式中使用 $$ ... $$ 语法（双美元符），终端自动转换为可读格式。\n",
    ]
    for f in items:
        lines.append(f"- **{f.name}**: `{f.plain}`")
        lines.append(f"  → LaTeX: `{f.latex}`")
        lines.append(f"  → {f.note}")
    lines.append("\n⚠️ 禁止使用不存在的术语（如 DSRR、DRRR）。如不确定，用中文描述代替。\n")
    return "\n".join(lines)


# Compact block for finance chat (most common formulas only)
FORMULA_PROMPT_BLOCK_CORE = build_formula_prompt_block([
    "pe", "pb", "roe", "sharpe", "max_drawdown", "eps", "cagr",
])

# Full block for analysis / research prompts
FORMULA_PROMPT_BLOCK_FULL = build_formula_prompt_block()


# ── CLI plain-text renderer ───────────────────────────────────────────────────

import re as _re

_PLAIN_LOOKUP: dict[str, str] = {f.latex: f.plain for f in FORMULAS.values()}

# Symbol map: LaTeX → Unicode
_LATEX_SYMBOLS = {
    r'\times': '×', r'\div': '÷', r'\pm': '±', r'\approx': '≈',
    r'\leq': '≤', r'\geq': '≥', r'\neq': '≠', r'\to': '→',
    r'\alpha': 'α', r'\beta': 'β', r'\gamma': 'γ', r'\sigma': 'σ',
    r'\mu': 'μ', r'\pi': 'π', r'\lambda': 'λ', r'\theta': 'θ',
    r'\Sigma': 'Σ', r'\sum': 'Σ', r'\infty': '∞', r'\cdot': '·',
    r'\sqrt': '√', r'\partial': '∂',
    # LaTeX spacing commands (these are NOT caught by \\[A-Za-z]+ since ; is not a letter)
    r'\;': ' ', r'\,': '', r'\:': ' ', r'\!': '',
    r'\quad': '  ', r'\qquad': '   ',
    r'\medspace': ' ', r'\thickspace': ' ', r'\thinspace': '',
    r'\text{Net Income}': '净利润',
    r'\text{Net Profit}': '净利润',
    r"\text{Shareholders' Equity}": '股东权益',
    r'\text{Shareholders Equity}': '股东权益',
    r'\text{Stock Price}': '股价',
    r'\text{EPS}': 'EPS',
    r'\text{Revenue}': '营收',
    r'\text{COGS}': '营业成本',
    r'\text{Peak}': '波峰',
    r'\text{Trough}': '波谷',
    r'\text{Book Value per Share}': '每股净资产',
    r'\text{Market Cap}': '市值',
    r'\text{Annual Revenue}': '年营收',
    r'\text{Total Assets}': '总资产',
    r'\text{Enterprise Value}': '企业价值',
    r'\text{Preferred Dividends}': '优先股股息',
    r'\text{Weighted Average Shares}': '加权平均流通股数',
    r'\bar': '',
}


def _replace_frac_once(text: str) -> str:
    """Replace the first simple or nested LaTeX \frac block with readable text."""
    start = text.find(r'\frac')
    if start < 0:
        return text

    def _read_group(pos: int) -> tuple[str, int] | None:
        if pos >= len(text) or text[pos] != "{":
            return None
        depth = 0
        chars: list[str] = []
        for idx in range(pos, len(text)):
            ch = text[idx]
            if ch == "{":
                if depth > 0:
                    chars.append(ch)
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return "".join(chars), idx + 1
                chars.append(ch)
            else:
                chars.append(ch)
        return None

    num = _read_group(start + len(r'\frac'))
    if not num:
        return text
    den = _read_group(num[1])
    if not den:
        return text

    numerator = formula_to_plaintext(num[0])
    denominator = formula_to_plaintext(den[0])
    replacement = f"({numerator}) ÷ ({denominator})"
    return text[:start] + replacement + text[den[1]:]


def formula_to_plaintext(latex: str) -> str:
    """
    Convert a LaTeX formula string to clean Unicode plain-text for CLI display.

    Handles $...$ inline and $$...$$ display math.
    Produces readable output like "P/E = 股价 ÷ EPS" instead of garbled backslashes.
    """
    # Check canonical lookup first
    stripped = latex.strip().replace("\\\\", "\\")
    if stripped in _PLAIN_LOOKUP:
        return _PLAIN_LOOKUP[stripped]

    # Remove $$ and $ delimiters
    text = _re.sub(r'\$\$|\$', '', stripped)
    text = _re.sub(r'\\\[|\\\]', '', text)
    text = _re.sub(r'\\\(|\\\)', '', text)

    # Apply symbol substitutions (longest first to avoid partial matches)
    for cmd, sym in sorted(_LATEX_SYMBOLS.items(), key=lambda x: -len(x[0])):
        text = text.replace(cmd, sym)

    # \frac{a}{b} → (a) ÷ (b), including common nested fractions.
    for _ in range(12):
        updated = _replace_frac_once(text)
        if updated == text:
            break
        text = updated

    # \text{...} and common styling commands → content
    text = _re.sub(
        r'\\(?:text|mathrm|mathbf|mathit|mathcal|operatorname|overline|underline|bar|hat|tilde|vec)\{([^{}]*)\}',
        r'\1', text
    )

    # \left, \right → nothing
    text = _re.sub(r'\\(?:left|right)[(\[|)\].]?', '', text)

    # \sqrt{x} → √(x)
    text = _re.sub(r'\\sqrt\{([^{}]*)\}', r'√(\1)', text)

    # ^{n} → ^n,  _{n} → _n
    text = _re.sub(r'\^\{([^{}]{1,12})\}', r'^\1', text)
    text = _re.sub(r'_\{([^{}]{1,12})\}', r'_\1', text)
    text = _re.sub(r'\^([A-Za-z0-9])', r'^\1', text)
    text = _re.sub(r'_([A-Za-z0-9])', r'_\1', text)

    # Non-alpha LaTeX spacing commands (\; \, \: \! are NOT caught by \\[A-Za-z]+)
    text = _re.sub(r'\\[;,!:]', ' ', text)
    # Clean remaining backslash commands
    text = _re.sub(r'\\([A-Za-z]+)', r'\1', text)
    text = text.replace(r'\%', '%')
    text = _re.sub(r'\{([^{}]{1,24})\}', r'\1', text)

    # Clean up whitespace
    text = _re.sub(r'\s{2,}', ' ', text)
    text = _re.sub(r'\s*([=+\-×÷·])\s*', r' \1 ', text)
    text = _re.sub(r'\s{2,}', ' ', text).strip()
    return text


def strip_latex_for_cli(text: str) -> str:
    """
    Full-text LaTeX stripping for CLI output.
    Converts $$...$$ and $...$ blocks to clean plaintext.
    Replaces display math with a prefixed formula line.
    """
    if '\\' not in text and '$' not in text:
        return text

    # Display math $$...$$ → "  ▶ <plain formula>"
    def _render_display(m: re.Match) -> str:
        inner = m.group(1).strip()
        plain = formula_to_plaintext(f"$${inner}$$")
        return f"\n  ▶ {plain}\n"

    text = _re.sub(r'\$\$(.+?)\$\$', _render_display, text, flags=_re.DOTALL)

    # Inline math $...$ → plain
    def _render_inline(m: re.Match) -> str:
        inner = m.group(1).strip()
        return formula_to_plaintext(f"${inner}$")

    text = _re.sub(r'\$([^$\n]{1,120})\$', _render_inline, text)

    # Gracefully handle a single dangling "$" before a formula-like expression.
    text = _re.sub(
        r'\$([A-Za-zΑ-ωβμσΣ][^$\n]{1,120})',
        lambda m: formula_to_plaintext(m.group(1)),
        text,
    )

    # Display blocks \[...\] → "  ▶ <plain formula>"
    def _render_block(m: re.Match) -> str:
        inner = m.group(1).strip()
        plain = formula_to_plaintext(inner)
        return f"\n  ▶ {plain}\n"

    text = _re.sub(r'\\\[(.+?)\\\]', _render_block, text, flags=_re.DOTALL)

    # Inline \(...\)
    text = _re.sub(r'\\\((.+?)\\\)', lambda m: formula_to_plaintext(m.group(1)), text)

    return text
