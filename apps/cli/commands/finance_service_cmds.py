"""CLI discovery for supported financial-market services."""

from __future__ import annotations


class FinanceServiceCommandsMixin:
    def cmd_markets(self, args: str):
        """Show the applicable finance-service contract for a market/query."""
        from apps.cli.finance_service_catalog import market_service_summary

        summary = market_service_summary(args)
        selected = summary["selected"]
        if HAS_RICH:
            from rich.table import Table

            console.print()
            title = "金融市场服务" if not selected else f"金融市场服务 · {selected}"
            table = Table(title=title, show_header=True, header_style="bold cyan")
            table.add_column("市场", style="bold", no_wrap=True)
            table.add_column("已接入服务")
            table.add_column("分析框架")
            for item in summary["markets"]:
                prediction = item["prediction"]
                services = " · ".join(item["services"])
                if prediction:
                    services += f"\n预测：{prediction}"
                table.add_row(item["label"], services, " · ".join(item["analysis"]))
            console.print(table)
            console.print("[dim]说明：实时结论必须先取得可用数据；数据缺失、过期或覆盖不足时将明确降级，不生成伪精确预测。[/dim]")
        else:
            for item in summary["markets"]:
                print(f"{item['label']}: {', '.join(item['services'])}")
