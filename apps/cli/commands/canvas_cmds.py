"""CanvasCommandsMixin — /canvas command (live artifact preview server).

Extracted following the same convention as broker_cmds.py/backtest_cmds.py:
module globals (HAS_RICH, console, etc.) are imported lazily inside each
method body to avoid circular imports at load time.
"""
from __future__ import annotations


class CanvasCommandsMixin:
    """Mixin providing the /canvas live-preview-server command.

    Off by default — nothing in preview_server.py runs until this command
    is explicitly typed. See preview_server.py's module docstring for why
    this stays opt-in rather than auto-starting.
    """

    async def cmd_canvas(self, args: str):
        """实时预览面板: /canvas [stop] —— 启动/停止本地预览服务器，报告和图表生成后会自动在浏览器里实时更新。"""
        from aria_cli import HAS_RICH, console, _print_error

        sub = args.strip().lower()
        import preview_server

        if sub == "stop":
            session = preview_server.get_active_session()
            if session is None:
                msg = "预览服务器未在运行"
                console.print(f"[yellow]{msg}[/yellow]") if HAS_RICH else print(msg)
                return
            await preview_server.stop_session()
            msg = "✓ 已停止预览服务器"
            console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)
            return

        existing = preview_server.get_active_session()
        if existing is not None and existing.url:
            msg = f"预览服务器已在运行: {existing.url}"
            console.print(f"[cyan]{msg}[/cyan]") if HAS_RICH else print(msg)
            return

        try:
            session = await preview_server.start_session()
        except Exception as exc:
            _print_error(f"预览服务器启动失败: {exc}", "换个端口范围或检查网络配置后重试")
            return

        preview_server.open_in_browser(session.url)
        msg = (
            f"✓ 预览服务器已启动: {session.url}\n"
            f"之后生成的报告/图表会自动在这个浏览器标签里实时更新，无需手动刷新。\n"
            f"/canvas stop 可以随时关闭。"
        )
        console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)
