#!/usr/bin/env python3
"""demo_player.py — scripted Aria Code demo for VHS recording.

Plays a pre-canned interactive session that looks exactly like the real REPL.
Run:  python3 demo_player.py
"""
from __future__ import annotations
import sys, time, os, shutil

# ── ANSI helpers ──────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
PURPLE = "\033[38;5;141m"
CYAN   = "\033[38;5;87m"
GREEN  = "\033[38;5;120m"
YELLOW = "\033[38;5;227m"
ORANGE = "\033[38;5;215m"
RED    = "\033[38;5;210m"
GREY   = "\033[38;5;244m"
WHITE  = "\033[38;5;255m"
BG_BAR = "\033[48;5;235m"

W = shutil.get_terminal_size((110, 40)).columns

def p(*args, end="\n", flush=True, delay=0.0):
    print(*args, end=end, flush=flush)
    if delay:
        time.sleep(delay)

def rule(char="─", color=GREY):
    p(f"{color}{char * W}{RESET}")

def typewrite(prompt: str, line: str, speed: float = 0.03):
    """Print prompt then type line char by char."""
    p(f"\n{PURPLE}{BOLD}>{RESET} {GREY}{prompt}{RESET}", end="", flush=True)
    time.sleep(0.3)
    # erase prompt hint, type actual input
    p(f"\r{PURPLE}{BOLD}>{RESET} ", end="", flush=True)
    for ch in line:
        p(ch, end="", flush=True)
        time.sleep(speed)
    p()  # newline = Enter
    time.sleep(0.6)


# ── Banner ────────────────────────────────────────────────────────────────────
def show_banner():
    os.system("clear")
    p()
    p(f"  {PURPLE}{BOLD}▣ Aria Code{RESET}  {GREY}v4.0{RESET}  {DIM}本地优先 AI 金融终端{RESET}")
    p(f"  {GREY}model    {CYAN}qwen2.5-coder:7b{RESET}  {GREEN}● local{RESET}")
    p(f"  {GREY}status   {GREEN}Ollama online{RESET}  {GREY}·  3 models ready{RESET}")
    p(f"  {GREY}data     {YELLOW}Finnhub{RESET} {GREY}·{RESET} {YELLOW}Eastmoney{RESET} {GREY}·{RESET} {YELLOW}akshare{RESET}")
    p()
    p(f"  {DIM}try  {RESET}{CYAN}quote AAPL 600519{RESET}  {GREY}·{RESET}  {CYAN}/backtest momentum SPY{RESET}  {GREY}·{RESET}  {CYAN}/help{RESET}")
    rule()
    time.sleep(1.2)


# ── Scene 1: multi-market quote ───────────────────────────────────────────────
def scene_quote():
    typewrite("quote AAPL NVDA 600519", "quote AAPL NVDA 600519", speed=0.045)
    time.sleep(0.5)

    p(f"\n  {BOLD}{WHITE}实时行情{RESET}  {GREY}Finnhub · Eastmoney · 2026-06-16{RESET}\n")

    rows = [
        ("AAPL",   "Apple Inc",        "USD", "297.07", "+2.04%", GREEN,  "4.36T",  "美股"),
        ("NVDA",   "NVIDIA Corp",      "USD", "133.38", "+3.21%", GREEN,  "3.24T",  "美股"),
        ("600519", "贵州茅台",          "CNY", "1680.00","+1.83%", GREEN,  "2.11T",  "A股"),
    ]

    # header
    p(f"  {GREY}{'Symbol':<10}{'Name':<22}{'Price':>12}{'Chg':>9}{'Mkt Cap':>12}{'Market':>8}{RESET}")
    rule("·", GREY)

    for sym, name, cur, price, chg, chg_col, mcap, mkt in rows:
        p(f"  {CYAN}{BOLD}{sym:<10}{RESET}{WHITE}{name:<22}{RESET}"
          f"{BOLD}{cur} {price:>8}{RESET}"
          f"  {chg_col}{chg:>7}{RESET}"
          f"  {GREY}{mcap:>10}  {mkt}{RESET}",
          delay=0.15)

    rule("·", GREY)
    p(f"\n  {GREY}RSI  {RESET}{YELLOW}AAPL 40.6 中性{RESET}  {GREY}·{RESET}  "
      f"{GREEN}NVDA 61.2 偏强{RESET}  {GREY}·{RESET}  {GREEN}600519 62.3 偏强{RESET}")
    p(f"  {GREY}tips  {RESET}{DIM}/signal NVDA  ·  /team AAPL  ·  /peer AAPL MSFT GOOGL{RESET}")
    time.sleep(1.5)


# ── Scene 2: AI analysis ──────────────────────────────────────────────────────
def scene_analyze():
    typewrite("分析 NVDA 动量 — RSI MACD 和投资论点", "分析 NVDA 动量 — RSI MACD 和投资论点", speed=0.04)
    time.sleep(0.8)

    p(f"\n  {BOLD}{WHITE}NVIDIA Corp (NVDA){RESET}  {GREY}── 技术快照{RESET}\n")

    metrics = [
        ("现价",    f"{GREEN}{BOLD}USD 133.38{RESET}",  f"{GREEN}+3.21% 今日{RESET}"),
        ("RSI(14)", f"{YELLOW}61.2  中性偏强{RESET}",   "未进入超买区间"),
        ("MACD",    f"{GREEN}+2.87  金叉{RESET}",       "3 天前形成，趋势延续"),
        ("布林带",  "带宽 0.19",                        "波动率正常，上轨 $141.2"),
        ("MA20",    f"{GREEN}$128.40  价格上方{RESET}",  "短期均线多头排列"),
    ]
    for label, val, note in metrics:
        p(f"  {GREY}{label:<10}{RESET}{val:<38}{DIM}{note}{RESET}", delay=0.18)

    p()
    p(f"  {BOLD}{GREEN}信号：↑ 看多{RESET}  {GREY}(动量完好，关注 RSI 是否突破 70){RESET}")
    p(f"  {GREY}支撑：{RESET}$128.4 / $121.6     {GREY}压力：{RESET}$138.0 / $145.5")
    p()

    # streaming thesis
    p(f"  {BOLD}{WHITE}投资论点{RESET}", end="", flush=True)
    thesis = ("  AI 基础设施支出周期仍处早期，数据中心 GPU 需求刚性强。"
              "Blackwell 架构供不应求，FY26 营收预期持续上调。"
              "短期技术面动量健康，中期持有逻辑完整。")
    p()
    for ch in thesis:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(0.018)
    p(f"\n\n  {GREY}2.3s · qwen2.5-coder:7b (local){RESET}")
    time.sleep(1.8)


# ── Scene 3: backtest ─────────────────────────────────────────────────────────
def scene_backtest():
    typewrite("/backtest momentum NVDA AAPL MSFT 2024-01-01 2025-12-31",
              "/backtest momentum NVDA AAPL MSFT 2024-01-01 2025-12-31", speed=0.04)
    time.sleep(0.6)

    p(f"\n  {BOLD}{WHITE}动量策略回测{RESET}  {GREY}2024-01-01 → 2025-12-31{RESET}\n")

    p(f"  {GREY}{'策略':<22}{'总收益':>10}{'夏普比率':>12}{'最大回撤':>12}{'胜率':>10}{RESET}")
    rule("·", GREY)

    results = [
        ("动量 (Aria)",    "+47.3%", "1.82", "-12.4%", "63%", GREEN),
        ("买入持有 SPY",   "+26.1%", "1.21", "-19.3%", "—",   YELLOW),
        ("买入持有 QQQ",   "+31.8%", "1.34", "-17.1%", "—",   YELLOW),
    ]
    for name, ret, sharpe, dd, wr, col in results:
        p(f"  {col}{BOLD}{name:<22}{RESET}{col}{ret:>10}{RESET}"
          f"  {WHITE}{sharpe:>10}{RESET}  {RED}{dd:>10}{RESET}  {GREY}{wr:>8}{RESET}",
          delay=0.2)

    rule("·", GREY)
    p(f"\n  {GREEN}{BOLD}动量策略跑赢基准 +15.5%{RESET}  {GREY}·  Kelly 建议仓位 18.4%  ·  共 248 次交易{RESET}")
    p(f"  {DIM}tips  /wf NVDA momentum  ·  /kelly NVDA 0.63 2.1  ·  /corr NVDA AAPL SPY{RESET}")
    time.sleep(1.8)


# ── Scene 4: shell mode + @ autocomplete hint ─────────────────────────────────
def scene_shell():
    typewrite("! git log --oneline -3", "! git log --oneline -3", speed=0.05)
    time.sleep(0.4)

    p(f"  {GREEN}d4e3ab9{RESET} {WHITE}feat(v4.0): keyboard shortcuts, shell mode, 19+ providers{RESET}")
    p(f"  {GREEN}3e80289{RESET} {WHITE}feat(agent): 6项核心思考/输出逻辑改进{RESET}")
    p(f"  {GREEN}b854093{RESET} {WHITE}revert(brand): switch back to SVG logo{RESET}")
    p(f"  {DIM}↑ shell output injected into AI context{RESET}")
    time.sleep(1.2)

    typewrite("分析以上 commits 的代码质量趋势", "分析以上 commits 的代码质量趋势", speed=0.04)
    time.sleep(0.5)

    thesis2 = ("  最近 3 次提交显示：v4.0 功能密度高（+4912/-2245 行），"
               "重构 agent 逻辑合理，logo revert 说明团队注重视觉一致性。"
               "建议下一步补充集成测试覆盖 shell 模式和 @ 文件补全路径。")
    for ch in thesis2:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(0.016)
    p(f"\n\n  {GREY}1.8s · qwen2.5-coder:7b (local){RESET}")
    time.sleep(2.0)


# ── Bottom toolbar (static, painted last) ─────────────────────────────────────
def show_toolbar():
    bar = (f"{BG_BAR}  {CYAN}qwen2.5-coder:7b{RESET}{BG_BAR}"
           f"  {GREY}·  ~/aria-code {GREEN}⎇ main{RESET}{BG_BAR}"
           f"  {GREY}·  {YELLOW}rw{RESET}{BG_BAR}"
           f"  {GREY}·  local-only{RESET}{BG_BAR}"
           f"  {GREY}·  /help{RESET}{BG_BAR}"
           f"{' ' * 20}{RESET}")
    p(f"\n{bar}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    show_banner()
    scene_quote()
    scene_analyze()
    scene_backtest()
    scene_shell()
    show_toolbar()
    p()
    time.sleep(3.0)


if __name__ == "__main__":
    main()
