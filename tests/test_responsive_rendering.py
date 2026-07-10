from types import SimpleNamespace

from rich.console import Console

from ui.render.responsive import (
    StackedRecord,
    render_stacked_records,
    select_structured_layout,
)


def _recording_console(width: int) -> Console:
    return Console(record=True, width=width, color_system=None)


def test_structured_layout_uses_three_stable_breakpoints():
    assert select_structured_layout(80) == "stacked"
    assert select_structured_layout(100) == "compact"
    assert select_structured_layout(120) == "full"


def test_stacked_records_render_without_table_borders():
    console = _recording_console(80)

    render_stacked_records(
        console,
        title="测试持仓",
        records=[StackedRecord("AAPL  Apple", ("持仓 10", "盈亏 +20.00"))],
        footer="共 1 只",
    )

    output = console.export_text()
    assert "#1  AAPL  Apple" in output
    assert "持仓 10" in output
    assert "共 1 只" in output
    assert "┏" not in output
    assert "│" not in output


def _position():
    return SimpleNamespace(
        symbol="AAPL",
        name="Apple Inc.",
        quantity=10,
        available_qty=8,
        cost_price=190.0,
        current_price=200.0,
        market_value=2000.0,
        pnl=100.0,
        pnl_pct=5.26,
    )


def _order():
    return SimpleNamespace(
        order_id="order_12345678",
        symbol="AAPL",
        name="Apple Inc.",
        side="buy",
        order_type="limit",
        quantity=10,
        filled_qty=4,
        price=198.0,
        avg_price=197.5,
        status="partial",
        created_at="2026-07-10 10:30:00",
    )


def test_cli_broker_views_switch_between_stacked_compact_and_full(monkeypatch):
    import aria_cli

    outputs = {}
    for width in (80, 100, 120):
        console = _recording_console(width)
        monkeypatch.setattr(aria_cli, "console", console)
        monkeypatch.setattr(aria_cli, "HAS_RICH", True)
        aria_cli._print_broker_positions([_position()], "模拟账户")
        aria_cli._print_broker_orders([_order()], "模拟账户", "all")
        outputs[width] = console.export_text()

    assert "#1  AAPL  Apple Inc." in outputs[80]
    assert "可卖 8" in outputs[80]
    assert "委托 10 @ 198.000" in outputs[80]
    assert "#1" not in outputs[100]
    assert "成本" not in outputs[100]
    assert "订单号" not in outputs[100]
    assert "成本" in outputs[120]
    assert "订单号" in outputs[120]
    assert "成交量" in outputs[120]
