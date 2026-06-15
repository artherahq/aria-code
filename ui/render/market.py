"""Terminal renderers for market commands."""

from __future__ import annotations

from typing import Any, Callable, Iterable


ValueFormatter = Callable[..., str]


def _num_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric:
        return None
    return numeric


def _format_quote_bound(value: Any) -> str:
    numeric = _num_or_none(value)
    if numeric is None:
        return str(value) if value not in (None, "") else "-"
    return f"{numeric:.2f}"


def compact_quote_market_cap(value: Any, currency: str = "USD") -> str:
    numeric = _num_or_none(value)
    if numeric is None or numeric <= 0:
        return ""
    curr = (currency or "USD").upper()
    if curr == "CNY":
        return f"  Mkt Cap: ¥{numeric / 1e8:.0f}亿"
    prefix = "$" if curr == "USD" else f"{curr} "
    if numeric >= 1e12:
        return f"  Mkt Cap: {prefix}{numeric / 1e12:.2f}T"
    if numeric >= 1e9:
        return f"  Mkt Cap: {prefix}{numeric / 1e9:.1f}B"
    if numeric >= 1e6:
        return f"  Mkt Cap: {prefix}{numeric / 1e6:.0f}M"
    return f"  Mkt Cap: {prefix}{numeric:,.0f}"


def render_quote_plain(symbol: str, quote: dict[str, Any], *, name: str | None = None) -> str:
    label = name or quote.get("name") or symbol
    price = quote.get("price", "-")
    change = _num_or_none(quote.get("change_pct")) or 0.0
    sign = "+" if change >= 0 else ""
    return f"  {symbol:<8} {str(price):<10} {sign}{change:.2f}%  {label}"


def print_quote_result(
    *,
    console: Any,
    has_rich: bool,
    symbol: str,
    quote: dict[str, Any],
    name: str | None = None,
) -> None:
    """Render a single quote row."""

    label = name or quote.get("name") or symbol
    if not quote.get("success"):
        err = quote.get("error", "failed")
        if has_rich:
            console.print(f"  [red]{symbol}: {err}[/red]")
        else:
            print(f"  {symbol}: {err}")
        return

    if not has_rich:
        print(render_quote_plain(symbol, quote, name=label))
        return

    price = quote.get("price", "-")
    change = _num_or_none(quote.get("change_pct")) or 0.0
    currency = quote.get("currency", "")
    high = _format_quote_bound(quote.get("high", "-"))
    low = _format_quote_bound(quote.get("low", "-"))
    market_cap = compact_quote_market_cap(quote.get("market_cap"), currency)
    color = "green" if change >= 0 else "red"
    sign = "+" if change >= 0 else ""
    console.print(
        f"  [bold]{symbol:<8}[/bold] [dim]{str(label)[:20]:<22}[/dim]"
        f"  [bold]{currency} {price}[/bold]"
        f"  [{color}]{sign}{change:.2f}%[/{color}]"
        f"  [dim]Hi:{high}  Lo:{low}{market_cap}[/dim]"
    )


def _rsi_color(value: Any) -> str:
    if value is None:
        return "dim"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "dim"
    if numeric > 70:
        return "red"
    if numeric < 30:
        return "green"
    return "white"


def _rsi_label(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "中性"
    if numeric > 70:
        return "超买"
    if numeric < 30:
        return "超卖"
    return "中性"


def _macd_color(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "red"
    return "green" if numeric > 0 else "red"


def _macd_label(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return ""
    return "金叉" if numeric > 0 else "死叉"


def _quality_line(service_result: Any) -> str:
    quality = getattr(service_result, "quality", {}) or {}
    missing = getattr(service_result, "missing_fields", []) or []
    stale = bool(getattr(service_result, "stale", False))
    return (
        f"data:{quality.get('status', 'ok')} "
        f"stale:{'yes' if stale else 'no'} "
        f"missing:{', '.join(missing) if missing else 'none'}"
    )


def render_ta_plain(symbol: str, days: int, service_result: Any, formatter: ValueFormatter) -> str:
    data = getattr(service_result, "data", {}) or {}
    lines = [
        f"{symbol} 技术指标",
        _quality_line(service_result),
        (
            f"价格: {formatter(data.get('price'))}  "
            f"RSI: {formatter(data.get('rsi'))}  "
            f"MACD_hist: {formatter(data.get('macd_hist'), digits=4)}"
        ),
    ]
    ma_parts = [
        f"{name.upper()}: {data[name]}"
        for name in ("ma5", "ma10", "ma20", "ma60")
        if data.get(name)
    ]
    if ma_parts:
        lines.append("  ".join(ma_parts))
    return "\n".join(lines)


def print_ta_result(
    *,
    console: Any,
    has_rich: bool,
    symbol: str,
    days: int,
    service_result: Any,
    formatter: ValueFormatter,
    ma_names: Iterable[str] = ("ma5", "ma10", "ma20", "ma60", "ma120"),
) -> None:
    """Render a technical-analysis service result to the active terminal."""

    data = getattr(service_result, "data", {}) or {}
    if not has_rich:
        print(render_ta_plain(symbol, days, service_result, formatter))
        return

    providers = " → ".join(getattr(service_result, "provider_chain", []) or [])
    console.print()
    console.print(
        f"  [bold]{symbol}[/bold] 技术指标  "
        f"[dim]{days}日数据  provider:{providers or data.get('provider', '')}[/dim]"
    )
    if getattr(service_result, "quality", None):
        console.print(f"  [dim]{_quality_line(service_result)}[/dim]")
    console.print()
    console.print(f"  当前价格  [bold]{formatter(data.get('price'))}[/bold]")

    rsi = data.get("rsi")
    rsi_color = _rsi_color(rsi)
    console.print(
        f"  RSI(14)  [{rsi_color}]{formatter(rsi)}[/{rsi_color}]  {_rsi_label(rsi)}"
    )

    macd_hist = data.get("macd_hist")
    if any(data.get(key) is not None for key in ("macd", "macd_signal", "macd_hist")):
        macd_color = _macd_color(macd_hist)
        console.print(
            f"  MACD     {formatter(data.get('macd'), digits=4)}  "
            f"Signal:{formatter(data.get('macd_signal'), digits=4)}  "
            f"[{macd_color}]Hist:{formatter(macd_hist, digits=4)}  "
            f"{_macd_label(macd_hist)}[/{macd_color}]"
        )

    bb_pos = data.get("bb_position", 0.5)
    if any(data.get(key) is not None for key in ("bb_upper", "bb_mid", "bb_lower")):
        console.print(
            f"  布林带   上:{formatter(data.get('bb_upper'))}  "
            f"中:{formatter(data.get('bb_mid'))}  下:{formatter(data.get('bb_lower'))}  "
            f"位置:{formatter(bb_pos)}"
        )

    console.print()
    for name in ma_names:
        if data.get(name):
            console.print(f"  {name.upper():<7} {data[name]}", end="  ")
    console.print()
    console.print()
