"""BrokerCommandsMixin — /broker /account /positions /orders commands.

Extracted from aria_cli.py. Module globals (HAS_RICH, console, etc.) are
imported lazily inside each method body to avoid circular imports at load time.
"""
from __future__ import annotations


class BrokerCommandsMixin:
    """Mixin providing broker/account/positions/orders commands."""

    async def cmd_broker(self, args: str):
        """券商账户管理: /broker list | connect <id> | disconnect <id> | add <type> | status | init"""
        from aria_cli import HAS_RICH, console, Panel, rich_box, _HAS_BROKERS, _print_error
        if not _HAS_BROKERS:
            _print_error("brokers 模块未加载", "请确认 brokers/ 目录存在")
            return

        parts = args.strip().split(maxsplit=1)
        sub   = parts[0].lower() if parts else "list"
        rest  = parts[1].strip() if len(parts) > 1 else ""

        if sub == "list":
            await self._cmd_broker_list()
        elif sub == "status":
            await self._cmd_broker_status()
        elif sub == "connect":
            await self._cmd_broker_connect(rest)
        elif sub == "disconnect":
            await self._cmd_broker_disconnect(rest)
        elif sub in ("add", "new"):
            await self._cmd_broker_add(rest)
        elif sub == "remove":
            await self._cmd_broker_remove(rest)
        elif sub in ("default", "use"):
            await self._cmd_broker_default(rest)
        elif sub == "init":
            await self._cmd_broker_init()
        else:
            if HAS_RICH:
                console.print(Panel(
                    "[dim]用法:[/dim]\n"
                    "  [bold]/broker list[/bold]              — 显示所有已配置券商\n"
                    "  [bold]/broker connect[/bold] [id]     — 连接券商\n"
                    "  [bold]/broker disconnect[/bold] [id]  — 断开连接\n"
                    "  [bold]/broker status[/bold]           — 查看连接状态\n"
                    "  [bold]/broker add[/bold] <type>       — 添加新券商配置\n"
                    "  [bold]/broker remove[/bold] <id>      — 删除券商配置\n"
                    "  [bold]/broker default[/bold] <id>     — 设置默认账户\n"
                    "  [bold]/broker init[/bold]             — 输出所有类型的配置模板",
                    title="[bold]/broker[/bold]",
                    border_style="dim", box=rich_box.ROUNDED, padding=(0, 1),
                ))

    async def _cmd_broker_list(self):
        from aria_cli import (HAS_RICH, console, Panel, rich_box,
                              _list_broker_configs, _BROKERS_CONFIG_PATH, _get_broker_registry)
        cfgs = _list_broker_configs()
        if not cfgs:
            await self._prompt_no_broker_action()
            return

        reg = _get_broker_registry()
        if HAS_RICH:
            from rich.table import Table
            tbl = Table(title="[bold]已配置券商[/bold]", show_header=True, header_style="bold")
            tbl.add_column("ID",      style="bold")
            tbl.add_column("类型",    style="dim")
            tbl.add_column("名称")
            tbl.add_column("市场",    style="dim")
            tbl.add_column("状态")
            tbl.add_column("默认", justify="center")
            for c in cfgs:
                bid    = c.get("id", "")
                btype  = c.get("type", "")
                label  = c.get("label", bid)
                b      = reg.get(bid) if reg else None
                is_active = reg and reg.active() and reg.active().broker_id == bid
                if b and b.is_connected:
                    status = "[green]● 已连接[/green]"
                else:
                    status = "[dim]○ 未连接[/dim]"
                market_map = {
                    "xtquant":"A股","easytrader":"A股","futu":"港/美/A",
                    "tiger":"美/港/A","longbridge":"港/美/A",
                    "ibkr":"美股","alpaca":"美股","webull":"美股",
                }
                mkt    = c.get("market", market_map.get(btype, "—"))
                default_mark = "[green]✓[/green]" if c.get("default") or is_active else ""
                tbl.add_row(bid, btype, label, mkt, status, default_mark)
            console.print(tbl)
        else:
            for c in cfgs:
                print(f"  {c.get('id',''):<20} {c.get('type',''):<12} {c.get('label','')}")

    async def _cmd_broker_status(self):
        from aria_cli import HAS_RICH, console, _get_broker_registry
        reg = _get_broker_registry()
        connected = reg.list_connected() if reg else []
        if not connected:
            if HAS_RICH:
                console.print("[dim]当前无已连接券商。运行 [bold]/broker connect[/bold] 建立连接。[/dim]")
            else:
                print("无已连接券商")
            return
        for b in connected:
            try:
                acct = b.account_info()
                line = (
                    f"[green]●[/green] [bold]{b.label}[/bold] ({b.broker_type})"
                    f"  账户: {acct.masked_account}"
                    f"  总资产: [bold]{acct.currency} {acct.total_assets:,.2f}[/bold]"
                    f"  可用: {acct.cash:,.2f}"
                )
            except Exception as e:
                line = f"[yellow]●[/yellow] [bold]{b.label}[/bold] ({b.broker_type})  [dim]查询失败: {e}[/dim]"
            if HAS_RICH:
                console.print(line)
            else:
                print(line)

    async def _cmd_broker_connect(self, broker_id: str):
        from aria_cli import (HAS_RICH, console, _list_broker_configs,
                              _BROKERS_CONFIG_PATH, _get_broker_registry, _print_error)
        cfgs = _list_broker_configs()
        if not cfgs:
            _print_error("尚未配置任何券商", f"请先编辑 {_BROKERS_CONFIG_PATH}")
            return
        if not broker_id:
            from brokers.config import get_default_broker_config
            cfg = get_default_broker_config()
            if not cfg:
                _print_error("未设置默认券商", "请用 /broker connect <id> 指定")
                return
            broker_id = cfg["id"]

        reg = _get_broker_registry()
        label = broker_id
        try:
            if HAS_RICH:
                with console.status(f"[dim]正在连接 {broker_id}...[/dim]", spinner="dots"):
                    import asyncio as _aio
                    loop = _aio.get_event_loop()
                    broker = await loop.run_in_executor(None, reg.connect, broker_id)
            else:
                broker = reg.connect(broker_id)
            label = broker.label
            msg = f"[green]✓[/green] 已连接 [bold]{label}[/bold] ({broker.broker_type})"
            if HAS_RICH:
                console.print(msg)
            else:
                print(f"已连接 {label}")
        except Exception as e:
            _print_error(f"连接失败: {label}", str(e))

    async def _cmd_broker_disconnect(self, broker_id: str):
        from aria_cli import HAS_RICH, console, _get_broker_registry, _print_error
        reg = _get_broker_registry()
        if not broker_id:
            b = reg.active() if reg else None
            if not b:
                _print_error("无活跃券商", "请指定 id：/broker disconnect <id>")
                return
            broker_id = b.broker_id
        if reg:
            reg.disconnect(broker_id)
        msg = f"[dim]已断开连接: {broker_id}[/dim]"
        if HAS_RICH:
            console.print(msg)
        else:
            print(f"已断开: {broker_id}")

    async def _cmd_broker_add(self, broker_type: str):
        from aria_cli import (HAS_RICH, console, Panel, rich_box, _print_error,
                              _supported_broker_types, _get_broker_template,
                              _add_broker_cfg, _BROKERS_CONFIG_PATH)
        from ui.picker import arrow_select

        supported = _supported_broker_types()

        # ── 按市场分组、固定宽度对齐 ─────────────────────────────────────────
        # 每组: (分组标签, [broker_key, ...])
        _GROUPS = [
            ("A 股",       ["xtquant", "easytrader"]),
            ("港股 / 美股", ["futu", "tiger", "longbridge"]),
            ("美股 / 国际", ["ibkr", "alpaca", "webull"]),
        ]
        _CAT = {
            "xtquant": "A股",    "easytrader": "A股",
            "futu": "港/美/A",   "tiger": "港/美/A",   "longbridge": "港/美/A",
            "ibkr": "全球",      "alpaca": "美股",     "webull": "美股",
        }

        # ── 选择券商类型 ─────────────────────────────────────────────────────
        if not broker_type or broker_type not in supported:
            # Build ordered list with separators for display grouping.
            # Separator entries have key=None and won't be assigned.
            all_items = []  # (display_label, desc_str, key_or_None)
            for g_label, g_keys in _GROUPS:
                all_items.append((f"─── {g_label} ", "", None))
                for k in g_keys:
                    if k in supported:
                        all_items.append((f"  {k:<12}", supported[k], k))

            picker_options = [(label, desc) for label, desc, _ in all_items]
            sep_indices    = {i for i, (_, _, key) in enumerate(all_items) if key is None}
            key_at         = {i: key for i, (_, _, key) in enumerate(all_items) if key}

            # Start cursor on first real entry (skip leading separator)
            first_real = next((i for i in range(len(all_items)) if i not in sep_indices), 0)

            if HAS_RICH:
                console.print(
                    "[bold]选择要添加的券商[/bold]  "
                    "[dim]↑↓ 移动  Enter 确认  q 取消[/dim]"
                )

            # If user lands on a separator, nudge to the next real entry.
            while True:
                idx = _arrow_select(picker_options, selected=first_real,
                                    title="", max_visible=12)
                if idx < 0:
                    return
                if idx in sep_indices:
                    # Find next real entry below; wrap to first if none
                    nxt = next((i for i in range(idx + 1, len(all_items))
                                if i not in sep_indices), first_real)
                    first_real = nxt
                    continue
                broker_type = key_at[idx]
                break

        tmpl = _get_broker_template(broker_type)
        if not tmpl:
            _print_error(f"无法获取 {broker_type} 模板", "")
            return

        # ── 开户 & 凭证获取指南 ───────────────────────────────────────────
        _GUIDE: dict[str, str] = {
            "alpaca": (
                "Alpaca Markets — 免费美股/加密货币 API（支持模拟盘）\n\n"
                "获取 API Key 步骤：\n"
                "  1. 注册账号: https://app.alpaca.markets/signup\n"
                "  2. 登录后进入 Paper Trading → API Keys\n"
                "  3. 点击 [Generate New Key]，复制 Key 和 Secret\n"
                "  4. 模拟盘(paper=true)无需入金即可使用\n"
                "  5. 实盘：修改 paper=false 并完成入金认证\n\n"
                "依赖：pip install alpaca-py"
            ),
            "tiger": (
                "老虎证券 OpenAPI — 港股 / 美股\n\n"
                "获取凭证步骤：\n"
                "  1. 在老虎证券 App 开户并完成实名认证\n"
                "  2. 访问开发者平台: https://quant.tigeropen.com\n"
                "  3. 创建应用，获取 Tiger ID 和 RSA 密钥对\n"
                "  4. 将私钥文件保存到 ~/.arthera/tiger_rsa.pem\n\n"
                "依赖：pip install tigeropen"
            ),
            "longbridge": (
                "长桥证券 OpenAPI — 港股 / 美股 / A股\n\n"
                "获取凭证步骤：\n"
                "  1. 在长桥 App 开户并完成入金\n"
                "  2. 开发者中心: https://open.longportapp.com\n"
                "  3. 创建应用获取 App Key、App Secret、Access Token\n\n"
                "依赖：pip install longbridge"
            ),
            "ibkr": (
                "Interactive Brokers TWS/Gateway — 全球市场\n\n"
                "连接步骤：\n"
                "  1. 开户: https://www.interactivebrokers.com\n"
                "  2. 下载并启动 TWS 或 IB Gateway（保持后台运行）\n"
                "  3. TWS → 配置 → API → 启用 Socket Client\n"
                "     实盘端口 7496，模拟端口 7497\n"
                "  4. Gateway 端口：实盘 4001，模拟 4002\n\n"
                "依赖：pip install ib_insync"
            ),
            "futu": (
                "富途牛牛 OpenAPI — 港股 / 美股\n\n"
                "连接步骤：\n"
                "  1. 在富途牛牛 App 开户\n"
                "  2. 下载并启动 FutuOpenD\n"
                "     (牛牛客户端 → 更多 → OpenD)\n"
                "  3. OpenD 默认监听 127.0.0.1:11111，保持运行\n"
                "  4. 开发者文档: https://openapi.futunn.com\n\n"
                "依赖：pip install futu-api"
            ),
            "webull": (
                "Webull — 美股（非官方 API，行情查询为主）\n\n"
                "获取凭证步骤：\n"
                "  1. 注册: https://www.webull.com\n"
                "  2. 使用注册邮箱/手机号 + 密码即可\n"
                "  3. device_id 首次留空，登录后自动填充\n"
                "  4. 建议仅用于行情查询，下单功能稳定性有限\n\n"
                "依赖：pip install webull"
            ),
            "xtquant": (
                "迅投 XTQuant — A股（中信/华鑫/浙商等券商）\n\n"
                "获取凭证步骤：\n"
                "  1. 在支持的券商（中信/华鑫/浙商等）开户\n"
                "  2. 从券商获取并安装 QMT 量化交易终端\n"
                "  3. 登录 QMT 后保持运行\n"
                "  4. account_id 即你的券商账号\n"
                "  5. 仅支持 Windows / Linux (Wine)\n\n"
                "依赖：pip install xtquant  (安装包需从券商获取)"
            ),
            "easytrader": (
                "EasyTrader — A股（同花顺/通达信/华泰/国君等）\n\n"
                "配置步骤：\n"
                "  1. 安装对应券商的交易客户端\n"
                "  2. broker_name 可选值:\n"
                "     huatai / guojun / ths / tdx / yh / zszq / xq\n"
                "  3. exe_path 填写客户端完整路径\n"
                "  4. 使用时需保持客户端登录运行\n"
                "  5. 仅支持 Windows\n\n"
                "依赖：pip install easytrader"
            ),
        }

        guide = _GUIDE.get(broker_type, "")
        cat   = _CAT.get(broker_type, "")
        if guide and HAS_RICH:
            console.print(Panel(
                guide,
                title=f"[bold]{supported[broker_type]}[/bold]"
                      + (f"  [dim]{cat}[/dim]" if cat else "")
                      + "  —  开户 & 凭证获取指南",
                border_style="blue", box=rich_box.ROUNDED, padding=(0, 2),
            ))
            try:
                input("\n  [Enter] 继续填写配置   Ctrl+C 取消 › ")
            except (EOFError, KeyboardInterrupt):
                if HAS_RICH:
                    console.print("[dim]已取消[/dim]")
                return

        # ── 对话式配置向导 ─────────────────────────────────────────────────
        # 字段元组: (key, 说明, 默认值, 是否隐藏输入, 是否可选)
        _WIZARD: dict[str, list[tuple]] = {
            "alpaca": [
                ("id",         "配置 ID (用于 /broker connect)",  "alpaca_paper", False, False),
                ("label",      "显示名称",                         "Alpaca 模拟盘", False, False),
                ("api_key",    "API Key",                          "",              False, False),
                ("api_secret", "API Secret",                       "",              True,  False),
                ("paper",      "模拟盘 (true=模拟 / false=实盘)",  "true",          False, False),
            ],
            "tiger": [
                ("id",               "配置 ID",         "tiger_us",               False, False),
                ("label",            "显示名称",         "老虎",                    False, False),
                ("tiger_id",         "Tiger ID",         "",                       False, False),
                ("account",          "账户号",            "",                       False, False),
                ("private_key_path", "RSA 私钥路径",     "~/.arthera/tiger_rsa.pem", False, True),
            ],
            "longbridge": [
                ("id",           "配置 ID",      "lb_main",  False, False),
                ("label",        "显示名称",      "长桥",      False, False),
                ("app_key",      "App Key",       "",          False, False),
                ("app_secret",   "App Secret",    "",          True,  False),
                ("access_token", "Access Token",  "",          True,  False),
            ],
            "ibkr": [
                ("id",        "配置 ID",                                    "ibkr_main", False, False),
                ("label",     "显示名称",                                    "盈透",       False, False),
                ("host",      "TWS/Gateway 主机",                           "127.0.0.1", False, False),
                ("port",      "端口 (TWS实盘=7496 模拟=7497 Gateway=4001)", "7496",       False, False),
                ("client_id", "Client ID (每个连接唯一，整数)",               "1",          False, True),
            ],
            "futu": [
                ("id",     "配置 ID",            "futu_main", False, False),
                ("label",  "显示名称",            "富途",       False, False),
                ("host",   "OpenD 主机",          "127.0.0.1", False, False),
                ("port",   "OpenD 端口",          "11111",      False, False),
                ("market", "市场 (HK / US / CN)", "HK",         False, True),
            ],
            "webull": [
                ("id",        "配置 ID",      "webull_main", False, False),
                ("label",     "显示名称",      "Webull",      False, False),
                ("username",  "邮箱或手机号",  "",             False, False),
                ("password",  "密码",          "",             True,  False),
                ("device_id", "设备 ID",       "",             False, True),
            ],
            "xtquant": [
                ("id",         "配置 ID",  "xt_main",  False, False),
                ("label",      "显示名称",  "XTQuant",  False, False),
                ("account_id", "账户号",    "",          False, False),
            ],
            "easytrader": [
                ("id",          "配置 ID",    "et_main",                    False, False),
                ("label",       "显示名称",    "EasyTrader",                 False, False),
                ("broker_name", "券商名",      "huatai",                     False, False),
                ("exe_path",    "客户端路径",   "C:\\华泰证券\\xiadan.exe",    False, True),
            ],
        }

        fields = _WIZARD.get(broker_type, [])
        total  = len(fields)

        import getpass as _getpass

        if HAS_RICH:
            console.print(Panel(
                f"[bold]{supported[broker_type]}[/bold]  配置向导  "
                f"[dim]共 {total} 项[/dim]\n"
                f"[dim]Enter = 使用括号内默认值  /  标注 (可选) 的字段可直接跳过[/dim]",
                border_style="dim", box=rich_box.ROUNDED, padding=(0, 1),
            ))
            console.print()

        filled = dict(tmpl)
        for step, (key, label, default, secret, optional) in enumerate(fields, 1):
            # Progress prefix: [1/5]
            progress = f"[{step}/{total}]"
            opt_tag  = "  (可选)" if optional else ""

            if default:
                prompt_str = f"  {progress} {label}{opt_tag} [{default}]: "
            else:
                prompt_str = f"  {progress} {label}{opt_tag}: "

            try:
                if secret:
                    val = _getpass.getpass(prompt_str) or default
                else:
                    val = input(prompt_str).strip() or default
            except (EOFError, KeyboardInterrupt):
                if HAS_RICH:
                    console.print("\n[dim]已取消[/dim]")
                else:
                    print("\n已取消")
                return

            # Type coercion
            if key == "paper":
                filled[key] = val.lower() not in ("false", "0", "no", "f")
            elif key in ("port", "client_id") and val:
                try:
                    filled[key] = int(val)
                except ValueError:
                    filled[key] = val
            elif val:
                filled[key] = val

        filled.pop("_comment", None)

        # ── 保存配置 ──────────────────────────────────────────────────────
        try:
            _add_broker_cfg(filled)
            broker_id = filled.get("id", broker_type)
            if HAS_RICH:
                console.print()
                console.print(Panel(
                    f"[green]✓ 已保存[/green]  {broker_id}  [dim]→ {_BROKERS_CONFIG_PATH}[/dim]",
                    border_style="green", box=rich_box.ROUNDED, padding=(0, 1),
                ))
            else:
                print(f"✓ 已保存 {broker_id}")
        except Exception as exc:
            _print_error(f"保存失败: {exc}", f"请手动编辑 {_BROKERS_CONFIG_PATH}")
            return

        # ── 保存后即刻连接 ────────────────────────────────────────────────
        try:
            ans = input(f"\n  是否立即尝试连接 {broker_id}? (y/N) › ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans in ("y", "yes", "是"):
            await self._cmd_broker_connect(broker_id)
        else:
            if HAS_RICH:
                console.print(
                    f"[dim]稍后可运行 [bold]/broker connect {broker_id}[/bold] 建立连接[/dim]"
                )

    async def _cmd_broker_remove(self, broker_id: str):
        from aria_cli import HAS_RICH, console, _print_error, _remove_broker_cfg
        if not broker_id:
            _print_error("请指定要删除的券商 id", "/broker remove <id>")
            return
        removed = _remove_broker_cfg(broker_id)
        if removed:
            console.print(f"[dim]已删除券商配置: {broker_id}[/dim]") if HAS_RICH else print(f"已删除: {broker_id}")
        else:
            _print_error(f"未找到券商: {broker_id}", "")

    async def _cmd_broker_default(self, broker_id: str):
        from aria_cli import HAS_RICH, console, _print_error, _set_default_broker, _get_broker_registry
        if not broker_id:
            _print_error("请指定 id", "/broker default <id>")
            return
        ok = _set_default_broker(broker_id)
        if ok:
            reg = _get_broker_registry()
            if reg:
                reg.set_active(broker_id)
            msg = f"[green]✓[/green] 默认账户已设为: [bold]{broker_id}[/bold]"
            if HAS_RICH:
                console.print(msg)
            else:
                print(f"默认账户: {broker_id}")
        else:
            _print_error(f"未找到券商: {broker_id}", "请先用 /broker add 添加")

    async def _cmd_broker_init(self):
        from aria_cli import HAS_RICH, console, Panel, rich_box, _BROKERS_CONFIG_PATH
        from brokers.config import print_all_templates
        if HAS_RICH:
            console.print(Panel(
                f"[dim]将以下内容保存到[/dim] [bold]{_BROKERS_CONFIG_PATH}[/bold] [dim]，填写实际凭证后运行 /broker connect 连接。[/dim]\n\n"
                f"[dim](仅保留你需要的券商，删除不用的)[/dim]",
                title="[bold]所有券商配置模板[/bold]",
                border_style="dim", box=rich_box.ROUNDED, padding=(0, 1),
            ))
            from rich.syntax import Syntax
            console.print(Syntax(print_all_templates(), "json", theme="monokai", line_numbers=False))
        else:
            print(print_all_templates())

    async def cmd_account(self, args: str):
        """显示账户资金汇总。"""
        from aria_cli import _HAS_BROKERS, _print_error, _get_broker_registry, _print_broker_account
        if not _HAS_BROKERS:
            _print_error("brokers 模块未加载", "")
            return
        broker_id = args.strip()
        reg = _get_broker_registry()
        try:
            broker = reg.get(broker_id) if broker_id else reg.active()
            if not broker:
                broker = await self._auto_connect_broker(broker_id)
            if not broker:
                return
            import asyncio as _aio
            acct = await _aio.get_event_loop().run_in_executor(None, broker.account_info)
            _print_broker_account(acct)
        except Exception as e:
            _print_error(f"账户查询失败: {e}", "请检查券商连接状态 (/broker status)")

    async def cmd_positions(self, args: str):
        """显示当前持仓。"""
        from aria_cli import (HAS_RICH, console, _null_ctx, _HAS_BROKERS,
                              _print_error, _get_broker_registry, _print_broker_positions)
        if not _HAS_BROKERS:
            _print_error("brokers 模块未加载", "")
            return
        broker_id = args.strip()
        reg = _get_broker_registry()
        try:
            broker = reg.get(broker_id) if broker_id else reg.active()
            if not broker:
                broker = await self._auto_connect_broker(broker_id)
            if not broker:
                return
            import asyncio as _aio
            with console.status("[dim]获取持仓...[/dim]", spinner="dots") if HAS_RICH else _null_ctx():
                pos = await _aio.get_event_loop().run_in_executor(None, broker.positions)
            _print_broker_positions(pos, broker.label, broker.config.get("currency","CNY"))
        except Exception as e:
            _print_error(f"持仓查询失败: {e}", "请检查券商连接状态 (/broker status)")

    async def cmd_orders(self, args: str):
        """显示订单记录。"""
        from aria_cli import (HAS_RICH, console, _null_ctx, _HAS_BROKERS,
                              _print_error, _get_broker_registry, _print_broker_orders)
        if not _HAS_BROKERS:
            _print_error("brokers 模块未加载", "")
            return
        parts     = args.strip().split()
        status    = "all"
        broker_id = ""
        for p in parts:
            if p in ("open", "filled", "cancelled", "all"):
                status = p
            else:
                broker_id = p
        reg = _get_broker_registry()
        try:
            broker = reg.get(broker_id) if broker_id else reg.active()
            if not broker:
                broker = await self._auto_connect_broker(broker_id)
            if not broker:
                return
            import asyncio as _aio
            with console.status("[dim]获取订单...[/dim]", spinner="dots") if HAS_RICH else _null_ctx():
                orders = await _aio.get_event_loop().run_in_executor(
                    None, lambda: broker.orders(status=status, limit=30)
                )
            _print_broker_orders(orders, broker.label, status)
        except Exception as e:
            _print_error(f"订单查询失败: {e}", "请检查券商连接状态 (/broker status)")

    async def _prompt_no_broker_action(self) -> None:
        """未配置券商时显示可导航的操作菜单，选择后直接路由到对应功能。"""
        from aria_cli import (HAS_RICH, console, Panel, rich_box, _BROKERS_CONFIG_PATH)
        from ui.picker import arrow_select
        import subprocess, sys as _sys

        if HAS_RICH:
            console.print(Panel(
                "[yellow]尚未配置任何券商[/yellow]  —  请选择下一步操作：",
                border_style="yellow", box=rich_box.ROUNDED, padding=(0, 1),
            ))

        actions = [
            ("  添加新券商",       "交互式向导：选择券商 → 开户指引 → 填写凭证 → 一键连接"),
            ("  手动编辑配置文件",  f"用系统编辑器打开 {_BROKERS_CONFIG_PATH}"),
            ("  查看所有配置模板",  "输出全部券商的 JSON 模板供参考"),
            ("  暂时跳过",         "关闭此菜单，稍后再配置"),
        ]
        idx = _arrow_select(actions, selected=0, title="", max_visible=6)

        if idx == 0:
            await self._cmd_broker_add("")
        elif idx == 1:
            try:
                import pathlib as _pl, json as _json
                from brokers.config import print_all_templates
                path = _pl.Path(str(_BROKERS_CONFIG_PATH or
                                    _pl.Path.home() / ".arthera" / "brokers.json"))
                path.parent.mkdir(parents=True, exist_ok=True)

                # Pre-populate with full commented template if file is empty/missing
                needs_template = (
                    not path.exists()
                    or path.stat().st_size < 20
                    or path.read_text(encoding="utf-8").strip() in ('', '{"brokers": []}')
                )
                if needs_template:
                    path.write_text(print_all_templates(), encoding="utf-8")

                subprocess.Popen(["open", str(path)])

                if HAS_RICH:
                    from rich.syntax import Syntax
                    from ui.render.output import display_path as _display_path
                    console.print()
                    console.print(Panel(
                        f"[bold]已在编辑器中打开:[/bold] {_display_path(path, fallback='config')}\n\n"
                        f"[dim]文件已预填所有券商模板。\n"
                        f"删除不需要的券商块，填写你的实际凭证后保存。[/dim]\n\n"
                        f"[dim]保存后回到此终端，运行:[/dim]\n"
                        f"  [bold]/broker connect <id>[/bold]   建立连接\n"
                        f"  [bold]/broker list[/bold]           查看配置状态",
                        title="[bold]手动编辑配置[/bold]",
                        border_style="dim", box=rich_box.ROUNDED, padding=(0, 1),
                    ))
            except Exception as exc:
                if HAS_RICH:
                    console.print(f"[dim]配置文件路径: {_BROKERS_CONFIG_PATH}[/dim]")
                    console.print(f"[red]打开失败: {exc}[/red]")
        elif idx == 2:
            await self._cmd_broker_init()
        # idx == 3 or -1 → do nothing (skip)

    async def _auto_connect_broker(self, broker_id: str):
        """尝试自动连接；无配置时弹出操作菜单。"""
        from aria_cli import (HAS_RICH, _print_error,
                              _get_broker_registry, _list_broker_configs)
        reg  = _get_broker_registry()
        cfgs = _list_broker_configs()
        if not cfgs:
            await self._prompt_no_broker_action()
            return None
        target_id = broker_id or (cfgs[0].get("id", "") if cfgs else "")
        if not target_id:
            return None
        try:
            import asyncio as _aio
            return await _aio.get_event_loop().run_in_executor(None, reg.connect, target_id)
        except Exception as e:
            _print_error(f"自动连接 {target_id} 失败: {e}",
                         "请运行 /broker connect <id> 手动连接")
            return None
