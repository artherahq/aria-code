import builtins
import pathlib
import sys


_CLI_DIR = str(pathlib.Path(__file__).parents[1])
if _CLI_DIR not in sys.path:
    sys.path.insert(0, _CLI_DIR)


class _SnapshotMDC:
    def quote(self, symbol):
        prices = {
            "AAPL": {"name": "Apple", "price": 195.2, "change_pct": 0.8, "market_cap": 2_990_000_000_000, "high": 197.0, "low": 193.0},
            "NVDA": {"name": "NVIDIA", "price": 204.87, "change_pct": 2.22, "market_cap": 5_020_000_000_000, "high": 205.66, "low": 199.54},
            "MC.PA": {"name": "MC.PA", "price": 508.8, "change_pct": 0.73, "market_cap": 250_000_000_000, "high": 510.0, "low": 507.8},
            "300806": {"name": "斯迪克", "price": 12.34, "change_pct": -1.2, "market_cap": 5_000_000_000, "high": 12.8, "low": 12.1},
        }
        row = prices.get(symbol, prices["AAPL"])
        return {
            "success": True,
            "symbol": symbol,
            "name": row["name"],
            "price": row["price"],
            "change_pct": row["change_pct"],
            "market_cap": row["market_cap"],
            "high": row["high"],
            "low": row["low"],
            "currency": "USD",
            "provider": "test_provider",
            "provider_chain": ["test_provider"],
        }

    def fundamentals(self, symbol):
        return {"success": True, "provider": "fundamentals", "market_cap": 1_000_000_000_000}

    def technical_indicators(self, *_args, **_kwargs):
        symbol = _args[0] if _args else ""
        if symbol == "300806":
            return {
                "success": True,
                "provider": "local_pandas",
                "rsi": 66.0,
                "macd_hist": 0.25,
                "ma20": 11.8,
                "ma60": 10.7,
                "bb_upper": 13.0,
                "bb_lower": 10.4,
            }
        return {"success": False, "error": "history unavailable"}


def test_market_snapshot_output_avoids_na_placeholders(monkeypatch):
    import aria_cli

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "yfinance":
            raise ImportError("yfinance unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(aria_cli, "_HAS_MDC", True)
    monkeypatch.setattr(aria_cli, "_get_mdc", lambda: _SnapshotMDC())
    monkeypatch.setattr(aria_cli, "_get_provider_key", lambda _provider: "")

    result = aria_cli._try_handle_market_snapshot_analysis("分析 AAPL 市场")

    assert result["success"] is True
    assert "最新价" in result["response"]
    assert "N/A" not in result["response"]


def test_market_snapshot_handles_multi_symbol_company_names(monkeypatch):
    import aria_cli

    monkeypatch.setattr(aria_cli, "_HAS_MDC", True)
    monkeypatch.setattr(aria_cli, "_get_mdc", lambda: _SnapshotMDC())

    result = aria_cli._try_handle_market_snapshot_analysis("我想知道今天苹果和英伟达的市值和股票走势")

    assert result["success"] is True
    text = result["response"]
    assert "AAPL" in text
    assert "NVDA" in text
    assert "Market Cap" in text
    assert "NVDA  NVDA" not in text
    assert "- stale: none" in text
    assert "/ta AAPL" in text
    assert "/ta NVDA" in text


def test_market_snapshot_resolves_sidike_without_inheriting_previous_symbol(monkeypatch):
    import aria_cli
    from apps.cli.utils.market_detect import _extract_market_symbol

    monkeypatch.setattr(aria_cli, "_HAS_MDC", True)
    monkeypatch.setattr(aria_cli, "_get_mdc", lambda: _SnapshotMDC())
    monkeypatch.setattr(aria_cli, "_get_provider_key", lambda _provider: "")

    assert _extract_market_symbol("斯迪克的走势和预测") == "300806"
    history = [{"role": "user", "content": "紫金矿业走势"}]
    result = aria_cli._try_handle_market_snapshot_analysis("斯迪克的走势和预测", history=history)

    assert result["success"] is True
    text = result["response"]
    assert "斯迪克" in text
    assert "`300806`" in text
    assert "601899" not in text
    assert "预测参考" in text


def test_lvmh_prefetch_normalizes_symbol_name_and_currency(monkeypatch):
    import aria_cli

    monkeypatch.setattr(aria_cli, "_HAS_MDC", True)
    monkeypatch.setattr(aria_cli, "_get_mdc", lambda: _SnapshotMDC())

    block = aria_cli._try_prefetch_market_data("分析lvmh股票和成交量")

    assert "LVMH/路易威登(MC.PA)" in block
    assert "交易代码**：MC.PA（Euronext Paris）" in block
    assert "LVMH Moet Hennessy Louis Vuitton SE" in block
    assert "最新价**：EUR 508.8" in block
    assert "USD 508.8" not in block


def test_unresolved_market_name_does_not_inherit_history():
    import aria_cli

    history = [{"role": "user", "content": "紫金矿业走势"}]
    result = aria_cli._try_handle_market_snapshot_analysis("不存在公司走势", history=history)

    assert result["success"] is False or "无法识别" in result.get("response", "")


def test_tool_error_summary_hides_curl_details():
    import aria_cli

    summary = aria_cli._format_tool_summary(
        "get_market_data",
        {"success": False, "error": "curl: (28) Connection timed out after 30002 milliseconds"},
    )

    assert "请求超时" in summary
    assert "curl" not in summary.lower()


def test_run_command_activity_summary_uses_exit_code_field():
    from ui.render.output import _one_line_tool_summary

    icon, detail = _one_line_tool_summary(
        "run_command",
        {"success": True, "data": {"exit_code": 2}},
        0.0,
        {},
    )

    assert "red" in icon
    assert "exit 2" in detail


def test_activity_summary_hides_local_file_paths():
    from ui.render.output import _one_line_tool_summary

    _icon, detail = _one_line_tool_summary(
        "write_file",
        {"success": True, "data": {"path": "/Users/mac/Desktop/aria-code/secret.py", "lines": 12, "size_bytes": 48}},
        0.0,
        {"path": "/Users/mac/Desktop/aria-code/secret.py", "content": "x\n"},
    )

    assert "file tool" in detail
    assert "12 lines" in detail
    assert "/Users" not in detail
    assert "secret.py" not in detail


def test_activity_summary_hides_web_fetch_url():
    from ui.render.output import _one_line_tool_summary

    _icon, detail = _one_line_tool_summary(
        "web_fetch",
        {"success": True, "data": {"url": "https://example.com/private/report", "length": 1234}},
        0.0,
        {"url": "https://example.com/private/report"},
    )

    assert "web fetch" in detail
    assert "1,234c" in detail
    assert "example.com" not in detail
    assert "/private/report" not in detail


def test_activity_summary_hides_full_output_path():
    from ui.render.output import _one_line_tool_summary

    _icon, detail = _one_line_tool_summary(
        "run_command",
        {"success": True, "data": {"exit_code": 0, "full_output_path": "/Users/mac/.aria/artifacts/command-output.txt"}},
        0.0,
        {},
    )

    assert "full output saved" in detail
    assert "/Users" not in detail
    assert "command-output.txt" not in detail


def test_tool_display_label_marks_mcp_without_target_details():
    from ui.render.output import tool_display_label

    assert tool_display_label("mcp__github__read_file") == "github · read file · MCP"
    assert tool_display_label("web_search") == "web_search · web search"


def test_display_path_returns_filename_only():
    from ui.render.output import display_path

    assert display_path("/Users/mac/Desktop/aria-code/report.html") == "report.html"
    assert display_path("", fallback="artifact") == "artifact"


def test_report_markdown_prompt_omits_na_placeholders(monkeypatch, tmp_path):
    import aria_cli
    import packages.aria_services.data as service_data

    prompts = []

    class FakeTerminal:
        conversation = [{"role": "assistant", "content": "# Report\nok"}]
        config = {}

        async def send_message(self, prompt):
            prompts.append(prompt)

    class FakeCommands:
        terminal = FakeTerminal()

    class FakeBundle:
        quote = {"success": True, "price": 100.0, "provider": "test", "provider_chain": ["test"]}
        technical = {"success": False, "error": "history unavailable"}
        history = {}
        fundamentals = {}
        provider_chain = ["test"]
        missing_fields = ["rsi", "macd", "ma20", "ma60"]
        warnings = ["history unavailable"]
        errors = []
        status = "partial"
        quality = {
            "status": "partial",
            "stale": False,
            "providers": ["test"],
            "missing_fields": ["rsi", "macd", "ma20", "ma60"],
            "warnings": ["history unavailable"],
            "errors": [],
        }

    class FakeDataService:
        def bundle(self, *_args, **_kwargs):
            return FakeBundle()

    monkeypatch.setattr(service_data, "DataService", FakeDataService)
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)

    import asyncio
    asyncio.run(aria_cli.SlashCommands.cmd_report(FakeCommands(), "AAPL --format md"))

    assert prompts
    assert "N/A" not in prompts[0]
    assert "当前价" in prompts[0]
    assert "数据质量" in prompts[0]
    assert "数据状态: partial" in prompts[0]


def test_display_value_uses_dash_for_missing_values():
    import aria_cli

    assert aria_cli._display_value(None) == "—"
    assert aria_cli._display_value("N/A") == "—"


def test_analyze_context_uses_data_service_quality(monkeypatch):
    import asyncio
    import aria_cli
    import packages.aria_services.data as service_data

    class FakeBundle:
        quote = {"success": True, "price": 100.0, "change_pct": 1.2, "name": "Apple"}
        technical = {"success": True, "rsi": 55.0, "macd_hist": 0.12, "ma20": 98.0, "ma60": 90.0}
        fundamentals = {}
        history = {}
        provider_chain = ["fake_quote", "fake_ta"]
        missing_fields = ["fundamentals"]
        quality = {
            "status": "partial",
            "stale": False,
            "providers": ["fake_quote", "fake_ta"],
            "missing_fields": ["fundamentals"],
        }

    class FakeDataService:
        def __init__(self, *args, **kwargs):
            pass

        def bundle(self, *_args, **_kwargs):
            return FakeBundle()

    monkeypatch.setattr(service_data, "DataService", FakeDataService)
    monkeypatch.setattr(aria_cli, "_HAS_MDC", False)

    ctx = asyncio.run(aria_cli.SlashCommands._build_analyze_context(object(), "AAPL", False))

    assert "### Data Quality" in ctx
    assert "Status: partial" in ctx
    assert "Providers: fake_quote, fake_ta" in ctx
    assert "Missing fields: fundamentals" in ctx
    assert "Price: 100.00" in ctx


def test_team_result_sanitizer_removes_stale_split_prices():
    import aria_cli
    from agents.base import AgentResult
    from agents.team import TeamResult
    from data_service import DataBundle

    team_result = TeamResult(
        symbol="NVDA",
        agents_run=["technical"],
        results=[
            AgentResult(
                agent="technical",
                symbol="NVDA",
                analysis="Current price is ~$945 and target is $660.",
                confidence=0.8,
                signal="BUY",
            )
        ],
        synthesis="Entry around $580-$590, target $660.",
        final_signal="BUY",
        confidence=0.7,
    )
    bundle = DataBundle(
        symbol="NVDA",
        quote={"price": 204.87, "currency": "USD"},
        status="partial",
    )

    notes = aria_cli._sanitize_team_result_with_market_data(team_result, bundle)

    assert notes
    assert team_result.final_signal == "HOLD"
    assert team_result.confidence == 0.2
    assert team_result.results[0].success is False
    assert "当前参考价: 204.87" in team_result.results[0].analysis
    assert "$945" not in team_result.synthesis


def test_team_report_includes_data_quality_section(monkeypatch, tmp_path):
    import asyncio
    import aria_cli
    from agents.base import AgentResult
    from agents.team import TeamResult
    from data_service import DataBundle

    monkeypatch.setenv("ARIA_ARTIFACT_ROOT", str(tmp_path / "project-artifacts"))
    monkeypatch.setenv("ARIA_USER_OUTPUT_ROOT", str(tmp_path / "user-output"))
    team_result = TeamResult(
        symbol="NVDA",
        agents_run=["technical"],
        results=[
            AgentResult(
                agent="technical",
                symbol="NVDA",
                analysis="数据冲突，已降级。",
                confidence=0.2,
                signal="HOLD",
                error="stale_or_conflicting_price",
            )
        ],
        synthesis="综合结论已降级。",
        final_signal="HOLD",
        confidence=0.2,
    )
    bundle = DataBundle(
        symbol="NVDA",
        quote={"price": 204.87, "currency": "USD", "market_cap": 5_020_000_000_000},
        provider_chain=["finnhub", "yfinance"],
        missing_fields=["macd"],
        errors=["technical unavailable"],
        quality={"status": "partial", "stale": True, "errors": ["technical unavailable"]},
        status="partial",
    )

    asyncio.run(
        aria_cli.SlashCommands._save_team_report(
            object(),
            "NVDA",
            team_result,
            bundle,
            ["technical: removed stale/conflicting prices ($945)"],
        )
    )

    reports = list((tmp_path / "user-output").rglob("*_NVDA_team_report.md"))
    assert reports
    text = reports[0].read_text(encoding="utf-8")
    assert "## 数据质量" in text
    assert "是否过期: `yes`" in text
    assert "数据错误: `technical unavailable`" in text
    assert "当前参考价: `USD 204.87`" in text
    assert "输出校验" in text
    assert "TECHNICAL (UNUSABLE)" in text
