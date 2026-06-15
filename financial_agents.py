"""
financial_agents.py — Arthera Multi-Agent Financial Team

Architecture
────────────
每次 /team <ticker> 调用会并行启动 4 个专业 agent，最后由 Synthesis 合并：

  MacroAgent        → 宏观环境、利率、行业周期
  FundamentalAgent  → 财务指标、估值、竞争壁垒
  TechnicalAgent    → 图形形态、动量、关键价位
  RiskAgent         → 与投资组合的相关性、仓位建议、风险评分

每个 agent 拿到真实市场数据后，通过本地 Ollama 模型独立推理，
最后 SynthesisAgent 综合出可操作的投资建议。

Usage
─────
    from financial_agents import run_team_analysis
    import asyncio
    result = asyncio.run(run_team_analysis("NVDA", ollama_url, model, on_token))
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def _is_present(v: Any) -> bool:
    return v not in (None, "", "N/A", "-", "nan")


def _fmt_value(v: Any, digits: int = 2, suffix: str = "") -> str:
    if not _is_present(v):
        return "—"
    try:
        if isinstance(v, (int, float)):
            return f"{float(v):,.{digits}f}{suffix}"
        return str(v)
    except Exception:
        return str(v)


def _join_metric_lines(rows: List[tuple[str, Any]], *, empty: str) -> str:
    lines = []
    for label, value in rows:
        if _is_present(value) and value != "—":
            lines.append(f"  {label}: {value}")
    return "\n".join(lines) if lines else f"  {empty}"


# ── Lightweight Ollama caller (no extra deps) ────────────────────────────────

async def _ollama_chat(
    ollama_url: str,
    model: str,
    messages: list,
    temperature: float = 0.3,
    max_tokens: int = 1024,
    on_token: Optional[Callable] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> str:
    """Stream a single Ollama chat completion, return full text."""
    import aiohttp
    url  = f"{ollama_url}/api/chat"
    body = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "num_ctx": 8192,
        },
    }
    full = ""
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(url, json=body,
                                 timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    return f"[Ollama error {resp.status}: {err[:200]}]"
                async for line in resp.content:
                    if cancel_event and cancel_event.is_set():
                        break
                    text = line.decode("utf-8", errors="ignore").strip()
                    if not text:
                        continue
                    try:
                        data = json.loads(text)
                        token = data.get("message", {}).get("content", "")
                        if token:
                            full += token
                            if on_token:
                                on_token(token)
                        if data.get("done"):
                            break
                    except json.JSONDecodeError:
                        pass
    except Exception as e:
        return f"[Agent error: {e}]"
    return full


# ── Agent result dataclass ────────────────────────────────────────────────────

@dataclass
class AgentResult:
    name: str
    role: str
    analysis: str
    data_used: Dict[str, Any] = field(default_factory=dict)
    duration_s: float = 0.0
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None and bool(self.analysis)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "analysis": self.analysis,
            "duration_s": round(self.duration_s, 2),
            "error": self.error,
        }


# ═══════════════════════════════════════════════════════════════════════════
# Individual Agents
# ═══════════════════════════════════════════════════════════════════════════

async def _macro_agent(
    symbol: str,
    market_data: Dict[str, Any],
    ollama_url: str,
    model: str,
    on_status: Optional[Callable] = None,
) -> AgentResult:
    """宏观分析师：利率环境、行业周期、资金面."""
    t0 = time.time()
    if on_status:
        on_status("macro", "🔬 宏观分析师分析中...")

    indices   = market_data.get("indices", {})
    northbound = market_data.get("northbound", {})
    quote_d   = market_data.get("quote", {})

    # Build context
    idx_summary = "\n".join(
        f"  {name}: {d.get('price','-')} ({d.get('change_pct','-')}%)"
        for name, d in list(indices.items())[:8]
    ) or "  指数数据暂不可用"

    nb_text = ""
    if northbound.get("success"):
        nb_text = (f"\n北向资金: {northbound.get('direction','-')} "
                   f"{northbound.get('total_net',0):.1f}亿 "
                   f"(沪股通{northbound.get('sh_net',0):.1f}亿, "
                   f"深股通{northbound.get('sz_net',0):.1f}亿)")

    symbol_info = ""
    if quote_d.get("success"):
        symbol_info = (f"\n分析标的: {quote_d.get('name',symbol)} "
                       f"当前价格 {quote_d.get('price','-')} "
                       f"{quote_d.get('currency','')}, "
                       f"涨跌 {quote_d.get('change_pct',0):.2f}%")

    prompt = f"""你是机构量化基金的宏观策略分析师。请基于以下实时市场数据，分析 {symbol} 的宏观投资环境。

【实时市场数据 - {datetime.now().strftime('%Y-%m-%d %H:%M')}】
全球主要指数:
{idx_summary}{nb_text}{symbol_info}

请从以下维度分析（每点2-3句，简洁有力）：
1. **宏观环境评分** (1-10分) — 当前利率/流动性/风险偏好
2. **所在行业周期** — 处于扩张/顶部/收缩/底部哪个阶段
3. **资金面信号** — 主力资金、北向资金的行为含义
4. **宏观催化剂** — 未来30天值得关注的宏观事件
5. **宏观结论** — 一句话: 宏观环境对 {symbol} 是顺风还是逆风

用中文回答，专业且简洁，不超过300字。"""

    messages = [
        {"role": "system", "content": "你是专业宏观策略分析师，擅长解读宏观数据与市场信号。"},
        {"role": "user",   "content": prompt},
    ]
    analysis = await _ollama_chat(ollama_url, model, messages, temperature=0.3,
                                   max_tokens=600)
    return AgentResult(
        name="MacroAgent", role="宏观策略分析师",
        analysis=analysis,
        data_used={"indices_count": len(indices), "northbound": bool(nb_text)},
        duration_s=time.time() - t0,
    )


async def _fundamental_agent(
    symbol: str,
    market_data: Dict[str, Any],
    ollama_url: str,
    model: str,
    on_status: Optional[Callable] = None,
) -> AgentResult:
    """基本面研究员：财务指标、估值、竞争壁垒."""
    t0 = time.time()
    if on_status:
        on_status("fundamental", "📊 基本面研究员分析中...")

    fund = market_data.get("fundamentals", {})
    quote_d = market_data.get("quote", {})
    tech = market_data.get("technicals", {})

    if fund.get("success"):
        sector = " / ".join(x for x in (fund.get("sector"), fund.get("industry")) if _is_present(x))
        fund_rows = [
            ("市值", _fmt_cap(fund.get("market_cap"))),
            ("PE(TTM)", _fmt_value(fund.get("pe_ratio"))),
            ("远期PE", _fmt_value(fund.get("fwd_pe"))),
            ("PB", _fmt_value(fund.get("pb_ratio"))),
            ("PS", _fmt_value(fund.get("ps_ratio"))),
            ("EV/EBITDA", _fmt_value(fund.get("ev_ebitda"))),
            ("营收", _fmt_cap(fund.get("revenue"))),
            ("净利润", _fmt_cap(fund.get("net_income"))),
            ("EPS", _fmt_value(fund.get("eps"))),
            ("预测EPS", _fmt_value(fund.get("fwd_eps"))),
            ("Beta", _fmt_value(fund.get("beta"))),
            ("52周高点", _fmt_value(fund.get("52w_high"))),
            ("52周低点", _fmt_value(fund.get("52w_low"))),
            ("分析师目标价", _fmt_value(fund.get("analyst_target"))),
            ("分析师评级", fund.get("recommendation")),
            ("所在行业", sector),
        ]
        fund_text = "\n财务指标:\n" + _join_metric_lines(fund_rows, empty="基本面字段未返回")
    else:
        p = _fmt_value(quote_d.get("price"))
        fund_text = f"\n当前价格: {p}\n  基本面数据暂不可用，请明确说明估值判断受限。"

    tech_text = ""
    if tech.get("success"):
        tech_rows = [
            ("RSI(14)", _fmt_value(tech.get("rsi"))),
            ("MA20", _fmt_value(tech.get("ma20"))),
            ("MA60", _fmt_value(tech.get("ma60"))),
            ("布林带位置", _fmt_pct(tech.get("bb_position"))),
        ]
        tech_text = "\n技术面补充:\n" + _join_metric_lines(tech_rows, empty="技术面字段未返回")

    prompt = f"""你是顶级机构股票研究员，专注基本面深度研究。请分析 {symbol}。

【数据】{fund_text}{tech_text}

请提供（每点1-3句）：
1. **估值评估** — 当前估值贵/便宜/合理？与历史和同行对比
2. **盈利质量** — 营收/利润趋势，可持续性
3. **竞争壁垒** — 护城河宽窄，行业地位
4. **催化剂/风险** — 近期基本面的正负面变量
5. **基本面评分** (1-10) 与目标价区间

中文回答，专业简洁，不超过300字。"""

    messages = [
        {"role": "system", "content": "你是CFA持证的专业股票分析师，基本面研究专家。"},
        {"role": "user",   "content": prompt},
    ]
    analysis = await _ollama_chat(ollama_url, model, messages, temperature=0.25,
                                   max_tokens=600)
    return AgentResult(
        name="FundamentalAgent", role="基本面研究员",
        analysis=analysis,
        data_used={"has_fundamentals": fund.get("success", False)},
        duration_s=time.time() - t0,
    )


async def _technical_agent(
    symbol: str,
    market_data: Dict[str, Any],
    ollama_url: str,
    model: str,
    on_status: Optional[Callable] = None,
) -> AgentResult:
    """技术分析师：图形形态、动量指标、关键价位."""
    t0 = time.time()
    if on_status:
        on_status("technical", "📈 技术分析师分析中...")

    tech  = market_data.get("technicals", {})
    quote_d = market_data.get("quote", {})
    hist  = market_data.get("history", {})

    if not tech.get("success"):
        return AgentResult(
            name="TechnicalAgent", role="技术分析师",
            analysis="技术数据暂不可用，无法完成分析。",
            duration_s=time.time() - t0, error="no_tech_data",
        )

    price = tech.get("price", quote_d.get("price", 0))
    rsi   = tech.get("rsi")
    macd  = tech.get("macd", 0)
    macd_s = tech.get("macd_signal", 0)
    macd_h = tech.get("macd_hist", 0)
    bb_pos = tech.get("bb_position", 0.5)

    # 近期价格趋势
    trend_text = ""
    if hist.get("success") and hist.get("data"):
        closes = [d["close"] for d in hist["data"][-20:]]
        if len(closes) >= 5:
            ret_5d  = (closes[-1] / closes[-5]  - 1) * 100
            ret_20d = (closes[-1] / closes[0]   - 1) * 100
            trend_text = f"\n  近5日涨跌: {ret_5d:+.2f}%  |  近20日涨跌: {ret_20d:+.2f}%"

    # RSI 解读
    rsi_label = "超卖区" if rsi and rsi < 30 else "超买区" if rsi and rsi > 70 else "中性区"
    # MACD 信号
    macd_signal_txt = "金叉" if macd_h and macd_h > 0 else "死叉"
    # 布林带位置
    bb_label = "触下轨(超卖)" if bb_pos < 0.2 else "触上轨(超买)" if bb_pos > 0.8 else "中轨附近"

    tech_rows = [
        ("当前价格", _fmt_value(price)),
        ("RSI(14)", f"{float(rsi):.1f} [{rsi_label}]" if _is_present(rsi) else None),
        ("MACD", f"{float(macd):.4f}"),
        ("Signal", f"{float(macd_s):.4f}"),
        ("Histogram", f"{float(macd_h):.4f} [{macd_signal_txt}]" if _is_present(macd_h) else None),
        ("布林带位置", f"{float(bb_pos):.2f} [{bb_label}]" if _is_present(bb_pos) else None),
        ("MA5", _fmt_value(tech.get("ma5"))),
        ("MA10", _fmt_value(tech.get("ma10"))),
        ("MA20", _fmt_value(tech.get("ma20"))),
        ("MA60", _fmt_value(tech.get("ma60"))),
        ("MA120", _fmt_value(tech.get("ma120"))),
        ("布林上轨", _fmt_value(tech.get("bb_upper"))),
        ("布林中轨", _fmt_value(tech.get("bb_mid"))),
        ("布林下轨", _fmt_value(tech.get("bb_lower"))),
    ]
    tech_context = "\n技术指标 (实时):\n" + _join_metric_lines(tech_rows, empty="技术指标字段未返回")
    if trend_text:
        tech_context += trend_text

    prompt = f"""你是专业量化技术分析师。请基于以下技术数据分析 {symbol}。

【技术数据】{tech_context}

请提供：
1. **趋势判断** — 当前处于上升/震荡/下跌趋势，均线多空排列
2. **动量信号** — RSI和MACD发出什么信号，是否背离
3. **关键价位** — 近期支撑位和压力位（从均线和布林带推算）
4. **形态判断** — 当前可能的K线形态或图表形态
5. **技术评分** (1-10) 与操作建议（买入/持有/减仓/观望）

中文回答，专业精准，不超过280字。"""

    messages = [
        {"role": "system", "content": "你是专业技术分析师，擅长均线体系、MACD、RSI等量化技术指标。"},
        {"role": "user",   "content": prompt},
    ]
    analysis = await _ollama_chat(ollama_url, model, messages, temperature=0.2,
                                   max_tokens=550)
    return AgentResult(
        name="TechnicalAgent", role="技术分析师",
        analysis=analysis,
        data_used={"rsi": rsi, "macd_signal": macd_signal_txt, "bb_pos": bb_pos},
        duration_s=time.time() - t0,
    )


async def _risk_agent(
    symbol: str,
    market_data: Dict[str, Any],
    portfolio_symbols: List[str],
    ollama_url: str,
    model: str,
    on_status: Optional[Callable] = None,
) -> AgentResult:
    """风险官：波动率、相关性、仓位建议、下行风险."""
    t0 = time.time()
    if on_status:
        on_status("risk", "⚖️ 风险管理官分析中...")

    tech    = market_data.get("technicals", {})
    fund    = market_data.get("fundamentals", {})
    quote_d = market_data.get("quote", {})
    hist    = market_data.get("history", {})

    # 计算波动率
    vol_text = "历史波动率: 数据不足"
    max_dd_text = "最大回撤: 数据不足"
    if hist.get("success") and len(hist.get("data", [])) >= 20:
        import numpy as np
        closes = [d["close"] for d in hist["data"]]
        rets   = np.diff(np.log(closes))
        ann_vol = float(np.std(rets) * np.sqrt(252) * 100)
        vol_text = f"年化波动率: {ann_vol:.1f}%"
        # 最大回撤
        prices  = np.array(closes)
        peak    = np.maximum.accumulate(prices)
        dd      = (prices - peak) / peak * 100
        max_dd  = float(dd.min())
        max_dd_text = f"最大回撤(样本内): {max_dd:.1f}%"

    beta   = _fmt_value(fund.get("beta")) if fund.get("success") else "—"
    mktcap = _fmt_cap(fund.get("market_cap")) if fund.get("success") else "—"

    portfolio_text = (f"\n当前持仓: {', '.join(portfolio_symbols[:8])}"
                      if portfolio_symbols else "")

    risk_rows = [
        ("当前价格", _fmt_value(quote_d.get("price"))),
        ("Beta(vs市场)", beta),
        ("市值", mktcap),
        ("波动率", vol_text),
        ("最大回撤", max_dd_text),
        ("RSI", _fmt_value(tech.get("rsi")) if tech.get("success") else "—"),
    ]
    risk_text = _join_metric_lines(risk_rows, empty="风险字段未返回")

    prompt = f"""你是机构风控官，负责评估投资风险和仓位管理。请分析 {symbol}。

【风险数据】
{risk_text}{portfolio_text}

请提供：
1. **风险评级** (低/中/高/极高) 并说明理由
2. **波动率分析** — 与市场均值对比，隐含风险
3. **下行情景** — 若宏观走弱，可能的最大跌幅区间
4. **仓位建议** — 基于风险，建议持仓比例（激进/平衡/保守三种方案）
5. **止损位** — 建议止损价格区间和依据

中文回答，风险优先，不超过280字。"""

    messages = [
        {"role": "system", "content": "你是机构量化基金风险官，FRM持证，专注下行风险管理和仓位控制。"},
        {"role": "user",   "content": prompt},
    ]
    analysis = await _ollama_chat(ollama_url, model, messages, temperature=0.2,
                                   max_tokens=550)
    return AgentResult(
        name="RiskAgent", role="风险管理官",
        analysis=analysis,
        data_used={"vol_computed": "年化" in vol_text, "beta": beta},
        duration_s=time.time() - t0,
    )


async def _synthesis_agent(
    symbol: str,
    agent_results: List[AgentResult],
    market_data: Dict[str, Any],
    ollama_url: str,
    model: str,
    on_token: Optional[Callable] = None,
    on_status: Optional[Callable] = None,
) -> AgentResult:
    """综合研判：合并4个agent的观点，输出可操作的投资建议."""
    t0 = time.time()
    if on_status:
        on_status("synthesis", "📝 综合研判中...")

    sections = []
    for r in agent_results:
        if r.success:
            sections.append(f"【{r.role}】\n{r.analysis}")

    if not sections:
        return AgentResult(
            name="SynthesisAgent", role="综合研判",
            analysis="各专业agent均无有效输出，无法综合研判。",
            duration_s=time.time() - t0, error="no_agent_results",
        )

    quote_d = market_data.get("quote", {})
    price   = _fmt_value(quote_d.get("price"))
    name    = quote_d.get("name", symbol)
    chg_pct = quote_d.get("change_pct")
    chg_text = f"{float(chg_pct):+.2f}%" if _is_present(chg_pct) else "—"

    combined = "\n\n".join(sections)

    prompt = f"""你是量化基金首席投资官(CIO)。以下是你的专业团队对 {name}({symbol}) 的独立分析报告。
当前价格: {price}  今日涨跌: {chg_text}

━━━━━━━━━━━━━━━━━━━━━━
{combined}
━━━━━━━━━━━━━━━━━━━━━━

请综合以上观点，输出机构级投资决策报告，结构如下：

## 综合评分矩阵
| 维度 | 评分(1-10) | 核心依据 |
|宏观环境| | |
|基本面| | |
|技术面| | |
|风险| | |
|**综合** | | |

## 投资建议
**操作方向**: [强烈买入 / 买入 / 持有 / 减仓 / 卖出]
**目标价**: XX - XX（3-6个月）
**止损位**: XX
**建仓策略**: 如何分批建仓

## 核心逻辑（3条）
1.
2.
3.

## 主要风险（2条）
1.
2.

## 结论
一句话总结投资价值。

中文，专业，可操作性强。"""

    messages = [
        {"role": "system",
         "content": "你是经验丰富的量化基金CIO，擅长整合宏观、基本面、技术面和风险管理，给出可执行的投资决策。"},
        {"role": "user", "content": prompt},
    ]
    analysis = await _ollama_chat(ollama_url, model, messages, temperature=0.3,
                                   max_tokens=900, on_token=on_token)
    return AgentResult(
        name="SynthesisAgent", role="综合研判 (CIO)",
        analysis=analysis,
        data_used={"agents_used": len(sections)},
        duration_s=time.time() - t0,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════════════

async def run_team_analysis(
    symbol: str,
    ollama_url: str = "http://localhost:11434",
    model: str = "aria-sonata:4.5",
    portfolio_symbols: Optional[List[str]] = None,
    on_token: Optional[Callable] = None,
    on_status: Optional[Callable] = None,
    on_agent_done: Optional[Callable] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> Dict[str, Any]:
    """
    Run the full 4-agent financial team analysis for a symbol.

    Returns:
        {
          "symbol": ..., "name": ..., "price": ...,
          "agents": [AgentResult.to_dict(), ...],
          "synthesis": str,
          "total_duration_s": float,
          "market_data_summary": dict,
        }
    """
    t_start = time.time()
    portfolio_symbols = portfolio_symbols or []

    if on_status:
        on_status("data", f"📡 获取 {symbol} 实时数据...")

    # ── 1. 并行拉取所有市场数据 ──────────────────────────────────────────
    try:
        from market_data_client import MarketDataClient
        mdc = MarketDataClient()
        (quote_r, fund_r, hist_r, tech_r, idx_r, nb_r) = await asyncio.gather(
            asyncio.get_event_loop().run_in_executor(None, mdc.quote,      symbol),
            asyncio.get_event_loop().run_in_executor(None, mdc.fundamentals, symbol),
            asyncio.get_event_loop().run_in_executor(None, lambda: mdc.history(symbol, days=120)),
            asyncio.get_event_loop().run_in_executor(None, lambda: mdc.technical_indicators(symbol, days=120)),
            asyncio.get_event_loop().run_in_executor(None, mdc.indices),
            asyncio.get_event_loop().run_in_executor(None, mdc.northbound_flow),
        )
    except Exception as e:
        logger.error("Market data fetch failed: %s", e)
        quote_r = fund_r = hist_r = tech_r = idx_r = nb_r = {"success": False, "error": str(e)}

    market_data = {
        "quote":        quote_r,
        "fundamentals": fund_r,
        "history":      hist_r,
        "technicals":   tech_r,
        "indices":      idx_r.get("indices", {}) if idx_r.get("success") else {},
        "northbound":   nb_r,
    }

    if on_status:
        on_status("agents", "🚀 并行启动 4 个专业 Agent...")

    # ── 2. 并行运行 4 个基础 agent ─────────────────────────────────────────
    results: List[AgentResult] = await asyncio.gather(
        _macro_agent(symbol, market_data, ollama_url, model, on_status),
        _fundamental_agent(symbol, market_data, ollama_url, model, on_status),
        _technical_agent(symbol, market_data, ollama_url, model, on_status),
        _risk_agent(symbol, market_data, portfolio_symbols, ollama_url, model, on_status),
        return_exceptions=True,
    )

    # Filter out exceptions
    valid_results: List[AgentResult] = []
    for r in results:
        if isinstance(r, AgentResult):
            valid_results.append(r)
            if on_agent_done:
                on_agent_done(r)
        else:
            logger.warning("Agent raised exception: %s", r)

    # ── 3. Synthesis ───────────────────────────────────────────────────────
    synthesis = await _synthesis_agent(
        symbol, valid_results, market_data,
        ollama_url, model,
        on_token=on_token, on_status=on_status,
    )

    total = time.time() - t_start
    return {
        "success":      True,
        "symbol":       symbol,
        "name":         quote_r.get("name", symbol),
        "price":        quote_r.get("price"),
        "change_pct":   quote_r.get("change_pct"),
        "agents":       [r.to_dict() for r in valid_results],
        "synthesis":    synthesis.analysis,
        "total_duration_s": round(total, 2),
        "market_data_summary": {
            "quote_ok":        quote_r.get("success", False),
            "fundamentals_ok": fund_r.get("success", False),
            "history_bars":    len(hist_r.get("data", [])),
            "technicals_ok":   tech_r.get("success", False),
        },
    }


# ── Utility formatters ────────────────────────────────────────────────────────

def _fmt_cap(v) -> str:
    if v is None:
        return "—"
    try:
        v = float(v)
        if v >= 1e12:
            return f"${v/1e12:.2f}T"
        if v >= 1e9:
            return f"${v/1e9:.1f}B"
        if v >= 1e8:
            return f"¥{v/1e8:.1f}亿"
        if v >= 1e6:
            return f"${v/1e6:.0f}M"
        return f"{v:.0f}"
    except Exception:
        return str(v)

def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.2f}"
    except Exception:
        return str(v)
