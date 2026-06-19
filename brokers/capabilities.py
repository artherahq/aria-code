"""Broker capability catalog and setup playbooks.

This module is intentionally UI-free so CLI, daemon, docs, and tests can share
one source of truth for supported broker connectors.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from typing import Iterable


@dataclass(frozen=True)
class BrokerCapability:
    broker_type: str
    display_name: str
    markets: tuple[str, ...]
    sdk_module: str
    pip_package: str
    credential_fields: tuple[str, ...]
    local_runtime: str
    read_capabilities: tuple[str, ...]
    trade_capability: str
    safety_notes: tuple[str, ...]
    setup_steps: tuple[str, ...]

    @property
    def can_trade(self) -> bool:
        return self.trade_capability not in ("read_only", "unsupported")


_BROKER_CAPABILITIES: tuple[BrokerCapability, ...] = (
    BrokerCapability(
        broker_type="xtquant",
        display_name="迅投 XTQuant / QMT",
        markets=("A股",),
        sdk_module="xtquant",
        pip_package="xtquant",
        credential_fields=("account_id",),
        local_runtime="QMT 量化终端需已登录并保持运行；主要面向 Windows。",
        read_capabilities=("account", "positions", "orders"),
        trade_capability="live_trade",
        safety_notes=("实盘交易必须由 /broker_order 二次确认后执行。", "依赖包通常由券商提供，pip 可能不可直接安装。"),
        setup_steps=(
            "安装并登录券商 QMT/XTQuant 客户端。",
            "运行 /broker add xtquant 填写 account_id。",
            "运行 /broker connect <id>，再用 /account 与 /positions 验证。",
        ),
    ),
    BrokerCapability(
        broker_type="easytrader",
        display_name="EasyTrader",
        markets=("A股",),
        sdk_module="easytrader",
        pip_package="easytrader",
        credential_fields=("broker_name", "exe_path"),
        local_runtime="券商桌面交易客户端需已登录；主要面向 Windows。",
        read_capabilities=("account", "positions", "orders"),
        trade_capability="live_trade",
        safety_notes=("属于客户端自动化路径，稳定性取决于券商客户端界面。",),
        setup_steps=(
            "安装并登录同花顺/通达信/华泰/国君等交易客户端。",
            "运行 /broker add easytrader 填写 broker_name 与 exe_path。",
            "先用 /account 只读校验，再启用交易。",
        ),
    ),
    BrokerCapability(
        broker_type="futu",
        display_name="富途牛牛 OpenAPI",
        markets=("港股", "美股", "A股"),
        sdk_module="futu",
        pip_package="futu-api",
        credential_fields=("host", "port", "market"),
        local_runtime="FutuOpenD 需在本机或局域网运行，默认 127.0.0.1:11111。",
        read_capabilities=("account", "positions", "orders"),
        trade_capability="live_trade",
        safety_notes=("OpenD 的交易解锁、市场权限和账户类型会影响可用能力。",),
        setup_steps=(
            "安装 futu-api 并启动 FutuOpenD。",
            "运行 /broker add futu 填写 host、port、market。",
            "运行 /broker connect <id> 后检查 /positions 与 /orders。",
        ),
    ),
    BrokerCapability(
        broker_type="tiger",
        display_name="老虎证券 OpenAPI",
        markets=("美股", "港股", "A股"),
        sdk_module="tigeropen",
        pip_package="tigeropen",
        credential_fields=("tiger_id", "private_key_path", "account"),
        local_runtime="不需要本地网关，但需要开发者账号、账户号和 RSA 私钥。",
        read_capabilities=("account", "positions", "orders"),
        trade_capability="live_trade",
        safety_notes=("私钥文件必须保存在本机，不要写入对话或日志。",),
        setup_steps=(
            "在老虎开放平台注册应用并保存 RSA 私钥。",
            "运行 /broker add tiger 填写 tiger_id、account、private_key_path。",
            "用 /broker doctor 检查依赖与字段，再 /broker connect <id>。",
        ),
    ),
    BrokerCapability(
        broker_type="longbridge",
        display_name="长桥证券 OpenAPI",
        markets=("港股", "美股", "A股"),
        sdk_module="longbridge",
        pip_package="longbridge",
        credential_fields=("app_key", "app_secret", "access_token"),
        local_runtime="不需要本地网关；需要长桥 OpenAPI App Key/Secret/Token。",
        read_capabilities=("account", "positions", "orders"),
        trade_capability="live_trade",
        safety_notes=("Token 建议只保存在 brokers.json 或环境变量，不进入提示词。",),
        setup_steps=(
            "在长桥 OpenAPI 开发者中心创建应用。",
            "运行 /broker add longbridge 填写 App Key、Secret、Access Token。",
            "连接后使用 /account、/positions 校验账户数据。",
        ),
    ),
    BrokerCapability(
        broker_type="ibkr",
        display_name="Interactive Brokers",
        markets=("全球市场",),
        sdk_module="ib_insync",
        pip_package="ib_insync",
        credential_fields=("host", "port", "client_id"),
        local_runtime="TWS 或 IB Gateway 需已登录并开启 Socket API。",
        read_capabilities=("account", "positions", "orders"),
        trade_capability="live_trade",
        safety_notes=("模拟端口通常为 7497/4002，实盘端口通常为 7496/4001。",),
        setup_steps=(
            "启动 TWS 或 IB Gateway，并启用 API Socket。",
            "运行 /broker add ibkr 填写 host、port、client_id。",
            "建议先连模拟盘，再切实盘端口。",
        ),
    ),
    BrokerCapability(
        broker_type="alpaca",
        display_name="Alpaca Markets",
        markets=("美股", "加密货币"),
        sdk_module="alpaca",
        pip_package="alpaca-py",
        credential_fields=("api_key", "api_secret", "paper"),
        local_runtime="不需要本地网关；支持 paper=true 模拟盘。",
        read_capabilities=("account", "positions", "orders"),
        trade_capability="paper_or_live_trade",
        safety_notes=("默认建议使用 paper=true；实盘需确认账户权限。",),
        setup_steps=(
            "在 Alpaca 控制台生成 API Key/Secret。",
            "运行 /broker add alpaca，优先选择 paper=true。",
            "用 /broker connect <id> 与 /account 验证模拟盘。",
        ),
    ),
    BrokerCapability(
        broker_type="webull",
        display_name="Webull",
        markets=("美股",),
        sdk_module="webull",
        pip_package="webull",
        credential_fields=("username", "password", "device_id"),
        local_runtime="不需要本地网关；当前适配器默认只读。",
        read_capabilities=("account", "positions", "orders"),
        trade_capability="read_only",
        safety_notes=("非官方 API，默认不开放程序化下单。",),
        setup_steps=(
            "安装 webull SDK 并准备登录凭证。",
            "运行 /broker add webull。",
            "仅用于 /account、/positions、/orders 只读查询。",
        ),
    ),
)


def list_broker_capabilities() -> tuple[BrokerCapability, ...]:
    return _BROKER_CAPABILITIES


def get_broker_capability(broker_type: str) -> BrokerCapability | None:
    normalized = str(broker_type or "").strip().lower()
    for spec in _BROKER_CAPABILITIES:
        if spec.broker_type == normalized:
            return spec
    return None


def broker_dependency_state(spec: BrokerCapability) -> dict[str, object]:
    installed = bool(find_spec(spec.sdk_module))
    return {
        "broker_type": spec.broker_type,
        "module": spec.sdk_module,
        "package": spec.pip_package,
        "installed": installed,
        "install_hint": f"/install {spec.pip_package}" if not installed else "",
    }


def broker_connection_plan(broker_type: str) -> tuple[str, ...]:
    spec = get_broker_capability(broker_type)
    if not spec:
        return (
            "运行 /broker guide 查看支持的券商类型。",
            "选择券商后运行 /broker add <type>。",
        )
    dep = broker_dependency_state(spec)
    first = (
        f"依赖已安装: {spec.sdk_module}"
        if dep["installed"]
        else f"安装依赖: /install {spec.pip_package}"
    )
    return (first, *spec.setup_steps, "常用服务: /account, /positions, /orders, /risk, /report")


def broker_service_playbook() -> tuple[dict[str, str], ...]:
    """Return user-facing broker-to-service usage flows."""

    return (
        {
            "service": "账户与持仓",
            "commands": "/broker connect <id> → /account → /positions → /orders",
            "used_by": "LLM broker_query 工具、持仓风险分析、组合报告",
            "guardrail": "只读查询，不触发交易",
        },
        {
            "service": "策略到交易计划",
            "commands": "/backtest → /signal → broker_order",
            "used_by": "QuantEngine 信号、回测结果、Kelly/仓位测算",
            "guardrail": "下单前必须展示订单计划并等待用户确认",
        },
        {
            "service": "TradingView 告警联动",
            "commands": "TradingView alert → Aria daemon webhook → 分析/通知/订单草案",
            "used_by": "/tv、/daemon、webhook、通知渠道",
            "guardrail": "Webhook 默认只生成分析和订单草案，不自动实盘下单",
        },
        {
            "service": "报告与审计",
            "commands": "/report, /dashboard, /export",
            "used_by": "账户快照、持仓、行情、信号、风控、交易计划",
            "guardrail": "报告落本地 artifacts；敏感字段脱敏",
        },
    )


def filter_capabilities(names: Iterable[str]) -> tuple[BrokerCapability, ...]:
    wanted = {str(name).strip().lower() for name in names if str(name).strip()}
    if not wanted:
        return list_broker_capabilities()
    return tuple(spec for spec in _BROKER_CAPABILITIES if spec.broker_type in wanted)
