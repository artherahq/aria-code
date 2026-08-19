"""海外仓 ERP 分析结果 → 本地 HTML 仪表盘。

完全本地渲染（内嵌 CSS，不发任何网络请求），不依赖 Canva/Figma 之类的
第三方设计工具——跟 report_generator.py 给财务报告用的是同一条"本地渲染
+ 本地引擎导出 PDF"路线，PDF 导出直接复用它的 html_string_to_pdf()，
不重新写一套 Chrome/WebKit/weasyprint 兜底链。
"""

from __future__ import annotations

import html as _html
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from agents.team import TeamResult

_SIGNAL_COLOR = {
    "GOOD":    "#2ea043",
    "WATCH":   "#9a9a9a",
    "CONCERN": "#d29922",
    "SEVERE":  "#f85149",
}

_REPORTS_DIR = Path.home() / ".aria-code" / "reports" / "warehouse"


def _e(text: Any) -> str:
    return _html.escape(str(text if text is not None else ""))


def _signal_badge(signal: str) -> str:
    color = _SIGNAL_COLOR.get(signal, "#9a9a9a")
    return f'<span class="badge" style="background:{color}22;color:{color};border:1px solid {color}55">{_e(signal)}</span>'


def _agent_card(agent_name: str, result: Any) -> str:
    key_points = "".join(f"<li>{_e(p)}</li>" for p in (getattr(result, "key_points", None) or []))
    key_points_html = f'<ul class="points">{key_points}</ul>' if key_points else ""
    return f"""
    <div class="card">
      <div class="card-head">
        <span class="agent-name">{_e(agent_name)}</span>
        {_signal_badge(getattr(result, "signal", "WATCH"))}
        <span class="confidence">置信度 {getattr(result, "confidence", 0):.0%}</span>
      </div>
      <p class="analysis">{_e(getattr(result, "analysis", ""))}</p>
      {key_points_html}
    </div>"""


def _snapshot_summary(snapshot: Dict[str, Any]) -> str:
    counts = {
        "SKU":   len(snapshot.get("skus", []) or []),
        "库位":  len(snapshot.get("locations", []) or []),
        "货代":  len(snapshot.get("connectors", []) or []),
        "入库单": len(snapshot.get("inbounds", []) or []),
    }
    tiles = "".join(
        f'<div class="tile"><div class="tile-value">{v}</div><div class="tile-label">{_e(k)}</div></div>'
        for k, v in counts.items()
    )
    return f'<div class="tiles">{tiles}</div>'


def render_html(warehouse_id: str, snapshot: Dict[str, Any], team_result: TeamResult) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cards = "".join(
        _agent_card(r.agent, r) for r in team_result.results
    )
    synthesis_html = (
        f'<div class="synthesis"><h2>综合结论</h2><p>{_e(team_result.synthesis)}</p></div>'
        if team_result.synthesis else ""
    )
    return f"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>海外仓 ERP 分析 · {_e(warehouse_id)}</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px; background: #0d1117; color: #e6edf3;
    font-family: -apple-system, "PingFang SC", "Segoe UI", sans-serif;
  }}
  .header {{ display:flex; justify-content:space-between; align-items:baseline; margin-bottom: 24px; }}
  h1 {{ font-size: 22px; margin: 0; }}
  .meta {{ color: #8b949e; font-size: 13px; }}
  .final-signal {{ display:flex; align-items:center; gap: 12px; margin-bottom: 20px; }}
  .badge {{ padding: 3px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; }}
  .tiles {{ display:flex; gap: 12px; margin-bottom: 28px; }}
  .tile {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:14px 20px; text-align:center; min-width:90px; }}
  .tile-value {{ font-size: 22px; font-weight: 700; }}
  .tile-label {{ font-size: 12px; color:#8b949e; margin-top:4px; }}
  .synthesis {{ background:#161b22; border:1px solid #30363d; border-radius:10px; padding:18px 20px; margin-bottom:24px; }}
  .synthesis h2 {{ font-size: 15px; margin: 0 0 8px; color:#8b949e; }}
  .grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 16px; }}
  .card {{ background:#161b22; border:1px solid #30363d; border-radius:10px; padding:16px 18px; }}
  .card-head {{ display:flex; align-items:center; gap:10px; margin-bottom:10px; flex-wrap: wrap; }}
  .agent-name {{ font-weight:600; font-size: 14px; }}
  .confidence {{ margin-left:auto; font-size:12px; color:#8b949e; }}
  .analysis {{ font-size: 13px; line-height:1.6; color:#c9d1d9; margin: 0 0 8px; }}
  .points {{ margin:0; padding-left:18px; font-size:12px; color:#8b949e; }}
  .points li {{ margin-bottom: 3px; }}
  footer {{ margin-top: 32px; color:#484f58; font-size:11px; }}
</style>
</head>
<body>
  <div class="header">
    <h1>海外仓 ERP 分析 · {_e(warehouse_id)}</h1>
    <div class="meta">生成时间 {_e(generated_at)} · 本地渲染，无外部依赖</div>
  </div>
  <div class="final-signal">
    综合信号 {_signal_badge(team_result.final_signal)}
    <span class="confidence">团队置信度 {team_result.confidence:.0%} · 耗时 {team_result.elapsed_sec:.1f}s</span>
  </div>
  {_snapshot_summary(snapshot)}
  {synthesis_html}
  <div class="grid">
    {cards}
  </div>
  <footer>aria-code · agents/warehouse · 数据来源: 本地文件（非真实 ERP 接口）</footer>
</body>
</html>"""


def save_report(
    warehouse_id: str,
    snapshot: Dict[str, Any],
    team_result: TeamResult,
    *,
    open_browser: bool = False,
    also_pdf: bool = False,
) -> Path:
    """渲染 + 落盘到 ~/.aria-code/reports/warehouse/，按需打开浏览器 / 导出 PDF。"""
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(warehouse_id or "local"))
    html_path = _REPORTS_DIR / f"warehouse_{safe_id}_{stamp}.html"
    html_path.write_text(render_html(warehouse_id, snapshot, team_result), encoding="utf-8")

    if also_pdf:
        try:
            from report_generator import html_string_to_pdf
            pdf_path = html_path.with_suffix(".pdf")
            html_string_to_pdf(html_path.read_text(encoding="utf-8"), pdf_path)
        except Exception as exc:  # PDF 导出失败不该挡住 HTML 报告本身
            print(f"  ⚠ PDF 导出失败（HTML 报告仍然生成好了）: {exc}")

    if open_browser:
        webbrowser.open(f"file://{html_path.resolve()}")

    return html_path
