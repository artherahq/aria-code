"""
brokers/config.py — 券商配置加载与管理
========================================
配置文件位置：~/.aria-code/brokers.json（或由 ARIA_HOME 环境变量覆盖）

示例配置::

    {
      "brokers": [
        {
          "id":         "xt_main",
          "type":       "xtquant",
          "label":      "中信主账户",
          "account_id": "1234567890",
          "default":    true
        },
        {
          "id":         "ibkr_us",
          "type":       "ibkr",
          "label":      "盈透美股",
          "host":       "127.0.0.1",
          "port":       7496,
          "client_id":  1
        },
        {
          "id":         "alpaca_paper",
          "type":       "alpaca",
          "label":      "Alpaca 模拟盘",
          "api_key":    "PKxxx",
          "api_secret": "xxx",
          "paper":      true
        },
        {
          "id":         "futu_hk",
          "type":       "futu",
          "label":      "富途港股",
          "host":       "127.0.0.1",
          "port":       11111,
          "market":     "HK"
        },
        {
          "id":         "tiger_us",
          "type":       "tiger",
          "label":      "老虎美股",
          "tiger_id":   "xxx",
          "private_key_path": "~/.aria-code/tiger_rsa.pem",
          "account":    "xxx"
        },
        {
          "id":         "longbridge_cn",
          "type":       "longbridge",
          "label":      "长桥A股",
          "app_key":    "xxx",
          "app_secret": "xxx",
          "access_token": "xxx"
        }
      ]
    }

支持的 type 值:
    xtquant     迅投（中信、华鑫、浙商等）
    easytrader  easytrader（同花顺、通达信、华泰、国君）
    futu        富途牛牛 OpenAPI（港股/美股/A股）
    tiger       老虎证券 OpenAPI（美股/港股/A股）
    longbridge  长桥证券 OpenAPI（港股/美股/A股）
    ibkr        Interactive Brokers TWS/Gateway
    alpaca      Alpaca Markets（美股，支持模拟盘）
    webull      Webull（美股）
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


def _resolve_aria_home() -> Path:
    """Resolve the Aria config directory (same logic as aria_cli.py).

    Priority:
      1. ARIA_HOME env var (explicit override)
      2. ~/.arthera — legacy path, kept for backward compat if it exists
      3. ~/.aria-code — default for fresh installs
    """
    if "ARIA_HOME" in os.environ:
        return Path(os.environ["ARIA_HOME"]).expanduser()
    legacy = Path.home() / ".arthera"
    if legacy.exists():
        return legacy
    return Path.home() / ".aria-code"


BROKERS_CONFIG_PATH = _resolve_aria_home() / "brokers.json"


# ── 读写工具 ───────────────────────────────────────────────────────────────────

def load_config() -> Dict[str, Any]:
    """加载 brokers.json，文件不存在时返回空 {"brokers": []}。"""
    if not BROKERS_CONFIG_PATH.exists():
        return {"brokers": []}
    try:
        with open(BROKERS_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"brokers": []}


def save_config(cfg: Dict[str, Any]) -> None:
    """保存配置到 brokers.json，自动创建目录。"""
    BROKERS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BROKERS_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def list_broker_configs() -> List[Dict[str, Any]]:
    """返回所有已配置的券商列表。"""
    return load_config().get("brokers", [])


def get_broker_config(broker_id: str) -> Optional[Dict[str, Any]]:
    """根据 id 查找某个券商配置。"""
    for b in list_broker_configs():
        if b.get("id") == broker_id:
            return b
    return None


def get_default_broker_config() -> Optional[Dict[str, Any]]:
    """返回标记了 default=true 的券商，或第一个。"""
    brokers = list_broker_configs()
    if not brokers:
        return None
    for b in brokers:
        if b.get("default"):
            return b
    return brokers[0]


def add_broker_config(cfg: Dict[str, Any]) -> None:
    """新增一条券商配置（id 重复时覆盖）。"""
    data = load_config()
    brokers = data.get("brokers", [])
    brokers = [b for b in brokers if b.get("id") != cfg.get("id")]
    brokers.append(cfg)
    data["brokers"] = brokers
    save_config(data)


def remove_broker_config(broker_id: str) -> bool:
    """删除某个券商配置，返回 True 表示找到并删除。"""
    data = load_config()
    before = len(data.get("brokers", []))
    data["brokers"] = [b for b in data.get("brokers", []) if b.get("id") != broker_id]
    if len(data["brokers"]) < before:
        save_config(data)
        return True
    return False


def set_default_broker(broker_id: str) -> bool:
    """设置默认券商。"""
    data = load_config()
    found = False
    for b in data.get("brokers", []):
        if b.get("id") == broker_id:
            b["default"] = True
            found = True
        else:
            b.pop("default", None)
    if found:
        save_config(data)
    return found


# ── 配置校验 ──────────────────────────────────────────────────────────────────

# 每种 type 必需的字段
_REQUIRED_FIELDS: Dict[str, List[str]] = {
    "xtquant":    ["account_id"],
    "easytrader": ["broker_name"],
    "futu":       ["host", "port"],           # FutuOpenD must be running locally
    "tiger":      ["tiger_id", "private_key_path", "account"],
    "longbridge": ["app_key", "app_secret", "access_token"],
    "ibkr":       ["host", "port"],           # TWS/Gateway host:port required
    "alpaca":     ["api_key", "api_secret"],
    "webull":     ["username", "password"],   # read-only, still needs credentials
}

_TYPE_LABELS: Dict[str, str] = {
    "xtquant":    "迅投 XTQuant（中信/华鑫/浙商等）",
    "easytrader": "EasyTrader（同花顺/通达信/华泰/国君）",
    "futu":       "富途牛牛 OpenAPI",
    "tiger":      "老虎证券 OpenAPI",
    "longbridge": "长桥证券 OpenAPI",
    "ibkr":       "Interactive Brokers TWS/Gateway",
    "alpaca":     "Alpaca Markets（美股 / 模拟盘）",
    "webull":     "Webull（美股）",
}


_RECOMMENDED_FIELDS: Dict[str, List[str]] = {
    "futu":   ["market"],
    "ibkr":   ["client_id"],
    "alpaca": ["paper"],
}


def validate_broker_config(cfg: Dict[str, Any]) -> List[str]:
    """校验券商配置，返回错误列表（空列表=通过）。

    Warnings (prefixed with '⚠ ') are non-fatal but worth showing.
    """
    errors: List[str] = []
    if not cfg.get("id"):
        errors.append("缺少 'id' 字段")
    if not cfg.get("label"):
        errors.append("⚠ 建议设置 'label' 字段（账户显示名称）")
    broker_type = cfg.get("type", "")
    if not broker_type:
        errors.append("缺少 'type' 字段")
    elif broker_type not in _REQUIRED_FIELDS:
        errors.append(f"不支持的 type: {broker_type!r}  (支持: {', '.join(_REQUIRED_FIELDS)})")
    else:
        for req in _REQUIRED_FIELDS[broker_type]:
            if not cfg.get(req):
                errors.append(f"{broker_type} 缺少必需字段: '{req}'")
        for rec in _RECOMMENDED_FIELDS.get(broker_type, []):
            if not cfg.get(rec):
                errors.append(f"⚠ {broker_type} 建议设置 '{rec}' 字段")
    return errors


def supported_broker_types() -> Dict[str, str]:
    """返回所有支持的券商类型 {type: 描述}。"""
    return dict(_TYPE_LABELS)


# ── 生成配置模板 ──────────────────────────────────────────────────────────────

_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "xtquant": {
        "id": "xt_main",
        "type": "xtquant",
        "label": "中信主账户",
        "account_id": "YOUR_ACCOUNT_ID",
        "default": True,
        "_comment": "需安装 xtquant: pip install xtquant  (仅 Windows/Linux)"
    },
    "easytrader": {
        "id": "ht_main",
        "type": "easytrader",
        "label": "华泰账户",
        "broker_name": "huatai",
        "exe_path": "C:\\华泰证券\\xiadan.exe",
        "_comment": "broker_name 可选: huatai/guojun/ths/tdx/yh/zszq/xq"
    },
    "futu": {
        "id": "futu_hk",
        "type": "futu",
        "label": "富途港股",
        "host": "127.0.0.1",
        "port": 11111,
        "market": "HK",
        "_comment": "需安装 futu-api: pip install futu-api  并启动富途 OpenD"
    },
    "tiger": {
        "id": "tiger_us",
        "type": "tiger",
        "label": "老虎美股",
        "tiger_id": "YOUR_TIGER_ID",
        "private_key_path": "~/.aria-code/tiger_rsa.pem",
        "account": "YOUR_ACCOUNT",
        "_comment": "需安装 tigeropen: pip install tigeropen"
    },
    "longbridge": {
        "id": "lb_cn",
        "type": "longbridge",
        "label": "长桥A股",
        "app_key": "YOUR_APP_KEY",
        "app_secret": "YOUR_APP_SECRET",
        "access_token": "YOUR_ACCESS_TOKEN",
        "_comment": "需安装 longbridge: pip install longbridge"
    },
    "ibkr": {
        "id": "ibkr_us",
        "type": "ibkr",
        "label": "盈透美股",
        "host": "127.0.0.1",
        "port": 7496,
        "client_id": 1,
        "_comment": "需安装 ib_insync: pip install ib_insync  并启动 TWS/Gateway"
    },
    "alpaca": {
        "id": "alpaca_paper",
        "type": "alpaca",
        "label": "Alpaca 模拟盘",
        "api_key": "YOUR_API_KEY",
        "api_secret": "YOUR_API_SECRET",
        "paper": True,
        "_comment": "需安装 alpaca-py: pip install alpaca-py"
    },
    "webull": {
        "id": "webull_us",
        "type": "webull",
        "label": "Webull 美股",
        "username": "YOUR_EMAIL_OR_PHONE",
        "password": "YOUR_PASSWORD",
        "device_id": "",
        "_comment": "需安装 webull: pip install webull"
    },
}


def get_config_template(broker_type: str) -> Optional[Dict[str, Any]]:
    """返回指定券商类型的配置模板。"""
    import copy
    tmpl = _TEMPLATES.get(broker_type)
    return copy.deepcopy(tmpl) if tmpl else None


def print_all_templates() -> str:
    """返回所有配置模板的 JSON 字符串（用于 /broker init 命令）。"""
    return json.dumps({"brokers": list(_TEMPLATES.values())}, ensure_ascii=False, indent=2)
