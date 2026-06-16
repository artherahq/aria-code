#!/usr/bin/env python3
# ruff: noqa: E501
"""
Aria Code v3.0 — Claude Code 风格的量化投资终端 + 编程代理

Features:
  - SSE 流式 AI 对话 + Ollama 本地回退 (支持工具调用)
  - 本地工具系统: read_file, write_file, edit_file, list_files, search_code, run_command
  - Agentic 工具循环: AI 自动读取→分析→编辑→执行 (最多 8 轮)
  - 22 个远程 Aria 工具 + 6 个本地工具 + 15 个 Skills + 30 个 Slash 命令
  - Tab 补全, Rich Syntax 高亮, ESC 取消流式, Ctrl+D 退出
  - 会话管理 (保存/加载/恢复/导出)
  - 用户认证 + 上下文注入 + 反馈机制

Usage:
    aria-code                                          # 交互式 REPL（推荐）
    aria-code --resume                                 # 恢复上次会话
    aria-code -p "分析AAPL的技术面"                      # 单次查询
    aria-code quote AAPL MSFT                          # 快速报价
    python3 apps/cli/aria_cli.py                         # 交互式 REPL
    python3 apps/cli/aria_cli.py --resume                # 恢复上次会话
    python3 apps/cli/aria_cli.py -p "分析AAPL的技术面"     # 单次查询
    python3 apps/cli/aria_cli.py -p "写一个动量策略"       # AI 自动生成代码并保存
    python3 apps/cli/aria_cli.py quote AAPL MSFT          # 快速报价
    python3 apps/cli/aria_cli.py backtest momentum SPY    # 策略回测
    python3 apps/cli/aria_cli.py -p "AAPL PE" --json     # JSON 输出
"""

__version__ = "4.0.0"

import sys
import os
import asyncio
import json
import argparse
import readline
import logging
import time
import shlex
import pathlib
import signal
import uuid
import threading
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable
from change_store import ChangeConflictError, GLOBAL_CHANGE_STORE
from safety import evaluate_command_policy
from plan_utils import parse_plan_steps
from privacy import FeedbackRecord, FeedbackStore, PrivacySettings
from runtime import (
    AgentErrorPresentation,
    AgentTurnState,
    ApprovalDecision,
    RuntimeTrace,
    ToolTurnPlan,
    ToolExecutor,
    apply_approval_decision,
    detect_task_complete,
    run_parallel_tools,
    run_serial_tool,
)
from workspace import VerificationPlanner, WorkspaceFiles, WorkspaceSecurity
from apps.cli.commands.catalog import VISIBLE_SLASH_COMMANDS
from apps.cli.commands.market_context import build_analyze_context, build_analyze_prompt
from apps.cli.commands.market import parse_symbols, parse_technical_args, try_top_level_route
from ui.render.market import print_quote_result, print_ta_result
from apps.cli.commands.report import (
    all_agents_failed,
    build_markdown_report_prompt,
    export_report_pdf,
    generate_html_report,
    parse_report_args,
    report_agent_names,
    report_file_size_kb,
    save_markdown_report,
    update_report_index,
)
from apps.cli.commands.team import (
    parse_team_args,
    resolve_team_symbols,
    run_team_analysis,
    save_team_report,
    team_agent_names,
)
from ui.render.team import (
    VERDICT_STYLE,
    build_team_table_rows,
    calc_column_widths,
    render_team_rows_plain,
    render_team_table,
    render_verdict_banner,
    team_mode_label,
)
from ui.render.finance import (
    render_finance_result,
    render_macro_result,
    render_cb_rates,
    render_econ_calendar,
    render_options_chain,
    render_quality_scores,
    render_ichimoku,
    render_fear_greed,
    render_funding_rates,
    render_peer_comparison,
    render_house_price,
    render_reits_list,
    render_rental_yield,
    render_property_val,
    render_multi_city,
    render_asset_score,
    render_corr_matrix,
    render_portfolio_bt,
    render_sql_result,
    render_alerts,
)
from apps.cli.direct import dispatch_direct_command, is_watchable_direct_command
from apps.cli.tools.system_tools import (
    tool_run_command as _src_run_command,
    tool_web_fetch   as _src_web_fetch,
    tool_github      as _src_github,
)
from apps.cli.tools.notebook_tools import (
    tool_glob          as _src_glob,
    tool_notebook_read as _src_notebook_read,
    tool_notebook_edit as _src_notebook_edit,
)
from apps.cli.tools.market_tools import (
    tool_get_market_data as _src_get_market_data,
    tool_broker_query    as _src_broker_query,
    tool_broker_order    as _src_broker_order,
)
from apps.cli.handlers.broker_handlers import handle_broker_query as _src_handle_broker_query
from apps.cli.handlers.realty_handlers import handle_realty_query as _src_handle_realty_query
from apps.cli.handlers.chart_handlers import (
    handle_stock_chart_analysis_direct as _src_chart_analysis_direct,
    handle_stock_chart_analysis        as _src_chart_analysis,
)
from apps.cli.utils.market_detect import (  # noqa: F401 — re-exported
    _re_sym, _STOCK_PATTERN,
    _CRYPTO_WORDS, _COMPANY_TO_TICKER,
    _BROKER_INTENT_KW, _is_broker_intent,
    _is_broker_setup_intent, _detect_broker_type,
    _FINANCIAL_TERMS_BLOCKLIST,
    _extract_market_symbol, _extract_market_symbols, _extract_symbol_from_history,
    _is_stock_chart_analysis_request,
    _UNRESOLVED_CO_INDICATORS, _has_unresolved_company_mention,
    _REALTY_QUERY_KEYWORDS, _CN_CITIES, _INTL_CITIES, _STOCK_ONLY_MARKET_WORDS,
    _is_realty_query,
    _is_market_snapshot_request,
    _format_compact_market_cap, _market_snapshot_trend,
)

from apps.cli.commands.broker_cmds import BrokerCommandsMixin
from apps.cli.commands.backtest_cmds import BacktestCommandsMixin
from apps.cli.commands.workspace_cmds import WorkspaceCommandsMixin
from apps.cli.commands.model_cmds import ModelCommandsMixin
from apps.cli.commands.market_cmds import MarketCommandsMixin
from apps.cli.commands.portfolio_cmds import PortfolioCommandsMixin
from apps.cli.handlers.market_handlers import (
    _try_prefetch_market_data  as _src_prefetch_market_data,
    _try_handle_multi_market_snapshot  as _src_multi_snapshot,
    _try_handle_market_snapshot_analysis  as _src_market_snapshot_analysis,
)


# ── New modules: local LLM provider stack, finance tools, MCP, ariarc ──────
try:
    from model_capability import (
        get_model_capability, build_tool_system_prompt,
        RECOMMENDED_FINANCE_MODELS, parse_tool_calls_from_response as _parse_model_tool_calls,
    )
    _HAS_MODEL_CAP = True
except ImportError:
    _HAS_MODEL_CAP = False

try:
    from local_finance_tools import register_local_finance_tools
    _HAS_LOCAL_FINANCE = True
except ImportError:
    _HAS_LOCAL_FINANCE = False

try:
    from market_data_client import MarketDataClient as _MDC, get_mdc as _get_mdc
    _HAS_MDC = True
except ImportError:
    _HAS_MDC = False

# Session-level TA cache: persists across multiple /analyze calls in a session,
# so a single yfinance rate-limit hit doesn't wipe all indicator data.
# Structure: {symbol: {"data": <ti_dict>, "ts": float}}
_TA_SESSION_CACHE: dict = {}
_TA_SESSION_CACHE_TTL = 600  # 10 minutes

try:
    from financial_agents import run_team_analysis as _run_team
    _HAS_AGENTS = True
except ImportError:
    _HAS_AGENTS = False

try:
    from strategy_vault import get_vault as _get_vault, ai_review_strategy as _ai_review
    _HAS_VAULT = True
except ImportError:
    _HAS_VAULT = False

try:
    from mcp_client import MCPToolRegistry, MCP_CONFIG_PATH, init_mcp as _init_mcp
    _HAS_MCP = True
    _mcp_registry: Optional["MCPToolRegistry"] = None
except ImportError:
    _HAS_MCP = False
    _mcp_registry = None

try:
    from ariarc import AriaRC, get_ariarc, reload_ariarc
    _HAS_ARIARC = True
except ImportError:
    _HAS_ARIARC = False

try:
    from brokers import (
        get_registry as _get_broker_registry,
        list_broker_configs as _list_broker_configs,
        get_broker_config as _get_broker_cfg,
        add_broker_config as _add_broker_cfg,
        remove_broker_config as _remove_broker_cfg,
        set_default_broker as _set_default_broker,
        validate_broker_config as _validate_broker_cfg,
        supported_broker_types as _supported_broker_types,
        get_config_template as _get_broker_template,
        BROKERS_CONFIG_PATH as _BROKERS_CONFIG_PATH,
    )
    _HAS_BROKERS = True
except ImportError:
    _HAS_BROKERS = False
    def _get_broker_registry(): return None   # type: ignore
    def _list_broker_configs(): return []      # type: ignore
    _BROKERS_CONFIG_PATH = None

try:
    from plugin_loader import register_plugin_tools, find_plugin_file, PluginWatcher
    _HAS_PLUGIN = True
    _plugin_watcher: Optional["PluginWatcher"] = None
except ImportError:
    _HAS_PLUGIN = False
    _plugin_watcher = None

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("curl_cffi").setLevel(logging.CRITICAL)

# ============================================================================
# Rich Console (graceful fallback to ANSI if not installed)
# ============================================================================

# ── UI layer — console, flags, ESC watcher ────────────────────────────────────
from ui.console import (
    console, HAS_RICH, HAS_PT, _SYNTAX_THEME,
    _EscWatcher, _esc_watcher, _HAS_TERMIOS,
)
from ui.robot import RobotState, set_robot_state
# Rich re-exports (used directly in this file)
if HAS_RICH:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.live import Live
    from rich.text import Text
    from rich.status import Status
    from rich.syntax import Syntax
    from rich.panel import Panel
    from rich.rule import Rule
    from rich import box as rich_box
# prompt_toolkit re-exports
if HAS_PT:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings as _PTKeyBindings
# termios — already imported inside ui.console; alias for local use
if _HAS_TERMIOS:
    import termios, tty, select as _select


from ui.picker import arrow_select as _arrow_select, run_picker_in_thread as _run_picker_in_thread



# ============================================================================
# Configuration & Persistent Memory
# ============================================================================

def _resolve_config_dir() -> pathlib.Path:
    """Resolve the user config directory.

    Priority:
      1. ARIA_HOME environment variable (explicit override)
      2. ~/.arthera  — legacy path, kept for backward compat if it exists
      3. ~/.aria-code — new default for fresh installs
    """
    if "ARIA_HOME" in os.environ:
        return pathlib.Path(os.environ["ARIA_HOME"]).expanduser()
    legacy = pathlib.Path.home() / ".arthera"
    if legacy.exists():
        return legacy
    return pathlib.Path.home() / ".aria-code"

CONFIG_DIR = _resolve_config_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"
HISTORY_FILE = CONFIG_DIR / "history"
SESSIONS_DIR = CONFIG_DIR / "sessions"
PROVIDERS_FILE = CONFIG_DIR / "providers.json"  # Cloud API keys (Open Interpreter style)

# ── Cloud Provider key map ───────────────────────────────────────────────────
# Maps provider short name → environment variable name for API key.
_PROVIDER_KEY_MAP: Dict[str, str] = {
    # ── 国际主流 ──────────────────────────────────────────────────────
    "deepseek":    "DEEPSEEK_API_KEY",
    "openai":      "OPENAI_API_KEY",
    "anthropic":   "ANTHROPIC_API_KEY",
    "claude":      "ANTHROPIC_API_KEY",
    "groq":        "GROQ_API_KEY",
    "together":    "TOGETHER_API_KEY",
    "google":      "GOOGLE_API_KEY",
    "gemini":      "GOOGLE_API_KEY",        # alias
    "xai":         "XAI_API_KEY",
    "grok":        "XAI_API_KEY",           # alias
    "mistral":     "MISTRAL_API_KEY",
    "cohere":      "COHERE_API_KEY",
    "perplexity":  "PERPLEXITY_API_KEY",
    # ── 国内主流 ──────────────────────────────────────────────────────
    "dashscope":   "DASHSCOPE_API_KEY",
    "aliyun":      "DASHSCOPE_API_KEY",     # alias
    "siliconflow": "SILICONFLOW_API_KEY",
    "moonshot":    "MOONSHOT_API_KEY",
    "zhipu":       "ZHIPUAI_API_KEY",
    "glm":         "ZHIPUAI_API_KEY",       # alias
    "baidu":       "QIANFAN_ACCESS_KEY",
    "ernie":       "QIANFAN_ACCESS_KEY",    # alias
    "qianfan":     "QIANFAN_ACCESS_KEY",    # alias
    "bytedance":   "ARK_API_KEY",
    "doubao":      "ARK_API_KEY",           # alias
    "ark":         "ARK_API_KEY",           # alias
    "minimax":     "MINIMAX_API_KEY",
    "stepfun":     "STEPFUN_API_KEY",
    "01ai":        "ONEAI_API_KEY",
    "yi":          "ONEAI_API_KEY",         # alias
}

# Default base URLs for cloud providers (OpenAI-compatible unless noted)
_PROVIDER_BASE_URLS: Dict[str, str] = {
    # ── 国际主流 ──────────────────────────────────────────────────────
    "deepseek":    "https://api.deepseek.com",
    "openai":      "https://api.openai.com",
    "anthropic":   "https://api.anthropic.com",
    "claude":      "https://api.anthropic.com",
    "groq":        "https://api.groq.com/openai",
    "together":    "https://api.together.xyz",
    "google":      "https://generativelanguage.googleapis.com/v1beta/openai",
    "gemini":      "https://generativelanguage.googleapis.com/v1beta/openai",
    "xai":         "https://api.x.ai/v1",
    "grok":        "https://api.x.ai/v1",
    "mistral":     "https://api.mistral.ai/v1",
    "cohere":      "https://api.cohere.ai/compatibility/v1",
    "perplexity":  "https://api.perplexity.ai",
    # ── 国内主流 ──────────────────────────────────────────────────────
    "dashscope":   "https://dashscope.aliyuncs.com/compatible-mode",
    "aliyun":      "https://dashscope.aliyuncs.com/compatible-mode",
    "siliconflow": "https://api.siliconflow.cn",
    "moonshot":    "https://api.moonshot.cn/v1",
    "zhipu":       "https://open.bigmodel.cn/api/paas/v4",
    "glm":         "https://open.bigmodel.cn/api/paas/v4",
    "baidu":       "https://qianfan.baidubce.com/v2",
    "ernie":       "https://qianfan.baidubce.com/v2",
    "qianfan":     "https://qianfan.baidubce.com/v2",
    "bytedance":   "https://ark.cn-beijing.volces.com/api/v3",
    "doubao":      "https://ark.cn-beijing.volces.com/api/v3",
    "ark":         "https://ark.cn-beijing.volces.com/api/v3",
    "minimax":     "https://api.minimax.chat/v1",
    "stepfun":     "https://api.stepfun.com/v1",
    "01ai":        "https://api.lingyiwanwu.com/v1",
    "yi":          "https://api.lingyiwanwu.com/v1",
}


# ── Data / Market Service key map ────────────────────────────────────────────
# Maps service short name → environment variable name for API key.
# When the Arthera backend (Alibaba Cloud) is offline, these are used directly.
_DATA_KEY_MAP: Dict[str, str] = {
    "finnhub":      "FINNHUB_API_KEY",       # Real-time stock data + news (free tier: 60/min)
    "newsapi":      "NEWS_API_KEY",           # Financial news aggregator (free: 100/day)
    "brave":        "BRAVE_SEARCH_API_KEY",   # Web search (free: 2000/month)
    "tavily":       "TAVILY_API_KEY",         # AI-optimised web search (free: 1000/month)
    "coingecko":    "COINGECKO_API_KEY",      # Crypto data Pro (basic tier is free)
    "alphavantage": "ALPHA_VANTAGE_API_KEY",  # Stock history (free: 25/day)
    "polygon":      "POLYGON_API_KEY",        # US market data (free tier available)
    "fmp":          "FMP_API_KEY",            # Financial Modeling Prep (free tier)
    "twelvedata":   "TWELVEDATA_API_KEY",     # Global market data (free: 800/day)
}

# Registration / signup URLs for each data service
_DATA_SIGNUP_URLS: Dict[str, str] = {
    "finnhub":      "https://finnhub.io/register",
    "newsapi":      "https://newsapi.org/register",
    "brave":        "https://api.search.brave.com/app/keys",
    "tavily":       "https://app.tavily.com",
    "coingecko":    "https://www.coingecko.com/en/api",
    "alphavantage": "https://www.alphavantage.co/support/#api-key",
    "polygon":      "https://polygon.io/signup",
    "fmp":          "https://financialmodelingprep.com/register",
    "twelvedata":   "https://twelvedata.com/register",
}

# LLM provider signup URLs
_LLM_SIGNUP_URLS: Dict[str, str] = {
    # ── 国际主流 ──────────────────────────────────────────────────────
    "deepseek":    "https://platform.deepseek.com/api_keys",
    "openai":      "https://platform.openai.com/api-keys",
    "anthropic":   "https://console.anthropic.com/settings/keys",
    "claude":      "https://console.anthropic.com/settings/keys",
    "groq":        "https://console.groq.com/keys",
    "together":    "https://api.together.ai/settings/api-keys",
    "google":      "https://aistudio.google.com/app/apikey",
    "gemini":      "https://aistudio.google.com/app/apikey",
    "xai":         "https://console.x.ai",
    "grok":        "https://console.x.ai",
    "mistral":     "https://console.mistral.ai/api-keys",
    "cohere":      "https://dashboard.cohere.com/api-keys",
    "perplexity":  "https://www.perplexity.ai/settings/api",
    # ── 国内主流 ──────────────────────────────────────────────────────
    "dashscope":   "https://dashscope.console.aliyun.com/apiKey",
    "aliyun":      "https://dashscope.console.aliyun.com/apiKey",
    "siliconflow": "https://cloud.siliconflow.cn/account/ak",
    "moonshot":    "https://platform.moonshot.cn/console/api-keys",
    "zhipu":       "https://open.bigmodel.cn/usercenter/apikeys",
    "baidu":       "https://qianfan.cloud.baidu.com/user/accessToken",
    "ernie":       "https://qianfan.cloud.baidu.com/user/accessToken",
    "bytedance":   "https://ark.volcengine.com/api-key",
    "doubao":      "https://ark.volcengine.com/api-key",
    "minimax":     "https://platform.minimaxi.com/user-center/basic-information/interface-key",
    "stepfun":     "https://platform.stepfun.com/interface-key",
    "01ai":        "https://platform.lingyiwanwu.com/apikeys",
    "yi":          "https://platform.lingyiwanwu.com/apikeys",
}

# One-line description for each provider (shown in picker)
_PROVIDER_DESC: Dict[str, str] = {
    # ── 国际 LLM ──────────────────────────────────────────────────────
    "deepseek":    "DeepSeek-V3/R1  强推理·中文优秀·价格极低",
    "openai":      "GPT-4.5 / o3 / o4-mini / o3-pro  最广泛兼容·持续更新",
    "anthropic":   "Claude Sonnet 4 / Opus 4  长文档·代码·分析",
    "groq":        "Llama3/Qwen  超高速推理 (免费额度)",
    "together":    "开源模型聚合  Llama/Qwen/DeepSeek 等 100+ 模型",
    "google":      "Gemini 2.5 Pro / 2.0 Flash  多模态·超长上下文",
    "xai":         "Grok-3 / Grok-3-Fast  实时网络数据·Twitter整合",
    "mistral":     "Mistral Large / Codestral  欧洲顶级·代码生成强",
    "cohere":      "Command R+  RAG检索增强·企业文档处理",
    "perplexity":  "Sonar  实时联网搜索·研究报告",
    # ── 国内 LLM ──────────────────────────────────────────────────────
    "dashscope":   "通义千问 Max/Long/Turbo  阿里云·国内访问稳定",
    "siliconflow": "Qwen/GLM/DeepSeek  国内多模型聚合·价格低",
    "moonshot":    "Kimi  超长上下文 128K  中文理解·长文档",
    "zhipu":       "GLM-4-Plus / GLM-4-Flash  中文推理·代码生成",
    "baidu":       "ERNIE 4.5 Turbo  百度文心·国内最强中文",
    "bytedance":   "Doubao / 豆包  字节跳动·多模态·免费额度大",
    "minimax":     "MiniMax-Text-01  百万Token上下文",
    "stepfun":     "Step-2-16K  阶跃星辰·复杂推理",
    "01ai":        "Yi-Large / Yi-Vision  零一万物·中英双语",
    # ── Data ──────────────────────────────────────────────────────────
    "finnhub":     "实时美股行情+新闻  免费 60次/min",
    "alphavantage":"美股历史数据+技术指标  免费 25次/day",
    "polygon":     "美股全量数据+期权链  免费层可用",
    "fmp":         "财务报表+估值数据  免费层",
    "twelvedata":  "全球行情  A股/港股/美股  免费 800次/day",
    "newsapi":     "全球新闻聚合  免费 100次/day",
    "coingecko":   "加密货币行情+项目数据  基础免费",
    "tavily":      "AI搜索引擎  1000次/month 免费",
    "brave":       "网页搜索  2000次/month 免费",
}

# Detailed guide: where to get key + what it unlocks (shown in Panel before input)
_PROVIDER_GUIDE: Dict[str, str] = {
    "deepseek": (
        "1. 打开 platform.deepseek.com/api_keys\n"
        "2. 注册/登录 → 点击「创建 API Key」\n"
        "3. 复制 sk-xxxxxxxx 格式的密钥\n\n"
        "解锁: DeepSeek-V3 (最强中文推理) · DeepSeek-R1 (CoT思维链)\n"
        "价格: V3 约 ¥1/百万 token，远低于 GPT-4o"
    ),
    "openai": (
        "1. 打开 platform.openai.com/api-keys\n"
        "2. 登录 → 「Create new secret key」\n"
        "3. 复制 sk-proj-xxxxxxxx 格式密钥\n\n"
        "解锁: GPT-4o / GPT-4o-mini / o1 / o1-mini\n"
        "注意: 需绑定付款方式才能使用 GPT-4o"
    ),
    "anthropic": (
        "1. 打开 console.anthropic.com/settings/keys\n"
        "2. 登录 → 「Create Key」\n"
        "3. 复制 sk-ant-xxxxxxxx 格式密钥\n\n"
        "解锁: Claude Sonnet 4 · Claude Opus · Claude Haiku\n"
        "优势: 200K上下文·长文档分析·代码审查最强"
    ),
    "groq": (
        "1. 打开 console.groq.com/keys\n"
        "2. 登录 → 「Create API Key」\n"
        "3. 复制 gsk_xxxxxxxx 格式密钥\n\n"
        "解锁: Llama3-70B · Mixtral · Gemma\n"
        "优势: 每秒 500+ tokens，目前最快的免费推理"
    ),
    "together": (
        "1. 打开 api.together.ai/settings/api-keys\n"
        "2. 注册 → 「Create API Key」\n"
        "3. 复制 xxxxxxxx 格式密钥\n\n"
        "解锁: Llama3/Qwen/DeepSeek/Yi 等 100+ 开源模型\n"
        "新用户赠 $5 免费额度"
    ),
    "dashscope": (
        "1. 打开 dashscope.console.aliyun.com/apiKey\n"
        "2. 用阿里云账号登录\n"
        "3. 点击「创建新的 API-KEY」\n\n"
        "解锁: 通义千问2.5 / 通义千问Max / 通义千问Long\n"
        "优势: 国内访问无需代理，中文理解优秀"
    ),
    "siliconflow": (
        "1. 打开 cloud.siliconflow.cn/account/ak\n"
        "2. 注册/登录 → 「创建 API Key」\n"
        "3. 复制密钥\n\n"
        "解锁: Qwen2.5 / GLM-4 / DeepSeek / Yi 等\n"
        "新用户赠 14元免费额度，国内直连"
    ),
    "moonshot": (
        "1. 打开 platform.moonshot.cn/console/api-keys\n"
        "2. 注册/登录 → 「新建 API Key」\n"
        "3. 复制 sk-xxxxxxxx 格式密钥\n\n"
        "解锁: Kimi (moonshot-v1-8k/32k/128k)\n"
        "优势: 128K超长上下文，处理长文档首选"
    ),
    "zhipu": (
        "1. 打开 open.bigmodel.cn/usercenter/apikeys\n"
        "2. 注册/登录 → 「添加新的 API Key」\n"
        "3. 复制密钥\n\n"
        "解锁: GLM-4 / GLM-4-Flash / GLM-4V (多模态)\n"
        "GLM-4-Flash 速度快，新用户有免费额度"
    ),
    # Data services
    "finnhub": (
        "1. 打开 finnhub.io/register 注册\n"
        "2. 进入 Dashboard → 复制 API Key\n\n"
        "解锁: 美股实时报价 · 公司新闻 · 基本面数据\n"
        "免费额度: 60次/分钟，足够个人使用"
    ),
    "alphavantage": (
        "1. 打开 alphavantage.co/support/#api-key\n"
        "2. 填写邮箱 → 即时获取 Key\n\n"
        "解锁: 美股日K/周K历史数据 · RSI/MACD等技术指标\n"
        "免费额度: 25次/天，500次/月"
    ),
    "polygon": (
        "1. 打开 polygon.io/signup 注册\n"
        "2. Dashboard → API Keys → 复制 Key\n\n"
        "解锁: 美股实时+历史 · 期权链 · 新闻\n"
        "免费层: 延迟15分钟数据，基础期权数据"
    ),
    "fmp": (
        "1. 打开 financialmodelingprep.com/register\n"
        "2. 注册 → Dashboard → 复制 API Key\n\n"
        "解锁: 财报(资产负债表/利润表) · PE/PB等估值\n"
        "免费层: 250次/天，历史财报数据"
    ),
    "twelvedata": (
        "1. 打开 twelvedata.com/register 注册\n"
        "2. Dashboard → API Keys → 复制\n\n"
        "解锁: 全球行情 (A股/港股/美股/加密/外汇)\n"
        "免费额度: 800次/天，支持日K历史数据"
    ),
    "newsapi": (
        "1. 打开 newsapi.org/register 注册\n"
        "2. 注册后即显示 API Key\n\n"
        "解锁: 全球新闻聚合 · 按股票名称搜索相关新闻\n"
        "免费层: 100次/天（仅限开发者模式）"
    ),
    "coingecko": (
        "1. 打开 coingecko.com/en/api 注册\n"
        "2. 选择 Demo 计划 (免费) → 生成 Key\n\n"
        "解锁: 加密货币实时价格 · 历史数据 · 项目信息\n"
        "Demo Key: 30次/分钟，基础行情足够"
    ),
    "tavily": (
        "1. 打开 app.tavily.com 注册\n"
        "2. 控制台 → 复制 API Key (tvly-xxxxxxxx)\n\n"
        "解锁: AI优化的网页搜索，返回结构化摘要\n"
        "免费额度: 1000次/月"
    ),
    "brave": (
        "1. 打开 api.search.brave.com/app/keys\n"
        "2. 注册 → 「Add Key」→ 选择 Free 计划\n\n"
        "解锁: 网页搜索 (无追踪，隐私优先)\n"
        "免费额度: 2000次/月"
    ),
    # ── 新增国际 Provider ──────────────────────────────────────────────
    "google": (
        "1. 打开 aistudio.google.com/app/apikey\n"
        "2. 用 Google 账号登录 → 「Create API key」\n"
        "3. 复制 AIzaSy... 格式的密钥\n\n"
        "解锁: Gemini 2.5 Pro · Gemini 2.0 Flash · 多模态视觉\n"
        "用法: /model google/gemini-2.0-flash-exp\n"
        "免费额度: Flash 每分钟 15次，每天 1500次"
    ),
    "gemini": (
        "同 google provider，填入 Google AI Studio 的 API Key\n\n"
        "推荐模型:\n"
        "  gemini-2.5-pro        — 最强推理，128K 上下文\n"
        "  gemini-2.0-flash-exp  — 超快，每分钟 15 次免费\n"
        "  gemini-1.5-flash      — 稳定版，适合生产\n\n"
        "用法: /model gemini/gemini-2.5-pro"
    ),
    "xai": (
        "1. 打开 console.x.ai → 注册/登录\n"
        "2. 创建 API Key (xai-...)\n\n"
        "解锁: Grok-3 · Grok-3-Fast · Grok-3-Mini (推理)\n"
        "优势: 实时访问 Twitter/X 数据，最新新闻事件感知\n"
        "用法: /model xai/grok-3\n"
        "价格: Grok-3 $3/M tokens，Fast $5/M tokens"
    ),
    "grok": (
        "同 xai provider，填入 xAI Console 的 API Key\n\n"
        "推荐模型:\n"
        "  grok-3           — 旗舰推理\n"
        "  grok-3-fast      — 高速版\n"
        "  grok-3-mini      — 轻量思考模型\n\n"
        "用法: /model grok/grok-3-fast"
    ),
    "mistral": (
        "1. 打开 console.mistral.ai → 注册\n"
        "2. 「API Keys」→ 「Create new key」\n"
        "3. 复制密钥\n\n"
        "解锁: Mistral Large 2 · Mistral Small · Codestral (代码)\n"
        "用法: /model mistral/mistral-large-latest\n"
        "优势: 欧洲 GDPR 合规，Codestral 为代码生成最强之一\n"
        "免费额度: 新用户有试用额度"
    ),
    "cohere": (
        "1. 打开 dashboard.cohere.com → 注册\n"
        "2. 「API Keys」→ 复制 Trial key\n\n"
        "解锁: Command R+ · Command R · Embed · Rerank\n"
        "用法: /model cohere/command-r-plus\n"
        "优势: RAG 检索增强最强，企业文档处理首选\n"
        "Trial Key: 免费可用，速率限制较低"
    ),
    "perplexity": (
        "1. 打开 perplexity.ai/settings/api → 注册\n"
        "2. 「Generate」→ 复制 pplx-... 密钥\n\n"
        "解锁: sonar · sonar-pro · sonar-reasoning (联网推理)\n"
        "用法: /model perplexity/sonar-pro\n"
        "优势: 实时联网，自动引用来源，研究报告首选\n"
        "价格: sonar $1/M tokens，sonar-pro $3/M tokens"
    ),
    # ── 新增国内 Provider ──────────────────────────────────────────────
    "baidu": (
        "1. 打开 qianfan.cloud.baidu.com → 用百度账号登录\n"
        "2. 「用户中心」→「Access Token」→ 记录 Key 和 Secret\n\n"
        "解锁: ERNIE 4.5 Turbo · ERNIE Speed · ERNIE-Lite\n"
        "用法: /model baidu/ernie-4.5-turbo-128k\n"
        "优势: 国内最强中文理解，百度搜索知识整合\n"
        "免费额度: ERNIE Speed/Lite 大量免费 Token"
    ),
    "ernie": (
        "同 baidu provider，填入百度千帆平台的 Access Key\n\n"
        "推荐模型:\n"
        "  ernie-4.5-turbo-128k  — 旗舰，128K 上下文\n"
        "  ernie-speed-128k      — 高速，大量免费\n"
        "  ernie-lite-8k         — 轻量，免费额度最大\n\n"
        "用法: /model ernie/ernie-4.5-turbo-128k"
    ),
    "bytedance": (
        "1. 打开 ark.volcengine.com → 注册字节跳动账号\n"
        "2. 「API Key 管理」→ 创建 API Key\n"
        "3. 同时需要创建「推理接入点」获取 endpoint-id\n\n"
        "解锁: Doubao-1.5-Pro · Doubao-1.5-Lite · Doubao Vision\n"
        "用法: /model bytedance/<endpoint-id>\n"
        "优势: 字节跳动首选，多模态，免费额度很大\n"
        "新用户: 500万免费 Token"
    ),
    "doubao": (
        "同 bytedance provider，填入火山方舟 API Key\n\n"
        "推荐模型 (需先在控制台创建接入点):\n"
        "  doubao-1.5-pro-32k     — 旗舰\n"
        "  doubao-1.5-lite-32k    — 轻量快速\n"
        "  doubao-pro-vision-32k  — 多模态\n\n"
        "用法: /model doubao/<你的endpoint-id>"
    ),
    "minimax": (
        "1. 打开 platform.minimaxi.com → 注册\n"
        "2. 「接口密钥」→ 生成 API Key\n\n"
        "解锁: MiniMax-Text-01 (百万 Token 上下文!)\n"
        "用法: /model minimax/MiniMax-Text-01\n"
        "优势: 100万 Token 超长上下文，超长文档/代码库分析首选\n"
        "价格: 约 ¥1/百万 Token"
    ),
    "stepfun": (
        "1. 打开 platform.stepfun.com → 注册\n"
        "2. 「接口密钥」→ 创建 API Key\n\n"
        "解锁: step-2-16k · step-2-mini · step-1v-32k (视觉)\n"
        "用法: /model stepfun/step-2-16k\n"
        "优势: 阶跃星辰，数理逻辑和推理能力突出\n"
        "新用户: 有免费额度"
    ),
    "01ai": (
        "1. 打开 platform.lingyiwanwu.com → 注册\n"
        "2. 「API Keys」→ 创建密钥\n\n"
        "解锁: yi-large · yi-medium · yi-vision\n"
        "用法: /model 01ai/yi-large\n"
        "优势: 零一万物，中英双语均衡，视觉理解能力强"
    ),
    "yi": (
        "同 01ai provider，填入零一万物平台的 API Key\n\n"
        "推荐模型:\n"
        "  yi-large         — 旗舰推理\n"
        "  yi-medium        — 速度/质量均衡\n"
        "  yi-vision        — 图像理解\n\n"
        "用法: /model yi/yi-large"
    ),
}


def _test_api_key(provider: str, key: str) -> tuple:
    """Test if an API key is valid. Returns (ok: bool, message: str)."""
    import urllib.request as _ur
    import urllib.error as _ue
    import json as _json

    provider = provider.lower()

    try:
        # ── Anthropic (different auth scheme) ────────────────────────────────
        if provider in ("anthropic", "claude"):
            req = _ur.Request(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                },
            )
            with _ur.urlopen(req, timeout=8) as r:
                return True, f"✅ Anthropic  HTTP {r.status}  key 有效"

        # ── ZhiPu (JWT-based, just try /v1/models) ───────────────────────────
        if provider == "zhipu":
            base = _PROVIDER_BASE_URLS.get("zhipu", "https://open.bigmodel.cn/api/paas/v4")
            req = _ur.Request(
                base.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            try:
                with _ur.urlopen(req, timeout=8) as r:
                    return True, f"✅ 智谱 GLM  HTTP {r.status}  key 有效"
            except _ue.HTTPError as e:
                if e.code in (401, 403):
                    return False, f"❌ 智谱 GLM  HTTP {e.code}  key 无效"
                return True, f"✅ 智谱 GLM  HTTP {e.code}  可连接"

        # ── Standard OpenAI-compat LLM providers ─────────────────────────────
        if provider in _PROVIDER_BASE_URLS:
            base = _PROVIDER_BASE_URLS[provider].rstrip("/")
            # Avoid double /v1 when base already ends with /v1 or /v2 etc.
            if base.endswith(("/v1", "/v2", "/v3", "/v4", "/openai")):
                url = base + "/models"
            else:
                url = base + "/v1/models"
            req = _ur.Request(url, headers={"Authorization": f"Bearer {key}"})
            try:
                with _ur.urlopen(req, timeout=8) as r:
                    return True, f"✅ {provider.capitalize()}  HTTP {r.status}  key 有效"
            except _ue.HTTPError as e:
                if e.code in (401, 403):
                    return False, f"❌ {provider.capitalize()}  HTTP {e.code}  key 无效或已过期"
                return True, f"✅ {provider.capitalize()}  HTTP {e.code}  可连接"

        # ── Data services ─────────────────────────────────────────────────────
        if provider == "finnhub":
            url = f"https://finnhub.io/api/v1/quote?symbol=AAPL&token={key}"
            req = _ur.Request(url, headers={"User-Agent": "aria-code/1.0"})
            with _ur.urlopen(req, timeout=8) as r:
                body = _json.loads(r.read())
                if body.get("error"):
                    return False, f"❌ Finnhub  error: {body['error']}"
                price = body.get("c", "?")
                return True, f"✅ Finnhub  AAPL现价 ${price}  key 有效"

        if provider == "alphavantage":
            url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=AAPL&apikey={key}"
            req = _ur.Request(url, headers={"User-Agent": "aria-code/1.0"})
            with _ur.urlopen(req, timeout=10) as r:
                body = _json.loads(r.read())
                if "Information" in body:
                    return False, f"❌ Alpha Vantage  超出频率限制或 key 无效"
                if "Global Quote" in body and body["Global Quote"]:
                    price = body["Global Quote"].get("05. price", "?")
                    return True, f"✅ Alpha Vantage  AAPL=${price}  key 有效"
                return False, f"❌ Alpha Vantage  返回异常: {str(body)[:80]}"

        if provider == "polygon":
            url = f"https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/2024-01-09/2024-01-09?adjusted=true&sort=asc&limit=1&apiKey={key}"
            req = _ur.Request(url, headers={"User-Agent": "aria-code/1.0"})
            try:
                with _ur.urlopen(req, timeout=8) as r:
                    body = _json.loads(r.read())
                    if body.get("status") == "OK":
                        return True, f"✅ Polygon  {body.get('resultsCount', 0)} 条数据  key 有效"
                    return False, f"❌ Polygon  {body.get('status', 'unknown')}: {body.get('error', '')}"
            except _ue.HTTPError as e:
                if e.code == 403:
                    return False, f"❌ Polygon  HTTP 403  key 无效"
                return True, f"✅ Polygon  HTTP {e.code}  可连接"

        if provider == "fmp":
            url = f"https://financialmodelingprep.com/api/v3/quote/AAPL?apikey={key}"
            req = _ur.Request(url, headers={"User-Agent": "aria-code/1.0"})
            with _ur.urlopen(req, timeout=8) as r:
                body = _json.loads(r.read())
                if isinstance(body, list) and body:
                    price = body[0].get("price", "?")
                    return True, f"✅ FMP  AAPL=${price}  key 有效"
                if isinstance(body, dict) and "Error Message" in body:
                    return False, f"❌ FMP  {body['Error Message']}"
                return False, f"❌ FMP  返回异常: {str(body)[:80]}"

        if provider == "twelvedata":
            url = f"https://api.twelvedata.com/api_usage?apikey={key}"
            req = _ur.Request(url, headers={"User-Agent": "aria-code/1.0"})
            with _ur.urlopen(req, timeout=8) as r:
                body = _json.loads(r.read())
                if body.get("status") == "error":
                    return False, f"❌ TwelveData  {body.get('message', 'key 无效')}"
                used = body.get("current_usage", {}).get("daily", {}).get("used", "?")
                limit = body.get("current_usage", {}).get("daily", {}).get("limit", "?")
                return True, f"✅ TwelveData  今日已用 {used}/{limit}  key 有效"

        if provider == "newsapi":
            url = f"https://newsapi.org/v2/top-headlines?country=us&pageSize=1&apiKey={key}"
            req = _ur.Request(url, headers={"User-Agent": "aria-code/1.0"})
            try:
                with _ur.urlopen(req, timeout=8) as r:
                    body = _json.loads(r.read())
                    if body.get("status") == "ok":
                        return True, f"✅ NewsAPI  {body.get('totalResults', 0)} 条新闻  key 有效"
                    return False, f"❌ NewsAPI  {body.get('message', 'key 无效')}"
            except _ue.HTTPError as e:
                err_body = _json.loads(e.read().decode()) if e.read else {}
                return False, f"❌ NewsAPI  HTTP {e.code}  {err_body.get('message', '')}"

        if provider == "coingecko":
            url = "https://pro-api.coingecko.com/api/v3/ping"
            req = _ur.Request(url, headers={"x-cg-pro-api-key": key, "User-Agent": "aria-code/1.0"})
            try:
                with _ur.urlopen(req, timeout=8) as r:
                    return True, f"✅ CoinGecko Pro  key 有效"
            except _ue.HTTPError as e:
                if e.code == 401:
                    url2 = f"https://api.coingecko.com/api/v3/ping?x_cg_demo_api_key={key}"
                    req2 = _ur.Request(url2, headers={"User-Agent": "aria-code/1.0"})
                    try:
                        with _ur.urlopen(req2, timeout=8) as r2:
                            return True, f"✅ CoinGecko Demo  key 有效"
                    except Exception:
                        pass
                return False, f"❌ CoinGecko  HTTP {e.code}  key 无效"

        if provider == "tavily":
            import urllib.parse as _up
            data = _json.dumps({"api_key": key, "query": "test", "max_results": 1}).encode()
            req = _ur.Request(
                "https://api.tavily.com/search",
                data=data,
                headers={"Content-Type": "application/json", "User-Agent": "aria-code/1.0"},
            )
            try:
                with _ur.urlopen(req, timeout=10) as r:
                    return True, f"✅ Tavily  HTTP {r.status}  key 有效"
            except _ue.HTTPError as e:
                if e.code == 401:
                    return False, f"❌ Tavily  HTTP 401  key 无效"
                return True, f"✅ Tavily  HTTP {e.code}  可连接"

        if provider == "brave":
            req = _ur.Request(
                "https://api.search.brave.com/res/v1/web/search?q=AAPL&count=1",
                headers={"X-Subscription-Token": key, "User-Agent": "aria-code/1.0"},
            )
            try:
                with _ur.urlopen(req, timeout=8) as r:
                    return True, f"✅ Brave Search  HTTP {r.status}  key 有效"
            except _ue.HTTPError as e:
                if e.code == 401:
                    return False, f"❌ Brave Search  HTTP 401  key 无效"
                return True, f"✅ Brave Search  HTTP {e.code}  可连接"

        return False, f"⚠ 未知 provider '{provider}'，无法测试"

    except _ue.URLError as e:
        return False, f"❌ 网络错误: {e.reason}"
    except Exception as e:
        return False, f"❌ 测试失败: {e}"


def _load_providers_json() -> Dict[str, Any]:
    """Load providers.json from the Aria config dir and return the 'llm' section.

    Returns an empty dict if the file doesn't exist or is malformed.
    """
    try:
        if PROVIDERS_FILE.exists():
            data = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
            return data.get("llm", data) if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save_providers_json(llm_section: Dict[str, Any]) -> None:
    """Persist LLM provider API keys to providers.json in the Aria config dir."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing: Dict = {}
    if PROVIDERS_FILE.exists():
        try:
            existing = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing["llm"] = llm_section
    PROVIDERS_FILE.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def _save_data_key(service: str, key: str) -> None:
    """Persist a data service API key to providers.json under 'data' section."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing: Dict = {}
    if PROVIDERS_FILE.exists():
        try:
            existing = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    data_section = existing.get("data", {})
    data_section[service] = {"api_key": key}
    existing["data"] = data_section
    PROVIDERS_FILE.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_data_keys() -> Dict[str, str]:
    """Return a dict of {service: api_key} for all configured data services.
    Merges environment variables (priority) and providers.json."""
    result: Dict[str, str] = {}
    # 1. Environment variables
    for svc, env_var in _DATA_KEY_MAP.items():
        val = os.getenv(env_var, "")
        if val:
            result[svc] = val
    # 2. providers.json "data" section
    try:
        if PROVIDERS_FILE.exists():
            raw = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
            for svc, entry in raw.get("data", {}).items():
                if svc not in result and entry.get("api_key"):
                    result[svc] = entry["api_key"]
    except Exception:
        pass
    return result


def _get_provider_key(provider: str) -> str:
    """Return the configured API key for a provider (env var takes priority)."""
    env_var = (_PROVIDER_KEY_MAP.get(provider.lower())
               or _DATA_KEY_MAP.get(provider.lower(), ""))
    if env_var:
        val = os.getenv(env_var, "")
        if val:
            return val
    # Check providers.json under both "llm" and "data" sections
    try:
        if PROVIDERS_FILE.exists():
            raw = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
            for section in ("llm", "data"):
                entry = raw.get(section, {}).get(provider.lower(), {})
                if entry.get("api_key"):
                    return entry["api_key"]
    except Exception:
        pass
    return ""

DEFAULT_CONFIG = {
    "api_url": os.getenv(
        "ARTHERA_API_URL",
        "http://localhost:8000"  # 直接运行时用 8000；Docker 模式设 ARTHERA_API_URL=http://localhost:8100
    ),
    "local_url": "http://localhost:8000",  # quant engine is the unified service
    "ollama_url": os.getenv("OLLAMA_URL", "http://localhost:11434"),
    "model": "qwen2.5-coder:1.5b",  # smallest available local model; upgrade chain handles coding tasks
    "thinking_mode": "auto",
    "watchlist": ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA"],
    "auth_token": None,
    "user_id": None,
    "last_session_id": None,
    "auto_save_sessions": True,
    "command_policy": "safe",   # safe | balanced | full
    "permission_mode": "workspace-write",  # read-only | workspace-write | full-access
    "network_enabled": True,
    "data_sharing": False,
    "feedback_upload": False,
    "write_policy": "desktop_only",  # desktop_only | confirm_outside | always_confirm
    "input_style": "panel",    # panel | box | plain
    "input_theme": "auto",     # auto | dark | light
    "local_mode": False,        # True = skip AWS, always use Ollama
    "conversation_history": [],
    "ui_lang": "",              # "" = auto-detect from OS locale on first run; "zh" | "en"
}

# Module-level write/command policies — updated whenever config is loaded/changed.
# Used by standalone tool functions without terminal access.
_ACTIVE_WRITE_POLICY = ["desktop_only"]  # list so closures can mutate it
_ACTIVE_COMMAND_POLICY = ["safe"]
_ACTIVE_PERMISSION_MODE = ["workspace-write"]
_PERMISSION_CYCLE = ["read-only", "workspace-write", "full-access"]
_ACTIVE_NETWORK_ENABLED = [True]


def _sync_write_policy(config: dict):
    """Sync module-level write/command policies from config dict."""
    _ACTIVE_WRITE_POLICY[0] = config.get("write_policy", "desktop_only")
    _ACTIVE_COMMAND_POLICY[0] = config.get("command_policy", "safe")
    _ACTIVE_PERMISSION_MODE[0] = config.get("permission_mode", "workspace-write")
    _ACTIVE_NETWORK_ENABLED[0] = bool(config.get("network_enabled", True))


def _run_event_hook(event: str, env_extra: dict = None):
    """Execute hook scripts for a given lifecycle event.

    Looks in ~/.arthera/hooks/<event>.sh and .aria/hooks/<event>.sh.
    Passes ARIA_* env vars to the script. Silently skips if not found.
    Inspired by Claude Code's hooks system (PreToolUse / PostToolUse / etc.).

    Events: prompt_submit, response_done, compact, session_start, session_end
    """
    import subprocess as _sp, os as _os
    dirs = [
        CONFIG_DIR / "hooks",
        pathlib.Path.cwd() / ".aria" / "hooks",
    ]
    env = dict(_os.environ)
    env["ARIA_EVENT"] = event
    if env_extra:
        env.update(env_extra)
    for hdir in dirs:
        script = hdir / f"{event}.sh"
        if script.exists() and script.stat().st_size > 0:
            try:
                _sp.run(
                    [str(script)], env=env, timeout=10,
                    capture_output=True, text=True, check=False
                )
            except Exception:
                pass


def load_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            merged = {**DEFAULT_CONFIG, **saved}
            saved_model = merged.get("model", "")
            # Only reset stale aria-* model names that no longer exist in Ollama.
            # Community models (llama3.2:3b, mistral:7b, etc.) are kept as-is.
            _stale_prefixes = ("aria-opus", "aria-prelude", "aria-sonata:3", "aria-sonata:4")
            if any(saved_model.startswith(p) for p in _stale_prefixes):
                merged["model"] = DEFAULT_CONFIG["model"]
            # Detect system language on first run (ui_lang not yet written to config)
            if not merged.get("ui_lang"):
                try:
                    from apps.cli.i18n import detect_system_lang as _dsl
                    merged["ui_lang"] = _dsl()
                except Exception:
                    merged["ui_lang"] = "en"
            _sync_write_policy(merged)
            return merged
        except Exception:
            pass
    # ── First run: no config file yet ────────────────────────────────────────
    cfg = dict(DEFAULT_CONFIG)
    # Auto-detect system language
    try:
        from apps.cli.i18n import detect_system_lang as _dsl, auto_select_model as _asm
        cfg["ui_lang"] = _dsl()
        # Auto-select best installed Ollama model
        ollama_url = cfg.get("ollama_url", "http://localhost:11434")
        best = _asm(ollama_url, fallback=DEFAULT_CONFIG["model"])
        cfg["model"] = best
    except Exception:
        cfg["ui_lang"] = "en"
    _sync_write_policy(cfg)
    return cfg


def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    exclude = {"conversation_history"}
    to_save = {k: v for k, v in cfg.items() if k not in exclude}
    with open(CONFIG_FILE, "w") as f:
        json.dump(to_save, f, indent=2, ensure_ascii=False)


# ============================================================================
# Aria Tool Executor — calls /api/aria/execute-tool
# ============================================================================

ARIA_TOOLS = [
    ("get_market_data",         "Stock quotes, prices, chart data"),
    ("get_crypto_data",         "Cryptocurrency market data"),
    ("get_forex_data",          "Foreign exchange rates"),
    ("get_commodities_data",    "Commodities prices (gold, oil, etc.)"),
    ("get_futures_data",        "Futures contract data"),
    ("get_bonds_data",          "Bond yields and data"),
    ("backtest_strategy",       "Run strategy backtesting"),
    ("calculate_factors",       "Calculate quantitative factors"),
    ("get_alpha158_factors",    "Alpha158 factor set"),
    ("get_risk_metrics",        "Risk metrics and VaR"),
    ("optimize_positions",      "Portfolio optimization"),
    ("stress_test_strategy",    "Strategy stress testing"),
    ("check_strategy_compliance", "Strategy compliance check"),
    ("recommend_strategy",      "AI strategy recommendation"),
    ("analyze_news",            "News sentiment analysis"),
    ("web_search",              "Web search for research"),
    ("get_world_bank_reports",  "World Bank economic reports"),
    ("generate_chart",          "Generate chart visualization"),
    ("generate_report",         "Generate analysis report"),
    ("assess_portfolio_risk",   "Portfolio risk assessment"),
    ("get_sector_performance",  "Sector performance heatmap"),
    ("get_market_indices",      "Global market indices"),
]


# ============================================================================
# Models Registry — like Claude Code model picker
# ============================================================================

MODELS = {
    # ════════════════════════════════════════════════════════════════════
    # ── Qwen 家族（阿里巴巴）────────────────────────────────────────────
    # ════════════════════════════════════════════════════════════════════
    "qwen7b": {
        "id": "qwen2.5:7b",
        "name": "Qwen 2.5",
        "version": "7B",
        "tag": "Default",
        "speed": "★★★★",
        "intelligence": "★★★★★",
        "description": "主力推荐：中英双语最强 7B，工具调用稳定，金融/代码俱佳",
        "capabilities": ["chat", "tool calls", "financial analysis", "coding", "Chinese"],
        "thinking": False, "tools": True,
        "max_tokens": 4096, "num_ctx": 32768, "temperature": 0.3,
        "badge": "Default",
    },
    "qwen-coder": {
        "id": "qwen2.5-coder:7b",
        "name": "Qwen Coder",
        "version": "7B",
        "tag": "Code",
        "speed": "★★★★",
        "intelligence": "★★★★★",
        "description": "代码专精：量化策略生成、回测脚本、Python 金融工具",
        "capabilities": ["strategy code", "backtest", "Python", "quant development"],
        "thinking": False, "tools": True,
        "max_tokens": 4096, "num_ctx": 32768, "temperature": 0.2,
        "badge": "Code",
    },
    "qwen14b": {
        "id": "qwen2.5:14b",
        "name": "Qwen 2.5",
        "version": "14B",
        "tag": "Pro",
        "speed": "★★★",
        "intelligence": "★★★★★",
        "description": "高质量推理：需 ~10GB VRAM，复杂分析/长文档首选",
        "capabilities": ["complex analysis", "long context", "Chinese"],
        "thinking": False, "tools": True,
        "max_tokens": 8192, "num_ctx": 32768, "temperature": 0.3,
        "badge": "Pro",
    },
    "qwen32b": {
        "id": "qwen2.5:32b",
        "name": "Qwen 2.5",
        "version": "32B",
        "tag": "Max",
        "speed": "★★",
        "intelligence": "★★★★★",
        "description": "旗舰本地：需 ~20GB VRAM，媲美 GPT-4o 水平",
        "capabilities": ["flagship reasoning", "long context", "deep analysis"],
        "thinking": False, "tools": True,
        "max_tokens": 8192, "num_ctx": 32768, "temperature": 0.3,
        "badge": "Max",
    },
    "qwen3-8b": {
        "id": "qwen3:8b",
        "name": "Qwen 3",
        "version": "8B",
        "tag": "Latest",
        "speed": "★★★★",
        "intelligence": "★★★★★",
        "description": "Qwen3 最新一代：混合推理模式，/think 开启深度思考",
        "capabilities": ["hybrid reasoning", "thinking mode", "chat", "code"],
        "thinking": True, "tools": True,
        "max_tokens": 8192, "num_ctx": 32768, "temperature": 0.6,
        "badge": "Latest",
    },
    "qwen3-30b": {
        "id": "qwen3:30b-a3b",
        "name": "Qwen 3 MoE",
        "version": "30B-A3B",
        "tag": "MoE",
        "speed": "★★★★",
        "intelligence": "★★★★★",
        "description": "混合专家 MoE：30B参数激活3B，速度与质量双赢",
        "capabilities": ["MoE", "fast reasoning", "tool calls"],
        "thinking": True, "tools": True,
        "max_tokens": 8192, "num_ctx": 32768, "temperature": 0.6,
        "badge": "MoE",
    },
    "qwen-fast": {
        "id": "qwen2.5-coder:1.5b",
        "name": "Qwen Fast",
        "version": "1.5B",
        "tag": "Fast",
        "speed": "★★★★★",
        "intelligence": "★★★",
        "description": "超快响应：简单问答、实时报价、快速指令，~1GB RAM",
        "capabilities": ["fast chat", "simple queries", "ultra-low latency"],
        "thinking": False, "tools": False,
        "max_tokens": 2048, "num_ctx": 8192, "temperature": 0.3,
        "badge": "Fast",
    },
    # ════════════════════════════════════════════════════════════════════
    # ── DeepSeek 家族──────────────────────────────────────────────────
    # ════════════════════════════════════════════════════════════════════
    "deepseek-r1": {
        "id": "deepseek-r1:7b",
        "name": "DeepSeek R1",
        "version": "7B",
        "tag": "Think",
        "speed": "★★★",
        "intelligence": "★★★★★",
        "description": "深度推理：复杂投资决策、多步骤分析、Chain-of-Thought",
        "capabilities": ["deep reasoning", "chain-of-thought", "complex quant"],
        "thinking": True, "tools": False,
        "max_tokens": 4096, "num_ctx": 32768, "temperature": 0.3,
        "badge": "Think",
    },
    "deepseek-r1-1.5b": {
        "id": "deepseek-r1:1.5b",
        "name": "DeepSeek R1",
        "version": "1.5B",
        "tag": "Tiny",
        "speed": "★★★★★",
        "intelligence": "★★★",
        "description": "最小推理模型：~1GB，边缘设备/低内存机器首选",
        "capabilities": ["lightweight reasoning", "simple CoT"],
        "thinking": True, "tools": False,
        "max_tokens": 2048, "num_ctx": 8192, "temperature": 0.3,
        "badge": "Fast",
    },
    "deepseek-r1-14b": {
        "id": "deepseek-r1:14b",
        "name": "DeepSeek R1",
        "version": "14B",
        "tag": "Pro",
        "speed": "★★★",
        "intelligence": "★★★★★",
        "description": "强化推理 14B：数学/代码/金融逻辑最强本地选择",
        "capabilities": ["strong reasoning", "math", "code analysis"],
        "thinking": True, "tools": False,
        "max_tokens": 8192, "num_ctx": 32768, "temperature": 0.3,
        "badge": "Think",
    },
    # ════════════════════════════════════════════════════════════════════
    # ── Meta Llama 家族 ───────────────────────────────────────────────
    # ════════════════════════════════════════════════════════════════════
    "llama3.2-3b": {
        "id": "llama3.2:3b",
        "name": "Llama 3.2",
        "version": "3B",
        "tag": "Light",
        "speed": "★★★★★",
        "intelligence": "★★★",
        "description": "Meta 轻量级：~2GB，快速对话，英文性能出色",
        "capabilities": ["fast chat", "English", "summarization"],
        "thinking": False, "tools": True,
        "max_tokens": 4096, "num_ctx": 8192, "temperature": 0.3,
        "badge": "Fast",
    },
    "llama3.1-8b": {
        "id": "llama3.1:8b",
        "name": "Llama 3.1",
        "version": "8B",
        "tag": "Standard",
        "speed": "★★★★",
        "intelligence": "★★★★",
        "description": "Meta 主力 8B：英文任务顶级，工具调用完整支持",
        "capabilities": ["chat", "tool calls", "English", "reasoning"],
        "thinking": False, "tools": True,
        "max_tokens": 4096, "num_ctx": 131072, "temperature": 0.3,
        "badge": "Default",
    },
    "llama3.3-70b": {
        "id": "llama3.3:70b",
        "name": "Llama 3.3",
        "version": "70B",
        "tag": "Large",
        "speed": "★★",
        "intelligence": "★★★★★",
        "description": "Meta 最强开源：70B 需 ~40GB VRAM，媲美 GPT-4o",
        "capabilities": ["flagship English", "complex reasoning", "long context"],
        "thinking": False, "tools": True,
        "max_tokens": 8192, "num_ctx": 131072, "temperature": 0.3,
        "badge": "Pro",
    },
    # ════════════════════════════════════════════════════════════════════
    # ── Mistral / Mixtral 家族 ────────────────────────────────────────
    # ════════════════════════════════════════════════════════════════════
    "mistral-7b": {
        "id": "mistral:7b",
        "name": "Mistral",
        "version": "7B",
        "tag": "EU",
        "speed": "★★★★",
        "intelligence": "★★★★",
        "description": "欧洲顶级开源：结构化输出强，JSON 工具调用稳定",
        "capabilities": ["structured output", "tool calls", "JSON", "English"],
        "thinking": False, "tools": True,
        "max_tokens": 4096, "num_ctx": 32768, "temperature": 0.3,
        "badge": "Default",
    },
    "mistral-nemo": {
        "id": "mistral-nemo:12b",
        "name": "Mistral Nemo",
        "version": "12B",
        "tag": "Balanced",
        "speed": "★★★★",
        "intelligence": "★★★★★",
        "description": "Mistral × Nvidia：128K上下文，多语言支持佳",
        "capabilities": ["long context", "multilingual", "tool calls"],
        "thinking": False, "tools": True,
        "max_tokens": 8192, "num_ctx": 131072, "temperature": 0.3,
        "badge": "Pro",
    },
    # ════════════════════════════════════════════════════════════════════
    # ── Microsoft Phi 家族 ────────────────────────────────────────────
    # ════════════════════════════════════════════════════════════════════
    "phi4": {
        "id": "phi4:14b",
        "name": "Phi-4",
        "version": "14B",
        "tag": "STEM",
        "speed": "★★★",
        "intelligence": "★★★★★",
        "description": "微软 STEM 旗舰：数学/代码/科学推理超越同级，14B 需 ~8GB",
        "capabilities": ["math", "STEM", "code", "science reasoning"],
        "thinking": False, "tools": True,
        "max_tokens": 8192, "num_ctx": 16384, "temperature": 0.3,
        "badge": "STEM",
    },
    "phi4-mini": {
        "id": "phi4-mini:3.8b",
        "name": "Phi-4 Mini",
        "version": "3.8B",
        "tag": "Compact",
        "speed": "★★★★★",
        "intelligence": "★★★★",
        "description": "微软精简版：3.8B 打败多数 7B，数学/代码能力突出",
        "capabilities": ["math", "code", "compact", "fast"],
        "thinking": False, "tools": True,
        "max_tokens": 4096, "num_ctx": 16384, "temperature": 0.3,
        "badge": "Fast",
    },
    # ════════════════════════════════════════════════════════════════════
    # ── Google Gemma 3 家族 ───────────────────────────────────────────
    # ════════════════════════════════════════════════════════════════════
    "gemma3-4b": {
        "id": "gemma3:4b",
        "name": "Gemma 3",
        "version": "4B",
        "tag": "Google",
        "speed": "★★★★★",
        "intelligence": "★★★★",
        "description": "Google 轻量：4B 支持图像理解，多模态能力出色",
        "capabilities": ["multimodal", "vision", "fast chat", "Google"],
        "vision": True, "thinking": False, "tools": True,
        "max_tokens": 4096, "num_ctx": 8192, "temperature": 0.3,
        "badge": "Fast",
    },
    "gemma3-12b": {
        "id": "gemma3:12b",
        "name": "Gemma 3",
        "version": "12B",
        "tag": "Vision",
        "speed": "★★★★",
        "intelligence": "★★★★★",
        "description": "Google 中型：12B 视觉+文本综合能力强，~8GB VRAM",
        "capabilities": ["multimodal", "vision", "reasoning", "multilingual"],
        "vision": True, "thinking": False, "tools": True,
        "max_tokens": 8192, "num_ctx": 16384, "temperature": 0.3,
        "badge": "Vision",
    },
    # ════════════════════════════════════════════════════════════════════
    # ── Cloud 路由（需订阅或 API Key）────────────────────────────────
    # ════════════════════════════════════════════════════════════════════
    "gpt-oss-120b": {
        "id": "gpt-oss:120b-cloud",
        "name": "GPT-OSS",
        "version": "120B",
        "tag": "Cloud·120B",
        "speed": "★★★",
        "intelligence": "★★★★★",
        "description": "云端 120B 中继：机构级分析，复杂金融报告",
        "capabilities": ["institutional analysis", "long-form reports", "complex reasoning"],
        "thinking": False, "tools": True,
        "max_tokens": 8192, "num_ctx": 131072, "temperature": 0.3,
        "badge": "Cloud",
    },
    "deepseek-v3-cloud": {
        "id": "deepseek-v3.1:671b-cloud",
        "name": "DeepSeek V3",
        "version": "671B",
        "tag": "Cloud·671B",
        "speed": "★★★",
        "intelligence": "★★★★★",
        "description": "云端 671B 旗舰：最强推理，研报级分析，需订阅",
        "capabilities": ["flagship reasoning", "research report", "quant strategy"],
        "thinking": False, "tools": True,
        "max_tokens": 8192, "num_ctx": 131072, "temperature": 0.3,
        "badge": "Cloud",
    },
}

# Model aliases: short names / Ollama IDs → MODELS key
MODEL_ALIASES = {
    # ── Qwen 2.5 ──────────────────────────────────────────────────────
    "qwen7b": "qwen7b",   "q7": "qwen7b",   "sonata": "qwen7b",   "s": "qwen7b",
    "qwen14b": "qwen14b", "q14": "qwen14b",
    "qwen32b": "qwen32b", "q32": "qwen32b",
    "qwen-coder": "qwen-coder", "coder": "qwen-coder", "c": "qwen-coder",
    "qwen-fast": "qwen-fast",   "fast": "qwen-fast",   "prelude": "qwen-fast", "p": "qwen-fast",
    # ── Qwen 3 ────────────────────────────────────────────────────────
    "qwen3": "qwen3-8b",     "q3": "qwen3-8b",    "qwen3-8b": "qwen3-8b",
    "qwen3-30b": "qwen3-30b", "q3-moe": "qwen3-30b", "moe": "qwen3-30b",
    # ── DeepSeek ──────────────────────────────────────────────────────
    "deepseek-r1": "deepseek-r1", "r1": "deepseek-r1", "r1-7b": "deepseek-r1",
    "r1-1.5b": "deepseek-r1-1.5b", "r1-tiny": "deepseek-r1-1.5b",
    "r1-14b": "deepseek-r1-14b",  "r1-pro": "deepseek-r1-14b",
    # ── Llama ─────────────────────────────────────────────────────────
    "llama3.2": "llama3.2-3b",    "llama3": "llama3.2-3b",    "l3": "llama3.2-3b",
    "llama3.1": "llama3.1-8b",    "llama3.1-8b": "llama3.1-8b", "l31": "llama3.1-8b",
    "llama3.3": "llama3.3-70b",   "llama70b": "llama3.3-70b", "l33": "llama3.3-70b",
    # ── Mistral ───────────────────────────────────────────────────────
    "mistral": "mistral-7b",   "m7": "mistral-7b",
    "nemo": "mistral-nemo",    "mistral12b": "mistral-nemo",
    # ── Phi (Microsoft) ───────────────────────────────────────────────
    "phi4": "phi4",       "phi": "phi4",
    "phi4-mini": "phi4-mini", "phi-mini": "phi4-mini",
    # ── Gemma (Google) ────────────────────────────────────────────────
    "gemma": "gemma3-4b",    "gemma3": "gemma3-4b",   "g4": "gemma3-4b",
    "gemma12b": "gemma3-12b", "gemma3-12b": "gemma3-12b",
    # ── Cloud relay ───────────────────────────────────────────────────
    "gpt-oss": "gpt-oss-120b", "120b": "gpt-oss-120b",
    "deepseek-v3": "deepseek-v3-cloud", "v3": "deepseek-v3-cloud", "671b": "deepseek-v3-cloud",
    # ── 旧名向后兼容 ──────────────────────────────────────────────────
    "sonata-thinking": "deepseek-r1", "st": "deepseek-r1",
    "sonata-verbose":  "qwen7b",      "sv": "qwen7b",
    # ── Ollama model ID → registry key ────────────────────────────────
    "qwen2.5:7b":                "qwen7b",
    "qwen2.5:14b":               "qwen14b",
    "qwen2.5:32b":               "qwen32b",
    "qwen2.5:3b":                "qwen-fast",
    "qwen2.5-coder:7b":          "qwen-coder",
    "qwen2.5-coder:14b":         "qwen-coder",
    "qwen2.5-coder:1.5b":        "qwen-fast",
    "qwen3:8b":                  "qwen3-8b",
    "qwen3:30b-a3b":             "qwen3-30b",
    "deepseek-r1:7b":            "deepseek-r1",
    "deepseek-r1:1.5b":          "deepseek-r1-1.5b",
    "deepseek-r1:14b":           "deepseek-r1-14b",
    "llama3.2:3b":               "llama3.2-3b",
    "llama3.1:8b":               "llama3.1-8b",
    "llama3.3:70b":              "llama3.3-70b",
    "mistral:7b":                "mistral-7b",
    "mistral-nemo:12b":          "mistral-nemo",
    "phi4:14b":                  "phi4",
    "phi4-mini:3.8b":            "phi4-mini",
    "gemma3:4b":                 "gemma3-4b",
    "gemma3:12b":                "gemma3-12b",
    "gpt-oss:120b-cloud":        "gpt-oss-120b",
    "deepseek-v3.1:671b-cloud":  "deepseek-v3-cloud",
    # ── 旧 aria 模型 ID ───────────────────────────────────────────────
    "aria-sonata:4.5":           "qwen7b",
    "aria-sonata:4.5-thinking":  "deepseek-r1",
    "aria-sonata:4.5-verbose":   "qwen7b",
    "aria-sonata:4.6":           "qwen7b",
    "aria-sonata:4.6-thinking":  "deepseek-r1",
    "aria-prelude:4.3":          "qwen-fast",
    "aria-prelude:1.5b":         "qwen-fast",
}

# ── 模型降级优先级（单一事实源：预检 / 运行时 fallback 共用）────────────────
# 按能力/稳定性排序：先选大容量本地模型，再退化到轻量模型
_MODEL_FALLBACK_PREFIXES = [
    # 首选：7B+ 本地全能模型
    "qwen3:8b",            # Qwen3 最新，混合推理
    "qwen3:30b-a3b",       # Qwen3 MoE，快速
    "qwen2.5:14b",         # Qwen2.5 高质量
    "qwen2.5:7b",          # Qwen2.5 主力
    "qwen2.5-coder:7b",    # 代码专精
    # 次选：其他家族本地模型
    "llama3.3:70b",        # Meta 旗舰（需大 VRAM）
    "llama3.1:8b",         # Meta 8B 稳定
    "mistral-nemo:12b",    # Mistral 12B
    "mistral:7b",          # Mistral 7B
    "phi4:14b",            # Microsoft Phi-4
    "phi4-mini:3.8b",      # Microsoft Phi-4 Mini
    "gemma3:12b",          # Google Gemma 12B
    "gemma3:4b",           # Google Gemma 4B
    "deepseek-r1:14b",     # DeepSeek R1 推理
    "deepseek-r1:7b",      # DeepSeek R1 7B
    # 轻量回落
    "qwen2.5-coder:3b",    # 小模型
    "qwen2.5:3b",          # 小模型
    "llama3.2:3b",         # Meta 轻量
    "deepseek-r1:1.5b",    # 极小推理
    # Cloud relay（需订阅）
    "gpt-oss",
    "deepseek-v3.1",
]


def _pick_best_installed_model(installed, preferred: str = ""):
    """从已安装模型中选出实际将使用的模型（预检与运行时共用此逻辑）。

    优先精确匹配 preferred；否则按 _MODEL_FALLBACK_PREFIXES 能力顺序；
    全部未命中才退化到字母排序第一个。installed 为空返回 None。
    """
    if not installed:
        return None
    if preferred and preferred in installed:
        return preferred
    for pref in _MODEL_FALLBACK_PREFIXES:
        cand = next((m for m in sorted(installed) if m.startswith(pref)), None)
        if cand:
            return cand
    return sorted(installed)[0]


def detect_ollama_models(ollama_url: str = "http://localhost:11434") -> list:
    """Query Ollama /api/tags and return list of available model names.

    Always bypasses HTTP_PROXY so localhost is reached directly even when a
    system proxy (VPN / clash / surge) is active.
    """
    import urllib.request
    # Force direct connection — bypass any HTTP_PROXY / HTTPS_PROXY env vars
    _opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with _opener.open(f"{ollama_url}/api/tags", timeout=5) as r:
            data = json.loads(r.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        # Also try 127.0.0.1 if hostname is "localhost" (IPv6 resolution fallback)
        if "localhost" in ollama_url:
            try:
                fallback = ollama_url.replace("localhost", "127.0.0.1")
                with _opener.open(f"{fallback}/api/tags", timeout=5) as r:
                    data = json.loads(r.read())
                return [m["name"] for m in data.get("models", [])]
            except Exception:
                pass
        return []


def detect_ollama_models_rich(ollama_url: str = "http://localhost:11434") -> tuple:
    """Return (models_list, error_str) where each entry in models_list is a dict:
        {"name": str, "size_label": str, "family": str, "quant": str}
    error_str is None on success, or a short human-readable reason on failure.
    """
    import urllib.request
    _opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _try(url: str):
        with _opener.open(f"{url}/api/tags", timeout=5) as r:
            return json.loads(r.read())

    data = None
    last_err = None
    for u in [ollama_url] + ([ollama_url.replace("localhost", "127.0.0.1")]
                              if "localhost" in ollama_url else []):
        try:
            data = _try(u)
            break
        except OSError as e:
            last_err = str(e)
        except Exception as e:
            last_err = str(e)

    if data is None:
        return [], last_err or "connection failed"

    results = []
    for m in data.get("models", []):
        det  = m.get("details", {})
        size = det.get("parameter_size", "")
        fam  = det.get("family", "")
        qnt  = det.get("quantization_level", "")
        results.append({
            "name":       m["name"],
            "size_label": size,    # e.g. "1.5B", "7B", "671.0B"
            "family":     fam,     # e.g. "qwen2", "deepseek2"
            "quant":      qnt,     # e.g. "Q4_K_M", "MXFP4"
        })
    return results, None


# ── Response cache for stateless queries (TTL = 60s) ─────────────────────────
# Avoids sending the same market/concept query to Ollama multiple times
# in rapid succession (e.g., user retries or tab-completion tests).
import hashlib as _hashlib
_RESPONSE_CACHE: dict = {}   # key → (response_text, expire_ts)
_RESPONSE_CACHE_TTL = 60.0   # seconds

def _cache_get(key: str) -> str | None:
    """Return cached response text if still valid, else None."""
    entry = _RESPONSE_CACHE.get(key)
    if entry and time.time() < entry[1]:
        return entry[0]
    return None

def _cache_set(key: str, value: str) -> None:
    """Store response in cache with TTL expiry."""
    _RESPONSE_CACHE[key] = (value, time.time() + _RESPONSE_CACHE_TTL)
    # Keep cache small — evict expired entries when it grows large
    if len(_RESPONSE_CACHE) > 200:
        now = time.time()
        for k in list(_RESPONSE_CACHE.keys()):
            if _RESPONSE_CACHE[k][1] < now:
                del _RESPONSE_CACHE[k]

def _cache_key(model: str, message: str) -> str:
    raw = f"{model}::{message.strip().lower()}"
    return _hashlib.md5(raw.encode()).hexdigest()

def _is_simple_greeting(message: str) -> bool:
    text = (message or "").strip().lower()
    greetings = {
        "hi", "hello", "hey", "你好", "您好", "嗨", "哈喽", "在吗",
        "早上好", "下午好", "晚上好",
    }
    return text in greetings or (len(text) <= 8 and any(g in text for g in greetings))


def _offline_greeting_response() -> dict:
    return {
        "success": True,
        "response": (
            "你好，我是 Aria Code。\n\n"
            "当前云端模型不可用，且本地 Ollama 服务没有启动；简单问候可以直接响应。"
            "如果要进行代码修改、市场分析或长文本推理，请先启动本地模型：\n\n"
            "```bash\n"
            "ollama serve\n"
            "```\n\n"
            "然后可用 `ollama list` 检查已安装模型，或运行 `/health` 查看 Aria Code 状态。"
        ),
        "provider": "builtin",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "thinking_tokens": 0},
    }


def _ollama_unavailable_result(ollama_url: str, err: str = "") -> dict:
    host = ollama_url or "http://localhost:11434"
    detail = f"\n\nDetail: {err}" if err else ""
    return {
        "success": False,
        "provider": "ollama",
        "error": (
            "Local Ollama is not reachable.\n\n"
            f"Host: {host}\n"
            "Start it in another terminal:\n\n"
            "  ollama serve\n\n"
            "Then verify:\n\n"
            "  curl http://127.0.0.1:11434/api/tags\n"
            "  ollama list\n\n"
            "If you do not want local fallback, use a working cloud/API provider or disable local mode."
            f"{detail}"
        ),
    }


def resolve_model_key(model_str: str) -> str:
    """Resolve any model alias/ID/key to a MODELS key.

    For community Ollama models (qwen2.5-coder, llama3.2, deepseek-r1, etc.)
    that are NOT in the MODELS registry, returns the sentinel "_community_"
    so callers know to use model_capability.get_model_capability() instead
    of falling back to hardcoded "prelude" settings.
    """
    if model_str in MODELS:
        return model_str
    if model_str in MODEL_ALIASES:
        return MODEL_ALIASES[model_str]
    # Community/custom Ollama model — not in registry
    return "_community_"


def get_model_cfg(model_str: str) -> dict:
    """Return the best available config dict for *model_str*.

    For registered models (MODELS table): returns the table entry.
    For community Ollama models: synthesizes a config from model_capability.
    Never silently falls back to 'prelude' settings for an unrelated model.
    """
    key = resolve_model_key(model_str)
    if key in MODELS:
        return MODELS[key]
    # Community model — build config from model_capability registry
    if _HAS_MODEL_CAP:
        cap = get_model_capability(model_str)
        return {
            "id":          model_str,
            "name":        model_str,
            "num_ctx":     cap.context_window,
            "temperature": cap.temperature,
            "max_tokens":  min(cap.context_window // 4, 8192),
            "thinking":    cap.thinking,
            "tools":       cap.tool_calls,
        }
    # Last resort fallback — use qwen7b (sonata) settings as a safe default
    return MODELS.get("sonata", MODELS.get("qwen7b", next(iter(MODELS.values()))))

THINKING_MODES = {
    "auto":     {"label": "Auto",     "description": "Let Aria decide when to think deeply"},
    "instant":  {"label": "Instant",  "description": "Fast responses, no extended thinking"},
    "thinking": {"label": "Thinking", "description": "Always show reasoning chain"},
}


# ============================================================================
# Skills System — Claude Code-style expandable prompt templates
# ============================================================================

SKILLS = [
    {
        "command": "/morning-brief",
        "name": "Morning Brief",
        "category": "research",
        "description": "Daily market briefing with key events and outlook",
        "args": "[focus_area]",
        "prompt": (
            "Generate a comprehensive morning market briefing:\n"
            "1. US market futures and overnight moves\n"
            "2. Key economic events and earnings today\n"
            "3. Global markets overview (Asia, Europe)\n"
            "4. Top sector movers and themes\n"
            "5. Trading outlook and key levels to watch\n"
            "{extra}"
        ),
        "tools_hint": ["web_search", "get_market_indices", "get_sector_performance", "analyze_news"],
    },
    {
        "command": "/deep-analysis",
        "name": "Deep Analysis",
        "category": "analysis",
        "description": "Multi-factor stock deep dive (technical + fundamental + sentiment)",
        "args": "<symbol>",
        "prompt": (
            "Perform a comprehensive multi-factor analysis of {symbol}:\n"
            "1. Technical Analysis: trend, support/resistance, indicators (RSI, MACD, Bollinger)\n"
            "2. Fundamental Analysis: PE, PB, revenue growth, margins, debt ratios\n"
            "3. Sentiment Analysis: recent news sentiment, analyst ratings, social buzz\n"
            "4. Risk Assessment: VaR, beta, max drawdown potential\n"
            "5. Verdict: Bull/Bear/Neutral with confidence level and price targets"
        ),
        "tools_hint": ["web_search", "get_market_data", "calculate_factors", "analyze_news", "peer_comparison", "piotroski_fscore", "get_risk_metrics"],
    },
    {
        "command": "/trade-idea",
        "name": "Trade Idea",
        "category": "strategy",
        "description": "AI-generated trade ideas with entry/exit levels",
        "args": "[market_or_sector]",
        "prompt": (
            "Generate 3 actionable trade ideas{context}:\n"
            "For each idea provide:\n"
            "1. Symbol and direction (Long/Short)\n"
            "2. Entry zone, stop loss, and 2 take-profit levels\n"
            "3. Risk-reward ratio\n"
            "4. Catalyst: what's driving the trade\n"
            "5. Timeframe (swing/position/day)\n"
            "6. Confidence level (1-10)"
        ),
        "tools_hint": ["web_search", "get_market_data", "analyze_news", "recommend_strategy"],
    },
    {
        "command": "/risk-report",
        "name": "Risk Report",
        "category": "risk",
        "description": "Portfolio risk analysis with VaR, stress tests, and correlation",
        "args": "[symbols...]",
        "prompt": (
            "Generate a comprehensive risk report for portfolio: {symbols}\n"
            "1. Portfolio VaR (95%, 99%) — daily and monthly\n"
            "2. Correlation matrix between holdings\n"
            "3. Concentration risk by sector/geography\n"
            "4. Stress test scenarios (2008 crisis, COVID crash, rate hike)\n"
            "5. Tail risk analysis\n"
            "6. Recommendations: rebalancing suggestions to reduce risk"
        ),
        "tools_hint": ["assess_portfolio_risk", "get_risk_metrics", "stress_test_strategy"],
    },
    {
        "command": "/sector-rotation",
        "name": "Sector Rotation",
        "category": "strategy",
        "description": "Sector rotation analysis with economic cycle positioning",
        "args": "",
        "prompt": (
            "Analyze current sector rotation dynamics:\n"
            "1. Current economic cycle phase (early/mid/late/recession)\n"
            "2. All 11 GICS sectors: performance, momentum, relative strength\n"
            "3. Leading vs lagging sectors and why\n"
            "4. Sector rotation strategy: which sectors to overweight/underweight\n"
            "5. Top stock picks from the strongest sectors\n"
            "6. Historical analog: which past period is most similar"
        ),
        "tools_hint": ["get_sector_performance", "get_market_indices", "analyze_news"],
    },
    {
        "command": "/macro-outlook",
        "name": "Macro Outlook",
        "category": "research",
        "description": "Macroeconomic analysis: rates, inflation, growth & cycle",
        "args": "[region]",
        "prompt": (
            "Provide a macroeconomic outlook{context}:\n"
            "1. GDP growth forecast and trends\n"
            "2. Inflation trajectory (CPI, PCE) and central bank response\n"
            "3. Interest rate path: current level and expectations\n"
            "4. Employment situation: jobs, wages, participation\n"
            "5. Key risks: geopolitical, financial, systemic\n"
            "6. Asset class implications: equities, bonds, commodities, crypto"
        ),
        "tools_hint": ["web_search", "get_world_bank_reports", "get_bonds_data", "analyze_news"],
    },
    {
        "command": "/factor-screen",
        "name": "Factor Screen",
        "category": "quant",
        "description": "Factor-based stock screening (value, momentum, quality, etc.)",
        "args": "<factor_type>",
        "prompt": (
            "Screen US stocks using {factor} factor strategy:\n"
            "1. Define the factor criteria and thresholds\n"
            "2. Top 10 stocks ranking highest on {factor}\n"
            "3. For each: symbol, score, key metrics, sector\n"
            "4. Historical factor performance: how has {factor} performed\n"
            "5. Current factor environment: is {factor} in favor?\n"
            "6. Combined multi-factor overlay suggestion"
        ),
        "tools_hint": ["calculate_factors", "get_alpha158_factors", "get_market_data"],
    },
    {
        "command": "/crypto-scan",
        "name": "Crypto Scanner",
        "category": "crypto",
        "description": "Cryptocurrency market scan with top movers and DeFi trends",
        "args": "[focus]",
        "prompt": (
            "Scan the cryptocurrency market:\n"
            "1. BTC and ETH: price, trend, dominance, key levels\n"
            "2. Top 5 gainers and top 5 losers (24h)\n"
            "3. Market sentiment: Fear & Greed index, funding rates\n"
            "4. DeFi and Layer-2 highlights\n"
            "5. Upcoming catalysts: halvings, upgrades, token unlocks\n"
            "6. Trading opportunities with risk levels\n"
            "{extra}"
        ),
        "tools_hint": ["get_crypto_data", "analyze_news"],
    },
    {
        "command": "/backtest-report",
        "name": "Backtest Report",
        "category": "quant",
        "description": "Run and analyze a strategy backtest with detailed metrics",
        "args": "<strategy> <symbol> [start] [end]",
        "prompt": (
            "Run a detailed backtest of '{strategy}' strategy on {symbol} from {start} to {end}:\n"
            "1. Performance summary: total return, annualized, Sharpe, Sortino\n"
            "2. Risk metrics: max drawdown, VaR, downside deviation\n"
            "3. Trade analysis: win rate, avg win/loss, profit factor\n"
            "4. Monthly returns breakdown\n"
            "5. Comparison vs buy-and-hold and benchmark (SPY)\n"
            "6. Optimization suggestions: parameter sensitivity"
        ),
        "tools_hint": ["backtest_strategy", "get_market_data", "get_risk_metrics"],
    },
    {
        "command": "/watchlist-scan",
        "name": "Watchlist Scan",
        "category": "tools",
        "description": "Scan all watchlist stocks for signals and alerts",
        "args": "",
        "prompt": (
            "Scan my watchlist ({symbols}) and for each stock provide:\n"
            "1. Current price and daily change\n"
            "2. Technical signal: Buy/Sell/Hold based on key indicators\n"
            "3. Any earnings or events upcoming\n"
            "4. News sentiment (positive/neutral/negative)\n"
            "5. Overall alert level: Green/Yellow/Red\n"
            "Sort by urgency of action needed."
        ),
        "tools_hint": ["get_market_data", "analyze_news"],
    },
    {
        "command": "/gen-strategy",
        "name": "Generate Strategy Code",
        "category": "code",
        "description": "Generate complete Python trading strategy code",
        "args": "<strategy_type> [symbol]",
        "prompt": (
            "Generate a complete, production-ready Python backtrader trading strategy.\n"
            "Strategy type: {strategy}\n"
            "Target symbol: {symbol}\n\n"
            "Requirements:\n"
            "1. Full backtrader Strategy class with __init__, next, notify_order\n"
            "2. Proper indicator initialization (use bt.indicators)\n"
            "3. Entry/exit logic with clear conditions\n"
            "4. Position sizing (percent sizer or fixed)\n"
            "5. Risk management: stop-loss and take-profit\n"
            "6. Logging via self.log()\n"
            "7. Complete cerebro setup code at the bottom\n\n"
            "Return ONLY the Python code wrapped in ```python``` fences. "
            "Include inline comments explaining the logic."
        ),
        "tools_hint": ["recommend_strategy", "backtest_strategy"],
    },
    {
        "command": "/gen-analysis",
        "name": "Generate Analysis Script",
        "category": "code",
        "description": "Generate a Python analysis/visualization script",
        "args": "<topic> [symbols...]",
        "prompt": (
            "Generate a Python script for financial analysis and visualization.\n"
            "Topic: {topic}\n"
            "Symbols: {symbols}\n\n"
            "Requirements:\n"
            "1. Use pandas, numpy, matplotlib/plotly, yfinance\n"
            "2. Fetch real market data with yfinance\n"
            "3. Compute relevant metrics/indicators\n"
            "4. Create informative charts/plots\n"
            "5. Print a summary table of key findings\n"
            "6. Include error handling for data fetching\n\n"
            "Return ONLY the Python code wrapped in ```python``` fences. "
            "Include inline comments."
        ),
        "tools_hint": ["get_market_data", "calculate_factors"],
    },
    {
        "command": "/gen-bot",
        "name": "Generate Trading Bot",
        "category": "code",
        "description": "Generate live trading bot with exchange API (ccxt)",
        "args": "<exchange> <strategy>",
        "prompt": (
            "Generate a Python trading bot for live execution.\n"
            "Exchange: {exchange}\n"
            "Strategy: {strategy}\n\n"
            "Requirements:\n"
            "1. Use ccxt library for exchange connection\n"
            "2. Market data fetching and order execution\n"
            "3. Signal generation based on the strategy logic\n"
            "4. Risk management: max position size, daily loss limit\n"
            "5. Logging with timestamps\n"
            "6. Graceful shutdown handling (SIGINT)\n"
            "7. Configuration via environment variables (API keys)\n"
            "8. Paper trading mode toggle\n\n"
            "Return ONLY the Python code wrapped in ```python``` fences. "
            "NEVER include actual API keys. Use env vars."
        ),
        "tools_hint": ["recommend_strategy"],
    },
    {
        "command": "/orcl-deep",
        "name": "Oracle Corp Deep Dive",
        "category": "analysis",
        "description": "Full multi-factor analysis of Oracle Corporation (ORCL)",
        "args": "",
        "prompt": (
            "Perform a comprehensive analysis of Oracle Corporation (ORCL):\n"
            "1. Technical: trend, RSI, MACD, Bollinger Bands, key support/resistance\n"
            "2. Fundamental: cloud ARR growth, OCI revenue, margins, PE vs SAP/NOW/MSFT\n"
            "3. AI infrastructure thesis: Oracle's GPU cluster deals (xAI, OpenAI, Meta)\n"
            "4. Competitive moat: Autonomous DB, ERP lock-in, Cerner healthcare\n"
            "5. Balance sheet: debt from cloud capex, FCF generation, buyback pace\n"
            "6. Risks & catalysts: cloud transition pace, FX, Oracle DB migration risk\n"
            "7. Price target range (bull/base/bear) and conviction score"
        ),
        "tools_hint": ["get_market_data", "calculate_factors", "analyze_news", "get_risk_metrics"],
    },
    {
        "command": "/train-status",
        "name": "Training Status",
        "category": "tools",
        "description": "Check Aria model training and data pipeline status",
        "args": "",
        "prompt": (
            "Check the current Aria model training status.\n"
            "1. Locate the project root via the ARIA_PROJECT_ROOT environment variable, or "
            "search upward from the current directory for a 'packages/ml/llm/training' folder.\n"
            "2. List checkpoint directories inside 'packages/ml/llm/training/outputs/' "
            "(any subdirectory containing 'trainer_state.json').\n"
            "3. Read the latest checkpoint's trainer_state.json: report current step, "
            "total steps, epoch, eval_loss, and best_model_checkpoint.\n"
            "4. Check for model_versions.json in the training outputs and report the "
            "currently deployed version if present.\n"
            "5. List recent training data files under 'data/training/' (newest 5 files).\n"
            "Summarize: training progress (step/total, %), eval_loss trend, "
            "deployed version, and data pipeline status."
        ),
        "tools_hint": ["read_file", "list_files"],
    },
]


# ============================================================================
# Local Tool System — Claude Code-style file operations
# ============================================================================

import subprocess
import glob as glob_module
import re
import re as re_module
import difflib


def _is_safe_path(resolved: pathlib.Path) -> bool:
    """Return True if the resolved path is inside an allowed root directory.

    Allowed roots: home directory, /tmp, /var/folders (macOS temp).
    Blocks: /etc, /sys, /proc, /dev, and any path that resolves through a
    symlink to outside those roots (symlink traversal prevention).
    """
    return WorkspaceSecurity().is_safe_path(resolved)


def _tool_read_file(params: dict) -> dict:
    """Read file contents with optional line range."""
    path = params.get("path", "")
    if not path:
        return {"success": False, "error": "Missing 'path' parameter"}
    try:
        offset = int(params.get("offset", 0) or 0)
        limit = int(params.get("limit", 0) or 0)
        result = WorkspaceFiles().read_file(path, offset=offset, limit=limit)
        return {"success": True, "data": {
            "path": result.path, "lines": result.lines,
            "content": result.content
        }}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _strip_markdown_fences(content: str) -> str:
    """Thin shim — implementation in apps/cli/tools/write_tools.py."""
    from apps.cli.tools.write_tools import _strip_markdown_fences as _f
    return _f(content)


def _auto_fix_python(content: str, path: str) -> str:
    """Thin shim — implementation in apps/cli/tools/write_tools.py."""
    from apps.cli.tools.write_tools import _auto_fix_python as _f
    return _f(content, path)


def _write_policy_confirm(p: pathlib.Path, content: str, existed: bool) -> tuple:
    """Thin shim — implementation in apps/cli/tools/write_tools.py."""
    from apps.cli.tools.write_tools import _write_policy_confirm as _f
    return _f(p, content, existed)


def _tool_write_file(params: dict) -> dict:
    """Thin shim — implementation in apps/cli/tools/write_tools.py."""
    from apps.cli.tools.write_tools import tool_write_file as _f
    return _f(params)


def _tool_edit_file(params: dict) -> dict:
    """Thin shim — implementation in apps/cli/tools/write_tools.py."""
    from apps.cli.tools.write_tools import tool_edit_file as _f
    return _f(params)


def _tool_list_files(params: dict) -> dict:
    """List files in a directory, optionally matching a glob pattern."""
    path = params.get("path", ".")
    pattern = params.get("pattern", "*")
    try:
        data = WorkspaceFiles().list_files(path, pattern)
        return {"success": True, "data": {
            "path": data["path"], "pattern": data["pattern"],
            "count": data["count"], "items": data["items"]
        }}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _tool_search_code(params: dict) -> dict:
    """Search for a pattern in files (like grep)."""
    pattern = params.get("pattern", "")
    path = params.get("path", ".")
    file_glob = params.get("glob", "**/*.py")
    if not pattern:
        return {"success": False, "error": "Missing 'pattern' parameter"}
    try:
        data = WorkspaceFiles().search_code(pattern, path, file_glob)
        return {"success": True, "data": {
            "pattern": data["pattern"], "path": data["path"],
            "count": data["count"], "matches": data["matches"]
        }}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _tool_run_command(params: dict) -> dict:
    """Run a shell command — thin wrapper supplying global defaults."""
    params.setdefault("permission_mode", _ACTIVE_PERMISSION_MODE[0])
    params.setdefault("network_enabled", _ACTIVE_NETWORK_ENABLED[0])
    return _src_run_command(params, console=console, has_rich=HAS_RICH)


def _tool_web_fetch(params: dict) -> dict:
    return _src_web_fetch(params)


def _tool_github(params: dict) -> dict:
    params.setdefault("permission_mode", _ACTIVE_PERMISSION_MODE[0])
    params.setdefault("network_enabled", _ACTIVE_NETWORK_ENABLED[0])
    return _src_github(params, console=console, has_rich=HAS_RICH)


def _tool_glob(params: dict) -> dict:
    return _src_glob(params)


def _tool_notebook_read(params: dict) -> dict:
    return _src_notebook_read(params)


def _tool_notebook_edit(params: dict) -> dict:
    return _src_notebook_edit(params)


def _tool_broker_query(params: dict) -> dict:
    return _src_broker_query(params)


def _tool_broker_order(params: dict) -> dict:
    return _src_broker_order(params)


def _tool_get_market_data(params: dict) -> dict:
    return _src_get_market_data(params)


# Local tool registry: name → (handler, description, for display)
LOCAL_TOOLS = {
    # ── Core file tools ──────────────────────────────────────────────────────
    "read_file":      (_tool_read_file,      "Read a file's contents"),
    "write_file":     (_tool_write_file,     "Create or overwrite a file"),
    "edit_file":      (_tool_edit_file,      "Edit a file (find & replace)"),
    "list_files":     (_tool_list_files,     "List files in a directory"),
    "search_code":    (_tool_search_code,    "Search for patterns in code (grep)"),
    "run_command":    (_tool_run_command,    "Execute a shell command"),
    # ── Extended tools (Claude Code parity) ─────────────────────────────────
    "web_fetch":      (_tool_web_fetch,      "Fetch a URL and return page text"),
    "github":         (_tool_github,         "GitHub API/CLI: PRs, issues, diffs, search"),
    "glob":           (_tool_glob,           "Fast glob file-pattern search"),
    "notebook_read":  (_tool_notebook_read,  "Read a Jupyter notebook (.ipynb)"),
    "notebook_edit":  (_tool_notebook_edit,  "Edit a cell in a Jupyter notebook"),
    # ── Market data ─────────────────────────────────────────────────────────
    "get_market_data": (_tool_get_market_data, "Fetch real-time quote + technical indicators for any stock/ETF/crypto"),
    # ── Broker account data ──────────────────────────────────────────────────
    "broker_query": (_tool_broker_query, "Query connected broker: account balance, positions, or orders"),
    "broker_order": (_tool_broker_order, "Propose a trade order — requires explicit user confirmation before execution"),
}

# ── Register computer-use tools (browser automation + desktop control) ──────
_HAS_COMPUTER_USE = False
try:
    from computer_use_tools import COMPUTER_USE_TOOLS, COMPUTER_USE_SCHEMAS as _CU_SCHEMAS
    LOCAL_TOOLS.update(COMPUTER_USE_TOOLS)
    _HAS_COMPUTER_USE = True
    logger.info("Registered %d computer-use tools", len(COMPUTER_USE_TOOLS))
except ImportError:
    _CU_SCHEMAS: list = []

# ── Register local finance fallback tools (yfinance / akshare / ccxt) ──────
# These fill in for remote Aria tools when local_mode=True or backend offline.
if _HAS_LOCAL_FINANCE:
    try:
        _n_finance = register_local_finance_tools(LOCAL_TOOLS, LOCAL_TOOL_SCHEMAS)
        if _n_finance:
            logger.info("Registered %d local finance tools", _n_finance)
    except Exception as _exc:
        logger.debug("Local finance tools init error: %s", _exc)

# ── Register project plugin tools (aria_tools.py auto-discovery) ─────────
if _HAS_PLUGIN:
    try:
        _n_plugin, _plugin_path = register_plugin_tools(LOCAL_TOOLS, LOCAL_TOOL_SCHEMAS)
        if _n_plugin and _plugin_path:
            logger.info("Registered %d plugin tools from %s", _n_plugin, _plugin_path.name)
    except Exception as _exc:
        logger.debug("Plugin tool registration error: %s", _exc)

# Ollama tool schemas (for function calling)
LOCAL_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Use this to understand existing code before modifying it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative file path"},
                    "offset": {"type": "integer", "description": "Start line (0-based), optional"},
                    "limit": {"type": "integer", "description": "Number of lines to read, optional"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or overwrite an existing file with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write to"},
                    "content": {"type": "string", "description": "Complete file content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit a file by replacing old_string with new_string. Read the file first to get the exact text to replace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to edit"},
                    "old_string": {"type": "string", "description": "Exact text to find and replace (must match exactly)"},
                    "new_string": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a directory. Use glob patterns like '**/*.py' to filter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (default: current dir)"},
                    "pattern": {"type": "string", "description": "Glob pattern (default: *)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search for a regex pattern in source files. Returns matching lines with file paths and line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path": {"type": "string", "description": "Directory to search in (default: current dir)"},
                    "glob": {"type": "string", "description": "File glob filter (default: **/*.py)"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command. Use for: git, pip, python, pytest, ls, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                    "cwd": {"type": "string", "description": "Working directory (optional)"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (max 300, default 120). Use 180+ for data-heavy scripts."},
                },
                "required": ["command"],
            },
        },
    },
    # ── Extended tools ─────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "Fetch the text/content of any URL: web pages, GitHub files, "
                "documentation, API responses, PyPI pages. "
                "GitHub blob URLs are auto-converted to raw content. "
                "Use this to read docs, README files, or look up library APIs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url":       {"type": "string",  "description": "Full URL to fetch (https://)"},
                    "max_chars": {"type": "integer", "description": "Max characters to return (default 12000, max 40000)"},
                    "timeout":   {"type": "integer", "description": "Request timeout seconds (default 15, max 30)"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github",
            "description": (
                "GitHub integration via gh CLI. Actions: "
                "list_prs, list_issues, view_pr, view_issue, create_pr, "
                "list_commits, search, read_file, pr_diff, pr_checks. "
                "Use cwd to specify repo directory. "
                "Requires: gh CLI installed and authenticated (gh auth login)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action":  {"type": "string", "description": "Action to perform (list_prs|list_issues|view_pr|view_issue|create_pr|list_commits|search|read_file|pr_diff|pr_checks)"},
                    "number":  {"type": "integer","description": "PR or issue number (for view_pr, view_issue, pr_diff)"},
                    "title":   {"type": "string", "description": "PR title (for create_pr)"},
                    "body":    {"type": "string", "description": "PR body (for create_pr)"},
                    "branch":  {"type": "string", "description": "Head branch (for create_pr)"},
                    "base":    {"type": "string", "description": "Base branch (for create_pr, default main)"},
                    "state":   {"type": "string", "description": "Filter state: open|closed|all (default open)"},
                    "limit":   {"type": "integer","description": "Max results (default 20)"},
                    "q":       {"type": "string", "description": "Search query (for search action)"},
                    "kind":    {"type": "string", "description": "Search kind: code|issues|repos (default code)"},
                    "ref":     {"type": "string", "description": "File ref in owner/repo@branch:path format (for read_file)"},
                    "cwd":     {"type": "string", "description": "Working directory (git repo root)"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": (
                "Fast file-pattern search. Returns a sorted list of matching file paths. "
                "Supports ** recursive globs: e.g. '**/*.py', 'src/**/*.ts', '*.json'. "
                "Use this to discover files before reading them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern (e.g. **/*.py, src/**/*.ts)"},
                    "path":    {"type": "string", "description": "Root directory to search (default: current dir)"},
                    "limit":   {"type": "integer","description": "Max files to return (default 200)"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notebook_read",
            "description": "Read a Jupyter notebook (.ipynb) — returns all cells with source and outputs as formatted text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the .ipynb file"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notebook_edit",
            "description": "Edit a specific cell in a Jupyter notebook by its index (0-based). Clears cell outputs after edit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":        {"type": "string",  "description": "Path to the .ipynb file"},
                    "cell_index":  {"type": "integer", "description": "0-based cell index to replace"},
                    "new_source":  {"type": "string",  "description": "New cell source code/text"},
                },
                "required": ["path", "cell_index", "new_source"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_data",
            "description": (
                "Fetch real-time market data for any stock, ETF, index, or cryptocurrency. "
                "Returns price, change, high/low, volume, RSI(14), MACD histogram, MA20/60, "
                "Bollinger Bands. Supports: US tickers (AAPL, NVDA), A-shares (6-digit code like 600519), "
                "HK stocks (0700.HK), crypto (BTC, ETH), indices (SPY, QQQ). "
                "You must look up the correct ticker symbol yourself — e.g. LVMH → MC.PA, "
                "路易威登/路易斯威登 → MC.PA or LVMUY, 宝马 → BMW.DE, 大众 → VWAGY."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": (
                            "Ticker symbol. Examples: AAPL, NVDA, 600519, 0700.HK, BTC, MC.PA. "
                            "For A-shares use the 6-digit code without exchange suffix. "
                            "Do NOT guess — if unsure about a ticker, say so and ask the user."
                        ),
                    },
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "broker_query",
            "description": (
                "Query a connected brokerage account for account balance, current holdings (positions), "
                "or order history. Use this when the user asks about their portfolio, cash balance, "
                "unrealized P&L, or recent orders. This tool is READ-ONLY — it never places or cancels orders. "
                "Call with query='account' for cash/balance, query='positions' for holdings, "
                "query='orders' for order history."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "enum": ["account", "positions", "orders"],
                        "description": "What to query: 'account' = cash/balance, 'positions' = holdings, 'orders' = order list",
                    },
                    "broker_id": {
                        "type": "string",
                        "description": f"Optional broker id from {CONFIG_DIR}/brokers.json. Omit to use the active/default broker.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["all", "open", "filled", "cancelled"],
                        "description": "For orders query: filter by status. Default 'all'.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of orders to return (default 20).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "broker_order",
            "description": (
                "Propose a trade order (buy or sell). "
                "IMPORTANT: This tool requires explicit user confirmation. "
                "When called without confirmed=true, it returns an order preview with a "
                "confirmation prompt. Only set confirmed=true after the user has explicitly "
                "said '确认下单', 'confirm order', or equivalent in this conversation turn. "
                "NEVER set confirmed=true on your own initiative."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Stock/ETF ticker symbol, e.g. AAPL, 600519, 0700.HK",
                    },
                    "side": {
                        "type": "string",
                        "enum": ["buy", "sell"],
                        "description": "Trade direction: 'buy' to purchase, 'sell' to liquidate",
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "Number of shares/units to trade (positive integer)",
                    },
                    "price": {
                        "type": "number",
                        "description": "Limit price. Omit for market orders.",
                    },
                    "order_type": {
                        "type": "string",
                        "enum": ["limit", "market"],
                        "description": "Order type: 'limit' (default) or 'market'",
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "Set to true ONLY after the user explicitly confirmed the order in this turn.",
                    },
                },
                "required": ["symbol", "side", "quantity"],
            },
        },
    },
]

# Append computer-use schemas if the module loaded successfully
if _HAS_COMPUTER_USE:
    LOCAL_TOOL_SCHEMAS.extend(_CU_SCHEMAS)


# Tools that require user confirmation before execution
_CONFIRM_TOOLS = {"write_file", "edit_file", "run_command"}
# In bot mode (ARIA_BOT_MODE=1): auto-approve all tools and suppress visual output
_ARIA_BOT_MODE: bool = bool(os.environ.get("ARIA_BOT_MODE"))
_auto_approve_session: bool = _ARIA_BOT_MODE  # Set True when user chooses "Yes, allow all"

# Per-tool session allow list — populated by "Always allow [tool] this session" choice.
# More granular than _auto_approve_session: allows write_file without approving run_command.
_session_always_allow: set = set()

# Load JSON hooks once at startup; reloaded on demand via /hooks reload
try:
    from apps.cli.hooks import load_hooks as _load_hooks, fire as _fire_json_hook
    _JSON_HOOKS: dict = _load_hooks()
    _HAS_JSON_HOOKS = True
except Exception:
    _JSON_HOOKS = {}
    _HAS_JSON_HOOKS = False

def _show_edit_preview(params: dict):
    """Show a diff preview for edit_file (Claude Code style, Panel-boxed)."""
    if _ARIA_BOT_MODE:
        return
    path = params.get("path", "")
    old_str = params.get("old_string", params.get("old_str", ""))
    new_str = params.get("new_string", params.get("new_str", ""))
    if not path or not old_str:
        return

    p = pathlib.Path(path).expanduser().resolve()
    try:
        tw = os.get_terminal_size().columns
    except Exception:
        tw = 80
    short = str(p)
    if len(short) > tw - 10:
        short = "…" + short[-(tw - 11):]

    if not HAS_RICH:
        print(f"\n  Edit file  {short}")
        return

    body_parts: list = []
    try:
        content = p.read_text(errors="replace")
        pos = content.find(old_str)
        if pos >= 0:
            line_num = content[:pos].count("\n") + 1
            all_lines = content.splitlines()
            old_lines = old_str.splitlines()
            new_lines = new_str.splitlines()

            # Context before (up to 2 lines)
            ctx_start = max(0, line_num - 3)
            for i in range(ctx_start, line_num - 1):
                if i < len(all_lines):
                    body_parts.append(f"[dim]{i+1:4}  {all_lines[i][:100]}[/dim]")

            # Removed lines
            for i, ol in enumerate(old_lines):
                ln = line_num + i
                body_parts.append(f"[red]{ln:4} -  {ol[:100]}[/red]")

            # Added lines
            for i, nl in enumerate(new_lines):
                ln = line_num + i
                body_parts.append(f"[green]{ln:4} +  {nl[:100]}[/green]")

            # Context after (up to 2 lines)
            after_start = line_num - 1 + len(old_lines)
            for i in range(after_start, min(after_start + 2, len(all_lines))):
                body_parts.append(f"[dim]{i+1:4}  {all_lines[i][:100]}[/dim]")
        else:
            # String not found — fallback to plain diff lines
            for ol in old_str.splitlines()[:6]:
                body_parts.append(f"[red]-  {ol[:100]}[/red]")
            for nl in new_str.splitlines()[:6]:
                body_parts.append(f"[green]+  {nl[:100]}[/green]")
    except Exception:
        for ol in old_str.splitlines()[:6]:
            body_parts.append(f"[red]-  {ol[:100]}[/red]")
        for nl in new_str.splitlines()[:6]:
            body_parts.append(f"[green]+  {nl[:100]}[/green]")

    console.print()
    console.print(Panel(
        "\n".join(body_parts) if body_parts else "[dim](no preview)[/dim]",
        title=f"[yellow]Edit file[/yellow] [dim]{short}[/dim]",
        title_align="left",
        border_style="yellow",
        box=rich_box.ROUNDED,
        padding=(0, 1),
    ))


def _show_write_preview(params: dict):
    """Show a content preview for write_file (Claude Code style, Panel-boxed)."""
    if _ARIA_BOT_MODE:
        return
    path = params.get("path", "")
    content = params.get("content", "")
    if not path:
        return
    # Show cleaned content (without markdown fences)
    content = _strip_markdown_fences(content)

    p = pathlib.Path(path).expanduser().resolve()
    try:
        tw = os.get_terminal_size().columns
    except Exception:
        tw = 80
    short = str(p)
    if len(short) > tw - 10:
        short = "…" + short[-(tw - 11):]

    existed = p.exists()
    action = "Overwrite file" if existed else "Write new file"
    action_color = "yellow" if existed else "green"
    lines = content.count("\n") + 1

    if not HAS_RICH:
        print(f"\n  {action}  {short} ({lines} lines)")
        return

    preview_lines = content.splitlines()[:8]
    body_parts = [f"[green]+ {pl[:100]}[/green]" for pl in preview_lines]
    if lines > 8:
        n = lines - 8
        body_parts.append(f"[dim]… +{n} more line{'s' if n != 1 else ''}[/dim]")
    body = "\n".join(body_parts)

    console.print()
    console.print(Panel(
        body,
        title=f"[{action_color}]{action}[/{action_color}] [dim]{short}  ({lines} lines)[/dim]",
        title_align="left",
        border_style=action_color if existed else "dim",
        box=rich_box.ROUNDED,
        padding=(0, 1),
    ))


def _apply_tool_approval(params: dict, decision: ApprovalDecision) -> dict:
    """Apply approval state to CLI globals and execution params."""
    global _auto_approve_session, _session_always_allow
    if decision.auto_approve_session:
        _auto_approve_session = True
    # Per-tool always_allow: stored in decision's extra metadata field
    _tool_always = getattr(decision, "_tool_always_allow", None)
    if _tool_always:
        _session_always_allow.add(_tool_always)
    return apply_approval_decision(params, decision)


def _confirm_tool_execution_decision(tool_name: str, params: dict,
                                     config_policy: str = None) -> ApprovalDecision:
    """Ask user to confirm before executing a destructive tool.
    Returns a structured approval decision.

    For run_command: pre-flight policy check happens HERE, before showing the
    picker. If the command would be blocked even with user approval (high-risk),
    show error immediately. If medium-risk with 'safe' policy, offer to upgrade
    policy inline so the user can act without leaving the flow.
    """
    if config_policy is None:
        config_policy = _ACTIVE_COMMAND_POLICY[0]
    if _auto_approve_session:
        # Still inject policy so run_command doesn't re-block
        if tool_name == "run_command":
            return ApprovalDecision.allow(policy=config_policy, user_approved=True)
        return ApprovalDecision.allow()
    # Per-tool session allow — user previously chose "Always allow [tool] this session"
    if tool_name in _session_always_allow:
        if tool_name == "run_command":
            return ApprovalDecision.allow(policy=config_policy, user_approved=True)
        return ApprovalDecision.allow()
    if tool_name not in _CONFIRM_TOOLS:
        return ApprovalDecision.allow()

    # ── JSON PreToolUse hook — can block execution ────────────────────────────
    if _HAS_JSON_HOOKS and _JSON_HOOKS.get("PreToolUse"):
        _allowed = _fire_json_hook(
            "PreToolUse", tool=tool_name, params=params, hooks=_JSON_HOOKS,
        )
        if not _allowed:
            return ApprovalDecision.deny(f"Blocked by PreToolUse hook")

    # ── Pre-flight for run_command ────────────────────────────────────────────
    if tool_name == "run_command":
        from safety import classify_command_risk
        cmd = params.get("command", "")
        risk = classify_command_risk(cmd)

        if risk == "high":
            # Always block high-risk regardless of user approval
            if HAS_RICH:
                console.print(Panel(
                    f"[red]✗ 高风险命令已拒绝[/red]\n[dim]{cmd[:120]}[/dim]\n"
                    f"[dim]高风险操作（rm -rf / docker / sudo 等）需要在终端手动执行。[/dim]",
                    border_style="red", box=rich_box.ROUNDED, padding=(0, 1),
                ))
            else:
                print(f"  ✗ 高风险命令已拒绝: {cmd[:80]}")
            return ApprovalDecision.deny("high-risk command")

        if risk == "medium" and config_policy == "safe":
            # Show a richer picker that includes a "Allow & upgrade policy" option
            if HAS_RICH:
                console.print()
                console.print(Panel(
                    f"[yellow]⚠ 此命令需要 balanced 策略（当前: safe）[/yellow]\n"
                    f"[dim]{cmd[:120]}[/dim]",
                    border_style="yellow", box=rich_box.ROUNDED, padding=(0, 1),
                ))
            options = [
                ("Allow once",         "仅此次允许（不改变策略）"),
                ("Allow & set balanced","允许并升级策略（本会话有效）"),
                ("Yes, allow all",     "本会话内所有命令自动允许"),
                ("No",                 "拒绝执行"),
            ]
            choice = _arrow_select(options, selected=0, title="")
            if choice == 0:
                return ApprovalDecision.allow(policy="balanced", user_approved=True)
            if choice == 1:
                # Persist to config if possible
                return ApprovalDecision.allow(
                    policy="balanced",
                    user_approved=True,
                    upgrade_policy=True,
                )
            if choice == 2:
                return ApprovalDecision.allow(
                    policy="balanced",
                    user_approved=True,
                    auto_approve_session=True,
                )
            return ApprovalDecision.deny("user denied")   # No

    # ── Default confirmation for write_file / edit_file / low-risk run ────────
    if tool_name == "edit_file":
        _show_edit_preview(params)
    elif tool_name == "write_file":
        _show_write_preview(params)
    elif tool_name == "run_command":
        # Header already printed by on_tool_call — just pass through policy
        pass

    _tool_label = {"write_file": "写文件", "edit_file": "编辑文件", "run_command": "运行命令"}.get(tool_name, tool_name)
    options = [
        ("Yes",                              ""),
        (f"Always allow {_tool_label}",      f"本会话内自动允许所有 {_tool_label}"),
        ("Yes, allow all tools",             "本会话内所有工具自动允许"),
        ("No",                               ""),
    ]
    choice = _arrow_select(options, selected=0, title="")

    if choice == 0:
        if tool_name == "run_command":
            return ApprovalDecision.allow(policy=config_policy, user_approved=True)
        return ApprovalDecision.allow()
    if choice == 1:
        # Per-tool always_allow for this session
        d = ApprovalDecision.allow(
            policy=config_policy if tool_name == "run_command" else None,
            user_approved=True,
        )
        d._tool_always_allow = tool_name  # type: ignore[attr-defined]
        return d
    if choice == 2:
        if tool_name == "run_command":
            return ApprovalDecision.allow(
                policy=config_policy,
                user_approved=True,
                auto_approve_session=True,
            )
        return ApprovalDecision.allow(auto_approve_session=True)
    return ApprovalDecision.deny("user denied")




def execute_local_tool(tool_name: str, params: dict) -> dict:
    """Execute a local tool by name."""
    executor = ToolExecutor(
        LOCAL_TOOLS,
        hook=_run_hook,
        config={
            "command_policy": _ACTIVE_COMMAND_POLICY[0],
            "permission_mode": _ACTIVE_PERMISSION_MODE[0],
            "network_enabled": _ACTIVE_NETWORK_ENABLED[0],
        },
    )
    return executor.execute_local(tool_name, params)


def _run_hook(hook_type: str, tool_name: str, params: dict, result: dict = None) -> None:
    """Fire-and-forget hook execution from .ariarc hooks config.

    hook_type: "pre_tool" | "post_tool" | "on_error"
    Hooks are shell commands with {key} template substitution from params/result.

    Example .ariarc:
      "hooks": {
        "pre_tool":  {"write_file": "echo 'Writing: {path}'"},
        "post_tool": {"run_command": "notify-send 'Done'"},
        "on_error":  "echo 'Error: {error}'"
      }
    """
    if not _HAS_ARIARC:
        return
    try:
        _arc = get_ariarc()
        hooks = _arc.data.get("hooks", {}) if hasattr(_arc, "data") else {}
        if not hooks:
            return
        hook_spec = hooks.get(hook_type, {})
        # hook_spec can be: dict keyed by tool_name, or bare string for all tools
        if isinstance(hook_spec, dict):
            cmd = hook_spec.get(tool_name) or hook_spec.get("*")
        else:
            cmd = hook_spec  # bare string applies to all tools
        if not cmd:
            return
        # Template substitution: {path}, {command}, {error}, etc.
        fmt_ctx: Dict[str, str] = {k: str(v) for k, v in (params or {}).items()}
        if result:
            fmt_ctx["error"] = str(result.get("error", ""))
            fmt_ctx["success"] = str(result.get("success", ""))
        try:
            cmd = cmd.format_map(fmt_ctx)
        except (KeyError, ValueError):
            pass  # Ignore missing keys in template
        import subprocess as _sp
        _sp.run(cmd, shell=True, timeout=5, capture_output=True)
    except Exception:
        pass  # Hooks must never crash the main flow

    # Also fire JSON hooks (PostToolUse / PreToolUse)
    if _HAS_JSON_HOOKS:
        try:
            _event = "PostToolUse" if hook_type == "post_tool" else (
                "PreToolUse" if hook_type == "pre_tool" else None
            )
            if _event:
                _fire_json_hook(
                    _event, tool=tool_name, params=params, result=result,
                    hooks=_JSON_HOOKS,
                )
        except Exception:
            pass


# TTL cache for read-only tool responses
_TOOL_CACHE: Dict[str, tuple] = {}  # key -> (result, timestamp)
_CACHE_TTL = {
    "get_market_data": 30, "get_crypto_data": 30, "get_forex_data": 30,
    "get_commodities_data": 60, "get_bonds_data": 60, "get_futures_data": 60,
    "get_news": 300, "get_sector_performance": 60, "get_market_overview": 60,
}


async def execute_aria_tool(base_url: str, tool_name: str, params: dict,
                           timeout: int = 30, auth_token: str = None,
                           max_retries: int = 2) -> dict:
    """Execute an Aria tool via the backend API with auto-retry and TTL cache."""
    # --- Parameter validation before sending to API ---
    _symbol_tools = {
        "get_market_data", "get_risk_metrics", "calculate_factors",
        "get_alpha158_factors", "assess_portfolio_risk",
    }
    _date_tools = {"backtest_strategy", "stress_test_strategy"}

    if tool_name in _symbol_tools and "symbol" in params:
        sym = str(params["symbol"]).strip().upper()
        if not re_module.match(r'^[A-Z0-9.\-/=]{1,12}$', sym):
            return {"success": False, "error": f"Invalid symbol format: '{sym}'"}
        params = {**params, "symbol": sym}

    if tool_name in _date_tools:
        for date_key in ("start_date", "end_date", "start", "end"):
            if date_key in params:
                date_val = str(params[date_key]).strip()
                if not re_module.match(r'^\d{4}-\d{2}-\d{2}$', date_val):
                    return {"success": False, "error": f"Invalid date format for '{date_key}': '{date_val}' (expected YYYY-MM-DD)"}
        # Check chronological order
        start = params.get("start_date") or params.get("start")
        end = params.get("end_date") or params.get("end")
        if start and end and start > end:
            return {"success": False, "error": f"start_date ({start}) must be before end_date ({end})"}

    # Check cache for read-only tools
    ttl = _CACHE_TTL.get(tool_name)
    if ttl:
        cache_key = f"{tool_name}:{json.dumps(params, sort_keys=True)}"
        cached = _TOOL_CACHE.get(cache_key)
        if cached and (time.time() - cached[1]) < ttl:
            return cached[0]
    import aiohttp
    url = f"{base_url}/api/aria/execute-tool"
    payload = {"tool_name": tool_name, "params": params}
    headers = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                    result = await resp.json()
                    if result.get("success") or attempt >= max_retries:
                        # Cache successful results for read-only tools
                        if result.get("success") and ttl:
                            _TOOL_CACHE[cache_key] = (result, time.time())
                        return result
                    last_error = result.get("error", "Unknown error")
        except Exception as e:
            last_error = str(e)
        if attempt < max_retries:
            await asyncio.sleep(1 * (attempt + 1))  # 1s, 2s backoff
    return {"success": False, "error": f"Failed after {max_retries + 1} attempts: {last_error}"}


# ============================================================================
# Ollama Local Client (fallback when AWS unavailable)
# ============================================================================

from apps.cli.prompts.coding import CODING_SYSTEM_PROMPT  # noqa: F401 — extracted


def _detect_lang(text: str) -> str:
    """Thin shim — implementation in apps/cli/prompts/system_prompts.py."""
    from apps.cli.prompts.system_prompts import detect_lang as _f
    return _f(text)


from apps.cli.prompts.system_prompts import LANG_RULE as _LANG_RULE


def _build_coding_prompt_lite(user_message: str) -> str:
    """Thin shim — implementation in apps/cli/prompts/system_prompts.py."""
    from apps.cli.prompts.system_prompts import build_coding_prompt_lite as _f
    return _f(user_message)


def _build_analysis_prompt_lite(user_message: str) -> str:
    """Thin shim — implementation in apps/cli/prompts/system_prompts.py."""
    from apps.cli.prompts.system_prompts import build_analysis_prompt_lite as _f
    return _f(user_message)


# NOTE: FINANCE_CHAT_PROMPT is a function now — it injects the current date dynamically.
def _build_finance_prompt(user_message: str = "") -> str:
    """Thin shim — implementation in apps/cli/prompts/system_prompts.py."""
    from apps.cli.prompts.system_prompts import build_finance_prompt as _f
    return _f(user_message)

FINANCE_CHAT_PROMPT = _build_finance_prompt()  # evaluated once at import; rebuilt per stream call

# ============================================================================
# ANALYSIS_SYSTEM_PROMPT: for stock/crypto/macro analysis queries that need
# real data via tool calls but don't require writing Python scripts
# ============================================================================

def _build_analysis_system_prompt() -> str:
    """Thin shim — implementation in apps/cli/prompts/system_prompts.py."""
    from apps.cli.prompts.system_prompts import build_analysis_system_prompt as _f
    return _f()

ANALYSIS_SYSTEM_PROMPT = _build_analysis_system_prompt()


def _build_prefetched_analysis_prompt(nano: bool = False) -> str:
    """Thin shim — implementation in apps/cli/prompts/system_prompts.py."""
    from apps.cli.prompts.system_prompts import build_prefetched_analysis_prompt as _f
    return _f(nano=nano)


# ── LaTeX → plain-text converter ────────────────────────────────────────────
import re as _re_latex

# Delegate to the canonical formula renderer when available
try:
    from finance_formulas import (
        FORMULA_PROMPT_BLOCK_CORE as _FORMULA_PROMPT_BLOCK_CORE,
        strip_latex_for_cli as _strip_latex_impl,
    )
    _HAS_FORMULA_LIB = True
except ImportError:
    _HAS_FORMULA_LIB = False
    _FORMULA_PROMPT_BLOCK_CORE = ""
    _strip_latex_impl = None  # type: ignore


def _strip_latex(text: str) -> str:
    """Convert LaTeX math notation to readable plain-text for terminal display.

    Delegates to finance_formulas.strip_latex_for_cli when available (preferred).
    Falls back to the legacy inline implementation otherwise.
    """
    if "\\" not in text and "$" not in text:
        return text

    if _HAS_FORMULA_LIB and _strip_latex_impl is not None:
        return _strip_latex_impl(text)

    # ── Legacy fallback (finance_formulas not importable) ───────────────────

    # Display-math blocks: \[ ... \] → ▶ prefix
    text = _re_latex.sub(r'\\\[\s*',   '\n  ▶ ', text)
    text = _re_latex.sub(r'\s*\\\]',   '\n',      text)
    text = _re_latex.sub(
        r'\$\$(.+?)\$\$',
        lambda m: '\n  ▶ ' + m.group(1).strip() + '\n',
        text, flags=_re_latex.DOTALL,
    )

    # Common math symbols — simple string replace (no regex needed)
    # Key: actual backslash + command name (Python string '\\sum' = \sum)
    _SYM = {
        '\\sum':'Σ', '\\prod':'Π', '\\int':'∫', '\\infty':'∞',
        '\\alpha':'α', '\\beta':'β', '\\gamma':'γ', '\\delta':'δ',
        '\\theta':'θ', '\\lambda':'λ', '\\mu':'μ', '\\sigma':'σ',
        '\\tau':'τ', '\\phi':'φ', '\\psi':'ψ', '\\omega':'ω',
        '\\pi':'π', '\\rho':'ρ', '\\epsilon':'ε',
        '\\times':'×', '\\cdot':'·', '\\pm':'±',
        '\\leq':'≤', '\\geq':'≥', '\\neq':'≠', '\\approx':'≈',
        '\\to':'→', '\\Rightarrow':'⇒', '\\partial':'∂',
        '\\forall':'∀', '\\exists':'∃', '\\in':'∈', '\\notin':'∉',
        '\\cup':'∪', '\\cap':'∩', '\\subset':'⊂',
        '\\ldots':'…', '\\cdots':'…', '\\left':'', '\\right':'',
        # LaTeX spacing commands — ';,:,!' are NOT caught by \\[A-Za-z]+ regex
        '\\;':' ', '\\,':'', '\\:':' ', '\\!':'',
        '\\quad':'  ', '\\qquad':'   ',
    }
    for cmd, sym in _SYM.items():
        text = text.replace(cmd, sym)

    # \text{X} \mathbf{X} \mathrm{X} \hat{X} etc → X
    # Use a single pattern that matches any \word{...}
    text = _re_latex.sub(
        r'\\(?:text|mathbf|mathrm|mathit|mathcal|boldsymbol|hat|bar|tilde|vec|overline|underline)\{([^{}]*)\}',
        r'\1', text,
    )

    # \frac{a}{b} → (a)/(b)
    for _ in range(3):   # handle nested fracs up to 3 deep
        text = _re_latex.sub(r'\\frac\{([^{}]*)\}\{([^{}]*)\}', r'(\1)/(\2)', text)
    # \sqrt{x} → √(x)
    text = _re_latex.sub(r'\\sqrt\{([^{}]*)\}', r'√(\1)', text)

    # ^{exp} → ^exp,   _{sub} → _sub
    text = _re_latex.sub(r'\^\{([^{}]{1,10})\}', r'^\1', text)
    text = _re_latex.sub(r'_\{([^{}]{1,10})\}',  r'_\1', text)

    # Non-alpha spacing commands: \; \, \: \! — not caught by \\[A-Za-z]+
    text = _re_latex.sub(r'\\[;,!:]', ' ', text)

    # Strip any remaining \command  (bare backslash commands with no braces)
    text = _re_latex.sub(r'\\([A-Za-z]+)', r'\1', text)

    # Inline math: $...$  → content only
    text = _re_latex.sub(r'\$([^$\n]{1,80})\$', r'\1', text)

    # Remove lone parens around single chars created by frac simplification
    text = _re_latex.sub(r'\(([A-Za-z0-9_^])\)/\(([A-Za-z0-9_^])\)', r'\1/\2', text)

    return text


# Intent classification — thin shims over apps/cli/intent.py
from apps.cli.intent import (
    CODING_KEYWORDS as _CODING_KEYWORDS,
    ANALYSIS_KEYWORDS as _ANALYSIS_KEYWORDS,
    ANALYSIS_NON_STOCK_TOPICS as _ANALYSIS_NON_STOCK_TOPICS,
    GENERAL_KNOWLEDGE_KEYWORDS as _GENERAL_KNOWLEDGE_KEYWORDS,
    FINANCE_CONCEPT_TERMS as _FINANCE_CONCEPT_TERMS,
    SPORTS_KEYWORDS as _SPORTS_KEYWORDS,
    is_coding_request as _is_coding_request,
    is_analysis_request as _is_analysis_request,
    is_general_knowledge as _is_general_knowledge,
    is_sports_query as _is_sports_query,
)


def _try_prefetch_sports_data(message: str) -> str:
    """Attempt to fetch live sports data relevant to the query."""
    try:
        from football_data_client import get_sports_context_for_query
        ctx = get_sports_context_for_query(message)
        return ctx or ""
    except Exception:
        return ""


def _load_project_context() -> str:
    """Load ARIA.md / CLAUDE.md by walking up from cwd (Claude Code style).

    Search order per directory: ARIA.md → .aria.md → CLAUDE.md
    Walks up at most 5 levels, stops at home dir.
    Multiple files are concatenated (child file takes precedence at top).
    Total cap: 12KB.
    """
    _MAX_BYTES = 12288
    _MAX_LEVELS = 5
    _NAMES = ("ARIA.md", ".aria.md", "CLAUDE.md")

    home = pathlib.Path.home()
    cwd  = pathlib.Path.cwd().resolve()

    found: list[tuple[pathlib.Path, str]] = []  # (file_path, content)
    current = cwd
    for _ in range(_MAX_LEVELS):
        for name in _NAMES:
            p = current / name
            if p.is_file():
                try:
                    content = p.read_text(encoding="utf-8")
                    found.append((p, content))
                except Exception:
                    pass
                break  # only one file per directory level
        if current == home or current.parent == current:
            break
        current = current.parent

    if not found:
        return ""

    # Child directories first (most specific context wins), then parents
    blocks: list[str] = []
    total = 0
    for fpath, content in found:
        rel = fpath.relative_to(home) if fpath.is_relative_to(home) else fpath
        snippet = content[:(_MAX_BYTES - total)]
        blocks.append(f"### {rel}\n{snippet}")
        total += len(snippet)
        if total >= _MAX_BYTES:
            break

    return "\n\n## Project Context\n" + "\n\n".join(blocks)


def _refresh_project_context() -> str:
    """Re-scan for ARIA.md (call at session start or /reload)."""
    global _PROJECT_CONTEXT
    _PROJECT_CONTEXT = _load_project_context()
    return _PROJECT_CONTEXT


# Cache project context at module level (refreshed per session)
_PROJECT_CONTEXT = _load_project_context()


def _fix_json_string(raw: str) -> str:
    """Fix common JSON issues from LLM output (triple quotes, unescaped newlines)."""
    # Fix Python triple-quoted strings: """...""" → proper JSON string
    triple_pattern = re_module.compile(r'"""\s*\n([\s\S]*?)"""')
    def _replace_triple(m):
        content = m.group(1)
        # Escape for JSON: backslashes, quotes, newlines
        content = content.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
        return '"' + content + '"'
    fixed = triple_pattern.sub(_replace_triple, raw)
    return fixed


def _parse_text_tool_calls(text: str) -> list:
    """Thin shim — implementation in apps/cli/message_processing.py."""
    from apps.cli.message_processing import parse_text_tool_calls as _f
    return _f(text)


def _strip_tool_call_tags(text: str) -> str:
    """Thin shim — implementation in apps/cli/message_processing.py."""
    from apps.cli.message_processing import strip_tool_call_tags as _f
    return _f(text)


def _compact_messages(messages: list, max_chars: int = 0, model_key: str = "qwen7b") -> list:
    """Thin shim — implementation in apps/cli/message_processing.py."""
    from apps.cli.message_processing import compact_messages as _f
    return _f(messages, max_chars=max_chars, model_key=model_key)




def _build_broker_context_block() -> str:
    """Thin shim — implementation in apps/cli/message_processing.py."""
    from apps.cli.message_processing import build_broker_context_block as _f
    return _f()






def _try_prefetch_market_data(message: str, history: list = None) -> str:
    """Thin wrapper — real implementation in apps.cli.handlers.market_handlers."""
    return _src_prefetch_market_data(message, history)


import re as _re_fi

# Matches absolute/relative paths and bare filenames with known extensions.
# Single capturing group so findall always returns the full matched path string.
_FILE_PATH_RE = _re_fi.compile(
    r'('
    r'(?:~/|\.{1,2}/|/(?:Users|home|workspace|tmp|private/tmp|var|private/var)/)\S+'  # abs/rel paths
    r'|'
    r'(?<!\w)[\w./-]{3,}\.(?:py|js|ts|json|yaml|yml|md|txt|csv|toml|sh|cfg|ini|env|log)(?!\w)'  # bare filenames
    r')'
)
_FILE_INJECT_CAP = 8000  # total chars injected across all files in one message


def _try_inject_file_paths(message: str) -> str:
    """Pre-read local files referenced in the user message and inject their content.

    Works like _try_prefetch_market_data() but for file paths.  Only reads files
    that actually exist and pass _is_safe_path(), capped at 8 KB total.
    Returns "" when no file paths are found or readable.
    """
    raw_matches = _FILE_PATH_RE.findall(message)
    candidates = [m for m in raw_matches if m]
    if not candidates:
        return ""
    injected, total = [], 0
    seen: set = set()
    for raw in candidates[:6]:
        raw = raw.strip().rstrip("，,。.）)")
        if not raw or raw in seen:
            continue
        seen.add(raw)
        try:
            p = pathlib.Path(raw).expanduser().resolve()
        except Exception:
            continue
        if not p.is_file():
            continue
        try:
            if not _is_safe_path(p):
                continue
        except Exception:
            continue
        try:
            content = p.read_text(errors="replace")
            remaining = _FILE_INJECT_CAP - total
            if remaining <= 0:
                break
            chunk = content[:remaining]
            line_count = content.count("\n") + 1
            injected.append(
                f"\n## 📄 File: {p} ({line_count} lines)\n"
                f"```\n{chunk}\n```\n"
                + ("*[truncated]*\n" if len(content) > remaining else "")
            )
            total += len(chunk)
        except Exception:
            continue
    if not injected:
        return ""
    header = "*以下为用户消息中引用的本地文件内容，请基于这些内容回答：*\n"
    return header + "\n".join(injected) + "\n---\n"


def _check_memory_trigger(text: str) -> Optional[str]:
    """Detect memory-saving trigger phrases in the first 60 chars of the message.

    Returns the fact string to save, or None if no trigger is found.
    This powers the auto-memory feature: "记住：QQQ是我们的核心仓位" → appended to ARIA.md.
    """
    _TRIGGERS = (
        "记住：", "记住:", "记住 ",
        "remember that ", "note that ", "please note ", "don't forget ",
        "请记住：", "请记住:", "请记住 ",
    )
    low = text.lower()
    prefix = low[:60]
    for t in _TRIGGERS:
        if t in prefix:
            idx = text.lower().index(t) + len(t)
            fact = text[idx:].strip().lstrip(":： ").strip()
            return fact if fact else None
    return None


# Financial/analytical terms that look like tickers but are NOT stock symbols.
# Prevents the regex from matching "DCF", "EPS", "RSI", etc. as ticker codes.
























# _fetch_snapshot_row_for_symbol is now in apps.cli.handlers.market_handlers
# (kept as local alias for any direct callers in this file)
from apps.cli.handlers.market_handlers import _fetch_snapshot_row_for_symbol  # noqa


def _try_handle_multi_market_snapshot(message: str, symbols: list) -> dict:
    """Thin wrapper — real implementation in apps.cli.handlers.market_handlers."""
    return _src_multi_snapshot(message, symbols)


def _try_handle_realty_query(message: str) -> dict:
    return _src_handle_realty_query(
        message,
        is_realty_query=_is_realty_query,
        cn_cities=_CN_CITIES,
        intl_cities=_INTL_CITIES,
    )


def _try_handle_market_snapshot_analysis(message: str, history: list = None) -> dict:
    """Thin wrapper — real implementation in apps.cli.handlers.market_handlers."""
    return _src_market_snapshot_analysis(message, history)




def _fmt_int(value) -> str:
    try:
        return f"{int(float(value)):,}"
    except Exception:
        return "N/A"


def _display_value(value, digits: int = 2, suffix: str = "") -> str:
    try:
        if value in (None, "", "N/A", "-", "nan"):
            return "—"
        if isinstance(value, (int, float)):
            return f"{float(value):,.{digits}f}{suffix}"
        return str(value)
    except Exception:
        return "—"




def _generate_chart_sync(symbol: str, period: str = "1y") -> dict:
    """
    `/chart` 命令的同步入口：为指定 symbol 生成 HTML 分析图表。
    A股代码先尝试 tushare/akshare 获取数据，美股走 yfinance。
    """
    # 对 A股代码做格式转换（600519 → 600519.SS / 000001 → 000001.SZ）
    sym_yf = symbol
    if re.match(r"^\d{6}$", symbol):
        if symbol.startswith(("6", "9")):
            sym_yf = symbol + ".SS"
        else:
            sym_yf = symbol + ".SZ"

    return _try_handle_stock_chart_analysis_direct(sym_yf, period=period)


def _fetch_macro_data(indicator: str, country: str = "WLD", days: int = 365):
    """Fetch macro data from FRED or World Bank, return list of (date, value) tuples."""
    try:
        from datasources.sources.fred_source import FREDSource, MACRO_ALIASES
        if indicator.upper() in MACRO_ALIASES or indicator.upper() in MACRO_ALIASES.values():
            src = FREDSource()
            h = src.history(indicator, days=days)
            if h and h.data is not None and not h.data.empty:
                return [(str(idx.date()), float(row["close"])) for idx, row in h.data.iterrows()]
    except Exception as _e:
        pass
    try:
        from datasources.sources.world_bank_source import WorldBankSource
        src = WorldBankSource()
        h = src.history(f"{country}:{indicator}", days=days)
        if h and h.data is not None and not h.data.empty:
            return [(str(idx.date()), float(row["close"])) for idx, row in h.data.iterrows()]
    except Exception:
        pass
    return None


def _fetch_edgar_data(symbol: str, sub: str = "filings"):
    """Fetch SEC EDGAR data for a US stock."""
    try:
        from datasources.sources.edgar_source import EDGARSource
        src = EDGARSource()
        if sub == "filings":
            return src.get_recent_filings(symbol)
        elif sub == "facts":
            return src.get_company_facts(symbol)
        elif sub == "insider":
            return src.get_insider_trades(symbol)
    except Exception as _e:
        pass
    return None


def _test_datasource(name: str) -> None:
    """Test connectivity of a named data source."""
    try:
        from datasources.router import _SOURCE_REGISTRY
        cls = _SOURCE_REGISTRY.get(name.lower())
        if not cls:
            if HAS_RICH:
                console.print(f"  [red]未知数据源: {name}[/red]")
            return
        src = cls()
        if not src.is_configured():
            if HAS_RICH:
                console.print(f"  [yellow]⚠ {name} 未配置（缺少 API key）[/yellow]")
            return
        # Try a simple query
        test_symbol = "AAPL" if "us" in getattr(cls, "markets", []) else "600519"
        q = src.quote(test_symbol)
        if HAS_RICH:
            if q:
                console.print(f"  [green]✓ {name} 正常 — {test_symbol} = {q.price:.2f}[/green]")
            else:
                console.print(f"  [yellow]⚠ {name} 返回空数据[/yellow]")
    except Exception as e:
        if HAS_RICH:
            console.print(f"  [red]✗ {name} 失败: {e}[/red]")


def _generate_stat_arb_chart(sym_a: str, sym_b: str, period: str = "2y") -> None:
    """Generate interactive z-score history chart for stat-arb pair."""
    try:
        import yfinance as _yf
        import numpy as _np
        import pathlib as _pl
        import re as _re
        import json as _json

        raw = _yf.download([sym_a, sym_b], period=period, progress=False, auto_adjust=True)
        if raw.empty:
            return
        # Support both multi-level and flat column formats
        if isinstance(raw.columns, _pd.MultiIndex):
            prices = raw["Close"][[sym_a, sym_b]].dropna()
        else:
            prices = raw[["Close"]].rename(columns={"Close": sym_a}).dropna()
            return  # need both

        spread = prices[sym_a] - prices[sym_b]
        roll   = spread.rolling(60)
        z      = ((spread - roll.mean()) / roll.std()).dropna()

        x     = [d.strftime("%Y-%m-%d") for d in z.index]
        z_val = [round(float(v), 3) for v in z.values]

        entry_lo, entry_hi = -2.0, 2.0
        stop_lo, stop_hi   = -3.5, 3.5

        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>{sym_a}/{sym_b} Z-Score</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
</head><body style="background:#0d1117;color:#e6edf3;margin:0;padding:16px;font-family:monospace">
<h2 style="color:#58a6ff">{sym_a} / {sym_b} — 配对价差 Z-Score ({period})</h2>
<div id="chart" style="width:100%;height:500px"></div>
<script>
const x = {_json.dumps(x)};
const z = {_json.dumps(z_val)};
const last_z = z[z.length-1];
const colors = z.map(v => v > {entry_hi} || v < {entry_lo} ? (Math.abs(v) > {stop_hi} ? '#f85149' : '#f0883e') : '#58a6ff');
Plotly.newPlot('chart', [
  {{x, y: z, type:'scatter', mode:'lines', name:'Z-Score',
    line:{{color:'#58a6ff', width:1.5}}}},
  {{x:[x[0],x[x.length-1]], y:[{entry_hi},{entry_hi}], type:'scatter', mode:'lines',
    name:'做空阈值 (+{entry_hi})', line:{{color:'#f0883e', width:1, dash:'dot'}}}},
  {{x:[x[0],x[x.length-1]], y:[{entry_lo},{entry_lo}], type:'scatter', mode:'lines',
    name:'做多阈值 ({entry_lo})', line:{{color:'#3fb950', width:1, dash:'dot'}}}},
  {{x:[x[0],x[x.length-1]], y:[{stop_hi},{stop_hi}], type:'scatter', mode:'lines',
    name:'止损上轨 (+{stop_hi})', line:{{color:'#f85149', width:1, dash:'dash'}}}},
  {{x:[x[0],x[x.length-1]], y:[{stop_lo},{stop_lo}], type:'scatter', mode:'lines',
    name:'止损下轨 ({stop_lo})', line:{{color:'#f85149', width:1, dash:'dash'}}}},
  {{x:[x[0],x[x.length-1]], y:[0,0], type:'scatter', mode:'lines',
    name:'均值归零', line:{{color:'#8b949e', width:1}}}}
], {{
  paper_bgcolor:'#0d1117', plot_bgcolor:'#161b22',
  font:{{color:'#e6edf3', family:'monospace'}},
  xaxis:{{gridcolor:'#21262d', tickfont:{{size:10}}}},
  yaxis:{{gridcolor:'#21262d', title:'Z-Score'}},
  legend:{{bgcolor:'#161b22', bordercolor:'#30363d'}},
  annotations:[{{
    x:x[x.length-1], y:last_z, text:`当前 Z=${{last_z.toFixed(2)}}`,
    showarrow:true, arrowcolor:'#e6edf3',
    font:{{color:'#e6edf3', size:12}}, bgcolor:'#30363d'
  }}]
}}, {{responsive:true}});
</script></body></html>"""

        safe = _re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{sym_a}_{sym_b}")
        from artifacts import create_artifact
        art = create_artifact("reports/stat-arb", f"{sym_a}_{sym_b}", f"{safe}_zscore", ".html")
        art.path.write_text(html, encoding="utf-8")
        if HAS_RICH:
            console.print(f"  [dim]Z-Score 图表: [link={art.path}]{art.path}[/link][/dim]")
            import subprocess
            try:
                subprocess.Popen(["open", str(art.path)])
            except Exception:
                pass
    except Exception as _e:
        if HAS_RICH:
            console.print(f"  [dim]Z-Score 图表生成跳过: {_e}[/dim]")


def _try_handle_broker_query(message: str) -> dict:
    return _src_handle_broker_query(
        message,
        has_brokers=_HAS_BROKERS,
        is_broker_intent=_is_broker_intent,
        get_broker_registry=_get_broker_registry,
    )


def _try_handle_stock_chart_analysis_direct(symbol: str, period: str = "1y") -> dict:
    return _src_chart_analysis_direct(symbol, period=period)


def _try_handle_stock_chart_analysis(message: str) -> dict:
    return _src_chart_analysis(
        message,
        is_chart_request=_is_stock_chart_analysis_request,
        extract_symbol=_extract_market_symbol,
    )


from apps.cli.providers.llm.ollama_stream import stream_ollama as _stream_ollama_src
import types as _types_rebind
stream_ollama = _types_rebind.FunctionType(
    _stream_ollama_src.__code__, globals(), "stream_ollama",
    _stream_ollama_src.__defaults__, _stream_ollama_src.__closure__
)
del _types_rebind

# ============================================================================
# Aria SSE Stream Client — cancel + auth + user context
# ============================================================================

async def stream_chat(base_url: str, message: str, history: list,
                      model: str = "qwen2.5:7b", thinking_mode: str = "auto",
                      user_context: dict = None, auth_token: str = None,
                      on_token=None, on_thinking=None, on_tool_call=None,
                      on_tool_result=None, on_status=None,
                      cancel_event: asyncio.Event = None) -> dict:
    """Thin shim — implementation lives in apps/cli/providers/llm/sse_stream.py."""
    from apps.cli.providers.llm.sse_stream import stream_chat as _stream_chat
    return await _stream_chat(
        base_url, message, history,
        model=model, thinking_mode=thinking_mode,
        user_context=user_context, auth_token=auth_token,
        on_token=on_token, on_thinking=on_thinking,
        on_tool_call=on_tool_call, on_tool_result=on_tool_result,
        on_status=on_status, cancel_event=cancel_event,
        project_context=_PROJECT_CONTEXT,
    )



def _extract_code_block(text: str) -> Optional[str]:
    """Extract the first code block from markdown-formatted text."""
    import re
    # Match ```python ... ``` or ``` ... ```
    pattern = r'```(?:python|py)?\s*\n(.*?)```'
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fallback: try to find any code block
    pattern2 = r'```\w*\s*\n(.*?)```'
    match2 = re.search(pattern2, text, re.DOTALL)
    if match2:
        return match2.group(1).strip()
    return None


def _build_user_context(config: dict) -> Optional[dict]:
    """Build user context from config for personalized AI responses."""
    ctx = {}
    watchlist = config.get("watchlist", [])
    if watchlist:
        ctx["watchlist"] = watchlist
    user_id = config.get("user_id")
    if user_id:
        ctx["user_id"] = user_id
    # Inject current datetime and session info
    now = datetime.now()
    ctx["current_datetime"] = now.strftime("%Y-%m-%d %H:%M")
    ctx["day_of_week"] = now.strftime("%A")
    # US market session heuristic (Mon-Fri, approximate ET hours)
    weekday = now.weekday()  # 0=Mon, 6=Sun
    hour_min = now.hour * 60 + now.minute
    if weekday < 5 and 570 <= hour_min <= 960:  # 9:30am-4:00pm
        ctx["market_status"] = "open"
    elif weekday < 5 and (240 <= hour_min < 570 or 960 <= hour_min < 1200):
        ctx["market_status"] = "pre/after-hours"
    else:
        ctx["market_status"] = "closed"
    # Active model name
    model_id = config.get("model", "qwen2.5:7b")
    mkey = resolve_model_key(model_id)
    minfo = MODELS.get(mkey, {})
    ctx["ai_model"] = minfo.get("name", model_id)
    if config.get("local_mode"):
        ctx["inference_mode"] = "local"
    return ctx if ctx else None


# ============================================================================
# Tool Output Formatters
# ============================================================================

def _clean_tool_error_message(error: object) -> str:
    from ui.render.output import clean_tool_error_message as _ctm
    return _ctm(error)


def _format_tool_summary(tool_name: str, result: dict) -> str:
    """Format tool result into a concise summary for AI follow-up context."""
    if not result.get("success"):
        return f"Error: {_clean_tool_error_message(result.get('error', 'failed'))}"
    data = result.get("data", {})
    if tool_name == "run_command":
        exit_code = data.get("exit_code", -1)
        stdout = data.get("stdout", "").strip()
        stderr = data.get("stderr", "").strip()
        out = f"exit_code={exit_code}"
        if stdout:
            out += f"\nstdout:\n{stdout[:2000]}"
        if stderr and exit_code != 0:
            out += f"\nstderr:\n{stderr[:500]}"
        # Add actionable hints for common errors
        if exit_code != 0:
            combined = (stdout + " " + stderr).lower()
            combined_raw = stdout + " " + stderr
            if "can't open file" in combined or "no such file" in combined:
                out += "\n\nHINT: The file does not exist. You must create it with write_file first, then run it."
            elif "modulenotfounderror" in combined or "no module named" in combined:
                # Extract module name
                import re as _re
                mod_match = _re.search(r"no module named ['\"]?(\w+)", combined)
                mod_name = mod_match.group(1) if mod_match else "<module_name>"
                out += f"\n\nHINT: Module '{mod_name}' is missing. Fix: run_command pip3 install {mod_name}, then run_command python3 to retry."
            elif "nameerror" in combined:
                # Extract the undefined name
                import re as _re
                name_match = _re.search(r"name ['\"](\w+)['\"] is not defined", combined_raw)
                if name_match:
                    missing_name = name_match.group(1)
                    out += (f"\n\nHINT: '{missing_name}' is not defined — you forgot to import it. "
                            f"Use edit_file to add the missing import (e.g., 'import {missing_name}') at the top of the script, then retry.")
                else:
                    out += "\n\nHINT: A variable or module is not defined. Use read_file to check imports, edit_file to add the missing import, then retry."
            elif "syntaxerror" in combined:
                import re as _re
                line_match = _re.search(r"line (\d+)", combined)
                line_hint = f" at line {line_match.group(1)}" if line_match else ""
                out += f"\n\nHINT: Syntax error{line_hint}. Use read_file to see the code, then edit_file to fix the exact line, then retry."
            elif "typeerror" in combined:
                out += "\n\nHINT: Type error — wrong argument types or wrong number of arguments. Use read_file to inspect, edit_file to fix, then retry."
            elif "keyerror" in combined or "indexerror" in combined:
                # Special hint for yfinance MultiIndex KeyError
                if any(col in combined_raw for col in ("'Close'", "'Open'", "'High'", "'Low'", "'Volume'")):
                    out += ("\n\nHINT: yfinance MultiIndex KeyError — yf.download() returns MultiIndex columns "
                            "when downloading multiple tickers. Fix: add `if isinstance(df.columns, pd.MultiIndex): "
                            "df.columns = df.columns.droplevel(1)` right after yf.download(). "
                            "Use edit_file to add this fix, then retry.")
                else:
                    out += "\n\nHINT: Data structure mismatch. Use read_file to check the code logic. The data may have different column names or fewer elements than expected."
            elif "attributeerror" in combined:
                out += "\n\nHINT: Attribute error — the object doesn't have that method/property. Check the library version or API docs. Use read_file then edit_file to fix."
            elif "valueerror" in combined:
                out += "\n\nHINT: Value error — invalid value passed to a function. Use read_file to check the data types and fix with edit_file."
            elif "permission denied" in combined:
                out += "\n\nHINT: Permission denied. Try adding chmod +x, or run with python3 explicitly."
            else:
                out += "\n\nHINT: Script failed. Use read_file to inspect the code, find the error, edit_file to fix it, then run_command to retry. Do NOT give up."
        else:
            # Script succeeded — auto-verify and auto-open output files (Claude Code verify phase)
            desktop = pathlib.Path.home() / "Desktop"
            try:
                recent_files = []
                for ext in ("*.png", "*.html", "*.csv", "*.pdf", "*.xlsx"):
                    for f in desktop.glob(ext):
                        if (time.time() - f.stat().st_mtime) < 30:
                            recent_files.append(f)
                # Also detect files mentioned in stdout (e.g., "Saved to /path/to/file.png")
                saved_pattern = re_module.findall(r'(?:saved?\s+(?:to|as|at)|wrote|output|created)[:\s]+([^\s\'"]+\.(?:png|html|csv|pdf))', stdout, re_module.IGNORECASE)
                for sp in saved_pattern:
                    p = pathlib.Path(sp).expanduser().resolve()
                    if p.exists() and p not in recent_files:
                        recent_files.append(p)
                if recent_files:
                    names = [f.name for f in recent_files]
                    out += f"\n\nVerified: output files created: {', '.join(names)}"
                    # Auto-open on macOS (non-blocking)
                    for f in recent_files[:3]:
                        try:
                            subprocess.Popen(["open", str(f)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        except Exception:
                            pass
                    if HAS_RICH:
                        console.print(f"  [dim]Opened {', '.join(names[:3])}[/dim]")
                else:
                    combined_check = (stdout + " " + stderr).lower()
                    if any(kw in combined_check for kw in ("chart", "plot", "figure", "savefig", "save")):
                        out += ("\n\nWARNING: Script ran but no output files detected on Desktop. "
                                "Check the save path uses os.path.expanduser('~/Desktop/filename.png').")
            except Exception:
                pass
        return out
    if tool_name == "write_file":
        return f"OK: {data.get('action', 'created')} {data.get('path', '')} ({data.get('lines', 0)} lines)"
    if tool_name == "edit_file":
        return f"OK: edited {data.get('path', '')} ({data.get('replacements', 0)} replacements)"
    if tool_name == "read_file":
        content = data.get("content", "")
        return f"OK: {data.get('lines', 0)} lines\n{content[:2000]}"
    if tool_name == "list_files":
        items = data.get("items", [])
        names = [it["name"] if isinstance(it, dict) else str(it) for it in items[:20]]
        return f"OK: {data.get('count', len(items))} items: {', '.join(names)}"
    if tool_name == "search_code":
        matches = data.get("matches", [])
        return f"OK: {len(matches)} matches\n" + "\n".join(str(m)[:200] for m in matches[:10])
    # Remote tools — JSON summary
    return json.dumps(data, ensure_ascii=False)[:2000]


def _format_tool_params(tool_name: str, params: dict) -> str:
    """Format tool params into a readable short string (Claude Code style)."""
    if not params:
        return ""
    if tool_name in ("read_file", "write_file", "edit_file"):
        return params.get("path", "")
    if tool_name == "run_command":
        return params.get("command", "")[:60]
    if tool_name == "list_files":
        p = params.get("path", ".")
        pat = params.get("pattern", "*")
        return f"{p}/{pat}" if pat != "*" else p
    if tool_name == "search_code":
        return params.get("pattern", "")[:40]
    if tool_name in ("get_market_data", "get_crypto_data", "get_forex_data",
                      "get_commodities_data", "get_futures_data", "get_bonds_data"):
        return params.get("symbol", params.get("symbols", ""))
    if tool_name == "backtest_strategy":
        return f"{params.get('strategy', '')} {params.get('symbol', '')}"
    if tool_name == "web_search":
        return params.get("query", "")[:60]
    if tool_name == "web_fetch":
        url = params.get("url", "")
        # Trim scheme + show only meaningful part of URL
        short = url.replace("https://", "").replace("http://", "")
        return short[:60] + ("…" if len(short) > 60 else "")
    if tool_name == "analyze_news":
        return params.get("symbol", params.get("query", ""))
    # Fallback: show first value
    for v in params.values():
        s = str(v)
        return s[:50] if len(s) > 50 else s
    return ""


_TOOL_ACTION_LABELS: dict = {
    # Market data
    "get_market_data":           "loading market data",
    "get_quote":                 "fetching quote",
    "get_ohlcv":                 "loading price history",
    "get_fundamental_data":      "loading fundamentals",
    "get_news":                  "fetching news",
    "get_earnings":              "loading earnings data",
    "get_crypto_data":           "loading crypto data",
    "get_forex_data":            "loading forex rates",
    "get_commodity_data":        "loading commodity data",
    # Technical / quant
    "get_technical_indicators":  "computing technical indicators",
    "calculate_factors":         "running factor model",
    "calculate_risk_metrics":    "calculating risk metrics",
    "get_options_chain":         "loading options chain",
    "get_peer_comparison":       "running peer comparison",
    "calculate_correlation":     "computing correlation matrix",
    # Backtest / strategy
    "run_backtest":              "running backtest simulation",
    "run_walk_forward":          "running walk-forward analysis",
    "portfolio_backtest":        "running portfolio simulation",
    "optimize_portfolio":        "optimizing portfolio weights",
    # Research / reports
    "generate_report":           "generating research report",
    "get_market_snapshot":       "scanning market",
    "get_sector_flow":           "loading sector flow data",
    "get_limit_up_pool":         "scanning limit-up pool",
    "get_north_bound_flow":      "loading north-bound capital flow",
    # File / code
    "read_file":                 "reading file",
    "write_file":                "writing file",
    "edit_file":                 "editing file",
    "list_files":                "listing files",
    "search_code":               "searching codebase",
    "run_command":               "executing command",
    # Macro / realty
    "get_macro_data":            "loading macro indicators",
    "get_house_price_index":     "loading house price data",
    "get_reits_data":            "loading REITs data",
    # Broker
    "get_account_info":          "fetching account info",
    "get_positions":             "loading positions",
    "get_orders":                "loading orders",
    "place_order":               "preparing order",
    # SQL / data
    "sql_query":                 "running SQL query",
    "export_to_excel":           "exporting to Excel",
}


def _render_answer_block(text: str) -> None:
    """Render the AI's final answer with a ⏺ bullet + hanging indent.

    Mirrors the tool-call rhythm (⏺ for every turn segment) so the answer
    aligns visually with the tool tree. Bullet sits at the margin, the
    Markdown body is indented past it — Claude Code's hanging-indent look.
    """
    if _ARIA_BOT_MODE:
        console.print(Markdown(_strip_latex(text)))
        return
    if not HAS_RICH:
        print(f"\n  ⏺  {text}")
        return
    from rich.padding import Padding
    console.print(f"\n  [#C08050]⏺[/#C08050]")
    console.print(Padding(Markdown(_strip_latex(text)), (0, 0, 0, 4)))


def _print_tool_call(tool_name: str, params: dict):
    """Print tool call header — Claude Code-style ⏺ bullet tree."""
    if _ARIA_BOT_MODE:
        return
    hint = _format_tool_params(tool_name, params)
    action = _TOOL_ACTION_LABELS.get(tool_name, tool_name.replace("_", " "))
    if HAS_RICH:
        if hint:
            console.print(f"\n  [#C08050]⏺[/#C08050]  [bold]{action}[/bold]  [dim]{hint}[/dim]")
        else:
            console.print(f"\n  [#C08050]⏺[/#C08050]  [bold]{action}[/bold]")
    else:
        label = f"{action}  {hint}" if hint else action
        print(f"\n  ⏺ {label}", end="", flush=True)


def _fuzzy_match(query: str, candidates: list, max_results: int = 3) -> list:
    """Find closest matches using simple edit distance."""
    def _edit_dist(a, b):
        if len(a) > len(b):
            a, b = b, a
        dists = range(len(a) + 1)
        for j, cb in enumerate(b):
            new_dists = [j + 1]
            for i, ca in enumerate(a):
                cost = 0 if ca == cb else 1
                new_dists.append(min(new_dists[-1] + 1, dists[i + 1] + 1, dists[i] + cost))
            dists = new_dists
        return dists[-1]

    scored = [(c, _edit_dist(query.lower(), c.lower())) for c in candidates]
    scored.sort(key=lambda x: x[1])
    # Only suggest if edit distance is reasonable (< half the length)
    threshold = max(3, len(query) // 2)
    return [c for c, d in scored[:max_results] if d <= threshold]


def _error_hint(error: str, context: str = "") -> str:
    from ui.render.output import error_hint as _eh
    return _eh(error, context)


class _null_ctx:
    """No-op context manager used when HAS_RICH is False and we can't use console.status."""
    def __enter__(self): return self
    def __exit__(self, *_): pass


def _print_broker_account(acct: "AccountInfo"):
    """Render AccountInfo in a Rich Panel."""
    if not HAS_RICH:
        print(f"{acct.label}  总资产:{acct.total_assets:,.2f}  可用:{acct.cash:,.2f}  市值:{acct.market_value:,.2f}")
        return
    pnl_color = "green" if acct.pnl_today >= 0 else "red"
    pnl_sign  = "+" if acct.pnl_today >= 0 else ""
    body = (
        f"[dim]账户:[/dim]  [bold]{acct.masked_account}[/bold]  [dim]({acct.broker_type})[/dim]\n\n"
        f"  总资产       [bold]{acct.currency} {acct.total_assets:>14,.2f}[/bold]\n"
        f"  持仓市值     [bold]{acct.market_value:>14,.2f}[/bold]\n"
        f"  可用现金     [bold]{acct.cash:>14,.2f}[/bold]\n"
        f"  冻结资金     [dim]{acct.frozen:>14,.2f}[/dim]\n"
        f"  当日盈亏     [{pnl_color}]{pnl_sign}{acct.pnl_today:>14,.2f}[/{pnl_color}]\n"
    )
    if acct.pnl_total:
        tp_color = "green" if acct.pnl_total >= 0 else "red"
        body += f"  累计盈亏     [{tp_color}]{pnl_sign}{acct.pnl_total:>14,.2f}[/{tp_color}]\n"
    console.print(Panel(body, title=f"[bold]{acct.label}[/bold]",
                        border_style="dim", box=rich_box.ROUNDED, padding=(0, 1)))


def _print_broker_positions(positions: list, broker_label: str, currency: str = "CNY"):
    """Render Position list as a Rich Table."""
    if not HAS_RICH:
        for p in positions:
            print(f"  {p.symbol:<8} {p.name:<12} 持仓:{p.quantity}  市值:{p.market_value:,.2f}  盈亏:{p.pnl:+,.2f} ({p.pnl_pct:+.2f}%)")
        return
    if not positions:
        console.print(f"[dim]{broker_label} — 当前无持仓[/dim]")
        return
    from rich.table import Table
    tbl = Table(title=f"[bold]{broker_label}[/bold] 持仓", show_header=True, header_style="bold")
    tbl.add_column("代码",   style="bold", no_wrap=True)
    tbl.add_column("名称",   max_width=12)
    tbl.add_column("持仓",   justify="right")
    tbl.add_column("可卖",   justify="right", style="dim")
    tbl.add_column("成本",   justify="right", style="dim")
    tbl.add_column("现价",   justify="right")
    tbl.add_column("市值",   justify="right")
    tbl.add_column("盈亏",   justify="right")
    tbl.add_column("盈亏%",  justify="right")
    total_mv = sum(p.market_value for p in positions)
    total_pnl= sum(p.pnl         for p in positions)
    for p in sorted(positions, key=lambda x: -abs(x.market_value)):
        pnl_color = "green" if p.pnl >= 0 else "red"
        pnl_sign  = "+" if p.pnl >= 0 else ""
        tbl.add_row(
            p.symbol, p.name[:12] or "—",
            f"{p.quantity:,.0f}", f"{p.available_qty:,.0f}",
            f"{p.cost_price:.3f}", f"{p.current_price:.3f}",
            f"{p.market_value:,.2f}",
            f"[{pnl_color}]{pnl_sign}{p.pnl:,.2f}[/{pnl_color}]",
            f"[{pnl_color}]{pnl_sign}{p.pnl_pct:.2f}%[/{pnl_color}]",
        )
    console.print(tbl)
    pnl_color = "green" if total_pnl >= 0 else "red"
    console.print(
        f"  [dim]共 {len(positions)} 只  总市值 {total_mv:,.2f}  "
        f"总盈亏 [{pnl_color}]{'+' if total_pnl>=0 else ''}{total_pnl:,.2f}[/{pnl_color}][/dim]"
    )


def _print_broker_orders(orders: list, broker_label: str, status_filter: str = "all"):
    """Render Order list as a Rich Table."""
    if not HAS_RICH:
        for o in orders:
            print(f"  {o.order_id[:8]} {o.symbol:<8} {o.side:<4} {o.quantity:>8.0f} @ {o.price:.3f}  {o.status}")
        return
    if not orders:
        console.print(f"[dim]{broker_label} — 无 {status_filter} 订单[/dim]")
        return
    from rich.table import Table
    tbl = Table(title=f"[bold]{broker_label}[/bold] 订单 [dim]({status_filter})[/dim]",
                show_header=True, header_style="bold")
    tbl.add_column("订单号",  style="dim",   max_width=12)
    tbl.add_column("代码",    style="bold",  no_wrap=True)
    tbl.add_column("名称",    max_width=10)
    tbl.add_column("方向",    justify="center")
    tbl.add_column("类型",    style="dim")
    tbl.add_column("委托量",  justify="right")
    tbl.add_column("成交量",  justify="right")
    tbl.add_column("委托价",  justify="right", style="dim")
    tbl.add_column("均价",    justify="right")
    tbl.add_column("状态")
    tbl.add_column("时间",    style="dim", max_width=16)
    _STATUS_STYLE = {"filled":"[green]成交[/green]","partial":"[yellow]部成[/yellow]",
                     "open":"[cyan]委托中[/cyan]","cancelled":"[dim]已撤[/dim]"}
    _SIDE_STYLE   = {"buy":"[green]买入[/green]","sell":"[red]卖出[/red]"}
    for o in orders:
        tbl.add_row(
            o.order_id[-8:], o.symbol, o.name[:10] or "—",
            _SIDE_STYLE.get(o.side, o.side),
            o.order_type,
            f"{o.quantity:,.0f}", f"{o.filled_qty:,.0f}",
            f"{o.price:.3f}", f"{o.avg_price:.3f}" if o.avg_price else "—",
            _STATUS_STYLE.get(o.status, o.status),
            o.created_at[:16] if o.created_at else "—",
        )
    console.print(tbl)


def _print_error(msg: str, context: str = ""):
    from ui.render.output import print_error as _pe
    _pe(msg, context, console=console, has_rich=HAS_RICH, rich_box=rich_box)


from contextlib import contextmanager as _contextmanager

@_contextmanager
def _null_ctx():
    """No-op context manager for conditional `with` blocks."""
    yield


# ── Verdict banner ─────────────────────────────────────────────────────────────

# Alias kept for any internal references that pre-date the move to team_render.
_VERDICT_STYLE: dict = VERDICT_STYLE


def _print_verdict_banner(verdict: str, subtitle: str = "", confidence: float = None) -> None:
    """Thin wrapper — rendering logic lives in team_render.render_verdict_banner."""
    render_verdict_banner(verdict, subtitle, confidence,
                          console=console, has_rich=HAS_RICH)


def _print_agent_table(sym: str, results: list, use_full: bool = False) -> None:
    """Thin wrapper — rendering logic lives in team_render.render_team_table."""
    import shutil as _shutil
    rows = build_team_table_rows(results)
    tw   = getattr(console, "width", None) or _shutil.get_terminal_size().columns
    render_team_table(sym, rows, use_full,
                      console=console, terminal_width=tw, has_rich=HAS_RICH)


def _team_live_price(data_bundle) -> Optional[float]:
    """Extract a usable live/reference price from a DataBundle-like object."""
    try:
        quote = getattr(data_bundle, "quote", {}) or {}
        value = quote.get("price") or quote.get("current_price") or quote.get("regular_market_price")
        if value is None:
            return None
        value = float(value)
        return value if value > 0 else None
    except Exception:
        return None




_TEAM_DOLLAR_RE = re.compile(r"(?<![A-Za-z0-9])\$\s*([0-9][0-9,]*(?:\.\d+)?)")


def _team_conflicting_prices(text: str, live_price: Optional[float]) -> list[float]:
    """Find dollar prices that are clearly incompatible with current quote.

    This is intentionally conservative: it only inspects explicit "$123" style
    figures and only flags values far outside the live-price range. The goal is
    to catch split-adjusted/stale LLM output such as NVDA $945 when live price is
    around $205, without rejecting normal support/target ranges nearby.
    """
    if not text or not live_price or live_price <= 0:
        return []
    conflicts: list[float] = []
    for raw in _TEAM_DOLLAR_RE.findall(text):
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if value <= 0:
            continue
        ratio = value / live_price
        if ratio >= 1.8 or ratio <= 0.35:
            conflicts.append(value)
    return conflicts[:8]


def _sanitize_team_result_with_market_data(team_result, data_bundle) -> list[str]:
    """Validate /team output against live data and mark stale/hallucinated parts."""
    notes: list[str] = []
    live_price = _team_live_price(data_bundle)
    if not team_result or not live_price:
        return notes

    for result in getattr(team_result, "results", []) or []:
        text = "\n".join([
            str(getattr(result, "analysis", "") or ""),
            "\n".join(str(p) for p in (getattr(result, "key_points", []) or [])),
        ])
        conflicts = _team_conflicting_prices(text, live_price)
        if not conflicts:
            continue
        result.analysis = (
            "该 Agent 输出包含与当前行情明显冲突的价格，已从报告正文中移除。\n\n"
            f"- 当前参考价: {live_price:.2f}\n"
            f"- 冲突价格: {', '.join(f'${v:g}' for v in conflicts)}\n"
            "- 请重新运行 /team，或先运行 /doctor 检查数据源与模型上下文。"
        )
        result.key_points = [f"数据冲突: 输出价格与当前参考价 {live_price:.2f} 不一致"]
        result.signal = "HOLD"
        result.confidence = min(float(getattr(result, "confidence", 0.0) or 0.0), 0.2)
        result.error = "stale_or_conflicting_price"
        notes.append(
            f"{getattr(result, 'agent', 'agent')}: removed stale/conflicting prices "
            f"({', '.join(f'${v:g}' for v in conflicts)})"
        )

    conflicts = _team_conflicting_prices(getattr(team_result, "synthesis", "") or "", live_price)
    if conflicts:
        team_result.synthesis = (
            "综合结论已降级：原始综合结论包含与当前行情明显冲突的价格，"
            "因此不应作为投资依据。\n\n"
            f"- 当前参考价: {live_price:.2f}\n"
            f"- 冲突价格: {', '.join(f'${v:g}' for v in conflicts)}\n"
            "- 建议先确认数据源健康，再重新运行 /team 或 /ta。"
        )
        team_result.final_signal = "HOLD"
        team_result.confidence = min(float(getattr(team_result, "confidence", 0.0) or 0.0), 0.2)
        notes.append(
            "synthesis: replaced stale/conflicting conclusion "
            f"({', '.join(f'${v:g}' for v in conflicts)})"
        )
    return notes
    console.print()


def _is_ashare_symbol(symbol: str) -> bool:
    """Quick check whether a symbol looks like a Chinese A-share code."""
    s = symbol.strip().lower()
    return (
        s.startswith("sh") or s.startswith("sz")
        or (len(s) == 6 and s.isdigit())
        or s.endswith(".ss") or s.endswith(".sz")
    )


# A-share code → Chinese name lookup with on-disk JSON cache (7-day TTL)
_ASHARE_NAMES_CACHE: dict = {}
_ASHARE_NAMES_LOADED: bool = False
_ASHARE_NAMES_FAIL_TS: float = 0.0  # timestamp of last fetch failure; retry after 5 min
_ASHARE_NAMES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "research", ".cache", "ashare_names.json")


def _ensure_ashare_names_loaded() -> dict:
    """Load (and auto-refresh) the A-share code→Chinese name mapping."""
    global _ASHARE_NAMES_CACHE, _ASHARE_NAMES_LOADED, _ASHARE_NAMES_FAIL_TS
    if _ASHARE_NAMES_LOADED:
        return _ASHARE_NAMES_CACHE

    import json as _json
    import time as _time

    # Back off for 5 minutes after a network failure to avoid hammering AKShare
    if _ASHARE_NAMES_FAIL_TS and _time.time() - _ASHARE_NAMES_FAIL_TS < 300:
        return _ASHARE_NAMES_CACHE

    cache_path = _ASHARE_NAMES_PATH
    cache_dir  = os.path.dirname(cache_path)

    # Try reading existing cache
    if os.path.exists(cache_path):
        try:
            mtime = os.path.getmtime(cache_path)
            if _time.time() - mtime < 7 * 86400:  # 7-day TTL
                with open(cache_path, encoding="utf-8") as _f:
                    _ASHARE_NAMES_CACHE = _json.load(_f)
                _ASHARE_NAMES_LOADED = True
                return _ASHARE_NAMES_CACHE
        except Exception:
            pass

    # Cache missing or stale — rebuild from akshare
    try:
        import akshare as _ak  # type: ignore
        df = _ak.stock_info_a_code_name()
        if df is not None and not df.empty:
            mapping: dict = {}
            for _, row in df.iterrows():
                code = str(row.get("code", row.iloc[0])).zfill(6)
                name = str(row.get("name", row.iloc[1]))
                mapping[code] = name
            _ASHARE_NAMES_CACHE = mapping
            # Persist to disk
            try:
                os.makedirs(cache_dir, exist_ok=True)
                with open(cache_path, "w", encoding="utf-8") as _f:
                    _json.dump(mapping, _f, ensure_ascii=False)
            except Exception:
                pass
            _ASHARE_NAMES_LOADED = True
    except Exception:
        _ASHARE_NAMES_FAIL_TS = _time.time()  # retry after 5-min backoff, not permanently locked

    return _ASHARE_NAMES_CACHE


def _ashare_code_to_name(symbol: str) -> str:
    """Return the Chinese company name for a 6-digit A-share code, or empty string."""
    # Normalise to bare 6-digit code
    code = symbol.upper().strip()
    code = code.replace(".SS", "").replace(".SZ", "")
    code = code.lstrip("SH").lstrip("SZ") if not code[:2].isdigit() else code
    code = code.zfill(6) if code.isdigit() else code

    names = _ensure_ashare_names_loaded()
    return names.get(code, "")


from ui.render.output import FINANCE_TOOL_NAMES as _FINANCE_TOOL_NAMES


def _print_tool_result(tool_name: str, result: dict, elapsed: float = 0, params: dict = None):
    from ui.render.output import print_tool_result as _ptr
    _ptr(
        tool_name, result, elapsed, params,
        console=console, has_rich=HAS_RICH, rich_box=rich_box,
        print_finance_fn=_print_finance_result,
        bot_mode=_ARIA_BOT_MODE,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Finance rendering — implementation lives in apps/cli/commands/finance_render.py
# These thin wrappers supply the module-level console / HAS_RICH / _ARIA_BOT_MODE.
# ─────────────────────────────────────────────────────────────────────────────

def _print_finance_result(tool_name: str, result: dict) -> None:
    render_finance_result(tool_name, result,
                          console=console, has_rich=HAS_RICH,
                          bot_mode=_ARIA_BOT_MODE)


def _render_macro_result(r: dict, title: str) -> None:
    render_macro_result(r, title, console=console, has_rich=HAS_RICH)


def _render_cb_rates(r: dict) -> None:
    render_cb_rates(r, console=console, has_rich=HAS_RICH)


def _render_econ_calendar(r: dict) -> None:
    render_econ_calendar(r, console=console, has_rich=HAS_RICH)


def _render_options_chain(r: dict) -> None:
    render_options_chain(r, console=console, has_rich=HAS_RICH)


def _render_quality_scores(symbol: str, f_r: dict, z_r: dict) -> None:
    render_quality_scores(symbol, f_r, z_r, console=console, has_rich=HAS_RICH)


def _render_ichimoku(r: dict) -> None:
    render_ichimoku(r, console=console, has_rich=HAS_RICH)


def _render_fear_greed(r: dict) -> None:
    render_fear_greed(r, console=console, has_rich=HAS_RICH)


def _render_funding_rates(r: dict) -> None:
    render_funding_rates(r, console=console, has_rich=HAS_RICH)


def _render_peer_comparison(r: dict) -> None:
    render_peer_comparison(r, console=console, has_rich=HAS_RICH)


def _render_house_price(r: dict) -> None:
    render_house_price(r, console=console, has_rich=HAS_RICH)


def _render_reits_list(r: dict) -> None:
    render_reits_list(r, console=console, has_rich=HAS_RICH)


def _render_rental_yield(r: dict) -> None:
    render_rental_yield(r, console=console, has_rich=HAS_RICH)


def _render_property_val(r: dict) -> None:
    render_property_val(r, console=console, has_rich=HAS_RICH)


def _render_multi_city(r: dict) -> None:
    render_multi_city(r, console=console, has_rich=HAS_RICH)


def _render_asset_score(r: dict) -> None:
    render_asset_score(r, console=console, has_rich=HAS_RICH)


def _render_corr_matrix(r: dict) -> None:
    render_corr_matrix(r, console=console, has_rich=HAS_RICH)


def _render_portfolio_bt(r: dict) -> None:
    render_portfolio_bt(r, console=console, has_rich=HAS_RICH)


def _render_sql_result(r: dict) -> None:
    render_sql_result(r, console=console, has_rich=HAS_RICH)


def _render_alerts(r: dict) -> None:
    render_alerts(r, console=console, has_rich=HAS_RICH)


def _prompt_float(label: str, default: float) -> float:
    """交互式数字输入，失败时返回 default。"""
    try:
        if HAS_RICH:
            from rich.prompt import Prompt
            raw = Prompt.ask(f"  {label}", default=str(default))
        else:
            raw = input(f"  {label}") or str(default)
        return float(raw)
    except ValueError:
        if HAS_RICH:
            console.print(f"  [yellow]请输入有效数字，已使用默认值 {default}[/yellow]")
        else:
            print(f"  请输入有效数字，已使用默认值 {default}")
        return default
    except KeyboardInterrupt:
        return default


def _prompt_str(label: str, default: str) -> str:
    """交互式字符串输入，失败时返回 default。"""
    try:
        if HAS_RICH:
            from rich.prompt import Prompt
            return Prompt.ask(f"  {label}", default=default)
        else:
            return input(f"  {label}") or default
    except (ValueError, KeyboardInterrupt):
        return default


def format_quote_output(data: dict):
    """Format market data as clean two-column rows."""
    if not HAS_RICH:
        return json.dumps(data, indent=2, ensure_ascii=False)

    d = data.get("data", data)
    symbol = d.get("symbol", "???")
    price = d.get("current_price", d.get("price", 0))
    change = d.get("change_percent", d.get("changePercent", 0))
    high52 = d.get("high_52w", d.get("yearHigh", "-"))
    low52 = d.get("low_52w", d.get("yearLow", "-"))
    volume = d.get("volume", "-")
    market_cap = d.get("market_cap", d.get("marketCap", "-"))

    color = "green" if change >= 0 else "red"
    arrow = "+" if change >= 0 else ""

    out = Text()
    out.append(f"  {symbol}\n", style="bold")
    price_str = f"${price:,.2f}" if isinstance(price, (int, float)) else str(price)
    out.append(f"  {'Price':<16s}", style="dim")
    out.append(f"{price_str}\n")
    out.append(f"  {'Change':<16s}", style="dim")
    out.append(f"{arrow}{change:.2f}%\n", style=color)
    if isinstance(high52, (int, float)):
        out.append(f"  {'52W High':<16s}", style="dim")
        out.append(f"${high52:,.2f}\n")
    if isinstance(low52, (int, float)):
        out.append(f"  {'52W Low':<16s}", style="dim")
        out.append(f"${low52:,.2f}\n")
    if volume != "-":
        vol_str = f"{volume:,}" if isinstance(volume, (int, float)) else str(volume)
        out.append(f"  {'Volume':<16s}", style="dim")
        out.append(f"{vol_str}\n")
    if market_cap and market_cap != "-":
        mc = market_cap
        if isinstance(mc, (int, float)):
            mc_str = f"${mc/1e12:.2f}T" if mc >= 1e12 else f"${mc/1e9:.2f}B" if mc >= 1e9 else f"${mc/1e6:.0f}M"
        else:
            mc_str = str(mc)
        out.append(f"  {'Market Cap':<16s}", style="dim")
        out.append(f"{mc_str}\n")
    # Sparkline from chart_prices
    chart_prices = d.get("chart_prices", [])
    if chart_prices and len(chart_prices) >= 2:
        prices = [p.get("close", p.get("price", 0)) if isinstance(p, dict) else p
                  for p in chart_prices]
        prices = [p for p in prices if isinstance(p, (int, float)) and p > 0]
        if len(prices) >= 2:
            spark = format_sparkline(prices, width=24)
            out.append(f"  {'1M':<16s}", style="dim")
            out.append(f"{spark}\n", style=color)
    return out




def format_sparkline(prices: list, width: int = 30) -> str:
    """Generate Unicode sparkline from price data."""
    if not prices or len(prices) < 2:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    mn, mx = min(prices), max(prices)
    rng = mx - mn or 1
    result = ""
    step = max(1, len(prices) // width)
    for i in range(0, len(prices), step):
        idx = int((prices[i] - mn) / rng * (len(blocks) - 1))
        result += blocks[idx]
    return result[:width]


# ============================================================================
# Session Manager — local persistence + cloud sync
# ============================================================================

class SessionManager:
    """Manage chat sessions with local file persistence."""

    def __init__(self):
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    def save_session(self, session_id: str, conversation: list, metadata: dict = None):
        meta = metadata or {}
        if not meta.get("created_at"):
            meta["created_at"] = datetime.now().isoformat()
        for msg in conversation:
            if msg["role"] == "user":
                meta.setdefault("title", msg["content"][:60])
                break
        data = {
            "id": session_id,
            "messages": conversation,
            "metadata": meta,
            "updated_at": datetime.now().isoformat(),
        }
        path = SESSIONS_DIR / f"{session_id}.json"
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def load_session(self, session_id: str) -> Optional[dict]:
        path = SESSIONS_DIR / f"{session_id}.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return None

    def list_sessions(self, limit: int = 20) -> list:
        sessions = []
        for path in sorted(SESSIONS_DIR.glob("*.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                with open(path) as f:
                    data = json.load(f)
                sessions.append({
                    "id": data.get("id", path.stem),
                    "title": data.get("metadata", {}).get("title", "Untitled"),
                    "messages": len(data.get("messages", [])),
                    "updated": data.get("updated_at", ""),
                })
            except Exception:
                continue
            if len(sessions) >= limit:
                break
        return sessions

    def delete_session(self, session_id: str) -> bool:
        path = SESSIONS_DIR / f"{session_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False


# ============================================================================
# Tab Completer — commands, skills, stock symbols
# ============================================================================

class ArtheraCompleter:
    """Tab completion for basic readline fallback."""

    def __init__(self, commands: list, skills: list, watchlist: list):
        self.tokens = list(commands) + [s["command"] for s in skills]
        self.tokens.extend([
            "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "NFLX",
            "AMD", "INTC", "SPY", "QQQ", "DIA", "IWM", "BTC", "ETH", "SOL",
            "JPM", "BAC", "GS", "V", "MA", "UNH", "JNJ", "PFE", "XOM", "CVX",
        ])
        self.tokens.extend(watchlist)
        self.matches = []

    def complete(self, text: str, state: int):
        if state == 0:
            self.matches = [t for t in self.tokens
                            if t.lower().startswith(text.lower())] if text else []
        try:
            return self.matches[state]
        except IndexError:
            return None


from ui.completer import AriaPTCompleter, ARIA_PT_STYLE, build_aria_pt_style




# ============================================================================
# Slash Commands
# ============================================================================

import types as _types

def _rebind_mixin_globals(mixin_cls):
    """Point mixin methods' __globals__ to this module's namespace so bare names resolve."""
    for _attr_name, _attr in list(vars(mixin_cls).items()):
        if isinstance(_attr, _types.FunctionType):
            _new_fn = _types.FunctionType(
                _attr.__code__, globals(), _attr.__name__,
                _attr.__defaults__, _attr.__closure__
            )
            setattr(mixin_cls, _attr_name, _new_fn)

_rebind_mixin_globals(BrokerCommandsMixin)
_rebind_mixin_globals(BacktestCommandsMixin)
_rebind_mixin_globals(WorkspaceCommandsMixin)
_rebind_mixin_globals(ModelCommandsMixin)
_rebind_mixin_globals(MarketCommandsMixin)
_rebind_mixin_globals(PortfolioCommandsMixin)

class SlashCommands(BrokerCommandsMixin, BacktestCommandsMixin, WorkspaceCommandsMixin, ModelCommandsMixin, MarketCommandsMixin, PortfolioCommandsMixin):
    """Claude Code-style slash command system."""

    def __init__(self, terminal: 'ArtheraTerminal'):
        self.terminal = terminal
        self.commands = {
            "/help":      (self.cmd_help,      "Show all commands and skills"),
            "/artifacts": (self.cmd_artifacts, "List generated reports, backtests, and data files"),
            "/quote":     (self.cmd_quote,     "Quick quote: /quote AAPL MSFT"),
            "/analyze":   (self.cmd_analyze,   "AI analysis: /analyze AAPL"),
            "/backtest":  (self.cmd_backtest,  "Backtest + HTML chart: /backtest momentum SPY --period 1y"),
            "/wf":        (self.cmd_walk_forward, "Walk-Forward: /wf SPY [momentum] [rolling]"),
            "/compare":   (self.cmd_compare,   "Strategy compare: /compare SPY [start] [end]"),
            "/macro":     (self.cmd_macro,     "宏观数据: /macro [us|cn|rates|calendar] [indicator]"),
            "/options":   (self.cmd_options,   "期权链: /options AAPL [calls|puts] [expiry]"),
            "/quality":   (self.cmd_quality,   "质量评分: /quality AAPL  (Piotroski + Altman Z)"),
            "/ichimoku":  (self.cmd_ichimoku,  "一目均衡表: /ichimoku AAPL"),
            "/feargreed":  (self.cmd_fear_greed,"加密恐惧贪婪指数: /feargreed"),
            "/funding":   (self.cmd_funding,   "永续资金费率: /funding [BTC ETH SOL] [exchange]"),
            "/peer":      (self.cmd_peer,      "同行对比: /peer AAPL [MSFT GOOGL META]"),
            "/realty":    (self.cmd_realty,    "不动产: /realty [market|reit|valuation|rent|compare|invest] [参数]"),
            "/football":  (self.cmd_football,  "足球分析: /football [standings|fixtures|predict|team|h2h] [参数]"),
            "/data":      (self.cmd_data,      "数据分析: /data [sql|export|load] [参数]"),
            "/alert":     (self.cmd_alert,     "价格预警: /alert [add|list|delete|check] AAPL gt 200"),
            "/corr":      (self.cmd_corr,      "相关性矩阵: /corr AAPL MSFT TSLA SPY"),
            "/ptbt":      (self.cmd_portfolio_bt, "组合回测: /ptbt AAPL MSFT GOOG [权重] [2y]"),
            "/watch":     (self.cmd_watch,     "Watchlist: /watch add AAPL | /watch list"),
            "/portfolio": (self.cmd_portfolio, "组合分析: /portfolio [analyze|rebalance] [symbols]"),
            "/journal":  (self.cmd_journal,  "持仓账本: /journal [add|trades|pnl|realized|export|delete]"),
            "/screen":    (self.cmd_screen,    "Screen stocks: /screen tech"),
            "/model":     (self.cmd_model,     "Select AI model (interactive picker)"),
            "/thinking":  (self.cmd_thinking,  "Toggle thinking: /thinking on"),
            "/tools":     (self.cmd_tools,     "List all Aria tools"),
            "/packages":  (self.cmd_packages,  "Aria/Arthera packages: /packages [connect arthera]"),
            "/services":  (self.cmd_services,  "Show CLI service tiers and workflows"),
            "/plan":      (self.cmd_plan,      "Draft executable plan: /plan step1 ; step2"),
            "/apply-plan":(self.cmd_apply_plan,"Execute pending plan steps"),
            "/plan-report":(self.cmd_plan_report,"Show/export last plan execution report"),
            "/git":       (self.cmd_git,       "Git helper: /git status|diff|summary"),
            "/gh":        (self.cmd_gh,        "GitHub CLI: /gh prs|issues|pr N|create-pr|search"),
            "/skills":    (self.cmd_skills,    "List all available skills"),
            "/status":    (self.cmd_status,    "Runtime status: engine · model · tools · context"),
            "/trace":     (self.cmd_trace,     "Show runtime tool trace"),
            "/health":    (self.cmd_health,    "Check backend health"),
            "/clear":     (self.cmd_clear,     "Clear conversation"),
            "/btw":       (self.cmd_btw,       "Side question (no history): /btw what was that function?"),
            "/recap":     (self.cmd_recap,     "Session recap: summarise what we've done so far"),
            "/history":   (self.cmd_history,   "Show conversation history"),
            "/compact":   (self.cmd_compact,   "Smart compact: /compact [--hard]"),
            "/regen":     (self.cmd_regen,     "Regenerate last AI response"),
            "/undo":      (self.cmd_undo,      "Undo last message pair"),
            "/fork":      (self.cmd_fork,      "Fork conversation at current point: /fork [name]"),
            "/copy":      (self.cmd_copy,      "Copy last response to clipboard"),
            "/cost":      (self.cmd_cost,      "Show session token usage and estimated cost"),
            "/todo":      (self.cmd_todo,      "Task tracking: /todo add|done|list|clear"),
            "/doctor":    (self.cmd_doctor,    "Diagnose installation, models, API keys"),
            "/hooks":     (self.cmd_hooks,     "Manage event hooks: /hooks list|edit|run"),
            "/login":     (self.cmd_login,     "Login: /login <email>"),
            "/logout":    (self.cmd_logout,    "Logout current user"),
            "/whoami":    (self.cmd_whoami,    "Show current user and token status"),
            "/sessions":  (self.cmd_sessions,  "List/search sessions: /sessions [keyword]"),
            "/save":      (self.cmd_save,      'Save session: /save ["name"]'),
            "/load":      (self.cmd_load,      "Load session: /load <id>"),
            "/rename":    (self.cmd_rename,     'Rename session: /rename "title"'),
            "/export":    (self.cmd_export,    "Export: /export json|csv|md [file]"),
            "/feedback":  (self.cmd_feedback,  "Local feedback: /feedback good|bad|note <text>"),
            "/privacy":   (self.cmd_privacy,   "Privacy controls: /privacy status|opt-in|opt-out|export|delete"),
            "/code":      (self.cmd_code,      "Generate & save code: /code <description> [--save file.py]"),
            "/scaffold":  (self.cmd_scaffold,  "Scaffold project: /scaffold <name> [--template strategy|analysis|pipeline]"),
            "/read":      (self.cmd_read,      "Read file: /read <path> [offset] [limit]"),
            "/write":     (self.cmd_write,     "Write file: /write [--stage] <path>"),
            "/edit":      (self.cmd_edit,      "Edit file: /edit <path>"),
            "/ls":        (self.cmd_ls,        "List files: /ls [path] [pattern]"),
            "/search":    (self.cmd_search,    "Search code: /search <pattern> [path] [glob]"),
            "/run":       (self.cmd_run,       "Run command: /run <command>"),
            "/verify":    (self.cmd_verify,    "Infer and run focused checks: /verify [--dry-run] [paths...]"),
            "/changes":   (self.cmd_changes,   "List staged file changes"),
            "/apply-change": (self.cmd_apply_change, "Apply staged change: /apply-change <id>"),
            "/reject-change": (self.cmd_reject_change, "Reject staged change: /reject-change <id>"),
            "/apply":     (self.cmd_apply,     "Extract & save code from last AI response"),
            "/news":      (self.cmd_news,      "Latest news: /news [topic|symbol]"),
            "/config":    (self.cmd_config,    "Show/set config: /config set key=value"),
            "/input":     (self.cmd_input,     "Input UI: /input panel|plain|box|theme auto|dark|light"),
            "/context":   (self.cmd_context,   "Show current AI context & session"),
            "/crypto":    (self.cmd_crypto,    "Crypto data: /crypto BTC ETH"),
            "/forex":     (self.cmd_forex,     "Forex rates: /forex EUR/USD"),
            "/commodity": (self.cmd_commodity, "Commodities: /commodity gold oil"),
            "/risk":      (self.cmd_risk,      "Risk metrics: /risk AAPL | /risk portfolio"),
            "/market":    (self.cmd_market,    "Market overview: /market [indices|sectors]"),
            "/optimize":  (self.cmd_optimize,  "Optimize portfolio: /optimize AAPL MSFT"),
            "/stress":    (self.cmd_stress,    "Stress test: /stress <strategy> [symbol]"),
            "/factors":   (self.cmd_factors,   "Factor analysis (local+remote): /factors AAPL"),
            "/compliance":(self.cmd_compliance,"Compliance check: /compliance <strategy>"),
            "/web":       (self.cmd_search_web,"Web search: /web <query>"),
            "/local":     (self.cmd_local,     "Toggle local-only mode (skip AWS): /local [on|off]"),
            "/orcl":      (self.cmd_orcl,      "Oracle Corp analysis: /orcl [deep]"),
            # ── New: MCP / ariarc / provider / recommend ─────────────────
            "/mcp":       (self.cmd_mcp,       "MCP servers: /mcp status | /mcp tools | /mcp reload"),
            "/ariarc":    (self.cmd_ariarc,    "Show .ariarc project config: /ariarc [reload]"),
            "/providers":  (self.cmd_providers, "List all local LLM backend providers and status"),
            "/recommend":  (self.cmd_recommend, "Recommend best local models for finance work"),
            # ── Finance shortcuts (local tools) ────────────────────────────
            "/screen-cn": (self.cmd_screen_cn, "A股选股筛选: /screen-cn [max_pe=50] [limit=20]"),
            "/limitup":   (self.cmd_limitup,   "A股涨停板池: /limitup [date YYYY-MM-DD]"),
            "/north":     (self.cmd_north,     "北向资金净流入: /north [days=10]"),
            "/optimize-port": (self.cmd_optimize_port, "Portfolio optimisation: /optimize-port AAPL MSFT GOOGL"),
            # ── Alibaba Cloud services ─────────────────────────────────────
            "/cloud":     (self.cmd_cloud,     "Aliyun cloud config: /cloud status|set|data|token|health|reset"),
            "/signal":    (self.cmd_signal,    "AI signal (BUY/SELL/HOLD): /signal sh600519 [CN|US]"),
            "/predict":   (self.cmd_predict,   "ML predictions: /predict sh600519 sh601318 [d=5]"),
            "/cloudbt":   (self.cmd_cloudbt,   "Cloud ML backtest: /cloudbt sh600519 [model=lightgbm] [months=12]"),
            "/insights":  (self.cmd_insights,  "AI market insights: /insights sh600519 sh601318"),
            # ── 金融 Agent 团队 ────────────────────────────────────────────────
            "/team":      (self.cmd_team,      "多Agent研究团队: /team NVDA [--agents macro,technical]"),
            "/chart":     (self.cmd_chart,     "生成股票图表(HTML): /chart AAPL | /chart 600519"),
            "/report":    (self.cmd_report,    "综合投资报告(图表+分析): /report AAPL"),
            "/shortterm": (self.cmd_shortterm, "A股短线分析(日线): /shortterm [000333 601138]"),
            "/longterm":  (self.cmd_longterm,  "A股长线分析(月线): /longterm [--quick]"),
            "/indices":   (self.cmd_indices,   "全球指数实时行情: /indices"),
            "/hot":       (self.cmd_hot,       "热门股榜单: /hot [cn|us] [top=20]"),
            "/ta":        (self.cmd_ta,        "技术指标: /ta NVDA [days=120]"),
            # ── 策略金库 ───────────────────────────────────────────────────────
            "/strategy":  (self.cmd_strategy,  "策略版本管理: /strategy save|list|diff|load|review"),
            # ── 券商账户 ──────────────────────────────────────────────────────────
            "/broker":    (self.cmd_broker,    "券商管理: /broker list|connect|disconnect|add|status"),
            "/account":   (self.cmd_account,   "账户资金: /account [broker_id]"),
            "/positions": (self.cmd_positions, "当前持仓: /positions [broker_id]"),
            "/orders":    (self.cmd_orders,    "订单记录: /orders [open|filled|all] [broker_id]"),
            # ── 记忆 / 项目引导 / 代码审查 ────────────────────────────────────
            "/note":      (self.cmd_note,      "追加笔记到 ARIA.md: /note <内容>"),
            "/memory":    (self.cmd_memory,    "记忆管理: /memory [show|add <内容>|clear|search]"),
            "/init":      (self.cmd_init,      "为当前项目生成 ARIA.md: /init [--force]"),
            "/review":    (self.cmd_review,    "AI 代码审查: /review [file] | /review --staged"),
            # ── Provider / 模型配置（Open Interpreter 风格）───────────────────
            "/apikey":    (self.cmd_apikey,    "API Key 向导: /apikey → 交互配置  /apikey list|test|remove"),
            "/setup":     (self.cmd_setup,     "首次配置向导: /setup"),
            # ── 量化专属（Aria 独有）────────────────────────────────────────────
            "/auto-strategy": (self.cmd_auto_strategy, "AI 策略自动优化闭环: /auto-strategy momentum SPY --target sharpe=1.5"),
            "/factor-lab":    (self.cmd_factor_lab,    "因子分析工作台: /factor-lab AAPL [days=252]"),
            "/execution":     (self.cmd_execution,     "执行算法对比: /execution AAPL buy 100000 [algo=compare]"),
            "/stat-arb":      (self.cmd_stat_arb,      "配对统计套利检验: /stat-arb GLD SLV"),
            "/edgar":         (self.cmd_edgar,         "SEC EDGAR 财报查询: /edgar AAPL [filings|facts|insider]"),
            "/datasource":    (self.cmd_datasource,    "数据源管理: /datasource | /datasource test FRED"),
            # ── financial-services 风格 workflow 命令 ────────────────────────────
            "/research":  (self.cmd_research,  "Market Researcher 工作流: /research <symbol>"),
            "/earnings":  (self.cmd_earnings_workflow, "财报分析工作流: /earnings <symbol> [quarter]"),
            # ── 经营权共创平台 Agent 命令 ────────────────────────────────────────────
            "/asset-diag":    (self.cmd_asset_diag,    "资产诊断 Agent: /asset-diag <asset_id|项目名>"),
            "/contract-draft":(self.cmd_contract_draft,"合同规则草案: /contract-draft <project_id>"),
            "/revenue-calc":  (self.cmd_revenue_calc,  "分账测算: /revenue-calc <project_id> <流水金额>"),
            "/risk-scan":     (self.cmd_realty_risk_scan, "项目风险扫描: /risk-scan [project_id]"),
            "/ops-report":    (self.cmd_ops_report,    "运营汇报生成: /ops-report <project_id>"),
            "/exit-calc":     (self.cmd_exit_calc,     "退出清算草案: /exit-calc <project_id>"),
            "/load-fork":     (self.cmd_load_fork,    "Restore forked conversation: /load-fork <id>"),
            # ── Vision / image input ──────────────────────────────────────────
            "/vision":    (self.cmd_vision,    "Load image for visual analysis: /vision <path>"),
            # ── Browser + desktop control ─────────────────────────────────────
            "/browser":    (self.cmd_browser,    "Browser: /browser <url> | /browser screenshot <url>"),
            "/screenshot": (self.cmd_screenshot, "Capture desktop screenshot for vision analysis"),
            # ── File analysis (multi-format, multi-layer) ─────────────────────
            "/file":      (self.cmd_file,      "文件分析: /file load|analyze|ask|list|clear <参数>"),
            # ── Project folder analysis (Claude Code / Codex style) ───────────
            "/project":   (self.cmd_project,   "项目分析: /project load|tree|grep|ask|task|status|info <参数>"),
        }
        # ── Visible commands: shown in /help (session/config/state management only)
        # All other commands still work when typed — just not cluttering /help.
        # Analysis, data, and market queries are handled by the LLM via tool calling.
        self._visible_cmds = set(VISIBLE_SLASH_COMMANDS)

        # Register skills as slash commands
        self.skill_map = {}
        for skill in SKILLS:
            self.skill_map[skill["command"]] = skill

    def is_command(self, text: str) -> bool:
        if not text.startswith("/"):
            return False
        # Only match registered commands and skills, not file paths like /Users/...
        cmd = text.split(maxsplit=1)[0].lower()
        return cmd in self.commands or cmd in self.skill_map

    async def execute(self, text: str):
        parts = text.split(maxsplit=1)
        cmd_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd_name in self.commands:
            handler, _ = self.commands[cmd_name]
            try:
                result = handler(args)
                if asyncio.iscoroutine(result):
                    await result
            except KeyboardInterrupt:
                if HAS_RICH:
                    console.print("\n[dim]已取消[/dim]")
                else:
                    print("\n已取消")
            except Exception as _cmd_err:
                import traceback as _tb
                _tb_str = _tb.format_exc()
                if HAS_RICH:
                    from rich.panel import Panel as _P
                    from rich import box as _rbox
                    console.print(_P(
                        f"[red]{type(_cmd_err).__name__}: {_cmd_err}[/red]\n"
                        f"[dim]{_tb_str.strip()[-800:]}[/dim]",
                        title=f"[red]{cmd_name} 崩溃[/red]",
                        border_style="red",
                        box=_rbox.ROUNDED,
                    ))
                else:
                    print(f"\n  ✗ {cmd_name} error: {_cmd_err}\n{_tb_str}")
        elif cmd_name in self.skill_map:
            await self._execute_skill(self.skill_map[cmd_name], args)
        else:
            # Fuzzy match: suggest closest command
            all_cmds = list(self.commands.keys()) + list(self.skill_map.keys())
            suggestions = _fuzzy_match(cmd_name, all_cmds, max_results=3)
            if HAS_RICH:
                console.print(f"[red]Unknown command: {cmd_name}[/red]")
                if suggestions:
                    console.print(f"  [dim]Did you mean: {', '.join(suggestions)}?[/dim]")
            else:
                print(f"Unknown command: {cmd_name}")
                if suggestions:
                    print(f"  Did you mean: {', '.join(suggestions)}?")

    # Per-command detailed help: (usage, examples)
    _COMMAND_HELP = {
        "/quote":     ("Usage: /quote [SYMBOL...]", ["/quote AAPL", "/quote AAPL MSFT GOOGL", "/quote  (uses watchlist)"]),
        "/analyze":   ("Usage: /analyze [SYMBOL]", ["/analyze AAPL", "/analyze TSLA"]),
        "/backtest":  ("Usage: /backtest [strategy] [symbol] [start] [end] [--period 1y] [--fast 20 --slow 60] [--output ./aria-output]", ["/backtest momentum SPY --period 1y", "/backtest sma_cross AAPL --fast 20 --slow 60 --output ./reports"]),
        "/wf":        ("Usage: /wf [symbol] [strategy] [method]", ["/wf SPY momentum rolling", "/wf QQQ breakout anchored"]),
        "/compare":   ("Usage: /compare [symbol] [start] [end]", ["/compare SPY", "/compare AAPL 2022-01-01 2025-01-01"]),
        "/watch":     ("Usage: /watch [add|remove|list] [SYMBOL]", ["/watch add AAPL", "/watch remove TSLA", "/watch list"]),
        "/crypto":    ("Usage: /crypto [SYMBOL...]", ["/crypto BTC", "/crypto ETH SOL"]),
        "/forex":     ("Usage: /forex [PAIR...]", ["/forex EUR/USD", "/forex GBP/USD JPY/USD"]),
        "/commodity": ("Usage: /commodity [NAME...]", ["/commodity gold", "/commodity oil silver"]),
        "/risk":      ("Usage: /risk [SYMBOL|portfolio]", ["/risk AAPL", "/risk portfolio"]),
        "/market":    ("Usage: /market [indices|sectors]", ["/market", "/market sectors"]),
        "/optimize":  ("Usage: /optimize [SYMBOL...]", ["/optimize AAPL MSFT GOOGL", "/optimize  (uses watchlist)"]),
        "/stress":    ("Usage: /stress [strategy] [symbol]", ["/stress momentum SPY"]),
        "/factors":   ("Usage: /factors [SYMBOL]", ["/factors AAPL"]),
        "/compliance":("Usage: /compliance [strategy]", ["/compliance momentum"]),
        "/web":       ("Usage: /web <query>", ["/web AAPL earnings Q4 2025", "/web Fed rate decision"]),
        "/services":  ("Usage: /services", ["/services"]),
        "/plan":      ("Usage: /plan <step1 ; step2 ; step3>", ["/plan git status ; rg TODO src ; pytest -q"]),
        "/apply-plan":("Usage: /apply-plan [--resume] [--from N]", ["/apply-plan", "/apply-plan --resume", "/apply-plan --from 2"]),
        "/plan-report":("Usage: /plan-report [md|json] [file] [--open]", ["/plan-report", "/plan-report md plan_report.md --open", "/plan-report json plan_report.json"]),
        "/git":       ("Usage: /git [status|diff|summary|patch|commit <msg>]", ["/git status", "/git patch apps/cli/aria_cli.py", '/git commit "feat: improve planner"']),
        "/gh":        ("Usage: /gh [prs|issues|pr N|issue N|search <q>|create-pr]", ["/gh prs", "/gh issues", "/gh pr 42", "/gh search 'async def'", "/gh create-pr"]),
        "/verify":    ("Usage: /verify [--dry-run] [path...]", ["/verify --dry-run", "/verify aria_cli.py", "/verify src/App.tsx"]),
        "/changes":   ("Usage: /changes [--all]", ["/changes", "/changes --all"]),
        "/apply-change": ("Usage: /apply-change <change_id>", ["/apply-change abc123"]),
        "/reject-change": ("Usage: /reject-change <change_id>", ["/reject-change abc123"]),
        "/news":      ("Usage: /news [topic|symbol]", ["/news", "/news AAPL", "/news technology"]),
        "/config":    ("Usage: /config [show] | /config set key=value", ["/config", "/config set model=aria-sonata:4.5", "/config set notify_webhook=https://...", "/config set brave_key=BSAAxxx"]),
        "/input":     ("Usage: /input [panel|box|plain|reset] | /input theme auto|dark|light", ["/input", "/input panel", "/input theme auto"]),
        "/privacy":   ("Usage: /privacy [status|opt-in|opt-out|export [path]|delete]", ["/privacy", "/privacy opt-in", "/privacy export"]),
        "/context":   ("Usage: /context", ["/context"]),
        "/trace":     ("Usage: /trace [--json]", ["/trace", "/trace --json"]),
        "/model":     ("Usage: /model [name|number|id]", ["/model", "/model qwen7b", "/model 2", "/model qwen2.5:7b"]),
        "/thinking":  ("Usage: /thinking [on|off|auto]", ["/thinking on", "/thinking off"]),
        "/login":     ("Usage: /login <email>  (password prompted securely)", ["/login user@example.com"]),
        "/whoami":    ("Usage: /whoami", ["/whoami"]),
        "/export":    ("Usage: /export [json|csv|md] [file]", ["/export md report.md", "/export json"]),
        "/save":      ("Usage: /save [name]", ["/save", '/save "AAPL Strategy Research"']),
        "/load":      ("Usage: /load <session_id>", ["/load abc123"]),
        "/sessions":  ("Usage: /sessions", ["/sessions"]),
        "/clear":     ("Usage: /clear", ["/clear"]),
        "/btw":       ("Usage: /btw <question>  (ephemeral — not added to history)", ["/btw what was the variable name?", "/btw which file has the config?"]),
        "/recap":     ("Usage: /recap  (session summary)", ["/recap"]),
        "/code":      ("Usage: /code <description> [--save file.py]", ["/code AAPL momentum backtest --save bt.py"]),
        "/write":     ("Usage: /write [--stage] <file_path>", ["/write report.py", "/write --stage strategy.py"]),
        # ── Financial analysis ──────────────────────────────────────────────
        "/team":      ("Usage: /team [SYMBOL] [--agents a,b] [--full]", ["/team NVDA", "/team AAPL --agents technical,risk", "/team watchlist", "/team SPY --full"]),
        "/ta":        ("Usage: /ta [SYMBOL] [days=N]", ["/ta AAPL", "/ta NVDA days=60"]),
        "/signal":    ("Usage: /signal [SYMBOL] [market]", ["/signal AAPL", "/signal sh600519 CN"]),
        "/predict":   ("Usage: /predict [SYMBOL...]", ["/predict sh600519 sh601318"]),
        "/research":  ("Usage: /research [topic or symbol]", ["/research NVDA AI chips", "/research 600519"]),
        "/earnings":  ("Usage: /earnings [SYMBOL]", ["/earnings AAPL", "/earnings TSLA"]),
        "/chart":     ("Usage: /chart [SYMBOL] [period]", ["/chart AAPL", "/chart NVDA 6mo"]),
        "/options":   ("Usage: /options [SYMBOL]", ["/options AAPL", "/options SPY"]),
        "/macro":     ("Usage: /macro [topic]", ["/macro", "/macro fed rates"]),
        "/peer":      ("Usage: /peer [SYMBOL]", ["/peer AAPL", "/peer TSLA"]),
        "/corr":      ("Usage: /corr [SYMBOL...]", ["/corr AAPL MSFT NVDA", "/corr  (uses watchlist)"]),
        "/report":    ("Usage: /report [SYMBOL] [--format html|md] [--output ./aria-output]", ["/report AAPL", "/report SPY --format md --output ./reports"]),
        "/artifacts": ("Usage: /artifacts [limit]", ["/artifacts", "/artifacts 50"]),
        "/shortterm": ("Usage: /shortterm [SYMBOL]", ["/shortterm AAPL", "/shortterm sh600519"]),
        "/longterm":  ("Usage: /longterm [SYMBOL]", ["/longterm AAPL", "/longterm sh600519"]),
        # ── China market ────────────────────────────────────────────────────
        "/screen-cn": ("Usage: /screen-cn [criteria]", ["/screen-cn momentum", "/screen-cn value"]),
        "/limitup":   ("Usage: /limitup", ["/limitup"]),
        "/north":     ("Usage: /north", ["/north"]),
        "/hot":       ("Usage: /hot [sector]", ["/hot", "/hot tech"]),
        "/indices":   ("Usage: /indices", ["/indices"]),
        # ── Portfolio & journal ─────────────────────────────────────────────
        "/portfolio": ("Usage: /portfolio [analyze|rebalance] [SYMBOL...]", ["/portfolio", "/portfolio analyze AAPL MSFT TSLA", "/portfolio rebalance"]),
        "/journal":   ("Usage: /journal [add|trades|pnl|realized|export|delete]", ["/journal", "/journal add buy AAPL 100 185.50", "/journal pnl", "/journal realized", "/journal export"]),
        "/optimize-port": ("Usage: /optimize-port [SYMBOL...]", ["/optimize-port AAPL MSFT NVDA"]),
        # ── Alerts & screening ──────────────────────────────────────────────
        "/alert":     ("Usage: /alert [add|list|delete|check] [SYMBOL] [gt|lt] [price]", ["/alert add AAPL gt 200", "/alert list", "/alert check", "/alert delete 1"]),
        "/screen":    ("Usage: /screen [criteria]", ["/screen tech growth", "/screen value dividend"]),
        "/watchlist-scan": ("Usage: /watchlist-scan", ["/watchlist-scan"]),
        # ── Real estate ─────────────────────────────────────────────────────
        "/realty":    ("Usage: /realty [market CITY] [calc buy|rent|roi] [compare] [trend CITY]", ["/realty market 北京", "/realty calc buy", "/realty compare", "/realty trend 上海"]),
        # ── Brokers ─────────────────────────────────────────────────────────
        "/broker":    ("Usage: /broker [list|connect NAME|disconnect|status]", ["/broker list", "/broker connect futu", "/broker status"]),
        "/account":   ("Usage: /account", ["/account"]),
        "/positions": ("Usage: /positions", ["/positions"]),
        "/orders":    ("Usage: /orders [pending|all]", ["/orders", "/orders pending"]),
        # ── Utilities ───────────────────────────────────────────────────────
        "/vision":      ("Usage: /vision <image_path>", ["/vision ~/Desktop/chart.png", "/vision /tmp/screenshot.png"]),
        "/browser":     ("Usage: /browser <url>  or  /browser screenshot <url>", ["/browser https://example.com", "/browser screenshot https://github.com"]),
        "/screenshot":  ("Usage: /screenshot [monitor]", ["/screenshot", "/screenshot 1"]),
        "/memory":    ("Usage: /memory [show|add|clear|search]", ["/memory show", "/memory add 我偏好技术分析", "/memory search 风险偏好"]),
        "/project":   ("Usage: /project [load|analyze|files|symbols|tasks|status|close]", ["/project load .", "/project analyze", "/project files", "/project tasks"]),
        "/mcp":       ("Usage: /mcp [list|connect|disconnect|tools]", ["/mcp list", "/mcp tools"]),
        "/skills":    ("Usage: /skills", ["/skills"]),
        "/tools":     ("Usage: /tools [list|call TOOL_NAME]", ["/tools", "/tools list"]),
        "/data":      ("Usage: /data [SYMBOL] [field]", ["/data AAPL", "/data sh600519 history"]),
        "/apikey":    ("Usage: /apikey → 向导  /apikey list → 查看  /apikey test <p> → 测试  /apikey set <p> <k>", ["/apikey", "/apikey list", "/apikey test deepseek", "/apikey set openai sk-..."]),
        "/ariarc":    ("Usage: /ariarc [show|init|set key=val]", ["/ariarc show", "/ariarc init", "/ariarc set default_symbols=AAPL,MSFT"]),
        "/setup":     ("Usage: /setup [mcp|broker|keys|all]", ["/setup", "/setup mcp", "/setup keys"]),
        "/doctor":    ("Usage: /doctor", ["/doctor"]),
        "/history":   ("Usage: /history [N]", ["/history", "/history 20"]),
        "/compact":   ("Usage: /compact", ["/compact"]),
        "/note":      ("Usage: /note [list|add|delete N]", ["/note add 重要观察点", "/note list", "/note delete 1"]),
        "/todo":      ("Usage: /todo [add|done|list|clear] [text]", ["/todo add 分析NVDA", "/todo list", "/todo done 1"]),
        "/copy":      ("Usage: /copy [N]", ["/copy", "/copy 3"]),
        "/read":      ("Usage: /read <file_path>", ["/read strategy.py", "/read data/prices.csv"]),
        "/edit":      ("Usage: /edit <file_path>", ["/edit strategy.py"]),
        "/run":       ("Usage: /run <command>", ["/run python strategy.py", "/run pytest -q"]),
        "/ls":        ("Usage: /ls [path]", ["/ls", "/ls src/"]),
        "/search":    ("Usage: /search <query>", ["/search AAPL earnings", "/search momentum strategy"]),
        "/local":     ("Usage: /local [on|off|status]", ["/local", "/local on", "/local off"]),
        "/providers": ("Usage: /providers", ["/providers"]),
        "/feargreed": ("Usage: /feargreed", ["/feargreed"]),
        "/funding":   ("Usage: /funding [SYMBOL]", ["/funding BTC", "/funding ETH"]),
        "/quality":   ("Usage: /quality [SYMBOL]", ["/quality AAPL", "/quality 600519"]),
        "/ichimoku":  ("Usage: /ichimoku [SYMBOL]", ["/ichimoku AAPL", "/ichimoku USDJPY"]),
        "/factor-lab":("Usage: /factor-lab [SYMBOL]", ["/factor-lab AAPL", "/factor-lab sh600519"]),
        "/execution": ("Usage: /execution SYMBOL buy|sell QTY [algo=compare] [price=N]", ["/execution AAPL buy 100000", "/execution SPY sell 50000 algo=is"]),
        "/stat-arb":  ("Usage: /stat-arb SYMBOL_A SYMBOL_B [period=2y]", ["/stat-arb GLD SLV", "/stat-arb SPY QQQ period=1y"]),
        "/edgar":     ("Usage: /edgar SYMBOL [filings|facts|insider]", ["/edgar AAPL", "/edgar MSFT facts", "/edgar TSLA insider"]),
        "/datasource":("Usage: /datasource | /datasource test SOURCE | /datasource config", ["/datasource", "/datasource test fred", "/datasource config"]),
        "/sector-rotation": ("Usage: /sector-rotation", ["/sector-rotation"]),
        "/auto-strategy":   ("Usage: /auto-strategy [objective] [SYMBOL...]", ["/auto-strategy momentum AAPL", "/auto-strategy mean_reversion SPY"]),
        "/morning-brief":   ("Usage: /morning-brief", ["/morning-brief"]),
        "/deep-analysis":   ("Usage: /deep-analysis [SYMBOL]", ["/deep-analysis NVDA"]),
        "/trade-idea":      ("Usage: /trade-idea [SYMBOL]", ["/trade-idea AAPL"]),
        "/review":          ("Usage: /review [file_or_code]", ["/review strategy.py", "/review"]),
        "/init":            ("Usage: /init [template]", ["/init", "/init quant"]),
        "/scaffold":        ("Usage: /scaffold [type] [name]", ["/scaffold strategy momentum", "/scaffold agent news"]),
        "/cost":            ("Usage: /cost [session|total|reset]", ["/cost", "/cost session", "/cost reset"]),
        "/rename":          ("Usage: /rename <new_name>", ['/rename "NVDA Research Session"']),
        "/feedback":        ("Usage: /feedback <message>", ["/feedback 分析结果不够准确"]),
        "/hooks":           ("Usage: /hooks [list|enable|disable]", ["/hooks list", "/hooks enable pre_trade"]),
        "/logout":          ("Usage: /logout", ["/logout"]),
        "/status":          ("Usage: /status", ["/status"]),
        "/health":          ("Usage: /health", ["/health"]),
        "/artifacts":       ("Usage: /artifacts [limit]", ["/artifacts", "/artifacts 50"]),
    }

    def cmd_help(self, args: str):
        # Contextual help: /help <command>
        target = args.strip().lower()
        if target:
            cmd_key = target if target.startswith("/") else f"/{target}"
            if cmd_key in self.commands:
                _, desc = self.commands[cmd_key]
                if HAS_RICH:
                    console.print()
                    console.print(f"  [bold #C08050]{cmd_key}[/bold #C08050]  [dim]{desc}[/dim]")
                    h = self._COMMAND_HELP.get(cmd_key)
                    if h:
                        console.print(f"  {h[0]}")
                        console.print()
                        console.print("  [dim]Examples:[/dim]")
                        for ex in h[1]:
                            console.print(f"    [bold]{ex}[/bold]")
                    console.print()
                else:
                    print(f"\n  {cmd_key}  {desc}")
                return
            # Check skills
            for s in SKILLS:
                if s["command"] == cmd_key:
                    if HAS_RICH:
                        console.print()
                        console.print(f"  [bold #C08050]{s['command']}[/bold #C08050]  [dim]{s['description']}[/dim]")
                        console.print(f"  [dim]Category:[/dim] {s['category']}")
                        console.print()
                    else:
                        print(f"\n  {s['command']}  {s['description']}")
                    return
            console.print(f"[dim]No help for: {target}. Try /help[/dim]" if HAS_RICH else f"No help for: {target}")
            return

        # Full help listing
        show_all = args.strip().lower() == "all"

        if HAS_RICH:
            console.print()
            if show_all:
                console.print("[bold]全部命令[/bold]  [dim](/help <command> for details)[/dim]")
            else:
                console.print("[bold]Commands[/bold]  [dim](/help <command> · /help all 显示全部)[/dim]")
            console.print()

            # Show visible commands (or all if /help all)
            shown = {
                name: desc
                for name, (_, desc) in self.commands.items()
                if show_all or name in self._visible_cmds
            }
            for name, desc in shown.items():
                console.print(f"  [bold #C08050]{name:18s}[/bold #C08050][dim]{desc}[/dim]")

            if not show_all:
                hidden_count = len(self.commands) - len(shown)
                console.print(
                    f"\n  [dim]+ {hidden_count} 个快捷命令已隐藏 · 直接聊天让 AI 完成分析[/dim]"
                )
            console.print()

            # --- Skills (grouped by category) ---
            categories: dict = {}
            for s in SKILLS:
                cat = s["category"]
                if cat not in categories:
                    categories[cat] = []
                categories[cat].append(s)

            console.print("[bold]Skills[/bold]")
            console.print()
            for cat, skills in categories.items():
                console.print(f"  [dim]{cat}[/dim]")
                for s in skills:
                    console.print(f"    [bold #C08050]{s['command']:20s}[/bold #C08050][dim]{s['description']}[/dim]")
            console.print()

            # Keyboard shortcuts
            console.print("[bold]Keyboard Shortcuts[/bold]")
            console.print()
            shortcuts = [
                ("ESC",    "Cancel current generation"),
                ("Ctrl+D", "Exit"),
                ("Ctrl+C", "Cancel / exit"),
                ("↑  ↓",   "History navigation"),
                ("Tab",    "Command autocomplete"),
                ('"""',    "Enter multi-line input mode"),
            ]
            for key, desc in shortcuts:
                console.print(f"  [bold #C08050]{key:14s}[/bold #C08050][dim]{desc}[/dim]")
            console.print()

            # Footer
            console.print(
                "[dim]直接输入问题 — AI 会自动分析并调用工具  · "
                "/model 切换模型 · /help all 查看全部命令[/dim]"
            )
        else:
            cmds_to_show = {
                n: d for n, (_, d) in self.commands.items()
                if show_all or n in self._visible_cmds
            }
            print("\nCommands:")
            for name, desc in cmds_to_show.items():
                print(f"  {name:18s} {desc}")
            print("\nSkills:")
            for s in SKILLS:
                print(f"  {s['command']:20s} {s['description']}")

    async def cmd_artifacts(self, args: str):
        try:
            limit = int(args.strip()) if args.strip() else 20
        except Exception:
            limit = 20
        from artifacts import artifact_root, recent_artifacts

        root = artifact_root()
        items = recent_artifacts(limit=limit, root=root)
        if not items:
            msg = f"No artifacts found under {root}"
            console.print(f"[dim]{msg}[/dim]") if HAS_RICH else print(msg)
            return

        if HAS_RICH:
            from rich.table import Table
            table = Table(title=f"Generated artifacts · {root}", show_header=True, header_style="bold")
            table.add_column("Kind", style="dim")
            table.add_column("Status")
            table.add_column("Topic")
            table.add_column("Path", overflow="fold")
            for item in items:
                table.add_row(
                    str(item.get("kind") or "artifact"),
                    str(item.get("status") or "unknown"),
                    str(item.get("topic") or ""),
                    str(item.get("path") or item.get("metadata_path") or ""),
                )
            console.print(table)
        else:
            print(f"Generated artifacts · {root}")
            for item in items:
                print(f"- {item.get('kind')} [{item.get('status')}] {item.get('topic')}: {item.get('path')}")
    async def cmd_analyze(self, args: str):
        """Deep analysis: fetch real quote + TA + fundamentals, then ask LLM."""
        symbol = args.strip().upper() or "AAPL"
        is_cn  = _is_ashare_symbol(symbol)

        if HAS_RICH:
            with console.status(f"[dim]正在获取 {symbol} 数据...[/dim]", spinner="dots"):
                ctx = await self._build_analyze_context(symbol, is_cn)
        else:
            print(f"Fetching data for {symbol}...")
            ctx = await self._build_analyze_context(symbol, is_cn)

        await self.terminal.send_message(build_analyze_prompt(symbol, ctx, is_cn))

    async def _build_analyze_context(self, symbol: str, is_cn: bool) -> str:
        """Fetch real market data and return a structured context string for the LLM."""
        return await build_analyze_context(
            symbol,
            is_cn,
            has_mdc=_HAS_MDC,
            get_mdc=_get_mdc if _HAS_MDC else None,
            ashare_name_lookup=_ashare_code_to_name,
            has_brokers=_HAS_BROKERS,
            get_broker_registry=_get_broker_registry if _HAS_BROKERS else None,
            logger=logger,
        )
    # ────────────────────────────────────────────────────────────────────────
    # New Industry Commands
    # ────────────────────────────────────────────────────────────────────────

    async def cmd_macro(self, args: str):
        """/macro [us|cn|rates|calendar] [indicator]  — 宏观经济数据仪表板"""
        import asyncio as _asyncio
        parts = args.strip().lower().split() if args.strip() else []
        region = parts[0] if parts else "all"
        indicator = parts[1] if len(parts) > 1 else "all"

        try:
            from macro_tools import get_us_macro, get_cn_macro, get_central_bank_rates, get_economic_calendar
        except ImportError:
            if HAS_RICH:
                console.print("[red]macro_tools 模块未找到[/red]")
            return

        loop = _asyncio.get_event_loop()

        if region in ("us", "all"):
            if HAS_RICH:
                with console.status("[dim]获取美国宏观数据 (FRED)...[/dim]", spinner="dots"):
                    r = await loop.run_in_executor(None, lambda: get_us_macro(indicator if region == "us" else "all"))
            else:
                r = get_us_macro(indicator if region == "us" else "all")
            _render_macro_result(r, "🇺🇸 美国宏观")

        if region in ("cn", "all"):
            if HAS_RICH:
                with console.status("[dim]获取中国宏观数据 (akshare)...[/dim]", spinner="dots"):
                    r_cn = await loop.run_in_executor(None, lambda: get_cn_macro(indicator if region == "cn" else "all"))
            else:
                r_cn = get_cn_macro(indicator if region == "cn" else "all")
            _render_macro_result(r_cn, "🇨🇳 中国宏观")

        if region in ("rates", "all"):
            if HAS_RICH:
                with console.status("[dim]获取央行利率...[/dim]", spinner="dots"):
                    r_rates = await loop.run_in_executor(None, get_central_bank_rates)
            else:
                r_rates = get_central_bank_rates()
            _render_cb_rates(r_rates)

        if region == "calendar":
            if HAS_RICH:
                with console.status("[dim]获取经济日历...[/dim]", spinner="dots"):
                    r_cal = await loop.run_in_executor(None, lambda: get_economic_calendar(7))
            else:
                r_cal = get_economic_calendar(7)
            _render_econ_calendar(r_cal)

    async def cmd_options(self, args: str):
        """/options <symbol> [calls|puts] [expiry]  — 期权链查询"""
        parts = args.strip().split() if args.strip() else []
        symbol = parts[0].upper() if parts else "AAPL"
        opt_type = "both"
        expiry = ""
        for p in parts[1:]:
            if p.lower() in ("calls", "puts"):
                opt_type = p.lower()
            elif "-" in p and len(p) == 10:
                expiry = p

        if not _HAS_LOCAL_FINANCE:
            if HAS_RICH: console.print("[red]local_finance_tools 未加载[/red]")
            return

        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        if HAS_RICH:
            with console.status(f"[dim]获取 {symbol} 期权链...[/dim]", spinner="dots"):
                from local_finance_tools import _get_options_chain
                r = await loop.run_in_executor(None, _get_options_chain,
                                               {"symbol": symbol, "type": opt_type, "expiry": expiry, "limit": 20})
        else:
            from local_finance_tools import _get_options_chain
            r = _get_options_chain({"symbol": symbol, "type": opt_type, "expiry": expiry, "limit": 20})

        if not r.get("success"):
            if HAS_RICH: console.print(f"[red]{r.get('error')}[/red]")
            return

        _render_options_chain(r)

        # ── B-S 理论定价附加展示（ATM call + put）──────────────────────────
        try:
            spot = r.get("current_price") or r.get("spot_price")
            if spot and spot > 0:
                import sys as _sys, pathlib as _pathlib
                _qe = str(_pathlib.Path(__file__).parents[1] / "Arthera")
                if _qe not in _sys.path:
                    _sys.path.insert(0, _qe)
                from packages.quant_engine.stochastic.options_pricing import (
                    OptionSpec, black_scholes,
                )
                T   = 30 / 365   # 近月合约估算
                r_f = 0.05
                # 从返回数据中提取第一个合约的 IV 作为 sigma 估算
                chain = r.get("calls", []) or r.get("chain", []) or []
                sigma = 0.25
                for row in chain[:5]:
                    iv = row.get("impliedVolatility") or row.get("iv")
                    if iv and 0.01 < float(iv) < 5.0:
                        sigma = float(iv)
                        break

                atm_call = black_scholes(OptionSpec(S=spot, K=round(spot, -1) or spot,
                                                     T=T, r=r_f, sigma=sigma, option_type="call"))
                atm_put  = black_scholes(OptionSpec(S=spot, K=round(spot, -1) or spot,
                                                     T=T, r=r_f, sigma=sigma, option_type="put"))
                if HAS_RICH:
                    from rich.table import Table
                    from rich import box as _box
                    tbl = Table(title=f"[bold]B-S ATM 理论价格[/bold]  σ={sigma:.0%}  T=30d  r=5%",
                                box=_box.SIMPLE, show_header=True, header_style="bold dim")
                    tbl.add_column("", style="dim")
                    tbl.add_column("理论价", justify="right")
                    tbl.add_column("Delta", justify="right")
                    tbl.add_column("Gamma", justify="right")
                    tbl.add_column("Theta/日", justify="right")
                    tbl.add_column("Vega/1%", justify="right")
                    tbl.add_column("Vanna", justify="right")
                    tbl.add_row("Call", f"{atm_call.price:.2f}", f"{atm_call.delta:+.3f}",
                                f"{atm_call.gamma:.4f}", f"{atm_call.theta:+.4f}",
                                f"{atm_call.vega:.4f}", f"{atm_call.vanna:.4f}")
                    tbl.add_row("Put",  f"{atm_put.price:.2f}",  f"{atm_put.delta:+.3f}",
                                f"{atm_put.gamma:.4f}",  f"{atm_put.theta:+.4f}",
                                f"{atm_put.vega:.4f}",  f"{atm_put.vanna:.4f}")
                    console.print(tbl)
                else:
                    print(f"B-S ATM call={atm_call.price:.2f} Δ={atm_call.delta:+.3f}  "
                          f"put={atm_put.price:.2f} Δ={atm_put.delta:+.3f}  σ={sigma:.0%}")
        except Exception:
            pass   # B-S 附加展示失败不阻断主流程

    async def cmd_quality(self, args: str):
        """/quality <symbol>  — Piotroski F-Score + Altman Z-Score 双维财务质量评估"""
        symbol = args.strip().upper() if args.strip() else "AAPL"
        if not _HAS_LOCAL_FINANCE:
            if HAS_RICH: console.print("[red]local_finance_tools 未加载[/red]")
            return

        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        if HAS_RICH:
            with console.status(f"[dim]计算 {symbol} 财务质量评分...[/dim]", spinner="dots"):
                from local_finance_tools import _piotroski_fscore, _altman_zscore
                f_r = await loop.run_in_executor(None, _piotroski_fscore, {"symbol": symbol})
                z_r = await loop.run_in_executor(None, _altman_zscore,    {"symbol": symbol})
        else:
            from local_finance_tools import _piotroski_fscore, _altman_zscore
            f_r = _piotroski_fscore({"symbol": symbol})
            z_r = _altman_zscore({"symbol": symbol})

        _render_quality_scores(symbol, f_r, z_r)

    async def cmd_ichimoku(self, args: str):
        """/ichimoku <symbol>  — 一目均衡表分析"""
        symbol = args.strip().upper() if args.strip() else "AAPL"
        if not _HAS_LOCAL_FINANCE:
            if HAS_RICH: console.print("[red]local_finance_tools 未加载[/red]")
            return

        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        if HAS_RICH:
            with console.status(f"[dim]计算 {symbol} 一目均衡表...[/dim]", spinner="dots"):
                from local_finance_tools import _calculate_ichimoku
                r = await loop.run_in_executor(None, _calculate_ichimoku, {"symbol": symbol})
        else:
            from local_finance_tools import _calculate_ichimoku
            r = _calculate_ichimoku({"symbol": symbol})

        _render_ichimoku(r)

    async def cmd_fear_greed(self, args: str):
        """/feargreed  — 加密货币恐惧贪婪指数"""
        if not _HAS_LOCAL_FINANCE:
            if HAS_RICH: console.print("[red]local_finance_tools 未加载[/red]")
            return

        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        if HAS_RICH:
            with console.status("[dim]获取恐惧贪婪指数...[/dim]", spinner="dots"):
                from local_finance_tools import _get_fear_greed_index
                r = await loop.run_in_executor(None, _get_fear_greed_index, {})
        else:
            from local_finance_tools import _get_fear_greed_index
            r = _get_fear_greed_index({})

        _render_fear_greed(r)

    async def cmd_funding(self, args: str):
        """/funding [BTC ETH SOL] [exchange]  — 永续合约资金费率"""
        parts = args.strip().split() if args.strip() else []
        exchange = "binance"
        syms = []
        for p in parts:
            if p.lower() in ("binance", "okx", "bybit", "coinbase"):
                exchange = p.lower()
            else:
                syms.append(p.upper() + "/USDT" if "/" not in p else p.upper())
        if not syms:
            syms = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

        if not _HAS_LOCAL_FINANCE:
            if HAS_RICH: console.print("[red]local_finance_tools 未加载[/red]")
            return

        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        if HAS_RICH:
            with console.status(f"[dim]获取 {exchange} 资金费率...[/dim]", spinner="dots"):
                from local_finance_tools import _get_funding_rates
                r = await loop.run_in_executor(None, _get_funding_rates,
                                               {"exchange": exchange, "symbols": syms})
        else:
            from local_finance_tools import _get_funding_rates
            r = _get_funding_rates({"exchange": exchange, "symbols": syms})

        _render_funding_rates(r)

    # ── /realty 不动产命令 ─────────────────────────────────────────────────────
    # ── /football 足球分析命令 ────────────────────────────────────────────────
    async def _run_in_executor(self, fn, *args):
        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, fn, *args)
        return result

    # ── /data 数据分析命令 ─────────────────────────────────────────────────────

    async def cmd_data(self, args: str):
        """
        /data sql "SELECT ..."     — DuckDB SQL 查询
        /data export [filename]    — 导出上次结果到 Excel
        /data load <csv_path>      — 加载 CSV 到 DuckDB
        /data tables               — 列出已加载的表
        """
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        parts = args.strip().split(None, 1) if args.strip() else []
        sub = parts[0].lower() if parts else "help"
        rest = parts[1] if len(parts) > 1 else ""

        try:
            from data_analysis_tools import (sql_query, sql_list_tables,
                                              export_to_excel, load_csv_data)
        except ImportError as e:
            if HAS_RICH: console.print(f"[red]data_analysis_tools 未加载: {e}[/red]")
            return

        if sub == "sql":
            query = rest.strip().strip('"').strip("'")
            if not query:
                if HAS_RICH: console.print("[dim]用法: /data sql \"SELECT ...\"|/dim]")
                return
            if HAS_RICH:
                with console.status("[dim]执行 SQL...[/dim]", spinner="dots"):
                    r = await loop.run_in_executor(None, sql_query, {"query": query})
            else:
                r = sql_query({"query": query})
            _render_sql_result(r)

        elif sub == "export":
            # Export the last finance tool result or a placeholder
            fname = rest.strip() or None
            # We'll export a sample from watchlist if available
            watchlist = self.terminal.config.get("watchlist", ["AAPL","MSFT","SPY"])
            try:
                import yfinance as _yf
                raw = _yf.download(watchlist[:5], period="1mo", progress=False, auto_adjust=True)
                closes = raw["Close"] if hasattr(raw.columns, "levels") else raw
                export_data = {"价格历史": closes.reset_index().to_dict("records")}
            except Exception:
                export_data = {"示例数据": [{"symbol": s, "note": "需 yfinance"} for s in watchlist]}
            p = {"data": export_data, "filename": fname}
            if HAS_RICH:
                with console.status("[dim]生成 Excel...[/dim]", spinner="dots"):
                    r = await loop.run_in_executor(None, export_to_excel, p)
            else:
                r = export_to_excel(p)
            if r.get("success"):
                msg = f"✓ 已导出: {r['path']}  ({r['total_rows']} 行)"
                if HAS_RICH: console.print(f"[green]{msg}[/green]")
                else: print(msg)
            else:
                if HAS_RICH: console.print(f"[red]{r.get('error')}[/red]")

        elif sub == "load":
            csv_path = rest.strip()
            if not csv_path:
                if HAS_RICH: console.print("[dim]用法: /data load <csv文件路径>[/dim]")
                return
            if HAS_RICH:
                with console.status("[dim]加载 CSV...[/dim]", spinner="dots"):
                    r = await loop.run_in_executor(None, load_csv_data, {"path": csv_path})
            else:
                r = load_csv_data({"path": csv_path})
            if r.get("success"):
                if HAS_RICH:
                    console.print(f"[green]✓ 已加载 {r['rows']} 行 → 表 {r['table_name']}[/green]")
                    console.print(f"[dim]列: {', '.join(r['columns'][:10])}[/dim]")
                    console.print(f"[dim]现在可以: /data sql \"SELECT * FROM {r['table_name']} LIMIT 10\"[/dim]")
            else:
                if HAS_RICH: console.print(f"[red]{r.get('error')}[/red]")

        elif sub == "tables":
            r = sql_list_tables()
            if r.get("success"):
                tables = r.get("tables", [])
                if HAS_RICH:
                    if tables:
                        console.print(f"[bold]已加载表:[/bold] {', '.join(tables)}")
                    else:
                        console.print("[dim]暂无已加载的表。使用 /data load <csv> 加载数据[/dim]")

        else:
            if HAS_RICH:
                console.print("[dim]用法: /data [sql|export|load|tables][/dim]")
                console.print("[dim]  /data sql \"SELECT * FROM my_table LIMIT 10\"[/dim]")
                console.print("[dim]  /data load ~/Desktop/data.csv[/dim]")
                console.print("[dim]  /data export my_report.xlsx[/dim]")
                console.print("[dim]  /data tables[/dim]")

    # ── /alert 价格预警 ────────────────────────────────────────────────────────

    async def cmd_alert(self, args: str):
        """
        /alert add AAPL gt 200     — 设置预警（gt/lt/cross_up/cross_down）
        /alert list                 — 列出所有预警
        /alert delete <id>          — 删除预警
        /alert check                — 检查所有预警状态
        """
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        parts = args.strip().split() if args.strip() else []
        sub = parts[0].lower() if parts else "list"

        try:
            from data_analysis_tools import (add_price_alert, list_price_alerts,
                                              delete_price_alert, check_alerts)
        except ImportError as e:
            if HAS_RICH: console.print(f"[red]data_analysis_tools 未加载: {e}[/red]")
            return

        if sub == "add":
            # /alert add AAPL gt 200 [note...]
            if len(parts) < 4:
                if HAS_RICH:
                    console.print("[dim]用法: /alert add <symbol> <gt|lt|cross_up|cross_down> <price> [备注][/dim]")
                return
            sym  = parts[1].upper()
            cond = parts[2].lower()
            try:
                price = float(parts[3])
            except ValueError:
                if HAS_RICH: console.print("[red]价格必须是数字[/red]")
                return
            note = " ".join(parts[4:]) if len(parts) > 4 else ""
            r = add_price_alert({"symbol": sym, "condition": cond, "price": price, "note": note})
            if r.get("success"):
                msg = r.get("message", "预警已设置")
                if HAS_RICH: console.print(f"[green]✓ {msg}[/green]")
                else: print(f"✓ {msg}")
            else:
                if HAS_RICH: console.print(f"[red]{r.get('error')}[/red]")

        elif sub == "list":
            r = list_price_alerts()
            _render_alerts(r)

        elif sub in ("delete", "del", "remove"):
            alert_id = parts[1] if len(parts) > 1 else ""
            if not alert_id:
                if HAS_RICH: console.print("[dim]用法: /alert delete <预警ID>[/dim]")
                return
            r = delete_price_alert({"alert_id": alert_id})
            if r.get("success"):
                if HAS_RICH: console.print(f"[green]✓ 已删除预警 {r['deleted_id']}[/green]")
            else:
                if HAS_RICH: console.print(f"[red]{r.get('error')}[/red]")

        elif sub == "check":
            if HAS_RICH:
                with console.status("[dim]检查价格预警...[/dim]", spinner="dots"):
                    r = await loop.run_in_executor(None, check_alerts)
            else:
                r = check_alerts()
            triggered = r.get("triggered", [])
            if triggered:
                if HAS_RICH:
                    console.print(f"[bold yellow]🔔 {len(triggered)} 个预警已触发![/bold yellow]")
                    for a in triggered:
                        console.print(f"  [yellow]{a['symbol']}[/yellow] {a.get('condition','')} "
                                      f"{a['price']} → 当前 [bold]{a.get('triggered_price','')}[/bold]")
            else:
                msg = r.get("message", "暂无触发的预警")
                if HAS_RICH: console.print(f"[dim]{msg}[/dim]")

        else:
            if HAS_RICH:
                console.print("[dim]用法: /alert [add|list|delete|check][/dim]")

    # ── /corr 相关性矩阵 ───────────────────────────────────────────────────────

    async def cmd_corr(self, args: str):
        """/corr AAPL MSFT TSLA SPY [1y|2y|6mo]  — 计算相关性矩阵"""
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        parts = args.strip().upper().split() if args.strip() else []

        # Last part can be period
        period = "1y"
        if parts and parts[-1].lower() in ("1y","2y","3y","6mo","ytd","5y"):
            period = parts[-1].lower()
            parts = parts[:-1]

        symbols = parts if parts else ["AAPL","MSFT","TSLA","SPY","QQQ"]

        try:
            from data_analysis_tools import calc_correlation_matrix
        except ImportError as e:
            if HAS_RICH: console.print(f"[red]data_analysis_tools 未加载: {e}[/red]")
            return

        if HAS_RICH:
            with console.status(f"[dim]计算 {', '.join(symbols)} 相关性矩阵...[/dim]", spinner="dots"):
                r = await loop.run_in_executor(None, calc_correlation_matrix,
                                               {"symbols": symbols, "period": period})
        else:
            r = calc_correlation_matrix({"symbols": symbols, "period": period})
        _render_corr_matrix(r)

    # ── /ptbt 多资产组合回测 ───────────────────────────────────────────────────

    async def cmd_portfolio_bt(self, args: str):
        """/ptbt AAPL MSFT GOOG [0.4 0.3 0.3] [2y] [monthly]  — 多资产组合回测"""
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        parts = args.strip().split() if args.strip() else []

        try:
            from data_analysis_tools import portfolio_backtest
        except ImportError as e:
            if HAS_RICH: console.print(f"[red]data_analysis_tools 未加载: {e}[/red]")
            return

        # Parse: symbols, optional weights (floats < 1), optional period, optional rebalance
        symbols, weights, period, rebalance = [], [], "2y", "monthly"
        _PERIODS   = {"1y","2y","3y","5y","6mo","ytd","max"}
        _REBALANCE = {"monthly","quarterly","none"}
        for p in parts:
            pl = p.lower()
            if pl in _PERIODS:   period = pl; continue
            if pl in _REBALANCE: rebalance = pl; continue
            try:
                f = float(p)
                if f < 2:   weights.append(f)
                else:        symbols.append(p.upper())
            except ValueError:
                symbols.append(p.upper())

        if not symbols:
            symbols = ["AAPL","MSFT","GOOGL","SPY"]
            if HAS_RICH:
                console.print(f"[dim]未指定标的，使用默认: {symbols}[/dim]")

        p_params = {"symbols": symbols, "period": period, "rebalance": rebalance}
        if weights: p_params["weights"] = weights

        if HAS_RICH:
            with console.status(f"[dim]回测 {', '.join(symbols)} ({period})...[/dim]", spinner="dots"):
                r = await loop.run_in_executor(None, portfolio_backtest, p_params)
        else:
            r = portfolio_backtest(p_params)
        _render_portfolio_bt(r)

    async def cmd_peer(self, args: str):
        """/peer <symbol> [peer1 peer2 ...]  — 同行估值对比"""
        parts = args.strip().upper().split() if args.strip() else []
        symbol = parts[0] if parts else "AAPL"
        peers  = parts[1:] if len(parts) > 1 else []

        if not _HAS_LOCAL_FINANCE:
            if HAS_RICH: console.print("[red]local_finance_tools 未加载[/red]")
            return

        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        if HAS_RICH:
            with console.status(f"[dim]获取 {symbol} 同行数据...[/dim]", spinner="dots"):
                from local_finance_tools import _peer_comparison
                r = await loop.run_in_executor(None, _peer_comparison,
                                               {"symbol": symbol, "peers": peers})
        else:
            from local_finance_tools import _peer_comparison
            r = _peer_comparison({"symbol": symbol, "peers": peers})

        _render_peer_comparison(r)

    async def cmd_compare(self, args: str):
        """多策略横向对比 → /api/v1/backtest/compare-strategies"""
        parts = args.split() if args else ["SPY"]
        symbol = parts[0].upper() if parts else "SPY"
        start = parts[1] if len(parts) > 1 else "2020-01-01"
        end = parts[2] if len(parts) > 2 else __import__("datetime").date.today().isoformat()
        api_url = self.terminal.config.get("api_url", "http://localhost:8000")
        import aiohttp

        async def _do():
            payload = {"symbol": symbol, "strategies": ["momentum","mean_reversion","breakout","turtle","ma_crossover"],
                       "start_date": start, "end_date": end, "initial_capital": 100000, "commission_rate": 0.0003}
            async with aiohttp.ClientSession() as sess:
                async with sess.post(f"{api_url}/api/v1/backtest/compare-strategies", json=payload, timeout=aiohttp.ClientTimeout(total=90)) as resp:
                    if resp.status != 200: raise RuntimeError(f"HTTP {resp.status}")
                    body = await resp.json()
                    return body.get("data", body)

        if HAS_RICH:
            with console.status(f"[dim]Comparing strategies on {symbol}...[/dim]", spinner="dots"):
                try: data = await _do()
                except Exception as e: _print_error(str(e), "tool"); return
        else:
            print(f"Comparing strategies on {symbol}...")
            try: data = await _do()
            except Exception as e: _print_error(str(e), "tool"); return

        strategies = data.get("strategies", [])
        bh = data.get("benchmark", {})
        if HAS_RICH:
            from rich.table import Table
            tbl = Table(title=f"[bold]{symbol} Strategy Comparison[/bold]  {start} → {end}", show_header=True, header_style="bold")
            for col in ["Rank", "Strategy", "Ann.Ret%", "Sharpe", "MaxDD%", "Calmar", "Sortino", "Win%", "Trades"]:
                tbl.add_column(col, justify="right")
            for s in strategies:
                tbl.add_row(
                    str(s.get("rank_by_sharpe", "")),
                    s["name"],
                    f"{s.get('annualized_return_pct',0):+.1f}%",
                    f"{s.get('sharpe_ratio',0):.3f}",
                    f"{s.get('max_drawdown_pct',0):.1f}%",
                    f"{s.get('calmar_ratio',0):.2f}",
                    f"{s.get('sortino_ratio',0):.2f}",
                    f"{s.get('win_rate_pct',0):.0f}%",
                    str(s.get("n_trades",0)),
                )
            tbl.add_row("—", "[dim]Buy & Hold[/dim]",
                f"{bh.get('annualized_return_pct',0):+.1f}%",
                f"{bh.get('sharpe_ratio',0):.3f}",
                f"{bh.get('max_drawdown_pct',0):.1f}%", "—","—","—","2")
            console.print(tbl)
        else:
            for s in strategies:
                print(f"{s['name']}: Ann={s.get('annualized_return_pct',0):+.1f}% Sharpe={s.get('sharpe_ratio',0):.2f} DD={s.get('max_drawdown_pct',0):.1f}%")

    def cmd_watch(self, args: str):
        parts = args.split() if args else ["list"]
        action = parts[0].lower() if parts else "list"
        watchlist = self.terminal.config.get("watchlist", [])

        if action == "add" and len(parts) > 1:
            symbol = parts[1].upper()
            if symbol not in watchlist:
                watchlist.append(symbol)
                self.terminal.config["watchlist"] = watchlist
                save_config(self.terminal.config)
                console.print(f"[green]Added {symbol} to watchlist[/green]" if HAS_RICH
                              else f"Added {symbol}")
            else:
                console.print(f"[dim]{symbol} already in watchlist[/dim]" if HAS_RICH
                              else f"{symbol} already in watchlist")

        elif action == "remove" and len(parts) > 1:
            symbol = parts[1].upper()
            if symbol in watchlist:
                watchlist.remove(symbol)
                self.terminal.config["watchlist"] = watchlist
                save_config(self.terminal.config)
                console.print(f"[dim]Removed {symbol} from watchlist[/dim]" if HAS_RICH
                              else f"Removed {symbol}")
            else:
                console.print(f"[red]{symbol} not in watchlist[/red]" if HAS_RICH
                              else f"{symbol} not in watchlist")

        else:  # list
            if HAS_RICH:
                if watchlist:
                    console.print(f"  [dim]Watchlist:[/dim] {', '.join(watchlist)}")
                else:
                    console.print("  [dim]Watchlist: Empty[/dim]")
            else:
                print(f"Watchlist: {', '.join(watchlist)}")
    def cmd_services(self, args: str):
        """Show CLI service tiers and core workflows."""
        service_groups = [
            (
                "CORE (Standard)",
                [
                    "Code agent with local tools (read/write/edit/search/run)",
                    "Slash command workflows for quote/analyze/backtest/risk/screen",
                    "Session save/load/export and interactive history management",
                    "Model switching + thinking mode controls for response depth",
                ],
            ),
            (
                "QUANTUM Automation",
                [
                    "Agentic multi-step loop (auto read -> analyze -> edit -> execute)",
                    "Auto-recovery guidance for failed commands and code fixes",
                    "Strategy generation, backtest reporting, and risk analysis skills",
                    "Cross-workspace research sync hooks (session + export pipeline)",
                ],
            ),
            (
                "ENTERPRISE Controls (included in Quantum)",
                [
                    "Service health diagnostics (/health) for backend + local model stack",
                    "Governed command execution with dangerous-command blocking",
                    "Audit-friendly session logs and reproducible command trails",
                    "MCP-ready service integration path via external tool endpoints",
                ],
            ),
        ]

        quick_flow = [
            "/model",
            "/gen-strategy momentum AAPL",
            "/backtest momentum AAPL 2024-01-01 2025-01-01",
            "/risk AAPL",
            "/export md strategy_report.md",
        ]

        if HAS_RICH:
            console.print()
            console.print("[bold]CLI Services[/bold] [dim](tiers + workflow)[/dim]")
            console.print()
            for group_name, items in service_groups:
                console.print(f"  [bold #C08050]{group_name}[/bold #C08050]")
                for item in items:
                    console.print(f"    [dim]> {item}[/dim]")
                console.print()

            console.print("  [bold]Quick Start Flow[/bold]")
            for cmd in quick_flow:
                console.print(f"    [bold]{cmd}[/bold]")
            console.print()
        else:
            print("\nCLI Services (tiers + workflow)\n")
            for group_name, items in service_groups:
                print(f"  {group_name}")
                for item in items:
                    print(f"    > {item}")
                print()

            print("  Quick Start Flow")
            for cmd in quick_flow:
                print(f"    {cmd}")
            print()

    def cmd_plan(self, args: str):
        """Create an executable plan and store it for /apply-plan.

        Supports multiple input styles:
            /plan 1. Fetch quote  2. Generate chart  3. Output report
            /plan fetch quote -> generate chart -> output report
            /plan step one; step two; step three
        """
        raw = args.strip()
        if not raw:
            if HAS_RICH:
                console.print("[dim]Usage: /plan <steps>  — see examples below[/dim]")
                console.print("[dim]  /plan fetch AAPL quote -> generate chart -> write report[/dim]")
                console.print("[dim]  /plan 1. Analyze sentiment  2. Build model  3. Backtest[/dim]")
            else:
                print("Usage: /plan <steps>")
                print("  /plan fetch AAPL quote -> generate chart -> write report")
                print("  /plan 1. Analyze sentiment  2. Build model  3. Backtest")
            return

        from plan_utils import parse_plan, format_plan
        plan_steps = parse_plan(raw)
        if not plan_steps:
            console.print("[dim]No valid steps found[/dim]" if HAS_RICH else "No valid steps found")
            return

        # Store plain descriptions for /apply-plan (backwards compatible)
        self.terminal.pending_plan = [s.description for s in plan_steps]

        if HAS_RICH:
            console.print()
            console.print(f"[bold]Execution Plan[/bold]  [dim]({len(plan_steps)} steps)[/dim]")
            console.print()
            for s in plan_steps:
                dep_str = f"  [dim](after {', '.join(str(d) for d in s.deps)})[/dim]" if s.deps else ""
                label   = f" [dim][{s.name}][/dim]" if s.name else ""
                console.print(f"  [dim]{s.index}.[/dim]{label} [bold]{s.description}[/bold]{dep_str}")
            console.print()
            console.print("[dim]Run /apply-plan to execute these steps.[/dim]")
            console.print()
        else:
            print(f"\nExecution Plan ({len(plan_steps)} steps)")
            for s in plan_steps:
                dep_str = f"  (after {', '.join(str(d) for d in s.deps)})" if s.deps else ""
                label   = f" [{s.name}]" if s.name else ""
                print(f"  {s.index}.{label} {s.description}{dep_str}")
            print("Run /apply-plan to execute these steps.\n")
    def cmd_plan_report(self, args: str):
        """Show or export last plan execution report."""
        rows = list(getattr(self.terminal, "last_plan_results", []) or [])
        if not rows:
            msg = "No plan report available. Run /apply-plan first."
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return

        parts = args.split()
        open_after = "--open" in parts
        parts = [p for p in parts if p != "--open"]
        fmt = parts[0].lower() if parts else "show"
        out_file = parts[1] if len(parts) > 1 else None

        if fmt == "show":
            if HAS_RICH:
                console.print()
                console.print("[bold]Last Plan Report[/bold]")
                for idx, row in enumerate(rows, 1):
                    status_color = "green" if row["status"] == "completed" else ("yellow" if row["status"] == "blocked" else "red")
                    console.print(
                        f"  [dim]{idx}.[/dim] [{status_color}]{row['status']}[/{status_color}] "
                        f"[bold]{row['step']}[/bold] [dim]({row['duration']}s, exit={row.get('exit_code')})[/dim]"
                    )
                    if row.get("error"):
                        console.print(f"     [red]{row['error']}[/red]")
                console.print()
            else:
                print("\nLast Plan Report")
                for idx, row in enumerate(rows, 1):
                    print(f"  {idx}. {row['status']}  {row['step']} ({row['duration']}s, exit={row.get('exit_code')})")
                    if row.get("error"):
                        print(f"     ERROR: {row['error']}")
            return

        if fmt not in {"md", "json"}:
            msg = "Usage: /plan-report [md|json] [file] [--open]"
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return

        if not out_file:
            out_file = f"plan_report.{fmt}"

        try:
            if fmt == "json":
                content = json.dumps(rows, ensure_ascii=False, indent=2)
            else:
                md_lines = ["# Plan Execution Report", ""]
                for idx, row in enumerate(rows, 1):
                    md_lines.append(
                        f"{idx}. **{row['status']}** `{row['step']}` "
                        f"({row['duration']}s, exit={row.get('exit_code')})"
                    )
                    if row.get("error"):
                        md_lines.append(f"   - Error: {row['error']}")
                md_lines.append("")
                content = "\n".join(md_lines)

            result = _tool_write_file({"path": out_file, "content": content})
            if result.get("success"):
                saved_path = result['data']['path']
                msg = f"Plan report saved to {saved_path}"
                console.print(f"[green]{msg}[/green]" if HAS_RICH else msg)
                if open_after:
                    self._open_file(saved_path)
            else:
                err = result.get("error", "Failed to save report")
                console.print(f"[red]{err}[/red]" if HAS_RICH else err)
        except Exception as e:
            console.print(f"[red]{e}[/red]" if HAS_RICH else str(e))

    def cmd_git(self, args: str):
        """Git helper shortcuts."""
        policy = self.terminal.config.get("command_policy", "safe")
        raw = args.strip()
        if not raw:
            sub = "status"
            sub_args = ""
        else:
            parts = raw.split(maxsplit=1)
            sub = parts[0].lower()
            sub_args = parts[1].strip() if len(parts) > 1 else ""

        mapping = {
            "status":  "git status --short --branch",
            "diff":    "git diff --stat",
            "summary": "git status --short --branch && git diff --stat",
            "branch":  "git branch -v",
            "stash":   "git stash list",
            "remote":  "git remote -v",
        }
        if sub == "patch":
            cmd = "git diff" if not sub_args else f"git diff -- {sub_args}"
        elif sub == "log":
            limit = sub_args if sub_args and sub_args.isdigit() else "15"
            cmd = f"git log --oneline --graph --decorate -{limit}"
        elif sub == "commit":
            status_probe = _tool_run_command({"command": "git status --porcelain", "policy": policy})
            if not status_probe.get("success"):
                err = status_probe.get("error", "Failed to inspect git status")
                console.print(f"[red]{err}[/red]" if HAS_RICH else err)
                return

            status_out = status_probe.get("data", {}).get("stdout", "").strip()
            if not status_out:
                msg = "No changes to commit."
                console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
                return

            changed_files = []
            for line in status_out.splitlines():
                if len(line) >= 4:
                    changed_files.append(line[3:].strip())
            unique_files = [f for f in changed_files if f]
            total_files = len(unique_files)
            file_preview = ", ".join(unique_files[:5]) if unique_files else "workspace"
            body_summary = f"Files changed: {total_files}"
            body_preview = f"Top files: {file_preview}"

            if not sub_args:
                files = []
                for line in status_out.splitlines()[:3]:
                    if len(line) >= 4:
                        files.append(line[3:].strip())
                sample = ", ".join(files) if files else "workspace"
                total = len(status_out.splitlines())
                sub_args = f"chore: update {total} file(s) ({sample})"
                if HAS_RICH:
                    console.print(f"[dim]Auto commit message:[/dim] {sub_args}")
                else:
                    print(f"Auto commit message: {sub_args}")

            cmd = (
                f"git add -A && git commit "
                f"-m {shlex.quote(sub_args)} "
                f"-m {shlex.quote(body_summary)} "
                f"-m {shlex.quote(body_preview)}"
            )
        elif sub in mapping:
            cmd = mapping[sub]
        else:
            msg = "Usage: /git [status|diff|summary|patch|log [N]|branch|stash|remote|commit <msg>]"
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return
        result = _tool_run_command({"command": cmd, "policy": policy})
        if not result.get("success"):
            console.print(f"[red]{result.get('error', 'Command failed')}[/red]" if HAS_RICH
                          else result.get("error", "Command failed"))
            return
        data = result.get("data", {})
        out = (data.get("stdout", "") + ("\n" + data.get("stderr", "") if data.get("stderr") else "")).strip()
        if out:
            if HAS_RICH:
                console.print(Syntax(out, "text", theme=_SYNTAX_THEME))
            else:
                print(out)

    def cmd_gh(self, args: str):
        """GitHub CLI helper — prs | issues | pr N | issue N | search | create-pr | diff N | checks N"""
        raw = args.strip()
        if not raw or raw in ("help", "--help"):
            lines = [
                "Usage: /gh <command>",
                "  prs            List open pull requests",
                "  issues         List open issues",
                "  pr <N>         View pull request #N",
                "  issue <N>      View issue #N",
                "  diff <N>       Show PR #N diff",
                "  checks <N>     Show PR #N CI checks",
                "  search <q>     Search code in this repo",
                "  create-pr      Create a PR (follow prompts)",
                "  commits [N]    Show last N commits (default 10)",
            ]
            for ln in lines:
                console.print(f"  [dim]{ln}[/dim]" if HAS_RICH else ln)
            return

        parts  = raw.split(maxsplit=1)
        sub    = parts[0].lower()
        subarg = parts[1].strip() if len(parts) > 1 else ""

        def _run(action: str, extra: dict = None):
            p = {"action": action}
            if extra:
                p.update(extra)
            r = _tool_github(p)
            if not r.get("success"):
                msg = r.get("error", "GitHub command failed")
                console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                return
            data = r.get("data", {})
            out = data.get("stdout", "") if isinstance(data, dict) else str(data)
            if out.strip():
                if HAS_RICH:
                    # Pretty-print JSON if possible
                    try:
                        import json as _jj
                        parsed = _jj.loads(out)
                        from rich.pretty import pprint as _pp
                        _pp(parsed, expand_all=False)
                    except Exception:
                        console.print(Syntax(out, "text", theme=_SYNTAX_THEME))
                else:
                    print(out)

        if sub in ("prs", "pr_list"):
            _run("list_prs")
        elif sub in ("issues", "issue_list"):
            _run("list_issues")
        elif sub == "pr" and subarg.isdigit():
            _run("view_pr", {"number": int(subarg)})
        elif sub == "issue" and subarg.isdigit():
            _run("view_issue", {"number": int(subarg)})
        elif sub == "diff" and subarg.isdigit():
            _run("pr_diff", {"number": int(subarg)})
        elif sub == "checks" and subarg.isdigit():
            _run("pr_checks", {"number": int(subarg)})
        elif sub in ("commits", "log"):
            n = int(subarg) if subarg.isdigit() else 10
            _run("list_commits", {"limit": n})
        elif sub == "search":
            if not subarg:
                console.print("[dim]Usage: /gh search <query>[/dim]" if HAS_RICH else "Usage: /gh search <query>")
                return
            _run("search", {"q": subarg, "kind": "code"})
        elif sub in ("create-pr", "createpr", "create_pr"):
            # Interactive prompts
            try:
                title = (console.input("  PR title: ") if HAS_RICH else input("  PR title: ")).strip()
                body  = (console.input("  PR body (optional): ") if HAS_RICH else input("  PR body (optional): ")).strip()
                base  = (console.input("  Base branch [main]: ") if HAS_RICH else input("  Base branch [main]: ")).strip() or "main"
                _run("create_pr", {"title": title, "body": body, "base": base})
            except (EOFError, KeyboardInterrupt):
                console.print("[dim]Cancelled[/dim]" if HAS_RICH else "Cancelled")
        else:
            console.print(f"[dim]Unknown /gh sub-command: {sub}. Try /gh help[/dim]" if HAS_RICH
                          else f"Unknown /gh sub-command: {sub}. Try /gh help")

    def _confirm_high_risk_command(self, command: str, risk: str, policy: str) -> bool:
        """Double-confirm high-risk commands even if policy allows them."""
        msg = f"High-risk command under policy '{policy}' (risk={risk}): {command}\nRun it? [y/N]: "
        try:
            answer = console.input(msg) if HAS_RICH else input(msg)
        except (EOFError, KeyboardInterrupt):
            return False
        return answer.strip().lower() in {"y", "yes"}

    def _open_file(self, path: str):
        """Open a local file using platform default app."""
        path_q = shlex.quote(path)
        if sys.platform == "darwin":
            os.system(f"open {path_q}")
        elif os.name == "nt":
            os.system(f'start "" {path_q}')
        else:
            os.system(f"xdg-open {path_q} >/dev/null 2>&1")

    async def cmd_status(self, args: str):
        """Runtime status panel: engine · tools · model · context · risk"""
        t = self.terminal
        cfg = t.config
        model_id  = cfg.get("model", "qwen2.5:7b")
        tool_count = len(ARIA_TOOLS) + len(LOCAL_TOOLS)
        skill_count = len(SKILLS)

        # Runtime
        _lp = t._last_provider or ""
        _badge = next((v.get("badge","") for v in MODELS.values() if v["id"]==model_id), "")
        if _lp == "ollama":
            runtime = "local (Ollama)"
        elif _lp in ("deepseek","openai","anthropic","groq","dashscope","together"):
            runtime = f"cloud ({_lp})"
        elif _badge == "Cloud" or "cloud" in model_id.lower():
            runtime = "cloud"
        else:
            runtime = "local" if getattr(t, "_ollama_alive", False) else "unknown"

        # Context usage
        conv = t.conversation
        est_tok = sum(len(m.get("content","")) for m in conv) // 3
        max_ctx = get_model_cfg(model_id).get("num_ctx", 16384)
        ctx_pct = min(100, int(est_tok / max_ctx * 100))

        # Model display name
        mk = next((k for k,v in MODELS.items() if v["id"]==model_id), None)
        model_display = MODELS[mk]["name"] if mk else model_id

        if HAS_RICH:
            console.print()
            console.print("[bold]Runtime Status[/bold]")
            console.print()
            rows = [
                ("runtime",   runtime),
                ("model",     model_display),
                ("engine",    "quant engine v3.0"),
                ("tools",     f"{tool_count} available  ·  {skill_count} skills"),
                ("risk",      "enabled"),
                ("context",   f"{est_tok:,} / {max_ctx:,} tokens  ({ctx_pct}%)"),
            ]
            # Loaded context sources
            if getattr(t, "_project_session", None):
                rows.append(("project", f"{t._project_session.name}  ({t._project_session.stats.get('total_files',0)} files)"))
            if getattr(t, "_file_session", None) and t._file_session.get_active():
                fc = t._file_session.get_active()
                rows.append(("file", f"{fc.filename}  ({fc.size_kb:.0f} KB)"))
            # Banner mode
            rows.append(("banner", cfg.get("banner", "full")))
            rows.append(("workspace", os.getcwd().replace(os.path.expanduser("~"), "~")))
            for k, v in rows:
                console.print(f"  [dim]{k:<12}[/dim][cyan]{v}[/cyan]")
            console.print()
        else:
            print("\nRuntime Status")
            print(f"  runtime  {runtime}")
            print(f"  model    {model_display}")
            print(f"  tools    {tool_count}")
            print(f"  context  {est_tok}/{max_ctx}")
            print()

    def cmd_trace(self, args: str):
        """Show runtime trace for recent tool calls."""
        trace = getattr(self.terminal, "runtime_trace", None)
        if trace is None:
            msg = "Runtime trace is unavailable."
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return
        if "--json" in args.split():
            payload = json.dumps(trace.to_dict(), ensure_ascii=False, indent=2)
            if HAS_RICH:
                console.print(Syntax(payload, "json", theme=_SYNTAX_THEME))
            else:
                print(payload)
            return
        calls = trace.tool_calls[-20:]
        if not calls:
            msg = "No tool calls recorded yet."
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return
        if HAS_RICH:
            console.print()
            console.print("[bold]Runtime Trace[/bold]")
            console.print()
            for call in calls:
                ok = bool(call.result.get("success"))
                style = "green" if ok else "red"
                console.print(
                    f"  [{style}]{'ok' if ok else 'err':<3}[/{style}] "
                    f"[bold]{call.tool}[/bold] "
                    f"[dim]{call.elapsed_ms:.0f} ms[/dim]"
                )
                if not ok and call.result.get("error"):
                    console.print(f"      [red]{str(call.result.get('error'))[:180]}[/red]")
            console.print()
        else:
            print("\nRuntime Trace")
            for call in calls:
                ok = "ok" if call.result.get("success") else "err"
                print(f"  {ok:<3} {call.tool} {call.elapsed_ms:.0f} ms")
            print()

    async def cmd_health(self, args: str):
        import aiohttp
        if HAS_RICH:
            console.print()
        urls = [
            ("AWS Backend", self.terminal.api_url, "/health"),
            ("Local Server", self.terminal.config.get("local_url", "http://localhost:8001"), "/health"),
            ("Ollama", self.terminal.config.get("ollama_url", "http://localhost:11434"), "/api/tags"),
        ]
        for label, url, path in urls:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{url}{path}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        data = await resp.json()
                        if label == "Ollama":
                            models = [m.get("name", "?") for m in data.get("models", [])[:3]]
                            detail = ", ".join(models)
                        else:
                            detail = f"v{data.get('version', '?')}"
                        if HAS_RICH:
                            console.print(f"  [green]●[/green] [dim]{label}[/dim]  {detail}")
                        else:
                            print(f"  + {label}  {detail}")
            except Exception:
                if HAS_RICH:
                    console.print(f"  [red]●[/red] [dim]{label}[/dim]  offline")
                else:
                    print(f"  - {label}  offline")
        if HAS_RICH:
            console.print()

    def cmd_clear(self, args: str):
        self.terminal.conversation = []
        os.system("clear" if os.name == "posix" else "cls")
        console.print("[dim]Conversation cleared[/dim]" if HAS_RICH else "Cleared")

    def cmd_btw(self, args: str):
        """/btw <question> — Quick side question shown in overlay, does NOT enter conversation history."""
        q = args.strip()
        if not q:
            console.print("[dim]/btw <question>  — quick question without polluting history[/dim]" if HAS_RICH else "/btw <question>")
            return
        conv = self.terminal.conversation
        if not conv:
            console.print("[dim](no conversation context yet)[/dim]" if HAS_RICH else "(no context)")
            return
        # Build a condensed context summary from recent history (read-only, no append)
        _ctx_slice = conv[-6:] if len(conv) >= 6 else conv
        _ctx = "\n".join(
            f"{m['role'].upper()}: {str(m.get('content', ''))[:300]}"
            for m in _ctx_slice
        )
        _btw_prompt = (
            f"[Side question — answer briefly, do not reference this note]\n"
            f"Context from conversation:\n{_ctx}\n\nQuestion: {q}"
        )
        # Run synchronously using the sync stream helper
        if HAS_RICH:
            from rich.panel import Panel as _Panel
            from rich import box as _rbox
            console.print(_Panel(f"[dim]{q}[/dim]", title="[dim]/btw[/dim]", box=_rbox.ROUNDED, border_style="dim"))
        import asyncio as _aio
        async def _ask_btw():
            _answer_parts: list[str] = []
            try:
                async for chunk in stream_chat(
                    self.terminal.config.get("ollama_url", "http://localhost:11434"),
                    _btw_prompt,
                    [],   # empty history — side question only
                    model=self.terminal.config.get("model", ""),
                    config=self.terminal.config,
                    tools=[],
                ):
                    if chunk.get("type") == "content":
                        _answer_parts.append(chunk.get("content", ""))
            except Exception as _e:
                _answer_parts = [f"(error: {_e})"]
            return "".join(_answer_parts)
        try:
            loop = _aio.get_event_loop()
            answer = loop.run_until_complete(_ask_btw()) if not loop.is_running() else "(run /btw from interactive prompt)"
        except Exception:
            answer = "(could not get answer)"
        if HAS_RICH:
            from rich.panel import Panel as _Panel
            from rich import box as _rbox
            console.print(_Panel(answer.strip(), title="[dim]↩ btw[/dim]", box=_rbox.ROUNDED, border_style="dim #C08050"))
        else:
            print(f"\n  [btw] {answer.strip()}\n")
        # NOT added to self.terminal.conversation — ephemeral by design

    def cmd_recap(self, args: str):
        """/recap — Summarise the current session in one paragraph."""
        conv = self.terminal.conversation
        if not conv:
            console.print("[dim]No conversation yet[/dim]" if HAS_RICH else "No conversation")
            return
        turns = len([m for m in conv if m.get("role") == "user"])
        topics: list[str] = []
        for m in conv:
            if m.get("role") == "user":
                snippet = str(m.get("content", ""))[:60].strip()
                if snippet:
                    topics.append(snippet)
        if HAS_RICH:
            from rich.panel import Panel as _Panel
            from rich import box as _rbox
            body = f"[dim]{turns} 轮对话[/dim]\n"
            for i, t in enumerate(topics[-6:], 1):
                body += f"  [dim]{i}.[/dim] {t}…\n"
            console.print(_Panel(body.rstrip(), title="[bold]会话摘要[/bold]", box=_rbox.ROUNDED, border_style="dim"))
        else:
            print(f"Session: {turns} turns")
            for i, t in enumerate(topics[-6:], 1):
                print(f"  {i}. {t}…")

    def cmd_history(self, args: str):
        if not self.terminal.conversation:
            console.print("[dim]No conversation history[/dim]" if HAS_RICH else "No history")
            return
        for msg in self.terminal.conversation[-10:]:
            role = msg["role"]
            content = msg["content"][:120]
            if HAS_RICH:
                prefix = "You" if role == "user" else "Aria"
                style = "bold" if role == "user" else "bold"
                console.print(f"[{style}]{prefix}:[/{style}] [dim]{content}[/dim]")
            else:
                print(f"{'You' if role == 'user' else 'Aria'}: {content}")

    def cmd_compact(self, args: str):
        """Smart compact: summarise conversation with AI then trim.

        Usage:
            /compact           — AI-powered summarisation (keeps context intact)
            /compact --hard    — hard trim to last 6 messages (old behavior)
        """
        if "--hard" in args:
            if len(self.terminal.conversation) > 10:
                kept = self.terminal.conversation[-6:]
                self.terminal.conversation = kept
                console.print(f"[dim]Hard-compacted to last {len(kept)} messages[/dim]" if HAS_RICH
                              else f"Hard-compacted to {len(kept)} messages")
            else:
                console.print("[dim]Context small enough, no compaction needed[/dim]" if HAS_RICH
                              else "No compaction needed")
            return
        # Smart compact via async helper
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_event_loop()
            loop.run_until_complete(self._smart_compact_async(silent=False))
        except RuntimeError:
            # Already inside an event loop (shouldn't happen in sync context but defensive)
            if len(self.terminal.conversation) > 6:
                self.terminal.conversation = self.terminal.conversation[-6:]
                console.print("[dim]Compacted (fallback)[/dim]")

    async def _smart_compact_async(self, silent: bool = False):
        """AI-powered conversation compaction (inspired by Claude Code).

        Sends all messages to the current model and asks for a dense summary,
        then replaces conversation with [system summary] + last 2 message pairs.
        Falls back to hard trim if the summary call fails.
        """
        conv = self.terminal.conversation
        if len(conv) <= 4:
            if not silent:
                console.print("[dim]Context small enough — no compaction needed[/dim]" if HAS_RICH
                              else "Context small enough")
            return

        if not silent and HAS_RICH:
            console.print("[dim]Summarising conversation...[/dim]")

        # Build a dense transcript for the summariser.
        # Tool results get special treatment: extract status + first 3 non-empty lines
        # rather than raw-truncating, so the summariser sees outcomes not noise.
        transcript_parts = []
        for m in conv:
            role = m.get("role", "")
            content = m.get("content", "")
            if role == "tool":
                lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
                has_err = any("error" in ln.lower() or "traceback" in ln.lower() for ln in lines[:10])
                excerpt = " | ".join(lines[:3]) if lines else content[:200]
                label = "Tool[⚠ error]" if has_err else "Tool"
                transcript_parts.append(f"{label}: {excerpt[:300]}")
            elif role == "user":
                transcript_parts.append(f"User: {content[:800]}")
            else:
                transcript_parts.append(f"Aria: {content[:1200]}")
        transcript = "\n\n".join(transcript_parts)

        summary_prompt = (
            "You are a context compressor for a quantitative finance AI assistant.\n"
            "Given the conversation transcript, produce a DENSE SUMMARY (≤350 words).\n"
            "You MUST preserve:\n"
            "  • All ticker symbols / asset names discussed\n"
            "  • Key numerical results (prices, rates, backtest metrics)\n"
            "  • Code files written or modified (file paths + purpose)\n"
            "  • Errors encountered and how they were resolved\n"
            "  • User preferences or decisions made\n"
            "  • The last task status (complete / in-progress / blocked)\n"
            "Write in concise third-person present tense. "
            "Start with: 'Session summary: ...'\n\n"
            f"TRANSCRIPT:\n{transcript}\n\nSUMMARY:"
        )

        summary = ""
        try:
            ollama_url = self.terminal.config.get("ollama_url", "http://localhost:11434")
            result = await stream_ollama(
                ollama_url,
                summary_prompt,
                history=[],   # no history — pure summarisation task
                model=self.terminal.config.get("model", "qwen2.5:7b"),
                enable_tools=False,
            )
            if result.get("success") and result.get("response"):
                summary = result["response"].strip()
        except Exception:
            pass

        if not summary:
            # Fallback: hard trim
            self.terminal.conversation = conv[-6:]
            if not silent:
                console.print("[dim]Compacted (summary failed, kept last 6 messages)[/dim]" if HAS_RICH
                              else "Compacted (summary fallback)")
            return

        # Build compacted conversation: summary + last 3 pairs (6 msgs) for
        # sufficient recency context. The summary acts as a pseudo-system message.
        kept_tail = conv[-6:] if len(conv) >= 6 else conv[:]
        self.terminal.conversation = [
            {
                "role": "user",
                "content": (
                    f"[会话摘要 — 早期对话已压缩]\n\n{summary}\n\n"
                    f"[以下是最近的对话记录]"
                )
            },
            {
                "role": "assistant",
                "content": "已获取摘要，继续之前的工作。"
            },
            *kept_tail,
        ]
        new_count = len(self.terminal.conversation)
        old_count = len(conv)
        if not silent:
            if HAS_RICH:
                console.print(
                    f"  [dim]✓ Compacted {old_count} → {new_count} messages "
                    f"(summary preserved context)[/dim]"
                )
            else:
                print(f"Compacted {old_count} → {new_count} messages")

    # ── Fork conversation ────────────────────────────────────────────────────

    def cmd_fork(self, args: str):
        """Fork conversation at this point — save snapshot, continue independently.

        Usage:
            /fork              — create fork with auto-name
            /fork my-analysis  — create fork with given name
        """
        import time as _t
        name = args.strip() or f"fork-{_t.strftime('%H%M%S')}"
        snapshot = {
            "name":   name,
            "ts":     _t.strftime("%Y-%m-%d %H:%M:%S"),
            "conv":   [dict(m) for m in self.terminal.conversation],
            "config": dict(self.terminal.config),
        }
        self.terminal._forks.append(snapshot)
        idx = len(self.terminal._forks) - 1
        if HAS_RICH:
            console.print(
                f"  [dim]↳ Forked as [bold]{name}[/bold] "
                f"(fork #{idx}, {len(snapshot['conv'])} messages). "
                f"Restore with /load-fork {idx}[/dim]"
            )
        else:
            print(f"Forked as '{name}' (#{idx}). Restore with /load-fork {idx}")

    def cmd_load_fork(self, args: str):
        """Restore a previously forked conversation snapshot.

        Usage: /load-fork <index>
        """
        forks = self.terminal._forks
        if not forks:
            console.print("[dim]No forks yet — use /fork to create one[/dim]" if HAS_RICH
                          else "No forks")
            return
        try:
            idx = int(args.strip())
        except (ValueError, IndexError):
            if HAS_RICH:
                for i, f in enumerate(forks):
                    console.print(f"  [dim]#{i}[/dim]  {f['name']}  [dim]{f['ts']}  {len(f['conv'])} msgs[/dim]")
            else:
                for i, f in enumerate(forks):
                    print(f"  #{i}  {f['name']}  {f['ts']}")
            return
        if idx < 0 or idx >= len(forks):
            console.print(f"[dim]Fork #{idx} not found[/dim]" if HAS_RICH else "Invalid index")
            return
        snap = forks[idx]
        self.terminal.conversation = [dict(m) for m in snap["conv"]]
        console.print(
            f"  [dim]✓ Restored fork [bold]{snap['name']}[/bold] "
            f"({len(snap['conv'])} messages)[/dim]"
            if HAS_RICH else f"Restored fork '{snap['name']}'"
        )

    # ── Copy last response to clipboard ──────────────────────────────────────

    def cmd_copy(self, args: str):
        """Copy Aria's last response to clipboard.

        Usage: /copy
        """
        text = self.terminal._last_response
        if not text:
            console.print("[dim]No response to copy yet[/dim]" if HAS_RICH else "Nothing to copy")
            return
        copied = False
        try:
            import subprocess as _sp
            _sp.run(["pbcopy"], input=text.encode(), check=True, timeout=3)
            copied = True
        except Exception:
            pass
        if not copied:
            try:
                import subprocess as _sp
                _sp.run(["xclip", "-selection", "clipboard"],
                        input=text.encode(), check=True, timeout=3)
                copied = True
            except Exception:
                pass
        if not copied:
            try:
                import subprocess as _sp
                _sp.run(["xdotool", "type", "--clearmodifiers", text],
                        check=True, timeout=3)
                copied = True
            except Exception:
                pass
        if copied:
            preview = text[:60].replace("\n", " ")
            console.print(
                f"  [dim]✓ Copied to clipboard: \"{preview}{'…' if len(text) > 60 else ''}\"[/dim]"
                if HAS_RICH else f"Copied: \"{preview}\""
            )
        else:
            console.print(
                "[yellow]Could not reach clipboard (pbcopy/xclip not found). "
                "Here is the response:[/yellow]\n" + text
                if HAS_RICH else "Clipboard unavailable. Response:\n" + text
            )

    # ── Cost / usage display ─────────────────────────────────────────────────

    def cmd_cost(self, args: str):
        """Show session token usage and estimated cost.

        Token pricing (rough estimates, OpenAI/DeepSeek comparable tier):
          - Input:   $0.14 / 1M tokens
          - Output:  $0.28 / 1M tokens
          - Thinking: $1.10 / 1M tokens  (if thinking model)
        Local Ollama models: $0 (free).
        """
        import time as _t
        elapsed = _t.time() - self.terminal._session_start
        inp = self.terminal._session_input_tokens
        out = self.terminal._session_output_tokens
        think = self.terminal._session_thinking_tokens
        turns = self.terminal._session_turns
        total = inp + out + think

        # Estimate cost (USD) — only meaningful for cloud providers
        is_local = self.terminal._last_provider in ("ollama", "ollama_cache", "local")
        cost_usd = 0.0
        if not is_local:
            cost_usd = (inp * 0.14 + out * 0.28 + think * 1.10) / 1_000_000

        hh = int(elapsed // 3600)
        mm = int((elapsed % 3600) // 60)
        ss = int(elapsed % 60)
        duration = f"{hh}h {mm:02d}m {ss:02d}s" if hh else f"{mm}m {ss:02d}s"

        if HAS_RICH:
            console.print()
            console.print("[bold]Session Usage[/bold]")
            console.print()
            console.print(f"  [dim]{'Duration':<22}[/dim]{duration}")
            console.print(f"  [dim]{'Turns':<22}[/dim]{turns}")
            console.print(f"  [dim]{'Input tokens':<22}[/dim]{inp:,}")
            console.print(f"  [dim]{'Output tokens':<22}[/dim]{out:,}")
            if think:
                console.print(f"  [dim]{'Thinking tokens':<22}[/dim]{think:,}")
            console.print(f"  [dim]{'Total tokens':<22}[/dim][bold]{total:,}[/bold]")
            if is_local:
                console.print(f"  [dim]{'Est. cost':<22}[/dim][green]$0.00 (local)[/green]")
            elif total > 0:
                console.print(f"  [dim]{'Est. cost':<22}[/dim]${cost_usd:.4f} USD")
            console.print(f"  [dim]{'Provider':<22}[/dim]{self.terminal._last_provider}")
            console.print()
        else:
            print(f"  Session: {duration}  Turns: {turns}")
            print(f"  Tokens: {inp:,} in / {out:,} out / {total:,} total")
            if not is_local and total > 0:
                print(f"  Est. cost: ${cost_usd:.4f}")

    # ── Todo / task tracking ─────────────────────────────────────────────────

    def cmd_todo(self, args: str):
        """Persistent task tracking for the current session.

        Usage:
            /todo                 — list all tasks
            /todo add <task>      — add a new task
            /todo done <id>       — mark task done
            /todo remove <id>     — remove task
            /todo clear           — wipe all tasks

        Inspired by Claude Code's TodoRead / TodoWrite tools.
        Tasks are stored in ~/.arthera/todos.json and injected into context.
        """
        import json as _json
        todo_file = CONFIG_DIR / "todos.json"

        def _load():
            try:
                if todo_file.exists():
                    return _json.loads(todo_file.read_text(encoding="utf-8"))
            except Exception:
                pass
            return []

        def _save(tasks):
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            todo_file.write_text(_json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")

        parts = args.strip().split(maxsplit=1)
        sub  = parts[0].lower() if parts else "list"
        rest = parts[1].strip() if len(parts) > 1 else ""
        tasks = _load()

        if sub in ("", "list", "ls"):
            if not tasks:
                console.print("[dim]No tasks. Add with: /todo add <task>[/dim]" if HAS_RICH
                              else "No tasks")
                return
            if HAS_RICH:
                console.print()
                for i, t in enumerate(tasks):
                    status_icon = "[green]✓[/green]" if t.get("done") else "[yellow]○[/yellow]"
                    style = "dim" if t.get("done") else ""
                    text = t.get("text", "")
                    console.print(f"  {status_icon} [dim]{i}[/dim]  [{style}]{text}[/{style}]" if style
                                  else f"  {status_icon} [dim]{i}[/dim]  {text}")
                pending = sum(1 for t in tasks if not t.get("done"))
                console.print(f"\n  [dim]{pending}/{len(tasks)} pending[/dim]")
                console.print()
            else:
                for i, t in enumerate(tasks):
                    mark = "✓" if t.get("done") else "○"
                    print(f"  {mark} {i}  {t.get('text', '')}")

        elif sub == "add":
            if not rest:
                console.print("[dim]Usage: /todo add <task text>[/dim]" if HAS_RICH
                              else "Usage: /todo add <task>")
                return
            task = {"text": rest, "done": False, "id": len(tasks)}
            tasks.append(task)
            _save(tasks)
            console.print(f"  [dim]✓ Added: {rest}[/dim]" if HAS_RICH else f"Added: {rest}")

        elif sub in ("done", "check", "complete"):
            try:
                idx = int(rest)
                tasks[idx]["done"] = True
                _save(tasks)
                console.print(f"  [dim]✓ Done: {tasks[idx]['text']}[/dim]" if HAS_RICH
                              else f"Done: {tasks[idx]['text']}")
            except (ValueError, IndexError):
                console.print("[dim]Usage: /todo done <id>[/dim]" if HAS_RICH else "Usage: /todo done <id>")

        elif sub in ("remove", "rm", "delete", "del"):
            try:
                idx = int(rest)
                removed = tasks.pop(idx)
                _save(tasks)
                console.print(f"  [dim]Removed: {removed['text']}[/dim]" if HAS_RICH
                              else f"Removed: {removed['text']}")
            except (ValueError, IndexError):
                console.print("[dim]Usage: /todo remove <id>[/dim]" if HAS_RICH else "bad index")

        elif sub == "clear":
            _save([])
            console.print("[dim]All tasks cleared[/dim]" if HAS_RICH else "Cleared")

        else:
            # Treat unrecognised subcommand as shorthand for /todo add
            full_text = (sub + " " + rest).strip()
            task = {"text": full_text, "done": False, "id": len(tasks)}
            tasks.append(task)
            _save(tasks)
            console.print(f"  [dim]✓ Added: {full_text}[/dim]" if HAS_RICH else f"Added: {full_text}")

    # ── Doctor diagnostic ────────────────────────────────────────────────────

    def cmd_doctor(self, args: str):
        """Diagnose Aria Code installation: models, API keys, backends, tools.

        Inspired by Claude Code's /doctor command.
        """
        try:
            from doctor import run_doctor

            report = run_doctor(
                self.terminal.config,
                check_network="--network" in (args or "").split(),
            )
            if HAS_RICH:
                from rich.table import Table as _DoctorTable
                table = _DoctorTable(title="Aria Code doctor", box=rich_box.ROUNDED)
                table.add_column("Status", width=8)
                table.add_column("Check", style="bold")
                table.add_column("Detail", style="dim")
                table.add_column("Suggestion", style="dim")
                icons = {"ok": "[green]OK[/green]", "warn": "[yellow]WARN[/yellow]", "err": "[red]ERR[/red]"}
                for check in report.checks:
                    table.add_row(
                        icons.get(check.status, check.status.upper()),
                        check.name,
                        check.detail,
                        check.suggestion,
                    )
                console.print()
                console.print(table)
                color = "green" if report.errors == 0 and report.warnings == 0 else ("yellow" if report.errors == 0 else "red")
                console.print(f"[{color}]{report.passed} passed · {report.warnings} warnings · {report.errors} errors[/{color}]")
                console.print()
            else:
                from doctor import format_doctor_plain
                print(format_doctor_plain(report))
            return
        except Exception as exc:
            console.print(f"[yellow]doctor module unavailable, using legacy checks: {exc}[/yellow]" if HAS_RICH else f"doctor module unavailable: {exc}")

        import importlib as _il, subprocess as _sp, shutil as _sh

        cfg = self.terminal.config
        ollama_url = cfg.get("ollama_url", "http://localhost:11434")
        api_url    = cfg.get("api_url", "http://localhost:8000")

        checks: List[tuple] = []  # (label, status, detail)

        def _ok(label, detail=""): checks.append(("ok",   label, detail))
        def _warn(label, detail=""): checks.append(("warn", label, detail))
        def _err(label, detail=""): checks.append(("err",  label, detail))

        # 1. Python version
        import sys as _sys
        pyver = f"{_sys.version_info.major}.{_sys.version_info.minor}.{_sys.version_info.micro}"
        if _sys.version_info >= (3, 9):
            _ok("Python", pyver)
        else:
            _warn("Python", f"{pyver} (3.9+ recommended)")

        # 2. Ollama connectivity
        try:
            import urllib.request as _ur
            _opener = _ur.build_opener(_ur.ProxyHandler({}))
            _r = _opener.open(f"{ollama_url}/api/tags", timeout=3)
            _data = json.loads(_r.read())
            models = [m["name"] for m in _data.get("models", [])]
            if models:
                _ok("Ollama", f"{len(models)} models: {', '.join(models[:4])}")
            else:
                _warn("Ollama", "running but no models installed (ollama pull qwen2.5-coder:1.5b)")
        except Exception as e:
            _err("Ollama", f"not reachable at {ollama_url} ({e})")

        # 3. Backend API
        try:
            import urllib.request as _ur
            _opener = _ur.build_opener(_ur.ProxyHandler({}))
            _r = _opener.open(f"{api_url}/health", timeout=3)
            _ok("Backend", f"running at {api_url}")
        except Exception as e:
            _warn("Backend", f"offline at {api_url} — local Ollama mode will be used")

        # 4. API keys
        key_checks = [
            ("finnhub",      "股票行情"),
            ("alphavantage", "历史数据"),
            ("newsapi",      "新闻"),
            ("brave",        "网络搜索"),
            ("coingecko",    "加密货币"),
        ]
        for svc, desc in key_checks:
            k = _get_provider_key(svc)
            if k:
                _ok(f"API key: {svc}", f"{desc} ({'*'*6}{k[-4:]})")
            else:
                _warn(f"API key: {svc}", f"{desc} 未配置 (/apikey set {svc} <key>)")

        # Check LLM cloud keys
        llm_keys = [("deepseek","DeepSeek"),("openai","OpenAI"),
                    ("siliconflow","SiliconFlow"),("moonshot","Moonshot")]
        _has_any_llm = False
        for svc, name in llm_keys:
            k = _get_provider_key(svc)
            if k:
                _ok(f"LLM key: {svc}", f"{name} configured")
                _has_any_llm = True
        if not _has_any_llm:
            _warn("LLM keys", "No cloud LLM keys — Ollama must be running for AI responses")

        # 5. Core Python packages
        _pkgs = [
            ("aiohttp",     "async HTTP"),
            ("rich",        "terminal UI"),
            ("prompt_toolkit", "autocomplete"),
            ("yfinance",    "market data"),
            ("pandas",      "data processing"),
            ("requests",    "HTTP client"),
        ]
        for pkg, desc in _pkgs:
            try:
                m = _il.import_module(pkg)
                ver = getattr(m, "__version__", "?")
                _ok(f"pkg: {pkg}", f"{desc} v{ver}")
            except ImportError:
                _warn(f"pkg: {pkg}", f"{desc} not installed (pip install {pkg})")

        # 6. ARIA.md / project context
        aria_md = pathlib.Path.cwd() / "ARIA.md"
        if aria_md.exists():
            lines = len(aria_md.read_text(encoding="utf-8").splitlines())
            _ok("ARIA.md", f"{lines} lines of project context")
        else:
            _warn("ARIA.md", f"not found in {pathlib.Path.cwd()} (use /init to create)")

        # 7. MCP servers
        if _HAS_MCP:
            try:
                reg = self.terminal._mcp_registry
                if reg and hasattr(reg, "list_tools"):
                    tools = reg.list_tools()
                    _ok("MCP", f"{len(tools)} tools from MCP servers")
                else:
                    _warn("MCP", "registry not started yet")
            except Exception:
                _warn("MCP", "loaded but no active servers")
        else:
            _warn("MCP", "mcp_client not found — MCP support disabled")

        # 8. Tools count
        tool_count = len(ARIA_TOOLS) + len(LOCAL_TOOLS)
        _ok("Aria tools", f"{tool_count} tools loaded")

        # Render results
        console.print() if HAS_RICH else None
        if HAS_RICH:
            console.print("[bold]Aria Code — Diagnostics[/bold]")
            console.print()
            icons = {"ok": "[green]✓[/green]", "warn": "[yellow]⚠[/yellow]", "err": "[red]✗[/red]"}
            for status, label, detail in checks:
                icon = icons[status]
                detail_str = f"  [dim]{detail}[/dim]" if detail else ""
                console.print(f"  {icon}  {label:<28}{detail_str}")
            console.print()
            n_ok = sum(1 for s, *_ in checks if s == "ok")
            n_w  = sum(1 for s, *_ in checks if s == "warn")
            n_e  = sum(1 for s, *_ in checks if s == "err")
            summary_color = "green" if n_e == 0 and n_w == 0 else ("yellow" if n_e == 0 else "red")
            console.print(f"  [{summary_color}]{n_ok} passed · {n_w} warnings · {n_e} errors[/{summary_color}]")
            console.print()

            # ── Data source configuration guide ───────────────────────────────
            _fh_ok  = bool(_get_provider_key("finnhub"))
            _av_ok  = bool(_get_provider_key("alphavantage"))
            _na_ok  = bool(_get_provider_key("newsapi"))
            _ak_ok  = True  # akshare is always available (no key needed)
            _llm_ok = any(_get_provider_key(p) for p in ("deepseek","openai","anthropic","groq"))

            _guide_needed = not (_fh_ok and _av_ok and _na_ok and _llm_ok)
            if _guide_needed:
                console.print("[bold]数据源配置指南[/bold]  [dim](完整功能需要以下 key)[/dim]")
                console.print()
                _guide_rows = [
                    # (service, key_configured, priority, what_it_unlocks, register_url, config_cmd)
                    ("finnhub",      _fh_ok,  "P0",
                     "美股/港股实时行情 + 完整 TA 指标（RSI/MACD/MA）",
                     "finnhub.io/register",  "finnhub"),
                    ("akshare",      _ak_ok,  "P0",
                     "A 股历史数据 + TA 指标（内置，无需 key）",
                     "",                      ""),
                    ("alphavantage", _av_ok,  "P1",
                     "补充历史 OHLCV、基本面数据（每日 500 次免费）",
                     "alphavantage.co/support",  "alphavantage"),
                    ("newsapi",      _na_ok,  "P1",
                     "全球财经新闻摘要（100 req/天免费）",
                     "newsapi.org/register",     "newsapi"),
                    ("deepseek",     _llm_ok, "P2",
                     "云端 LLM — 本地模型不够时的备用推理引擎",
                     "platform.deepseek.com",    "deepseek"),
                ]
                for svc, ok, pri, desc, url, cmd in _guide_rows:
                    if ok:
                        console.print(f"  [green]✓[/green]  [dim]{svc:<14}[/dim]{desc}")
                    else:
                        pri_color = "cyan" if pri == "P0" else ("yellow" if pri == "P1" else "dim")
                        console.print(f"  [dim]○[/dim]  [dim]{svc:<14}[/dim]{desc}")
                        if cmd:
                            console.print(
                                f"       [dim]注册：{url}  →  配置：[/dim]"
                                f"[bold cyan]/apikey set {cmd} <KEY>[/bold cyan]"
                            )
                console.print()
                console.print("[dim]配置后运行 /doctor 重新检查 · /apikey list 查看已有 key[/dim]")
                console.print()
        else:
            print("Aria Code Diagnostics")
            for status, label, detail in checks:
                mark = "✓" if status == "ok" else ("⚠" if status == "warn" else "✗")
                print(f"  {mark}  {label}  {detail}")

    # ── Hooks management ─────────────────────────────────────────────────────

    def cmd_hooks(self, args: str):
        """Manage Aria event hooks — scripts run on specific events.

        Two hook systems:
          1. JSON config: ~/.arthera/hooks.json  (recommended)
             Events: PreToolUse  PostToolUse  ResponseDone  SessionStart  SessionEnd
          2. Shell scripts: ~/.arthera/hooks/<event>.sh  (legacy)

        Usage:
            /hooks list           — show all hooks (JSON + shell)
            /hooks edit           — open ~/.arthera/hooks.json in $EDITOR
            /hooks edit <event>   — open shell hook script in $EDITOR (legacy)
            /hooks reload         — reload hooks.json without restarting
            /hooks run <event>    — manually trigger a hook
        """
        global _JSON_HOOKS  # modified by reload and edit subcommands
        hooks_dirs = [
            CONFIG_DIR / "hooks",
            pathlib.Path.cwd() / ".aria" / "hooks",
        ]
        parts = args.strip().split(maxsplit=1)
        sub  = parts[0].lower() if parts else "list"
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub == "reload":
            if _HAS_JSON_HOOKS:
                try:
                    _JSON_HOOKS = _load_hooks()
                    n = sum(len(v) for v in _JSON_HOOKS.values())
                    if HAS_RICH:
                        console.print(f"  [green]✓[/green] [dim]hooks.json reloaded ({n} entries)[/dim]")
                    else:
                        print(f"  hooks.json reloaded ({n} entries)")
                except Exception as exc:
                    if HAS_RICH:
                        console.print(f"  [red]✗ reload failed: {exc}[/red]")
                    else:
                        print(f"  reload failed: {exc}")
            return

        if sub == "list":
            # ── JSON hooks ────────────────────────────────────────────────────
            if _HAS_JSON_HOOKS:
                try:
                    from apps.cli.hooks import list_hooks as _list_json_hooks
                    _json_rows = _list_json_hooks()
                    if _json_rows:
                        if HAS_RICH:
                            console.print()
                            console.print("  [bold]JSON Hooks[/bold]  [dim](~/.arthera/hooks.json)[/dim]")
                            for r in _json_rows:
                                _block = " [red][blocking][/red]" if r["blocking"] else ""
                                _tool  = f"[{r['tool']}]" if r["tool"] != "*" else ""
                                console.print(
                                    f"  [cyan]{r['event']:<16}[/cyan]{_tool:<14}  "
                                    f"[dim]{r['command']}[/dim]{_block}"
                                )
                        else:
                            for r in _json_rows:
                                print(f"  {r['event']:<16} {r['tool']:<12} {r['command']}")
                except Exception:
                    pass

            # ── Shell script hooks ────────────────────────────────────────────
            found: List[tuple] = []
            for hdir in hooks_dirs:
                if hdir.exists():
                    for f in sorted(hdir.iterdir()):
                        if f.is_file() and not f.name.startswith("."):
                            found.append((str(hdir), f.name, str(f)))
            if not found:
                if HAS_RICH:
                    console.print(f"  [dim]No hooks found.[/dim]")
                    console.print(f"  [dim]Hook dirs:[/dim]")
                    for d in hooks_dirs:
                        console.print(f"    [dim]{d}[/dim]")
                    console.print(f"  [dim]Events: prompt_submit  response_done  tool_use  compact[/dim]")
                else:
                    print("No hooks. Dirs:", [str(d) for d in hooks_dirs])
                return
            if HAS_RICH:
                console.print()
                for hdir, name, path in found:
                    console.print(f"  [dim]{name:<28}[/dim]  {path}")
                console.print()
            else:
                for hdir, name, path in found:
                    print(f"  {name}  {path}")

        elif sub == "edit":
            if not rest:
                # No arg → open hooks.json (the JSON system)
                if _HAS_JSON_HOOKS:
                    from apps.cli.hooks import hooks_file_path, create_example_hooks
                    _hpath = hooks_file_path("global")
                    create_example_hooks(_hpath)
                    editor = os.getenv("EDITOR", "nano")
                    try:
                        import subprocess as _sp
                        _sp.run([editor, str(_hpath)])
                        # Auto-reload after edit
                        _JSON_HOOKS = _load_hooks()
                    except Exception as exc:
                        if HAS_RICH:
                            console.print(f"[red]Could not open editor: {exc}[/red]")
                        else:
                            print(f"Could not open editor: {exc}")
                return
            # Legacy: /hooks edit <event> → open shell script
            event = rest
            hdir = CONFIG_DIR / "hooks"
            hdir.mkdir(parents=True, exist_ok=True)
            script = hdir / f"{event}.sh"
            if not script.exists():
                script.write_text(
                    f"#!/bin/bash\n# Aria hook: {event}\n# "
                    f"Env vars: ARIA_EVENT ARIA_TOOL ARIA_TOOL_PARAMS ARIA_RESPONSE ARIA_SESSION\n\n"
                    f'echo "Hook {event} fired"\n',
                    encoding="utf-8"
                )
                script.chmod(0o755)
            editor = os.getenv("EDITOR", "nano")
            try:
                import subprocess as _sp
                _sp.run([editor, str(script)])
            except Exception as exc:
                console.print(f"[red]Could not open editor: {exc}[/red]" if HAS_RICH else str(exc))

        elif sub == "run":
            event = rest or "ResponseDone"
            if _HAS_JSON_HOOKS:
                _fire_json_hook(event, session_id=getattr(self.terminal, "session_id", ""), hooks=_JSON_HOOKS)
            _run_event_hook(event, {"ARIA_EVENT": event, "ARIA_SESSION": getattr(self.terminal, "session_id", "")})
            if HAS_RICH:
                console.print(f"  [dim]Hook '{event}' triggered[/dim]")
            else:
                print(f"Hook '{event}' triggered")

        else:
            if HAS_RICH:
                console.print("[dim]Usage: /hooks list|edit [event]|reload|run [event][/dim]")
            else:
                print("Usage: /hooks list|edit [event]|reload|run [event]")

    # ---- Regen / Undo commands ----

    async def cmd_regen(self, args: str):
        """Regenerate last AI response by re-sending the last user message."""
        # Find and remove last assistant message
        last_user_msg = None
        for i in range(len(self.terminal.conversation) - 1, -1, -1):
            if self.terminal.conversation[i]["role"] == "assistant":
                self.terminal.conversation.pop(i)
                break
        # Find the last user message
        for msg in reversed(self.terminal.conversation):
            if msg["role"] == "user":
                last_user_msg = msg["content"]
                break
        if last_user_msg:
            # Remove it from conversation (send_message will re-add it)
            for i in range(len(self.terminal.conversation) - 1, -1, -1):
                if self.terminal.conversation[i]["role"] == "user" and self.terminal.conversation[i]["content"] == last_user_msg:
                    self.terminal.conversation.pop(i)
                    break
            console.print("[dim]Regenerating...[/dim]" if HAS_RICH else "Regenerating...")
            await self.terminal.send_message(last_user_msg)
        else:
            console.print("[dim]No message to regenerate[/dim]" if HAS_RICH else "Nothing to regenerate")

    def cmd_undo(self, args: str):
        """Remove last user+assistant message pair from conversation."""
        if len(self.terminal.conversation) < 2:
            console.print("[dim]Nothing to undo[/dim]" if HAS_RICH else "Nothing to undo")
            return
        removed = 0
        # Remove last assistant, then last user
        for role in ("assistant", "user"):
            for i in range(len(self.terminal.conversation) - 1, -1, -1):
                if self.terminal.conversation[i]["role"] == role:
                    self.terminal.conversation.pop(i)
                    removed += 1
                    break
        if HAS_RICH:
            console.print(f"[dim]Undone ({removed} messages removed, {len(self.terminal.conversation)} remaining)[/dim]")
        else:
            print(f"Undone ({removed} removed)")

    async def cmd_retry(self, args: str):
        """Re-run last user message with higher temperature (more creative response)."""
        last_user_msg = None
        # Remove last assistant message
        for i in range(len(self.terminal.conversation) - 1, -1, -1):
            if self.terminal.conversation[i]["role"] == "assistant":
                self.terminal.conversation.pop(i)
                break
        # Find last user message
        for msg in reversed(self.terminal.conversation):
            if msg["role"] == "user":
                last_user_msg = msg["content"]
                break
        if not last_user_msg:
            console.print("[dim]No message to retry[/dim]" if HAS_RICH else "Nothing to retry")
            return
        # Remove last user msg too (send_message will re-add)
        for i in range(len(self.terminal.conversation) - 1, -1, -1):
            if self.terminal.conversation[i]["role"] == "user" and \
               self.terminal.conversation[i]["content"] == last_user_msg:
                self.terminal.conversation.pop(i)
                break
        # Temporarily bump temperature
        orig_model_key = resolve_model_key(self.terminal.config.get("model", "qwen2.5:7b"))
        _fallback_model = MODELS.get("qwen-fast") or MODELS.get("qwen7b") or next(iter(MODELS.values()))
        orig_temp = MODELS.get(orig_model_key, _fallback_model).get("temperature", 0.3)
        MODELS[orig_model_key]["temperature"] = min(0.9, orig_temp + 0.3)
        if HAS_RICH:
            console.print(f"[dim]Retrying with temperature {MODELS[orig_model_key]['temperature']:.1f}...[/dim]")
        else:
            print(f"Retrying (temp +0.3)...")
        try:
            await self.terminal.send_message(last_user_msg)
        finally:
            MODELS[orig_model_key]["temperature"] = orig_temp  # restore

    def cmd_note(self, args: str):
        """Save a persistent note to ARIA.md in current directory.

        Usage: /note <text>
        Notes are appended to ARIA.md and injected as project context in future sessions.
        """
        text = args.strip()
        if not text:
            console.print("[dim]Usage: /note <text>[/dim]" if HAS_RICH else "Usage: /note <text>")
            return
        aria_md = pathlib.Path.cwd() / "ARIA.md"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n- [{now_str}] {text}"
        if aria_md.exists():
            content = aria_md.read_text(encoding="utf-8")
            if "## Notes" not in content:
                content += "\n\n## Notes\n"
            content += entry
        else:
            content = f"# Aria Project Notes\n\n## Notes\n{entry}\n"
        aria_md.write_text(content, encoding="utf-8")
        # Refresh in-memory project context
        global _PROJECT_CONTEXT
        _PROJECT_CONTEXT = _load_project_context()
        if HAS_RICH:
            console.print(f"[dim]Note saved to {aria_md.name}[/dim]")
        else:
            print(f"Saved to {aria_md.name}")
    async def cmd_review(self, args: str):
        """AI code review for a file or git diff.

        Usage:
            /review                — review git diff HEAD (staged + unstaged)
            /review <file>         — review a specific file
            /review --staged       — review only staged changes
        """
        raw = args.strip()
        policy = self.terminal.config.get("command_policy", "safe")

        if raw and not raw.startswith("--"):
            # File review
            p = pathlib.Path(raw).expanduser()
            if not p.exists():
                msg = f"File not found: {raw}"
                console.print(f"[red]{msg}[/red]") if HAS_RICH else print(msg)
                return
            try:
                content = p.read_text(errors="replace")[:12000]
            except Exception as e:
                console.print(f"[red]Cannot read file: {e}[/red]") if HAS_RICH else print(f"Cannot read: {e}")
                return
            prompt = (
                f"请对以下 `{p.name}` 的代码进行专业审查，查找 Bug、安全问题和改进点。\n"
                f"每条发现用严重程度标签开头：**BUG**、**IMPROVEMENT**、**NIT**。\n"
                f"按文件组织输出，直接给结论，不要重复贴出全部代码。\n\n"
                f"```\n{content}\n```"
            )
        else:
            # Git diff review
            diff_cmd = "git diff --staged" if raw.startswith("--staged") else "git diff HEAD"
            tr = _tool_run_command({"command": diff_cmd})
            if not tr.get("success"):
                msg = tr.get("error", "git diff failed")
                console.print(f"[red]{msg}[/red]") if HAS_RICH else print(msg)
                return
            diff_text = (tr.get("data") or {}).get("stdout", "").strip()
            if not diff_text:
                console.print("[dim]No changes to review.[/dim]") if HAS_RICH else print("No changes to review.")
                return
            diff_text = diff_text[:12000]
            prompt = (
                "请审查以下 git diff，找出 Bug、潜在回归、安全问题和代码质量问题。\n"
                "每条发现用严重程度标签开头：**BUG**、**IMPROVEMENT**、**NIT**。\n"
                "按文件分组，直接给出结论。\n\n"
                f"```diff\n{diff_text}\n```"
            )

        await self.terminal.send_message(prompt)

    # ── Project scaffold templates ────────────────────────────────────────────

    # Scaffold templates moved to apps.cli.commands.scaffold_templates
    from apps.cli.commands.scaffold_templates import SCAFFOLD_TEMPLATES as _SCAFFOLD_TEMPLATES  # noqa

    @staticmethod
    def _create_scaffold(target_dir: pathlib.Path, template: dict) -> list:
        """Create dirs + write files from a scaffold template. Returns list of created paths."""
        created = []
        for d in template.get("dirs", []):
            dp = target_dir / d
            dp.mkdir(parents=True, exist_ok=True)
            created.append(str(dp))
        for rel, content in template.get("files", {}).items():
            fp = target_dir / rel
            fp.parent.mkdir(parents=True, exist_ok=True)
            if not fp.exists():
                fp.write_text(content, encoding="utf-8")
                created.append(str(fp))
        return created
    # ---- Aria-exclusive quant features ----
    # ── financial-services workflow 命令 ────────────────────────────────────────

    async def cmd_research(self, args: str):
        """Market Researcher 工作流（参考 anthropics/financial-services market-researcher agent）。

        触发完整研究流程：行情 → 技术图表 → 近期新闻 → 信号摘要 → 研究报告。

        Usage:
            /research AAPL
            /research BTC-USD
            /research 600519.SS
        """
        sym = args.strip().upper() or "AAPL"
        prompt = (
            f"请对 {sym} 进行完整的 Market Researcher 分析：\n"
            f"1. 获取实时行情并显示报价卡片\n"
            f"2. 生成 6 个月技术图表（含 SMA20、SMA50、BB、RSI）\n"
            f"3. 抓取最新 5 条相关新闻\n"
            f"4. 分析主要技术信号（趋势、超买/超卖、关键支撑/阻力）\n"
            f"5. 输出一份简明研究报告（结论 + 风险提示）\n\n"
            f"标的代码：{sym}"
        )
        await self.terminal.handle_user_input(prompt)

    async def cmd_earnings_workflow(self, args: str):
        """财报分析工作流（参考 anthropics/financial-services earnings-reviewer agent）。

        工具链：SEC Edgar → Finnhub financials → AI 摘要 → 财报 table card + 报告。

        Usage:
            /earnings AAPL
            /earnings MSFT Q1 2026
        """
        parts  = args.strip().split()
        sym    = parts[0].upper() if parts else "AAPL"
        period = " ".join(parts[1:]) if len(parts) > 1 else "最近一个季度"
        prompt = (
            f"请对 {sym} 进行 Earnings Reviewer 财报分析（{period}）：\n"
            f"1. 获取最新季报关键指标（EPS、营收、毛利率、同比增速）\n"
            f"2. 对比市场预期与实际结果（beat/miss 分析）\n"
            f"3. 提取管理层展望与主要风险因素\n"
            f"4. 以结构化 table card 呈现核心财务数据\n"
            f"5. 输出一份简明财报评论（3-5 段）\n\n"
            f"标的：{sym}，报告期：{period}"
        )
        await self.terminal.handle_user_input(prompt)

    # ── 经营权共创平台 Agent 命令 ─────────────────────────────────────────────────

    async def cmd_asset_diag(self, args: str):
        """资产诊断 Agent: /asset-diag <资产ID>

        对指定资产运行 AssetDiagnosisAgent，判断处置方式（出租/共创/出售）。
        优先从后端 API 拉取完整资产数据；无数据时以 ID 作为位置标识演示。

        Usage:
            /asset-diag asset_000001
            /asset-diag 中关村创业大街101号
        """
        asset_id = args.strip()
        if not asset_id:
            _p("用法: /asset-diag <资产ID或名称>  例: /asset-diag asset_000001", "dim")
            return

        # 1. 先尝试从后端拉取资产详情
        asset_info = {}
        api_url = self.terminal.config.get("api_url", "http://localhost:8000")
        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    f"{api_url}/api/realty/assets/{asset_id}",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        raw = body.get("data", {})
                        # 映射 API 字段 → Agent 期望字段
                        asset_info = {
                            "area":             raw.get("area_sqm", 0),
                            "location":         raw.get("address", asset_id),
                            "vacancy_days":     raw.get("vacancy_days", 0),
                            "expected_rent":    raw.get("monthly_rent_market", 0),
                            "allowed_business": raw.get("allowed_business_types", []),
                            "property_state":   raw.get("property_state", "正常"),
                            "floor_height":     raw.get("floor_height", 0),
                        }
                        _p(f"已从 API 加载资产: {raw.get('name', asset_id)}", "ok")
        except Exception:
            pass

        # 2. 无 API 数据时用最小演示集并提示
        if not asset_info:
            _p("[dim]提示: 未找到资产数据，以 ID 作为位置标识演示（结果仅供参考）[/dim]")
            asset_info = {
                "location": asset_id,
                "area": 0, "vacancy_days": 0,
                "expected_rent": 0, "allowed_business": [],
                "property_state": "正常",
            }

        await self._run_realty_agent("asset_diagnosis", asset_id, {
            "asset_info": asset_info,
        })

    async def cmd_contract_draft(self, args: str):
        """合同规则草案 Agent: /contract-draft <project_id>

        运行 ContractRulesAgent，将谈判结果转化为结构化合同条款草案。

        Usage:
            /contract-draft proj_001
            /contract-draft proj_001 --guaranteed 50000 --share 10
        """
        parts = args.split() if args else []
        project_id = parts[0] if parts else "demo_project"

        # 简单参数解析
        nego = {"guaranteed_amount": 0, "revenue_share_pct": 0}
        for i, p in enumerate(parts):
            if p == "--guaranteed" and i+1 < len(parts):
                try: nego["guaranteed_amount"] = float(parts[i+1])
                except ValueError: pass
            elif p == "--share" and i+1 < len(parts):
                try: nego["revenue_share_pct"] = float(parts[i+1])
                except ValueError: pass

        await self._run_realty_agent("contract_rules", project_id, {
            "negotiation": nego,
            "asset_info":  {"name": project_id},
            "operator_info": {},
        })

    async def cmd_revenue_calc(self, args: str):
        """分账测算: /revenue-calc <project_id> <总流水金额> [退款金额]

        运行 RevenueShareAgent，精确计算本期各方分账金额。

        Usage:
            /revenue-calc proj_001 200000
            /revenue-calc proj_001 200000 5000
        """
        parts = args.split() if args else []
        if len(parts) < 2:
            _p("用法: /revenue-calc <project_id> <总流水> [退款]  "
               "例: /revenue-calc proj_001 200000", "dim")
            return

        project_id = parts[0]
        try:
            gross   = float(parts[1])
            refunds = float(parts[2]) if len(parts) > 2 else 0.0
        except ValueError:
            _p("流水金额必须为数字", "error")
            return

        # 尝试从后端获取合同规则
        api_url = self.terminal.config.get("api_url", "http://localhost:8000")
        rules = {}
        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                async with sess.get(f"{api_url}/api/realty/contracts/{project_id}",
                                    timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        rules = body.get("data", {})
        except Exception:
            pass

        if not rules:
            _p(f"[dim]未找到 {project_id} 的合同规则，使用默认值演示[/dim]")
            rules = {"guaranteed_monthly": 30000, "revenue_share_pct": 10,
                     "revenue_share_base": 0, "platform_fee_pct": 5,
                     "risk_reserve_pct": 3, "settlement_cycle": "monthly"}

        await self._run_realty_agent("revenue_share", project_id, {
            "contract_rules":  rules,
            "transaction_data":{"gross_revenue": gross, "refunds": refunds},
        })

    async def cmd_realty_risk_scan(self, args: str):
        """项目风险扫描: /risk-scan [project_id]

        并行运行 cashflow_verify + energy_anomaly + fulfillment_risk 三个 Agent，
        生成综合风险报告。无 project_id 时扫描所有项目。

        Usage:
            /risk-scan
            /risk-scan proj_001
        """
        project_id = args.strip() or "demo_project"

        if HAS_RICH:
            console.print(f"\n  [bold]风险扫描[/bold]  项目: [cyan]{project_id}[/cyan]")

        # 先尝试从后端 API 扫描
        api_url = self.terminal.config.get("api_url", "http://localhost:8000")
        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    f"{api_url}/api/realty/risks/scan/{project_id}",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        _print_risk_scan(data)
                        return
        except Exception:
            pass

        # 降级：本地 Agent 并行运行
        await self._run_realty_team(
            ["cashflow_verify", "energy_anomaly", "fulfillment_risk"],
            project_id, {}
        )

    async def cmd_ops_report(self, args: str):
        """运营汇报生成: /ops-report <project_id>

        运行 OpsOptimizeAgent，分析坪效/客流/营销效果，生成运营优化建议报告。
        优先从后端 API 拉取运营数据，无数据时生成空模板（供人工填写）。

        Usage:
            /ops-report proj_001
        """
        project_id = args.strip() or "demo_project"
        api_url = self.terminal.config.get("api_url", "http://localhost:8000")

        project_info     = {"name": project_id, "area": 0, "business_type": "未知"}
        performance_data = {}
        marketing_data   = {}

        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                # 拉取项目基础信息
                async with sess.get(
                    f"{api_url}/api/realty/assets/{project_id}",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        raw = (await resp.json()).get("data", {})
                        project_info = {
                            "name":          raw.get("name", project_id),
                            "area":          raw.get("area_sqm", 0),
                            "business_type": raw.get("current_business_type", "未知"),
                            "open_date":     raw.get("open_date", ""),
                        }
                # 拉取最近分账数据估算坪效
                async with sess.get(
                    f"{api_url}/api/realty/revenue/splits?project_id={project_id}&page_size=3",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp2:
                    if resp2.status == 200:
                        splits = (await resp2.json()).get("data", {}).get("splits", [])
                        if splits:
                            revenues = [s["split_result"].get("gross_revenue", 0) for s in splits]
                            avg_rev = sum(revenues) / len(revenues)
                            performance_data = {
                                "monthly_revenue": avg_rev,
                                "daily_visits": 0,   # IoT 数据，暂无
                            }
                            _p(f"已加载近 {len(splits)} 期分账数据，月均流水 {avg_rev:,.0f}元", "ok")
        except Exception:
            pass

        if not performance_data:
            _p("[dim]提示: 未找到运营数据，建议先录入分账记录后再运行此命令[/dim]")

        await self._run_realty_agent("ops_optimize", project_id, {
            "project_info":     project_info,
            "performance_data": performance_data,
            "marketing_data":   marketing_data,
            "peer_benchmarks":  {"revenue_per_sqm": 300},
        })

    async def cmd_exit_calc(self, args: str):
        """退出清算草案: /exit-calc <project_id> [--reason <原因>]

        运行 ExitSettlementAgent，生成退出清算方案和交接清单草案。
        从后端 API 读取合同规则和未结账单，生成精确清算草案。

        Usage:
            /exit-calc proj_001
            /exit-calc proj_001 --reason 提前退出
        """
        parts = args.split() if args else []
        project_id = parts[0] if parts else "demo_project"
        reason = "到期终止"
        for i, p in enumerate(parts):
            if p == "--reason" and i+1 < len(parts):
                reason = " ".join(parts[i+1:])
                break

        api_url = self.terminal.config.get("api_url", "http://localhost:8000")
        project_info  = {"name": project_id}
        financials    = {"deposit_amount": 0, "unpaid_invoices": 0,
                         "guaranteed_monthly": 0, "exit_penalty_months": 3,
                         "prepayment_received": 0, "renovation_cost": 0}

        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                # 拉取合同规则
                async with sess.get(
                    f"{api_url}/api/realty/contracts/{project_id}",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        ctr = (await resp.json()).get("data", {})
                        from datetime import date
                        start = ctr.get("start_date", "")
                        used_months = 0
                        if start:
                            try:
                                from dateutil.relativedelta import relativedelta
                                d0 = date.fromisoformat(start)
                                delta = relativedelta(date.today(), d0)
                                used_months = delta.years * 12 + delta.months
                            except Exception:
                                pass
                        project_info.update({
                            "contract_years":  ctr.get("contract_years", 1),
                            "used_months":     used_months,
                            "contract_end":    ctr.get("end_date", ""),
                        })
                        financials.update({
                            "deposit_amount":     ctr.get("deposit_amount", 0),
                            "guaranteed_monthly": ctr.get("guaranteed_monthly", 0),
                            "exit_penalty_months":ctr.get("exit_penalty_months", 3),
                        })
                        _p(f"已加载合同规则: 保底 {ctr.get('guaranteed_monthly',0):,}元/月", "ok")
                # 拉取未结账单
                async with sess.get(
                    f"{api_url}/api/realty/invoices?project_id={project_id}&status=unpaid",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp2:
                    if resp2.status == 200:
                        body2 = await resp2.json()
                        summary = body2.get("data", {}).get("summary", {})
                        unpaid = summary.get("total_amount", 0) - summary.get("paid_amount", 0)
                        financials["unpaid_invoices"] = unpaid
                        if unpaid > 0:
                            _p(f"发现未结账单合计: {unpaid:,.2f}元", "ok")
        except Exception:
            pass

        await self._run_realty_agent("exit_settlement", project_id, {
            "project_info": project_info,
            "financials":   financials,
            "asset_condition": {},
            "exit_reason":  reason,
        })

    # ── 经营权共创 Agent 辅助方法 ─────────────────────────────────────────────

    async def _run_realty_agent(self, agent_name: str, project_id: str,
                                input_data: dict):
        """运行单个 realty Agent，打印结果（本地直接调用，不经过后端）"""
        if HAS_RICH:
            with console.status(
                f"[dim]运行 {agent_name} Agent...[/dim]", spinner="dots"
            ):
                result = await self._call_realty_agent(agent_name, project_id, input_data)
        else:
            print(f"Running {agent_name}...")
            result = await self._call_realty_agent(agent_name, project_id, input_data)

        if result:
            _print_realty_result(result, agent_name)

    async def _run_realty_team(self, agents: list, project_id: str, input_data: dict):
        """并行运行多个 realty Agent"""
        import asyncio
        if HAS_RICH:
            with console.status(
                f"[dim]并行扫描 {', '.join(agents)}...[/dim]", spinner="dots"
            ):
                tasks = [self._call_realty_agent(n, project_id, input_data) for n in agents]
                results = await asyncio.gather(*tasks, return_exceptions=False)
        else:
            tasks = [self._call_realty_agent(n, project_id, input_data) for n in agents]
            results = await asyncio.gather(*tasks, return_exceptions=False)

        for res, name in zip(results, agents):
            if res:
                _print_realty_result(res, name)

    async def _call_realty_agent(self, agent_name: str, project_id: str,
                                  input_data: dict):
        """从 registry 加载并调用 realty Agent"""
        try:
            from agents.registry import get_registry
            cls = get_registry().get(agent_name)
            if not cls:
                _p(f"Agent '{agent_name}' 未注册", "error")
                return None

            # 尝试获取 LLM provider
            llm = None
            try:
                from providers.llm.registry import list_available_providers, get_provider
                avail = [p for p in list_available_providers() if p.get("available")]
                if avail:
                    llm = get_provider(avail[0]["name"])
            except Exception:
                pass

            agent = cls(llm_provider=llm)
            result = await agent.analyze(project_id, input_data)
            return result
        except Exception as e:
            _p(f"Agent {agent_name} 执行失败: {e}", "error")
            return None



    # ---- Provider / API Key management (Open Interpreter style) ----
    # ---- Auth commands ----

    async def cmd_login(self, args: str):
        """Login to Arthera backend.

        Usage: /login <email>           — prompts for password securely
               /login                   — prompts for both email and password
        """
        import getpass as _getpass
        import aiohttp

        parts = args.split()
        if parts:
            email = parts[0]
        else:
            try:
                prompt_fn = console.input if HAS_RICH else input
                email = prompt_fn("  Email: ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("[dim]Cancelled[/dim]" if HAS_RICH else "Cancelled")
                return
        if not email:
            console.print("[dim]Usage: /login <email>[/dim]" if HAS_RICH else "Usage: /login <email>")
            return

        # Always prompt for password — never accept it as a CLI argument (security)
        try:
            _esc_watcher.pause()
            password = _getpass.getpass("  Password: ")
        except (EOFError, KeyboardInterrupt):
            console.print("[dim]Cancelled[/dim]" if HAS_RICH else "Cancelled")
            return
        finally:
            _esc_watcher.resume()

        if not password:
            console.print("[red]Password cannot be empty[/red]" if HAS_RICH else "Password cannot be empty")
            return

        if HAS_RICH:
            console.print("[dim]Authenticating...[/dim]")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.terminal.api_url}/auth/login",
                    json={"email": email, "password": password},
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    try:
                        data = await resp.json()
                    except Exception:
                        data = {}
                    if resp.status == 200 and data.get("token"):
                        self.terminal.config["auth_token"] = data["token"]
                        user_id = data.get("user_id", data.get("uid", email))
                        self.terminal.config["user_id"] = user_id
                        # Store token expiry if provided
                        if data.get("expires_at"):
                            self.terminal.config["token_expires_at"] = data["expires_at"]
                        save_config(self.terminal.config)
                        console.print(f"[green]✓ Logged in as {user_id}[/green]" if HAS_RICH
                                      else f"Logged in as {user_id}")
                    elif resp.status == 401:
                        _print_error("Invalid email or password", "login")
                    elif resp.status == 429:
                        _print_error("Too many login attempts — please wait before retrying", "login")
                    else:
                        err = data.get("error", data.get("message", f"Login failed (HTTP {resp.status})"))
                        _print_error(err, "login")
        except aiohttp.ClientConnectorError:
            _print_error(
                f"Cannot reach {self.terminal.api_url} — check your network connection or use /local on",
                "login"
            )
        except asyncio.TimeoutError:
            _print_error("Login request timed out (15s) — server may be unavailable", "login")
        except Exception as e:
            _print_error(f"Login error: {e}", "login")

    def cmd_logout(self, args: str):
        self.terminal.config["auth_token"] = None
        self.terminal.config["user_id"] = None
        self.terminal.config.pop("token_expires_at", None)
        save_config(self.terminal.config)
        console.print("[dim]Logged out[/dim]" if HAS_RICH else "Logged out")

    def cmd_whoami(self, args: str):
        """Show current authentication status."""
        cfg = self.terminal.config
        user_id = cfg.get("user_id")
        token = cfg.get("auth_token")
        expires = cfg.get("token_expires_at")

        if not token:
            console.print("[dim]Not logged in — use /login <email>[/dim]" if HAS_RICH
                          else "Not logged in")
            return

        if HAS_RICH:
            console.print()
            console.print(f"  [dim]User:[/dim]    {user_id or 'unknown'}")
            console.print(f"  [dim]Token:[/dim]   {token[:12]}...")
            if expires:
                # Check expiry
                try:
                    exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                    now = datetime.now(exp_dt.tzinfo)
                    if now > exp_dt:
                        console.print(f"  [dim]Expires:[/dim] [red]EXPIRED ({expires[:10]})[/red]")
                        console.print("  [dim]Run /login to refresh your session[/dim]")
                    else:
                        delta = exp_dt - now
                        hours = int(delta.total_seconds() // 3600)
                        console.print(f"  [dim]Expires:[/dim] {expires[:10]} [dim](in {hours}h)[/dim]")
                except Exception:
                    console.print(f"  [dim]Expires:[/dim] {expires}")
            console.print()
        else:
            print(f"User: {user_id or 'unknown'}")
            print(f"Token: {token[:12]}...")
            if expires:
                print(f"Expires: {expires}")

    # ---- Session commands ----

    def cmd_sessions(self, args: str):
        keyword = args.strip().lower()
        sessions = self.terminal.session_mgr.list_sessions()
        if keyword:
            sessions = [s for s in sessions if keyword in s["title"].lower()]
        if not sessions:
            msg = f"No sessions matching '{keyword}'" if keyword else "No saved sessions"
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return
        if HAS_RICH:
            console.print()
            header = f"  [bold]Sessions[/bold]  [dim]({len(sessions)} found)[/dim]" if keyword else "  [bold]Sessions[/bold]"
            console.print(header)
            for i, s in enumerate(sessions, 1):
                updated = s["updated"][:16] if s["updated"] else "-"
                console.print(f"    [dim]{i}.[/dim] [bold]{s['title']}[/bold]  "
                              f"[dim]{s['id'][:8]}  {s['messages']} msgs  {updated}[/dim]")
            console.print()
            console.print("  [dim]Use /load <number> to resume · /sessions <keyword> to search[/dim]")
        else:
            for i, s in enumerate(sessions, 1):
                print(f"  {i}. [{s['id'][:8]}] {s['title']} ({s['messages']} msgs)")

    def cmd_save(self, args: str):
        if not self.terminal.conversation:
            console.print("[dim]Nothing to save[/dim]" if HAS_RICH else "Nothing to save")
            return
        sid = self.terminal.session_id
        title = args.strip().strip('"').strip("'") if args.strip() else None
        meta = {}
        if title:
            meta["title"] = title
        self.terminal.session_mgr.save_session(sid, self.terminal.conversation, metadata=meta)
        self.terminal.config["last_session_id"] = sid
        save_config(self.terminal.config)
        display = f"{title} ({sid[:8]})" if title else f"{sid[:8]}..."
        console.print(f"[green]Session saved: {display}[/green]" if HAS_RICH
                      else f"Saved: {display}")

    def cmd_rename(self, args: str):
        """Rename current session."""
        title = args.strip().strip('"').strip("'")
        if not title:
            console.print("[dim]Usage: /rename <title>[/dim]" if HAS_RICH else "Usage: /rename <title>")
            return
        sid = self.terminal.session_id
        data = self.terminal.session_mgr.load_session(sid)
        if data:
            meta = data.get("metadata", {})
            meta["title"] = title
            self.terminal.session_mgr.save_session(sid, self.terminal.conversation, metadata=meta)
        else:
            self.terminal.session_mgr.save_session(sid, self.terminal.conversation, metadata={"title": title})
        console.print(f"[green]Renamed: {title}[/green]" if HAS_RICH else f"Renamed: {title}")

    def cmd_load(self, args: str):
        session_id = args.strip()
        if not session_id:
            # Try to load by index from /sessions listing
            sessions = self.terminal.session_mgr.list_sessions()
            if not sessions:
                console.print("[dim]No sessions. Usage: /load <session_id>[/dim]" if HAS_RICH
                              else "No sessions")
                return
            # Arrow-key picker for sessions
            options = []
            for s in sessions[:20]:
                title = s.get("metadata", {}).get("title", s["id"][:8])
                ts = s.get("updated", "")[:10]
                options.append((title, ts))
            choice = _arrow_select(options, selected=0, title="Load Session")
            if 0 <= choice < len(sessions):
                session_id = sessions[choice]["id"]
            else:
                if HAS_RICH:
                    console.print("[dim]Cancelled[/dim]")
                else:
                    print("Cancelled")
                return

        data = self.terminal.session_mgr.load_session(session_id)
        if data:
            self.terminal.conversation = data.get("messages", [])
            self.terminal.session_id = data["id"]
            title = data.get("metadata", {}).get("title", "Untitled")
            n = len(self.terminal.conversation)
            console.print(f"[green]Loaded: {title} ({n} messages)[/green]" if HAS_RICH
                          else f"Loaded: {title} ({n} msgs)")
        else:
            _print_error(f"Session not found: {session_id}", "session")

    # ---- Export command ----

    async def cmd_export(self, args: str):
        parts = args.split()
        fmt = parts[0].lower() if parts else "json"
        filename = parts[1] if len(parts) > 1 else None

        if not self.terminal.conversation:
            console.print("[dim]Nothing to export[/dim]" if HAS_RICH else "Nothing to export")
            return

        if fmt == "json":
            content = json.dumps(self.terminal.conversation, indent=2, ensure_ascii=False)
            ext = "json"
        elif fmt == "csv":
            lines = ["role,content"]
            for msg in self.terminal.conversation:
                escaped = msg["content"].replace('"', '""').replace('\n', ' ')
                lines.append(f'{msg["role"]},"{escaped}"')
            content = "\n".join(lines)
            ext = "csv"
        elif fmt == "md":
            lines = [f"# Aria Code Chat Export — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
            for msg in self.terminal.conversation:
                prefix = "**You:**" if msg["role"] == "user" else "**Aria:**"
                lines.append(f"{prefix}\n{msg['content']}\n")
            content = "\n".join(lines)
            ext = "md"
        elif fmt == "sft":
            # Export as Alpaca-format SFT training data (user→assistant pairs)
            conv = self.terminal.conversation
            pairs = []
            i = 0
            while i < len(conv) - 1:
                if conv[i]["role"] == "user" and conv[i + 1]["role"] == "assistant":
                    user_text = conv[i]["content"].strip()
                    assistant_text = conv[i + 1]["content"].strip()
                    # Skip very short or tool-result messages
                    if len(user_text) > 10 and len(assistant_text) > 20:
                        if not user_text.startswith("Tool results:"):
                            pairs.append({
                                "instruction": user_text,
                                "input": "",
                                "output": assistant_text,
                                "source": "aria_cli_export",
                                "timestamp": datetime.now().strftime("%Y-%m-%d"),
                            })
                    i += 2
                else:
                    i += 1
            if not pairs:
                console.print("[dim]No user→assistant pairs to export[/dim]" if HAS_RICH
                              else "No pairs to export")
                return
            content = json.dumps(pairs, indent=2, ensure_ascii=False)
            ext = "json"
            if not filename:
                filename = f"aria_sft_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            if HAS_RICH:
                console.print(f"[dim]{len(pairs)} training pairs extracted[/dim]")
            else:
                print(f"{len(pairs)} training pairs")
        else:
            console.print("[dim]Format: json, csv, md, or sft (SFT training data)[/dim]" if HAS_RICH
                          else "Format: json, csv, md, sft")
            return

        if not filename:
            filename = f"aria_code_chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
        with open(filename, "w") as f:
            f.write(content)
        console.print(f"[green]Exported to {filename}[/green]" if HAS_RICH
                      else f"Exported: {filename}")

    # ---- File operation commands (Claude Code-style) ----

    def cmd_read(self, args: str):
        """Read a file: /read <path> [offset] [limit]"""
        parts = args.split()
        if not parts:
            console.print("[dim]Usage: /read <file_path> [start_line] [num_lines][/dim]" if HAS_RICH
                          else "Usage: /read <path> [offset] [limit]")
            return
        params = {"path": parts[0]}
        if len(parts) > 1:
            try:
                params["offset"] = int(parts[1])
            except ValueError:
                pass
        if len(parts) > 2:
            try:
                params["limit"] = int(parts[2])
            except ValueError:
                pass
        result = _tool_read_file(params)
        if result["success"]:
            content = result["data"]["content"]
            if HAS_RICH:
                # Use Syntax for code files
                path = result["data"]["path"]
                ext = pathlib.Path(path).suffix
                lang_map = {".py": "python", ".js": "javascript", ".ts": "typescript",
                            ".tsx": "typescript", ".jsx": "javascript", ".json": "json",
                            ".yaml": "yaml", ".yml": "yaml", ".md": "markdown",
                            ".swift": "swift", ".html": "html", ".css": "css",
                            ".sh": "bash", ".sql": "sql", ".rs": "rust", ".go": "go"}
                lang = lang_map.get(ext, "text")
                # Strip line numbers we added, use Syntax's own
                raw = "\n".join(line.split("│ ", 1)[1] if "│ " in line else line
                                for line in content.split("\n"))
                console.print(f"\n[dim]{path} ({result['data']['lines']} lines)[/dim]")
                console.print(Syntax(raw, lang, line_numbers=True, theme=_SYNTAX_THEME))
            else:
                print(f"\n{result['data']['path']} ({result['data']['lines']} lines)")
                print(content)
        else:
            console.print(f"[red]{result['error']}[/red]" if HAS_RICH else result["error"])

    def cmd_write(self, args: str):
        """Write a file: /write [--stage] <path> then paste content, end with EOF line."""
        parts = args.strip().split()
        stage_only = False
        if "--stage" in parts:
            stage_only = True
            parts = [p for p in parts if p != "--stage"]
        path = " ".join(parts).strip()
        if not path:
            console.print("[dim]Usage: /write [--stage] <file_path>[/dim]" if HAS_RICH
                          else "Usage: /write [--stage] <path>")
            console.print("[dim]Then paste content, end with a line containing only 'EOF'[/dim]" if HAS_RICH
                          else "Paste content, end with EOF")
            return
        if HAS_RICH:
            mode = "Staging" if stage_only else "Writing"
            console.print(f"[dim]{mode} {path} — paste content, end with 'EOF' on a new line:[/dim]")
        else:
            print(f"{'Staging' if stage_only else 'Writing'} {path} — paste content, end with EOF:")
        lines = []
        try:
            while True:
                line = input()
                if line.strip() == "EOF":
                    break
                lines.append(line)
        except (EOFError, KeyboardInterrupt):
            console.print("[dim]Cancelled[/dim]" if HAS_RICH else "Cancelled")
            return
        content = "\n".join(lines) + "\n"
        result = _tool_write_file({"path": path, "content": content, "stage_only": stage_only})
        if not result["success"]:
            console.print(f"[red]{result['error']}[/red]" if HAS_RICH else result["error"])
        elif stage_only:
            change_id = result.get("data", {}).get("change_id", "")
            msg = f"Staged change {change_id}. Review with /changes, apply with /apply-change {change_id}."
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)

    async def cmd_edit(self, args: str):
        """Edit a file interactively: /edit <path> — AI edits based on instruction."""
        parts = args.strip().split(maxsplit=1)
        if not parts:
            console.print("[dim]Usage: /edit <file_path> <instruction>[/dim]" if HAS_RICH
                          else "Usage: /edit <path> <instruction>")
            return
        path = parts[0]
        instruction = parts[1] if len(parts) > 1 else None

        # Read the file first
        read_result = _tool_read_file({"path": path})
        if not read_result["success"]:
            console.print(f"[red]{read_result['error']}[/red]" if HAS_RICH else read_result["error"])
            return

        if not instruction:
            # Show file and ask for instruction
            if HAS_RICH:
                console.print(f"[dim]{read_result['data']['path']} ({read_result['data']['lines']} lines)[/dim]")
            try:
                instruction = (console.input("[bold]>[/bold] What to change: ") if HAS_RICH
                               else input("What to change: ")).strip()
            except (EOFError, KeyboardInterrupt):
                return
            if not instruction:
                return

        # Send to AI with file context and ask for edit
        file_content = read_result["data"]["content"]
        prompt = (
            f"I need you to edit the file `{path}`.\n\n"
            f"Current file content:\n```\n{file_content[:8000]}\n```\n\n"
            f"Instruction: {instruction}\n\n"
            f"Use the edit_file tool to make the changes. Remember to use the exact old_string from the file."
        )
        await self.terminal.send_message(prompt)

    def cmd_ls(self, args: str):
        """List files: /ls [path] [pattern]"""
        parts = args.split()
        path = parts[0] if parts else "."
        pattern = parts[1] if len(parts) > 1 else "*"
        result = _tool_list_files({"path": path, "pattern": pattern})
        if result["success"]:
            items = result["data"]["items"]
            if HAS_RICH:
                console.print(f"\n[dim]{result['data']['path']} ({result['data']['count']} items)[/dim]\n")
                for item in items:
                    if item["type"] == "dir":
                        console.print(f"  [bold]{item['name']}/[/bold]")
                    else:
                        size = item["size"]
                        size_str = f"{size:,}" if size < 10000 else f"{size/1024:.1f}K"
                        console.print(f"  {item['name']}  [dim]{size_str}[/dim]")
            else:
                for item in items:
                    suffix = "/" if item["type"] == "dir" else ""
                    print(f"  {item['name']}{suffix}")
        else:
            console.print(f"[red]{result['error']}[/red]" if HAS_RICH else result["error"])

    def cmd_search(self, args: str):
        """Search code: /search <pattern> [path] [glob]

        If the second word doesn't look like a file path (no / or .), the whole
        args string is treated as the pattern and CWD is searched.
        """
        args = args.strip().strip('"\'')
        parts = args.split()
        if not parts:
            console.print("[dim]Usage: /search <pattern> [path] [file_glob][/dim]" if HAS_RICH
                          else "Usage: /search <pattern> [path] [glob]")
            return

        # Determine if second token looks like a file path or directory
        def _looks_like_path(s: str) -> bool:
            return bool(s) and any(c in s for c in "/\\.")

        _QUOTES = '"\'`'
        if len(parts) == 1:
            # Single token: use as pattern, search CWD
            params = {"pattern": parts[0].strip(_QUOTES)}
        elif len(parts) >= 2 and _looks_like_path(parts[1]):
            # Second token is a path
            params = {"pattern": parts[0].strip(_QUOTES)}
            params["path"] = parts[1]
            if len(parts) > 2:
                params["glob"] = parts[2]
        else:
            # Multi-word pattern with no path (e.g. /search def cmd_model)
            # Find where the path arg starts (if any)
            path_idx = None
            for i, p in enumerate(parts[1:], 1):
                if _looks_like_path(p):
                    path_idx = i
                    break
            if path_idx:
                params = {"pattern": " ".join(parts[:path_idx]).strip(_QUOTES)}
                params["path"] = parts[path_idx]
                if path_idx + 1 < len(parts):
                    params["glob"] = parts[path_idx + 1]
            else:
                # Whole args is the pattern
                params = {"pattern": args.strip(_QUOTES)}
        result = _tool_search_code(params)
        if result["success"]:
            matches = result["data"]["matches"]
            if HAS_RICH:
                console.print(f"\n[dim]{result['data']['count']} matches for '{result['data']['pattern']}'[/dim]\n")
                for m in matches[:30]:
                    console.print(f"  [dim]{m['file']}:{m['line']}[/dim]  {m['content'][:100]}")
            else:
                print(f"\n{result['data']['count']} matches:")
                for m in matches[:30]:
                    print(f"  {m['file']}:{m['line']}  {m['content'][:100]}")
        else:
            console.print(f"[red]{result['error']}[/red]" if HAS_RICH else result["error"])

    def cmd_changes(self, args: str):
        """List staged file changes."""
        include_closed = "--all" in args.split()
        changes = GLOBAL_CHANGE_STORE.list(include_closed=include_closed)
        if not changes:
            msg = "No staged changes."
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return
        if HAS_RICH:
            console.print()
            for change in changes:
                added = sum(1 for line in change.diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
                removed = sum(1 for line in change.diff.splitlines() if line.startswith("-") and not line.startswith("---"))
                status = "applied" if change.applied else "rejected" if change.rejected else "pending"
                color = "green" if change.applied else "red" if change.rejected else "yellow"
                console.print(f"[{color}]{change.change_id}[/{color}] [bold]{change.path}[/bold] [dim]{status} +{added}/-{removed}[/dim]")
                preview = "\n".join(change.diff.splitlines()[:18])
                if preview:
                    console.print(Syntax(preview, "diff", theme=_SYNTAX_THEME))
            console.print()
        else:
            for change in changes:
                status = "applied" if change.applied else "rejected" if change.rejected else "pending"
                print(f"{change.change_id} {status} {change.path}")
                print("\n".join(change.diff.splitlines()[:18]))

    def cmd_apply_change(self, args: str):
        """Apply a staged file change."""
        change_id = args.strip()
        if not change_id:
            console.print("[dim]Usage: /apply-change <change_id>[/dim]" if HAS_RICH
                          else "Usage: /apply-change <change_id>")
            return
        try:
            change = GLOBAL_CHANGE_STORE.apply(change_id)
            msg = f"Applied change {change.change_id}: {change.path}"
            console.print(f"[green]{msg}[/green]" if HAS_RICH else msg)
        except Exception as exc:
            console.print(f"[red]{exc}[/red]" if HAS_RICH else str(exc))

    def cmd_reject_change(self, args: str):
        """Reject a staged file change."""
        change_id = args.strip()
        if not change_id:
            console.print("[dim]Usage: /reject-change <change_id>[/dim]" if HAS_RICH
                          else "Usage: /reject-change <change_id>")
            return
        try:
            change = GLOBAL_CHANGE_STORE.reject(change_id)
            msg = f"Rejected change {change.change_id}: {change.path}"
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
        except Exception as exc:
            console.print(f"[red]{exc}[/red]" if HAS_RICH else str(exc))

    def cmd_verify(self, args: str):
        """Infer and run focused verification checks."""
        parts = args.split()
        dry_run = "--dry-run" in parts
        paths = [p for p in parts if p != "--dry-run"]
        plan = VerificationPlanner(pathlib.Path.cwd()).infer(paths)
        if not plan.commands:
            msg = "No verification command inferred."
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return
        if HAS_RICH:
            console.print(f"[dim]Verification plan: {plan.reason}[/dim]")
            for idx, command in enumerate(plan.commands, 1):
                console.print(f"  [bold]{idx}.[/bold] {command}")
        else:
            print(f"Verification plan: {plan.reason}")
            for idx, command in enumerate(plan.commands, 1):
                print(f"  {idx}. {command}")
        if dry_run:
            return
        for command in plan.commands:
            result = _tool_run_command({
                "command": command,
                "policy": "balanced",
                "permission_mode": self.terminal.config.get("permission_mode", "workspace-write"),
                "network_enabled": bool(self.terminal.config.get("network_enabled", True)),
                "user_approved": True,
                "timeout": 300,
            })
            if not result.get("success"):
                console.print(f"[red]Verification failed: {command}[/red]" if HAS_RICH else f"Verification failed: {command}")
                console.print(f"[red]{result.get('error', '')}[/red]" if HAS_RICH else result.get("error", ""))
                return
            data = result.get("data", {})
            if data.get("stdout"):
                console.print(Syntax(data["stdout"], "text", theme=_SYNTAX_THEME) if HAS_RICH else data["stdout"])
            if data.get("stderr"):
                console.print(f"[yellow]{data['stderr']}[/yellow]" if HAS_RICH else data["stderr"])
        msg = "Verification passed."
        console.print(f"[green]{msg}[/green]" if HAS_RICH else msg)

    def cmd_run(self, args: str):
        """Run a command: /run <command>"""
        if not args.strip():
            console.print("[dim]Usage: /run [--dry-run] <command>[/dim]" if HAS_RICH
                          else "Usage: /run [--dry-run] <command>")
            return
        text = args.strip()
        dry_run = False
        if text.startswith("--dry-run "):
            dry_run = True
            text = text[len("--dry-run "):].strip()
        if not text:
            console.print("[dim]Usage: /run [--dry-run] <command>[/dim]" if HAS_RICH
                          else "Usage: /run [--dry-run] <command>")
            return

        policy = self.terminal.config.get("command_policy", "safe")
        decision = evaluate_command_policy(
            text,
            policy,
            mode=self.terminal.config.get("permission_mode", "workspace-write"),
            network_enabled=bool(self.terminal.config.get("network_enabled", True)),
        )
        if not dry_run and decision.allowed and decision.risk == "high":
            if not self._confirm_high_risk_command(decision.normalized_command, decision.risk, decision.policy):
                msg = "Cancelled by user."
                console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
                return
        result = _tool_run_command({
            "command": text,
            "policy": policy,
            "permission_mode": self.terminal.config.get("permission_mode", "workspace-write"),
            "network_enabled": bool(self.terminal.config.get("network_enabled", True)),
            "dry_run": dry_run,
        })
        if result["success"]:
            data = result["data"]
            if dry_run:
                msg = (
                    f"Dry run: risk={data.get('risk', '?')} "
                    f"policy={data.get('policy', '?')} "
                    f"approval={data.get('requires_approval', False)} "
                    f"network={data.get('network', False)} "
                    f"command={data.get('command', '')}"
                )
                console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
                return
            if data["stdout"]:
                if HAS_RICH:
                    console.print(Syntax(data["stdout"], "text", theme=_SYNTAX_THEME))
                else:
                    print(data["stdout"])
            if data["stderr"]:
                if HAS_RICH:
                    console.print(f"[red]{data['stderr']}[/red]")
                else:
                    print(data["stderr"], file=sys.stderr)
        else:
            console.print(f"[red]{result['error']}[/red]" if HAS_RICH else result["error"])

    def cmd_apply(self, args: str):
        """Extract code from last AI response and save to file."""
        filename = args.strip()
        last_response = ""
        for msg in reversed(self.terminal.conversation):
            if msg["role"] == "assistant":
                last_response = msg["content"]
                break
        if not last_response:
            console.print("[dim]No AI response to extract from[/dim]" if HAS_RICH
                          else "No response")
            return

        code = _extract_code_block(last_response)
        if not code:
            console.print("[dim]No code block found in last response[/dim]" if HAS_RICH
                          else "No code block found")
            return

        if not filename:
            # Show code preview and ask for filename
            preview = code[:500] + ("..." if len(code) > 500 else "")
            if HAS_RICH:
                console.print(f"\n[dim]Found code block ({len(code.splitlines())} lines):[/dim]")
                console.print(Syntax(preview, "python", theme=_SYNTAX_THEME))
            else:
                print(f"\nFound code ({len(code.splitlines())} lines):")
                print(preview)
            try:
                filename = (console.input("\n[bold]>[/bold] Save to: ") if HAS_RICH
                            else input("\nSave to: ")).strip()
            except (EOFError, KeyboardInterrupt):
                return
            if not filename:
                return

        result = _tool_write_file({"path": filename, "content": code})
        if not result["success"]:
            console.print(f"[red]{result['error']}[/red]" if HAS_RICH else result["error"])

    # ---- Code generation command ----

    async def cmd_code(self, args: str):
        """Generate code and optionally save to file. Usage: /code <description> [--save file.py]"""
        if not args.strip():
            if HAS_RICH:
                console.print("[dim]Usage: /code <description> [--save file.py][/dim]")
                console.print("[dim]Examples:[/dim]")
                console.print("[dim]  /code momentum strategy for AAPL[/dim]")
                console.print("[dim]  /code portfolio optimizer --save optimizer.py[/dim]")
                console.print("[dim]  /code backtest report generator --save report.py[/dim]")
            else:
                print("Usage: /code <description> [--save file.py]")
            return

        # Parse --save flag
        save_path = None
        description = args
        if "--save" in args:
            parts = args.split("--save")
            description = parts[0].strip()
            save_path = parts[1].strip() if len(parts) > 1 else None

        # Build code generation prompt
        prompt = (
            f"Generate complete, production-ready Python code for: {description}\n\n"
            "Requirements:\n"
            "- Include all necessary imports\n"
            "- Add clear inline comments\n"
            "- Include error handling\n"
            "- Use type hints where appropriate\n"
            "- Make it runnable as a standalone script\n\n"
            "Return the code wrapped in ```python``` fences."
        )

        if HAS_RICH:
            console.print(f"[bold]Generating code:[/bold] [bold]{description}[/bold]")
        else:
            print(f"Generating: {description}")

        # Use best available model for code gen
        original_model = self.terminal.config.get("model", "qwen2.5:7b")
        self.terminal.config["model"] = "qwen2.5:7b"

        await self.terminal.send_message(prompt)

        # Restore model
        self.terminal.config["model"] = original_model

        # Extract code from last AI response and save if requested
        if save_path:
            last_response = ""
            for msg in reversed(self.terminal.conversation):
                if msg["role"] == "assistant":
                    last_response = msg["content"]
                    break
            code = _extract_code_block(last_response)
            if code:
                if not save_path.endswith(".py"):
                    save_path += ".py"
                with open(save_path, "w") as f:
                    f.write(code)
                if HAS_RICH:
                    console.print(f"\n[green]Code saved to {save_path}[/green] "
                                  f"[dim]({len(code.splitlines())} lines)[/dim]")
                else:
                    print(f"\nSaved: {save_path} ({len(code.splitlines())} lines)")
            else:
                if HAS_RICH:
                    console.print("[dim]No code block found in response to save[/dim]")
                else:
                    print("No code block found to save")

    # ---- Scaffold command ----
    # ---- Feedback command ----

    async def cmd_feedback(self, args: str):
        """Rate the last AI response and store feedback locally by default.

        Usage: /feedback good|bad [comment]
               /feedback note <comment>
        """
        parts = args.strip().split(maxsplit=1)
        vote = parts[0].lower() if parts else ""
        comment = parts[1].strip() if len(parts) > 1 else ""

        aliases = {
            "good": "positive", "up": "positive", "1": "positive", "+": "positive",
            "bad": "negative", "down": "negative", "0": "negative", "-": "negative",
            "note": "note",
        }
        rating = aliases.get(vote)
        if rating is None or (rating == "note" and not comment):
            console.print("[dim]Usage: /feedback good|bad [comment] | /feedback note <comment>[/dim]" if HAS_RICH
                          else "Usage: /feedback good|bad [comment] | /feedback note <comment>")
            return

        # Find last assistant message and its position
        last_msg = None
        msg_idx = None
        for i in range(len(self.terminal.conversation) - 1, -1, -1):
            if self.terminal.conversation[i]["role"] == "assistant":
                last_msg = self.terminal.conversation[i]["content"][:500]
                msg_idx = i
                break
        if not last_msg:
            console.print("[dim]No AI response to rate[/dim]" if HAS_RICH else "No response to rate")
            return

        settings = PrivacySettings.from_config(self.terminal.config)
        record = FeedbackRecord.create(
            rating=rating,
            message=last_msg,
            comment=comment,
            model=self.terminal.config.get("model", ""),
            session_id=self.terminal.session_id,
            message_index=msg_idx,
            shared=settings.data_sharing and settings.feedback_upload,
        )
        store = FeedbackStore(CONFIG_DIR)

        # Persist locally first. This is the default and works offline.
        try:
            feedback_path = store.append(record)
        except Exception as exc:
            msg = f"Could not save feedback locally: {exc}"
            console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
            return

        # Optional remote upload only after explicit opt-in.
        api_success = False
        upload_attempted = settings.data_sharing and settings.feedback_upload
        if upload_attempted:
            try:
                import aiohttp
                headers = {}
                if self.terminal.config.get("auth_token"):
                    headers["Authorization"] = f"Bearer {self.terminal.config['auth_token']}"
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{self.terminal.api_url}/api/v2/ai/feedback",
                        json=json.loads(record.to_json()),
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        api_success = resp.status in (200, 201, 204)
            except Exception:
                api_success = False

        icon = "↑" if rating == "positive" else ("↓" if rating == "negative" else "note")
        if upload_attempted:
            sync_note = "" if api_success else " [dim](saved locally; upload failed)[/dim]"
        else:
            sync_note = " [dim](saved locally; sharing off)[/dim]"
        if HAS_RICH:
            comment_note = f" — {comment}" if comment else ""
            console.print(f"[green]Feedback {icon}[/green]{comment_note}{sync_note}")
            console.print(f"[dim]Path: {feedback_path}[/dim]")
        else:
            print(f"Feedback {icon}" + (f" — {comment}" if comment else "") +
                  (" (uploaded)" if api_success else " (saved locally)"))

    def cmd_privacy(self, args: str):
        """Manage local privacy and feedback-sharing settings."""
        parts = args.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else "status"
        rest = parts[1].strip() if len(parts) > 1 else ""
        store = FeedbackStore(CONFIG_DIR)
        settings = PrivacySettings.from_config(self.terminal.config)

        def _save_settings(new_settings: PrivacySettings):
            new_settings.apply_to_config(self.terminal.config)
            save_config(self.terminal.config)

        if sub in {"status", "show"}:
            lines = [
                "Privacy status",
                f"  data_sharing: {settings.data_sharing}",
                f"  feedback_upload: {settings.feedback_upload}",
                f"  feedback_records: {store.count()}",
                f"  local_feedback: {store.feedback_file}",
                "  default: local-only; no upload unless data_sharing and feedback_upload are true",
            ]
            if HAS_RICH:
                console.print()
                console.print("[bold]Privacy[/bold]")
                for line in lines[1:]:
                    console.print(f"[dim]{line}[/dim]")
            else:
                print("\n".join(lines))
            return

        if sub in {"opt-in", "on", "enable"}:
            _save_settings(PrivacySettings(data_sharing=True, feedback_upload=True))
            msg = "Data sharing enabled for feedback. Local copies are still kept."
            console.print(f"[green]{msg}[/green]" if HAS_RICH else msg)
            return

        if sub in {"opt-out", "off", "disable"}:
            _save_settings(PrivacySettings(data_sharing=False, feedback_upload=False))
            msg = "Data sharing disabled. Feedback stays local only."
            console.print(f"[green]{msg}[/green]" if HAS_RICH else msg)
            return

        if sub == "export":
            dest = rest or None
            try:
                path = store.export_jsonl(dest)
            except Exception as exc:
                msg = f"Export failed: {exc}"
                console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                return
            msg = f"Exported feedback to {path}"
            console.print(f"[green]{msg}[/green]" if HAS_RICH else msg)
            return

        if sub in {"delete", "clear"}:
            count = store.delete_all()
            msg = f"Deleted {count} local feedback record(s)."
            console.print(f"[green]{msg}[/green]" if HAS_RICH else msg)
            return

        msg = "Usage: /privacy [status|opt-in|opt-out|export [path]|delete]"
        console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)

    # ---- Market data commands (expose unused Aria tools) ----

    async def _run_tool_cmd(self, tool_name: str, params: dict, label: str = ""):
        """Generic helper: run tool with spinner and formatted output.

        Routing priority:
          1. LOCAL_TOOLS (via executor — never blocks event loop)
          2. Remote Aria backend (AWS) — if local not available
          3. Graceful error if both fail
        """
        display = label or tool_name

        # ── 1. Try LOCAL_TOOLS first (run in executor to avoid blocking) ──
        if tool_name in LOCAL_TOOLS:
            handler, _ = LOCAL_TOOLS[tool_name]
            if HAS_RICH:
                with console.status(f"[dim]{display}...[/dim]", spinner="dots"):
                    result = await asyncio.get_event_loop().run_in_executor(
                        None, handler, params
                    )
            else:
                print(f"Running {display}...")
                result = await asyncio.get_event_loop().run_in_executor(
                    None, handler, params
                )
        else:
            # ── 2. Fall through to remote Aria backend ────────────────────
            local_mode = self.terminal.config.get("local_mode", False)
            if local_mode:
                result = {
                    "success": False,
                    "error":   f"Tool '{tool_name}' has no local implementation. "
                               "Run '/local off' to use the Aria backend, or "
                               "add a handler in aria_tools.py.",
                }
            else:
                if HAS_RICH:
                    with console.status(f"[dim]Running {display}...[/dim]", spinner="dots"):
                        result = await execute_aria_tool(self.terminal.api_url, tool_name, params)
                else:
                    print(f"Running {display}...")
                    result = await execute_aria_tool(self.terminal.api_url, tool_name, params)

        if result.get("success"):
            data = result.get("data", {})
            if isinstance(data, dict) and HAS_RICH:
                out = Text()
                for k, v in data.items():
                    if k in ("chart_prices", "raw", "metadata"):
                        continue
                    label_str = k.replace("_", " ").title()
                    val_str = f"{v:.4f}" if isinstance(v, float) else str(v)
                    if isinstance(v, (int, float)):
                        color = "green" if v >= 0 else "red" if v < 0 else ""
                        out.append(f"  {label_str:<20s}", style="dim")
                        out.append(f"{val_str}\n", style=color if color else "")
                    else:
                        out.append(f"  {label_str:<20s}", style="dim")
                        out.append(f"{val_str}\n")
                console.print(out)
            else:
                console.print(f"  [dim]{json.dumps(data, ensure_ascii=False)[:500]}[/dim]" if HAS_RICH
                              else json.dumps(data, ensure_ascii=False)[:500])
        else:
            _print_error(f"Failed: {result.get('error', 'No data')}")

    async def _run_parallel(self, tool_name: str,
                             param_list: list,
                             label_fn=None):
        """Run a tool in parallel for multiple param dicts, display each result."""
        tasks = [
            asyncio.create_task(
                asyncio.get_event_loop().run_in_executor(
                    None, LOCAL_TOOLS[tool_name][0], p
                ) if tool_name in LOCAL_TOOLS
                else execute_aria_tool(self.terminal.api_url, tool_name, p)
            )
            for p in param_list
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for p, r in zip(param_list, results):
            lbl = label_fn(p) if label_fn else tool_name
            if isinstance(r, Exception):
                _print_error(f"{lbl}: {r}")
            else:
                _print_finance_result(tool_name, r)

    async def _fetch_and_display_finance(self, tool_name: str, params: dict, label: str,
                                          mdc_fallback_symbol: str = "") -> bool:
        """Try tool → local finance tool → market_data_client fallback. Returns True if data shown."""
        result = None
        # 1. LOCAL_TOOLS (ccxt / local finance)
        if tool_name in LOCAL_TOOLS:
            fn = LOCAL_TOOLS[tool_name][0]
            result = await asyncio.get_event_loop().run_in_executor(None, fn, params)
        # 2. Remote backend
        if not (result and result.get("success")):
            result = await execute_aria_tool(self.terminal.api_url, tool_name, params)
        # 3. MarketDataClient yfinance fallback
        if not (result and result.get("success")) and _HAS_MDC and mdc_fallback_symbol:
            try:
                mdc = _get_mdc()
                result = mdc.quote(mdc_fallback_symbol)
                if result.get("success"):
                    result["provider"] = "yfinance"
            except Exception:
                pass

        if result and result.get("success"):
            _print_finance_result(tool_name, result)
            # Also show basic price line if _print_finance_result didn't handle this tool
            if tool_name not in ("get_market_data", "get_crypto_data", "get_forex_data"):
                px   = result.get("price", result.get("rate", 0))
                chg  = result.get("change_pct", 0)
                sign = "+" if chg >= 0 else ""
                color = "green" if chg >= 0 else "red"
                prov  = result.get("provider", "")
                if HAS_RICH and px:
                    console.print(f"  [bold]{label:<12}[/bold]  {px}  [{color}]{sign}{chg:.2f}%[/{color}]  [dim]{prov}[/dim]")
            return True
        else:
            err = (result or {}).get("error") or "数据暂不可用"
            if HAS_RICH:
                console.print(f"  [yellow]⚠ {label}: {err}[/yellow]")
            else:
                print(f"  ⚠ {label}: {err}")
            return False

    async def cmd_crypto(self, args: str):
        """Crypto data: /crypto BTC ETH (with yfinance fallback)"""
        symbols = args.upper().split() if args else ["BTC"]
        if HAS_RICH:
            console.print()
        for sym in symbols:
            # yfinance crypto symbol: BTC → BTC-USD, ETH → ETH-USD
            yf_sym = sym + "-USD" if not sym.endswith("-USD") and "/" not in sym else sym
            await self._fetch_and_display_finance(
                "get_crypto_data", {"symbol": sym},
                label=sym, mdc_fallback_symbol=yf_sym
            )
        if HAS_RICH:
            console.print()

    async def cmd_forex(self, args: str):
        """Forex rates: /forex EUR/USD USD/CNY (with yfinance fallback)"""
        pairs = args.upper().split() if args else ["EUR/USD"]
        if HAS_RICH:
            console.print()
        for pair in pairs:
            # yfinance forex symbol: EUR/USD → EURUSD=X
            yf_pair = pair.replace("/", "") + "=X"
            await self._fetch_and_display_finance(
                "get_forex_data", {"pair": pair},
                label=pair, mdc_fallback_symbol=yf_pair
            )
        if HAS_RICH:
            console.print()

    async def cmd_commodity(self, args: str):
        """Commodities: /commodity gold oil silver (parallel fetch)"""
        items = args.lower().split() if args else ["gold"]
        await self._run_parallel(
            "get_commodities_data",
            [{"commodity": c} for c in items],
            label_fn=lambda p: f"commodity {p['commodity']}",
        )

    async def cmd_risk(self, args: str):
        """Risk metrics: /risk AAPL or /risk portfolio"""
        target = args.strip().upper() or "AAPL"
        if target == "PORTFOLIO":
            await self._run_tool_cmd("assess_portfolio_risk", {
                "holdings": self.terminal.config.get("watchlist", ["AAPL", "MSFT"]),
            }, "portfolio risk")
            return

        # Try remote tool; fall back to local get_risk_metrics if backend unavailable
        result = await execute_aria_tool(self.terminal.api_url, "get_risk_metrics", {"symbol": target})
        if result.get("success"):
            data = result.get("data", {})
            if HAS_RICH:
                console.print()
                for k, v in (data.items() if isinstance(data, dict) else {}.items()):
                    val_str = f"{v:.4f}" if isinstance(v, float) else str(v)
                    color = "green" if isinstance(v, float) and v >= 0 else ("red" if isinstance(v, float) and v < 0 else "")
                    console.print(f"  [dim]{k.replace('_',' ').title():<24s}[/dim] [{color}]{val_str}[/{color}]" if color
                                  else f"  [dim]{k.replace('_',' ').title():<24s}[/dim] {val_str}")
                console.print()
        elif "get_risk_metrics" in LOCAL_TOOLS:
            # Local fallback
            local_fn = LOCAL_TOOLS["get_risk_metrics"][0]
            local_result = await asyncio.get_event_loop().run_in_executor(None, local_fn, {"symbol": target})
            if local_result.get("success"):
                data = local_result.get("data", {})
                if HAS_RICH:
                    console.print()
                    console.print(f"  [bold]{target} Risk Metrics[/bold]  [dim](local calculation)[/dim]")
                    console.print()
                    for k, v in (data.items() if isinstance(data, dict) else {}.items()):
                        val_str = f"{v:.4f}" if isinstance(v, float) else str(v)
                        console.print(f"  [dim]{k.replace('_',' ').title():<24s}[/dim] {val_str}")
                    console.print()
                else:
                    print(f"  {target} Risk Metrics (local):")
                    for k, v in (data.items() if isinstance(data, dict) else {}.items()):
                        print(f"  {k}: {v}")
            else:
                console.print(f"[dim]Risk metrics unavailable for {target}: {local_result.get('error','')}[/dim]") if HAS_RICH else print(f"Risk unavailable: {local_result.get('error','')}")
        else:
            msg = f"⚠ 风险指标服务暂不可用 ({result.get('error','')[:60]})"
            console.print(f"[yellow]{msg}[/yellow]") if HAS_RICH else print(msg)

    async def cmd_market(self, args: str):
        """Market overview: /market [indices|sectors]"""
        sub = args.strip().lower()
        if sub == "sectors":
            await self._run_tool_cmd("get_sector_performance", {}, "sector performance")
        else:
            # Try remote tool first; fall back to local MarketDataClient if backend unavailable
            result = await execute_aria_tool(self.terminal.api_url, "get_market_indices", {})
            if result and result.get("success"):
                await self._run_tool_cmd("get_market_indices", {}, "market indices")
            elif _HAS_MDC:
                # Local fallback via MarketDataClient.indices()
                try:
                    mdc = _get_mdc()
                    idx_result = mdc.indices()
                    if idx_result.get("success") and idx_result.get("indices"):
                        if HAS_RICH:
                            console.print()
                            console.print("  [bold]Global Indices[/bold]  [dim](local data)[/dim]")
                            console.print()
                        for name, d in idx_result["indices"].items():
                            price = d.get("price", "N/A")
                            chg   = d.get("change_pct", 0)
                            sign  = "+" if chg >= 0 else ""
                            color = "green" if chg >= 0 else "red"
                            if HAS_RICH:
                                console.print(f"  [dim]{name:<20s}[/dim]  {price:>10}  [{color}]{sign}{chg:.2f}%[/{color}]")
                            else:
                                print(f"  {name:<20s}  {price:>10}  {sign}{chg:.2f}%")
                    else:
                        console.print("[dim]市场数据暂不可用。请检查网络连接。[/dim]") if HAS_RICH else print("Market data unavailable.")
                except Exception as _e:
                    console.print(f"[dim]本地数据获取失败: {_e}[/dim]") if HAS_RICH else print(f"Local data error: {_e}")
            else:
                console.print("[dim]后端不可用，本地数据模块未加载。使用 /indices 命令查看实时行情。[/dim]") if HAS_RICH else print("Backend unavailable. Try /indices.")

    async def cmd_optimize(self, args: str):
        """Optimize portfolio: /optimize [symbols...]"""
        symbols = args.upper().split() if args else self.terminal.config.get("watchlist", ["AAPL", "MSFT", "GOOGL"])
        await self._run_tool_cmd("optimize_positions", {
            "symbols": symbols, "objective": "max_sharpe",
        }, f"optimizing {len(symbols)} positions")

    async def cmd_stress(self, args: str):
        """Stress test: /stress <strategy> [symbol]"""
        parts = args.split() if args else ["momentum", "SPY"]
        strategy = parts[0] if parts else "momentum"
        symbol = parts[1].upper() if len(parts) > 1 else "SPY"
        await self._run_tool_cmd("stress_test_strategy", {
            "strategy": strategy, "symbol": symbol,
        }, f"stress test {strategy}/{symbol}")

    async def cmd_factors(self, args: str):
        """Factor analysis: /factors AAPL"""
        symbol = args.strip().upper() or "AAPL"
        await self._run_tool_cmd("calculate_factors", {"symbol": symbol}, f"factors {symbol}")

    async def cmd_factor_lab(self, args: str):
        """/factor-lab <SYMBOL> [days=252] — 量化因子工作台（动量/波动率/Sharpe/Amihud）"""
        parts  = args.strip().split()
        symbol = parts[0].upper() if parts else "AAPL"
        market = "CN" if any(symbol.startswith(p) for p in ("SH", "SZ", "6", "0", "3")) else "US"

        await self._run_tool_cmd(
            "equity_factor_scores",
            {"symbol": symbol, "period": "1y", "market": market},
            f"factor-lab {symbol}",
        )

    async def cmd_execution(self, args: str):
        """/execution <SYMBOL> <buy|sell> <qty> [algo=compare] [price=0] — 执行算法对比"""
        parts = args.strip().split()
        if len(parts) < 3:
            if HAS_RICH:
                console.print("[dim]Usage: /execution AAPL buy 100000 [algo=compare] [price=180][/dim]")
            return

        symbol    = parts[0].upper()
        side      = parts[1].lower()
        try:
            total_qty = float(parts[2].replace(",", ""))
        except ValueError:
            if HAS_RICH: console.print("[red]qty 必须是数字[/red]")
            return

        algo  = "compare"
        price = 0.0
        for p in parts[3:]:
            if p.startswith("algo="):
                algo = p.split("=", 1)[1]
            elif p.startswith("price="):
                try:
                    price = float(p.split("=", 1)[1])
                except ValueError:
                    pass

        if price <= 0:
            # 尝试从市场数据获取现价
            try:
                import yfinance as yf
                t = yf.Ticker(symbol)
                info = t.fast_info
                price = float(getattr(info, "last_price", 0) or 0)
            except Exception:
                price = 100.0

        await self._run_tool_cmd(
            "execution_schedule",
            {
                "symbol":          symbol,
                "side":            side,
                "total_qty":       total_qty,
                "benchmark_price": price,
                "algo":            algo,
            },
            f"执行计划 {symbol} {side} {total_qty:,.0f}股",
        )

    async def cmd_stat_arb(self, args: str):
        """/stat-arb <SYMBOL_A> <SYMBOL_B> [period=2y] — 配对协整检验 + 当前 z-score"""
        parts = args.strip().split()
        if len(parts) < 2:
            if HAS_RICH:
                console.print("[dim]Usage: /stat-arb GLD SLV [period=2y][/dim]")
            return

        sym_a  = parts[0].upper()
        sym_b  = parts[1].upper()
        period = "2y"
        for p in parts[2:]:
            if p.startswith("period="):
                period = p.split("=", 1)[1]

        await self._run_tool_cmd(
            "pair_stats",
            {"symbol_a": sym_a, "symbol_b": sym_b, "period": period},
            f"配对检验 {sym_a}/{sym_b}",
        )
        # Generate interactive z-score chart
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: _generate_stat_arb_chart(sym_a, sym_b, period)
        )

    async def cmd_compliance(self, args: str):
        """Compliance check: /compliance <strategy>"""
        strategy = args.strip() or "momentum"
        await self._run_tool_cmd("check_strategy_compliance", {
            "strategy": strategy,
        }, f"compliance {strategy}")

    async def cmd_search_web(self, args: str):
        """Web search: /web <query>"""
        query = args.strip()
        if not query:
            console.print("[dim]Usage: /web <search query>[/dim]" if HAS_RICH else "Usage: /web <query>")
            return
        await self._run_tool_cmd("web_search", {"query": query}, f"searching: {query[:30]}")

    # ---- Local mode toggle ----

    def cmd_local(self, args: str):
        """Toggle local-only mode (skip AWS, always use Ollama)."""
        cfg = self.terminal.config
        arg = args.strip().lower()
        if arg in ("on", "1", "true", "yes"):
            cfg["local_mode"] = True
        elif arg in ("off", "0", "false", "no"):
            cfg["local_mode"] = False
        else:
            cfg["local_mode"] = not cfg.get("local_mode", False)
        save_config(cfg)
        state = "ON" if cfg["local_mode"] else "OFF"
        model = cfg.get("model", "qwen2.5:7b")
        if HAS_RICH:
            color = "green" if cfg["local_mode"] else "yellow"
            console.print(f"  [{color}]Local mode {state}[/{color}]  model=[bold]{model}[/bold]  ollama={cfg.get('ollama_url','http://localhost:11434')}")
        else:
            print(f"  Local mode {state}  model={model}")

    # ---- Models list ----

    # ---- MCP server management ----

    async def cmd_mcp(self, args: str):
        """Manage MCP servers: /mcp status | /mcp tools | /mcp reload"""
        if not _HAS_MCP:
            console.print("  [dim]mcp_client.py not available[/dim]" if HAS_RICH else "MCP not available")
            return
        sub = args.strip().lower()
        reg = self.terminal._mcp_registry

        if sub in ("reload", "restart"):
            if reg:
                await reg.stop_all()
            self.terminal._mcp_started = False
            self.terminal._mcp_registry = None
            if HAS_RICH:
                console.print("  [dim]Restarting MCP servers…[/dim]")
            from mcp_client import MCPToolRegistry
            self.terminal._mcp_registry = MCPToolRegistry()
            results = await self.terminal._mcp_registry.start_all()
            n = self.terminal._mcp_registry.register_into(LOCAL_TOOLS, LOCAL_TOOL_SCHEMAS, overwrite=True)
            if HAS_RICH:
                console.print(f"  [green]MCP reloaded: {n} tools from {len(results)} servers[/green]")
            return

        if sub == "tools":
            if not reg:
                console.print("  [dim]No MCP servers running[/dim]" if HAS_RICH else "No MCP servers")
                return
            tools = reg.all_tools()
            if HAS_RICH:
                console.print(f"\n  [bold]MCP Tools[/bold] ({len(tools)} total)\n")
                for t in tools:
                    console.print(f"    [bold]{t['qualified_name']:40s}[/bold][dim]{t.get('description','')[:60]}[/dim]")
                console.print()
            else:
                for t in tools:
                    print(f"  {t['qualified_name']:40s} {t.get('description','')[:60]}")
            return

        # Default: status
        if HAS_RICH:
            console.print()
            console.print("  [bold]MCP Servers[/bold]")
            if not _HAS_MCP:
                console.print("  [dim]Not available (mcp_client.py missing)[/dim]")
            elif not reg:
                config_path = str(MCP_CONFIG_PATH)
                console.print(f"  [dim]No servers started. Configure: {config_path}[/dim]")
                console.print(f"  [dim]Example: add quant_engine MCP server pointing to your mcp_server.py[/dim]")
            else:
                for s in reg.status():
                    color = "green" if s["running"] else "red"
                    icon  = "●" if s["running"] else "○"
                    console.print(
                        f"  [{color}]{icon}[/{color}] [bold]{s['name']:20s}[/bold]"
                        f" [dim]{s['tool_count']} tools  {s['description'][:50]}[/dim]"
                    )
            console.print()
        else:
            if not reg:
                print(f"  No MCP servers. Configure {CONFIG_DIR}/mcp_servers.json")
            else:
                for s in reg.status():
                    print(f"  {'●' if s['running'] else '○'} {s['name']:20s} {s['tool_count']} tools")

    # ---- .ariarc project config ----

    def cmd_ariarc(self, args: str):
        """Show or reload .ariarc project configuration."""
        if not _HAS_ARIARC:
            console.print("  [dim]ariarc.py not available[/dim]" if HAS_RICH else "ariarc not available")
            return
        if "reload" in args.lower():
            arc = reload_ariarc()
            self.terminal.ariarc = arc
            if HAS_RICH:
                if arc.found:
                    console.print(f"  [green]ariarc reloaded: {arc.source_path}[/green]")
                else:
                    console.print("  [yellow]No .ariarc found in current directory tree[/yellow]")
            return

        arc = self.terminal.ariarc or get_ariarc()
        if HAS_RICH:
            console.print()
            if not arc.found:
                console.print("  [dim]No .ariarc found (create .ariarc in your project root)[/dim]")
                console.print()
                _example = """{
  "project": "My Quant Strategy",
  "description": "A-share momentum + mean-reversion strategy",
  "market": "cn",
  "default_symbols": ["sh600519", "sh601318", "sz000858"],
  "system_prompt": "Focus on A-share market mechanics and T+1 constraints.",
  "context_files": ["README.md"],
  "auto_context": ["strategy/main.py"],
  "commands": {
    "/morning-cn": "生成A股早盘简报，重点关注 {default_symbols}"
  }
}"""
                console.print(f"  [dim]Example .ariarc:[/dim]\n{_example}")
            else:
                d = arc.to_dict()
                console.print(f"  [bold]Project:[/bold] {arc.project or '(unnamed)'}")
                console.print(f"  [bold]Source:[/bold]  [dim]{d['source_path']}[/dim]")
                console.print(f"  [bold]Market:[/bold]  {arc.market}")
                if arc.default_symbols:
                    console.print(f"  [bold]Symbols:[/bold] {', '.join(arc.default_symbols)}")
                if arc.commands:
                    console.print(f"  [bold]Commands:[/bold] {', '.join(arc.commands.keys())}")
                if arc.tools_blacklist:
                    console.print(f"  [bold]Blocked tools:[/bold] {', '.join(arc.tools_blacklist)}")
                if arc.auto_context:
                    console.print(f"  [bold]Auto context:[/bold] {', '.join(arc.auto_context)}")
            console.print()
        else:
            if arc.found:
                import json as _j
                print(_j.dumps(arc.to_dict(), indent=2, ensure_ascii=False))

    # ---- Local LLM provider status ----
    # ---- Alibaba Cloud data service config ----
    # ---- AI Signal from cloud ----

    async def cmd_signal(self, args: str):
        """
        AI trading signal (BUY/SELL/HOLD) from Alibaba Cloud.
        Usage: /signal sh600519   /signal AAPL US
        """
        parts  = args.strip().split()
        symbol = parts[0].upper() if parts else "sh600519"
        market = parts[1].upper() if len(parts) > 1 else ("CN" if _is_ashare_symbol(symbol) else "US")
        await self._run_tool_cmd("get_ai_signal", {"symbol": symbol, "market": market},
                                 f"AI signal {symbol}")

    # ---- ML Predictions from cloud ----

    async def cmd_predict(self, args: str):
        """
        ML return predictions for a list of symbols.
        Usage: /predict sh600519 sh601318 sz000858
        """
        parts   = args.strip().split() if args.strip() else ["sh600519"]
        symbols = [s for s in parts if not s.isdigit() or len(s) == 6]
        days    = 5
        for p in parts:
            if p.startswith("d="):
                try:
                    days = int(p[2:])
                except ValueError:
                    pass
        await self._run_tool_cmd("get_predictions",
                                 {"symbols": symbols, "prediction_days": days},
                                 f"ML predict {len(symbols)} stocks")

    # ---- Cloud backtest ----

    async def cmd_cloudbt(self, args: str):
        """
        Full ML-powered backtest on Alibaba Cloud.
        Usage: /cloudbt sh600519 sh601318 [model=lightgbm] [months=12] [freq=weekly] [top=3]
        """
        parts   = args.strip().split() if args.strip() else []
        symbols = []
        kwargs: Dict[str, Any] = {}
        for p in parts:
            if "=" in p:
                k, v = p.split("=", 1)
                kwargs[k] = v
            else:
                symbols.append(p)
        if not symbols:
            symbols = ["sh600519"]
        params = {
            "symbols":        symbols,
            "model_type":     kwargs.get("model", "lightgbm"),
            "months":         int(kwargs.get("months", 12)),
            "rebalance_freq": kwargs.get("freq", "weekly"),
            "top_k":          int(kwargs.get("top", 3)),
        }
        await self._run_tool_cmd("cloud_backtest", params,
                                 f"cloud backtest {len(symbols)} stocks")

    # ---- Market insights ----

    async def cmd_insights(self, args: str):
        """
        AI market insights for a basket of stocks.
        Usage: /insights sh600519 sh601318 sz000858
        """
        parts   = args.strip().split() if args.strip() else ["sh600519"]
        symbols = parts
        await self._run_tool_cmd("get_market_insights",
                                 {"symbols": symbols},
                                 f"market insights {len(symbols)} stocks")

    # ---- Recommend local models ----

    def cmd_recommend(self, args: str):
        """Recommend best local models for financial analysis."""
        if HAS_RICH:
            console.print()
            console.print("  [bold]Recommended Local Models for Finance[/bold]")
            console.print()
            try:
                available = detect_ollama_models(
                    self.terminal.config.get("ollama_url", "http://localhost:11434")
                )
                for rec in RECOMMENDED_FINANCE_MODELS:
                    model_id = rec["model"]
                    installed = any(a.startswith(model_id.split(":")[0]) for a in available)
                    icon  = "[green]●[/green]" if installed else "[dim]○[/dim]"
                    vram  = rec.get("vram_gb", "?")
                    console.print(
                        f"  {icon} [bold]{model_id:30s}[/bold] "
                        f"[dim]VRAM≈{vram}GB  {rec['reason'][:60]}[/dim]"
                    )
                    if not installed:
                        console.print(f"    [dim]Install: {rec['install']}[/dim]")
                console.print()
            except Exception:
                console.print("  [dim]Could not check installed models[/dim]")
        else:
            for rec in RECOMMENDED_FINANCE_MODELS:
                print(f"  {rec['model']:30s} {rec['reason']}")
                print(f"    Install: {rec['install']}")

    # ---- Finance local tool shortcuts ----

    async def cmd_optimize_port(self, args: str):
        """Portfolio weight optimisation."""
        symbols = [s.strip().upper() for s in args.split() if s.strip()]
        if not symbols:
            console.print("  [dim]Usage: /optimize-port AAPL MSFT GOOGL [method=max_sharpe][/dim]" if HAS_RICH
                          else "Usage: /optimize-port AAPL MSFT [method=max_sharpe]")
            return
        # Check if last token is method=X
        method = "max_sharpe"
        if symbols and "=" in symbols[-1]:
            k, v = symbols.pop().split("=", 1)
            if k == "method":
                method = v
        params = {"symbols": symbols, "method": method}
        tool_name = "optimize_positions"
        if tool_name in LOCAL_TOOLS:
            await self._run_local_tool(tool_name, params, f"优化持仓 {method}")
        else:
            await self.terminal.send_message(
                f"帮我用 {method} 方法优化以下资产组合的仓位权重：{', '.join(symbols)}"
            )

    async def _run_local_tool(self, tool_name: str, params: dict, label: str = ""):
        """Run a LOCAL_TOOLS entry, display result with Rich formatting."""
        if tool_name not in LOCAL_TOOLS:
            if HAS_RICH:
                console.print(f"  [dim]Tool {tool_name!r} not available[/dim]")
            return
        handler, _ = LOCAL_TOOLS[tool_name]
        label_text = label or tool_name
        if HAS_RICH:
            with console.status(f"[dim]{label_text}…[/dim]", spinner="dots"):
                result = handler(params)
        else:
            print(f"  {label_text}…")
            result = handler(params)

        if not result.get("success", True):
            err = result.get("error", "unknown error")
            if HAS_RICH:
                console.print(f"  [red]Error:[/red] {err}")
            else:
                print(f"  Error: {err}")
            return

        # Pretty-print result
        _print_tool_result(tool_name, result, elapsed=0)

    # ════════════════════════════════════════════════════════════════════════
    # 金融 Agent 团队命令
    # ════════════════════════════════════════════════════════════════════════
    async def cmd_chart(self, args: str):
        """
        生成股票分析图表（HTML，含K线/均线/RSI/MACD）。
        Usage: /chart AAPL [period]
               /chart 600519 3m   (A股，3个月)
               /chart BTC-USD 2y
        支持 period: 1m 3m 6m 1y 2y 3y 5y ytd max
        """
        _VALID_PERIODS = {"1m","3m","6m","1y","2y","3y","5y","ytd","max",
                          "1mo","3mo","6mo"}
        parts  = args.strip().split()
        period = "1y"
        symbol_parts = []
        for p in parts:
            if p.lower() in _VALID_PERIODS:
                period = p.lower()
            else:
                symbol_parts.append(p.upper())
        symbol = symbol_parts[0] if symbol_parts else "AAPL"

        msg = f"生成 {symbol} 分析图表 ({period})..."
        if HAS_RICH:
            with console.status(f"[dim]{msg}[/dim]", spinner="dots"):
                result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: _generate_chart_sync(symbol, period=period)
                )
        else:
            print(f"  {msg}")
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: _generate_chart_sync(symbol, period=period)
            )

        if result.get("success"):
            path    = result.get("chart_path", "")
            issues  = result.get("review_issues") or []
            sup3    = result.get("support") or []
            res3    = result.get("resistance") or []
            rsi_val = result.get("rsi")
            if HAS_RICH:
                console.print(f"\n  ✅ 图表已生成: [link={path}]{path}[/link]")
                console.print(
                    f"  [dim]趋势: {result.get('trend','—')}  "
                    f"RSI: {f'{rsi_val:.1f}' if rsi_val else '—'}  "
                    f"支撑: {'/'.join(str(v) for v in sup3) or '—'}  "
                    f"阻力: {'/'.join(str(v) for v in res3) or '—'}[/dim]"
                )
                if issues:
                    console.print(f"  [yellow]⚠ 自审发现 {len(issues)} 个问题:[/yellow]")
                    for iss in issues:
                        console.print(f"    [yellow]· {iss}[/yellow]")
                else:
                    console.print("  [green dim]✓ 自审通过（数据质量正常）[/green dim]")
            else:
                print(f"\n  ✅ 图表已生成: {path}")
                print(f"  趋势: {result.get('trend','—')}  RSI: {f'{rsi_val:.1f}' if rsi_val else '—'}")
                if issues:
                    print(f"  ⚠ 自审发现 {len(issues)} 个问题:")
                    for iss in issues:
                        print(f"    · {iss}")
            import subprocess as _sp
            try:
                _sp.Popen(["open", path])
            except Exception:
                pass
        else:
            err = result.get("error") or result.get("response", "未知错误")
            _print_error(f"图表生成失败: {err[:120]}")
    async def cmd_shortterm(self, args: str):
        """
        运行 A股短线分析（日线级别，3-15交易日）并输出报告。
        Usage: /shortterm
               /shortterm 000333 601138 300750
        """
        import subprocess, sys as _sys
        _base = pathlib.Path(__file__).parent.parent.parent / "research" / "shortterm"
        script = _base / "run_shortterm.py"
        if not script.exists():
            _print_error(f"短线分析脚本未找到: {script}")
            return
        codes = args.strip().split()
        cmd   = [_sys.executable, str(script)]
        if codes:
            cmd += ["--code"] + codes
        if HAS_RICH:
            console.print("\n  📊 运行短线分析...\n")
        else:
            print("\n  📊 运行短线分析...\n")
        result = subprocess.run(cmd, text=True, capture_output=False)
        if result.returncode != 0:
            _print_error("短线分析执行失败，请检查 research/shortterm/")

    async def cmd_longterm(self, args: str):
        """
        运行 A股长线分析（月线级别，3-18个月目标）并输出报告。
        Usage: /longterm
               /longterm --quick   (只分析 core 级标的)
               /longterm 600519 000858
        """
        import subprocess, sys as _sys
        _base = pathlib.Path(__file__).parent.parent.parent / "research" / "longterm"
        script = _base / "run_longterm.py"
        if not script.exists():
            _print_error(f"长线分析脚本未找到: {script}")
            return
        parts = args.strip().split()
        cmd   = [_sys.executable, str(script)]
        if "--quick" in parts:
            cmd.append("--quick")
            parts.remove("--quick")
        if parts:
            cmd += ["--code"] + parts
        if HAS_RICH:
            console.print("\n  📈 运行长线分析...\n")
        else:
            print("\n  📈 运行长线分析...\n")
        result = subprocess.run(cmd, text=True, capture_output=False)
        if result.returncode != 0:
            _print_error("长线分析执行失败，请检查 research/longterm/")

    async def cmd_indices(self, args: str):
        """全球主要指数实时行情."""
        if not _HAS_MDC:
            console.print("  [dim]market_data_client 未加载[/dim]" if HAS_RICH else "market_data_client not loaded")
            return
        mdc = _get_mdc()
        if HAS_RICH:
            with console.status("[dim]获取全球指数...[/dim]", spinner="dots"):
                r = mdc.indices()
        else:
            print("  获取全球指数...")
            r = mdc.indices()

        if not r.get("success"):
            err = _clean_tool_error_message(r.get("error", "failed"))
            console.print(f"  [red]{err}[/red]" if HAS_RICH else err)
            return

        if HAS_RICH:
            console.print()
            console.print("  [bold]全球指数行情[/bold]  "
                          f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim]")
            console.print()
            for name, d in r["indices"].items():
                chg = d.get("change_pct", 0)
                color = "green" if chg >= 0 else "red"
                sign  = "+" if chg >= 0 else ""
                console.print(
                    f"  [bold]{name:<14s}[/bold]"
                    f"  {str(d.get('price','')):<12}"
                    f"  [{color}]{sign}{chg:.2f}%[/{color}]"
                )
            console.print()
        else:
            for name, d in r["indices"].items():
                chg = d.get("change_pct", 0)
                sign = "+" if chg >= 0 else ""
                print(f"  {name:<16} {str(d.get('price','')):<12} {sign}{chg:.2f}%")

    async def cmd_hot(self, args: str):
        """热门/活跃股票榜单.  Usage: /hot [cn|us] [top=20]"""
        if not _HAS_MDC:
            console.print("  [dim]market_data_client 未加载[/dim]" if HAS_RICH else "market_data_client not loaded")
            return
        parts  = args.strip().lower().split()
        market = "us" if "us" in parts else "cn"
        top_n  = 20
        for p in parts:
            if p.startswith("top="):
                try: top_n = int(p.split("=")[1])
                except ValueError: pass

        mdc = _get_mdc()
        if HAS_RICH:
            with console.status(f"[dim]获取{market.upper()}热门股...[/dim]", spinner="dots"):
                r = mdc.hot_stocks(market=market, top_n=top_n)
        else:
            r = mdc.hot_stocks(market=market, top_n=top_n)

        if not r.get("success"):
            console.print(f"  [red]{r.get('error','failed')}[/red]" if HAS_RICH else r.get('error'))
            return

        stocks = r.get("stocks", [])
        if HAS_RICH:
            console.print()
            console.print(f"  [bold]{market.upper()} 热门股 Top {len(stocks)}[/bold]  "
                          f"[dim]provider: {r.get('provider','')}[/dim]")
            console.print()
            for i, s in enumerate(stocks, 1):
                sym  = s.get("code") or s.get("symbol","")
                name = s.get("name", sym)
                p    = s.get("price", "-")
                chg  = s.get("change_pct", 0)
                color = "green" if chg >= 0 else "red"
                sign  = "+" if chg >= 0 else ""
                console.print(
                    f"  [dim]{i:2d}.[/dim] [bold]{name:<8s}[/bold] "
                    f"[dim]{sym:<8s}[/dim] {str(p):<8} "
                    f"[{color}]{sign}{chg:.2f}%[/{color}]"
                )
            console.print()
        else:
            for s in stocks:
                sym = s.get("code") or s.get("symbol","")
                print(f"  {s.get('name',sym):<10} {sym:<8} {s.get('price','-'):<8} {s.get('change_pct',0):+.2f}%")

    async def cmd_ta(self, args: str):
        """技术指标分析.  Usage: /ta NVDA [days=120]"""
        parsed = parse_technical_args(args)
        symbol = parsed.symbol
        days = parsed.days

        service_result = None
        if HAS_RICH:
            with console.status(f"[dim]计算 {symbol} 技术指标...[/dim]", spinner="dots"):
                from packages.aria_services.data import DataService
                service_result = DataService().technical_indicators(symbol, days=days)
        else:
            from packages.aria_services.data import DataService
            service_result = DataService().technical_indicators(symbol, days=days)
        if not service_result or not service_result.success:
            _ta_warns = (service_result.warnings or []) if service_result else []
            _ta_errs  = (service_result.errors   or []) if service_result else []
            _ta_data  = (service_result.data or {})    if service_result else {}
            _missing  = ", ".join(service_result.missing_fields) if service_result else ""
            _all_msgs = " ".join(_ta_warns + _ta_errs).lower()
            # Show current price when we have partial data (e.g. new IPO with 1 bar)
            _price_line = ""
            if _ta_data.get("price"):
                _price_line = f"  当前价格  [bold]{_display_value(_ta_data['price'])}[/bold]"
                if _ta_data.get("history_bars"):
                    _price_line += f"  [dim]({_ta_data['history_bars']} 个交易日数据)[/dim]"
                _price_line += "\n"
            if "数据不足" in _all_msgs or "新上市" in _all_msgs:
                _reason = f"[yellow]历史数据不足[/yellow] — {symbol} 上市时间较短（< 14 个交易日），TA 指标无法计算\n  [dim]可待更多交易日积累后重试，或运行 `/analyze {symbol}` 查看基本面[/dim]"
            elif "rate" in _all_msgs or "429" in _all_msgs or "too many" in _all_msgs:
                _reason = f"[yellow]数据源频率限制[/yellow] — 稍后重试，或用 `/apikey set finnhub <KEY>` 切换数据源"
            else:
                _err = "; ".join(_ta_errs or _ta_warns) or "数据源暂时不可用"
                _reason = f"[red]{_err[:120]}[/red]"
                if _missing:
                    _reason += f"  [dim]missing: {_missing}[/dim]"
            if HAS_RICH:
                if _price_line:
                    console.print(f"\n{_price_line}")
                console.print(f"  {_reason}\n")
            else:
                import re as _re
                print(f"\n  {_re.sub(r'[[/].*?]', '', _price_line + _reason)}\n")
            return

        print_ta_result(
            console=console,
            has_rich=HAS_RICH,
            symbol=symbol,
            days=days,
            service_result=service_result,
            formatter=_display_value,
        )

    # ════════════════════════════════════════════════════════════════════════
    # 策略金库命令
    # ════════════════════════════════════════════════════════════════════════
    def _extract_last_code(self) -> str:
        """从对话历史中提取最后一段 Python 代码块."""
        import re
        for msg in reversed(self.terminal.conversation):
            content = msg.get("content", "")
            # Match ```python ... ``` blocks
            matches = re.findall(r"```(?:python)?\n(.*?)```", content, re.DOTALL)
            if matches:
                # Return the longest code block
                return max(matches, key=len)
        return ""

    # ---- ORCL analysis ----

    async def cmd_orcl(self, args: str):
        """Oracle Corporation (ORCL) analysis."""
        deep = "deep" in args.lower()
        if deep:
            prompt = (
                "Perform a comprehensive multi-factor analysis of Oracle Corporation (ORCL):\n"
                "1. Technical: trend, RSI, MACD, key support/resistance levels\n"
                "2. Fundamental: revenue growth, cloud transition progress, margins, PE vs peers (MSFT, SAP, NOW)\n"
                "3. Competitive: OCI vs AWS/Azure/GCP market share, Autonomous DB moat\n"
                "4. AI angle: Oracle's AI infrastructure deals (NVIDIA partnership, xAI, OpenAI cloud)\n"
                "5. Risks: debt load from cloud capex, Cerner integration, FX exposure\n"
                "6. Verdict: Bull/Bear/Neutral with price target and conviction level"
            )
        else:
            prompt = (
                "Give me a quick snapshot of Oracle (ORCL):\n"
                "1. Current price, YTD performance vs S&P500\n"
                "2. Key metrics: PE, forward PE, revenue growth, cloud ARR\n"
                "3. Recent news and catalysts\n"
                "4. Technical signal: Buy/Hold/Sell\n"
                "5. One-line thesis"
            )
        await self.terminal._handle_ai_message(prompt)

    async def cmd_macro(self, args: str):
        """
        宏观经济指标查询（FRED / 世界银行 / macro_tools）。
        Usage: /macro                   — 美国宏观全览（macro_tools）
               /macro us|cn|rates       — 分地区宏观面板
               /macro US10Y             — 10年美债收益率（FRED）
               /macro CPI 3y            — CPI通胀历史3年
               /macro CN:GDP            — 中国GDP（世界银行）
        支持别名: US10Y US2Y FEDFUNDS CPI PCE GDP UNRATE NFP SP500 VIX M2
                  USDCNY USDEUR MORTGAGE HOUSING WILSHIRE
        """
        parts = args.strip().split()

        # Legacy sub-command routing: us / cn / rates / calendar / (empty)
        _LEGACY_SUBS = {"us", "cn", "rates", "calendar", "all"}
        if not parts or (parts and parts[0].lower() in _LEGACY_SUBS):
            # delegate to old macro_tools behavior
            try:
                from macro_tools import get_us_macro, get_cn_macro, get_central_bank_rates
                region    = parts[0].lower() if parts else "all"
                indicator = parts[1] if len(parts) > 1 else "all"
                loop = asyncio.get_event_loop()
                if region in ("us", "all"):
                    with console.status("[dim]获取美国宏观数据...[/dim]", spinner="dots") if HAS_RICH else _null_ctx():
                        r = await loop.run_in_executor(None, lambda: get_us_macro(indicator if region == "us" else "all"))
                    _render_macro_result(r, "🇺🇸 美国宏观")
                if region in ("cn", "all"):
                    with console.status("[dim]获取中国宏观数据...[/dim]", spinner="dots") if HAS_RICH else _null_ctx():
                        r_cn = await loop.run_in_executor(None, lambda: get_cn_macro("all"))
                    _render_macro_result(r_cn, "🇨🇳 中国宏观")
                if region in ("rates", "all"):
                    with console.status("[dim]获取央行利率...[/dim]", spinner="dots") if HAS_RICH else _null_ctx():
                        r_rates = await loop.run_in_executor(None, get_central_bank_rates)
                    _render_cb_rates(r_rates)
            except ImportError:
                console.print("  [dim]macro_tools 未找到，请用 /macro US10Y 查询具体指标[/dim]" if HAS_RICH
                             else "Use /macro US10Y for specific indicators")
            return

        if not parts:
            if HAS_RICH:
                console.print(
                    "  [dim]用法: /macro US10Y | /macro CPI 3y | /macro CN:GDP\n"
                    "  可用: US10Y US2Y FEDFUNDS CPI GDP UNRATE NFP SP500 VIX "
                    "M2 USDCNY USDEUR MORTGAGE[/dim]"
                )
            return

        indicator = parts[0].upper()
        period    = parts[1] if len(parts) > 1 else "1y"
        country   = "WLD"
        if ":" in indicator:
            country, indicator = indicator.split(":", 1)

        # Convert period to days
        _PERIOD_DAYS = {"1m":30,"3m":90,"6m":180,"1y":365,"2y":730,
                        "3y":1095,"5y":1825,"10y":3650,"max":365*30}
        days = _PERIOD_DAYS.get(period.lower(), 365)

        if HAS_RICH:
            with console.status(f"[dim]获取宏观数据 {indicator}...[/dim]", spinner="dots"):
                result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: _fetch_macro_data(indicator, country, days)
                )
        else:
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: _fetch_macro_data(indicator, country, days)
            )

        if not result:
            _print_error(f"无法获取 {indicator} 数据，请检查指标代码或网络连接")
            return

        if HAS_RICH:
            from rich.table import Table
            from rich import box as rich_box
            table = Table(
                title=f"[bold]{indicator}[/bold]  [{country}]  最近 {min(10, len(result))} 期",
                box=rich_box.SIMPLE, show_header=True,
                header_style="bold dim",
            )
            table.add_column("日期", style="dim", width=12)
            table.add_column("数值", justify="right")
            table.add_column("环比", justify="right", style="dim")
            vals = [(str(d), float(v)) for d, v in result[-20:]]
            for i, (d, v) in enumerate(reversed(vals[-10:])):
                prev_v = vals[-(i+2)][1] if i+1 < len(vals) else v
                chg    = v - prev_v
                chg_str = f"[green]+{chg:.3f}[/green]" if chg > 0 else (
                          f"[red]{chg:.3f}[/red]" if chg < 0 else "─")
                table.add_row(d, f"{v:,.4f}", chg_str)
            console.print(table)
        else:
            for d, v in result[-10:]:
                print(f"  {d}  {v}")

    async def cmd_edgar(self, args: str):
        """
        SEC EDGAR 美国上市公司财报与披露查询（完全免费）。
        Usage: /edgar AAPL              — 最近财报列表
               /edgar MSFT filings      — 10-K/10-Q 提交记录
               /edgar TSLA facts        — 财务事实（收入/利润历史）
               /edgar NVDA insider      — 内幕交易披露 (Form 4)
        """
        parts = args.strip().split()
        if not parts:
            console.print("  [dim]Usage: /edgar SYMBOL [filings|facts|insider][/dim]" if HAS_RICH
                         else "Usage: /edgar SYMBOL [filings|facts|insider]")
            return

        symbol = parts[0].upper()
        sub    = parts[1].lower() if len(parts) > 1 else "filings"

        if HAS_RICH:
            with console.status(f"[dim]查询 EDGAR {symbol}...[/dim]", spinner="dots"):
                result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: _fetch_edgar_data(symbol, sub)
                )
        else:
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: _fetch_edgar_data(symbol, sub)
            )

        if not result:
            _print_error(f"未找到 {symbol} 的 EDGAR 数据")
            return

        if HAS_RICH:
            from rich.table import Table
            from rich import box as rich_box
            if sub == "filings":
                table = Table(title=f"[bold]{symbol}[/bold] SEC 财报提交",
                              box=rich_box.SIMPLE, header_style="bold dim")
                table.add_column("类型", width=6)
                table.add_column("日期", width=12)
                table.add_column("链接", style="dim")
                for f in result[:10]:
                    table.add_row(f.get("form",""), f.get("date",""), f.get("url","")[:60])
                console.print(table)
            elif sub == "facts":
                console.print(f"  [bold]{symbol}[/bold] 财务摘要:")
                for metric, entries in result.get("metrics", {}).items():
                    if entries:
                        latest = entries[0]
                        console.print(f"  [dim]{metric}[/dim]  {latest.get('val',0):,.0f}  ({latest.get('end','')})")
            elif sub == "insider":
                console.print(f"  [bold]{symbol}[/bold] 近期内幕交易 ({len(result)} 条):")
                for f in result[:10]:
                    console.print(f"  [dim]{f.get('date','')}[/dim]  Form 4")
        else:
            print(f"  {symbol} EDGAR 数据: {len(result) if isinstance(result, list) else 'OK'}")

    async def cmd_datasource(self, args: str):
        """
        数据源管理：查看已配置的数据源及其状态。
        Usage: /datasource             — 列出所有数据源
               /datasource test FRED   — 测试指定数据源连通性
               /datasource config      — 显示配置文件路径
        """
        sub = args.strip().lower()

        if sub.startswith("test "):
            src_name = sub[5:].strip()
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: _test_datasource(src_name)
            )
            return

        if sub == "config":
            paths = [
                "~/.aria/datasources.yaml",
                "~/.aria/.env",
                str(CONFIG_DIR / "providers.json"),
            ]
            if HAS_RICH:
                console.print("  [bold]数据源配置文件:[/bold]")
                for p in paths:
                    import pathlib
                    full = pathlib.Path(p).expanduser()
                    exists = "[green]✓[/green]" if full.exists() else "[dim]✗ (未创建)[/dim]"
                    console.print(f"  {exists}  [dim]{p}[/dim]")
                console.print("\n  [dim]环境变量: TUSHARE_TOKEN FRED_API_KEY ALPHA_VANTAGE_KEY[/dim]")
            return

        # Default: list all sources
        try:
            from datasources.router import _SOURCE_REGISTRY, DataRouter
            router = DataRouter()
        except ImportError:
            _print_error("datasources 模块未找到")
            return

        if HAS_RICH:
            from rich.table import Table
            from rich import box as rich_box
            table = Table(title="数据源状态", box=rich_box.SIMPLE, header_style="bold dim")
            table.add_column("名称", width=16)
            table.add_column("市场", width=20)
            table.add_column("需要Key", width=8)
            table.add_column("状态", width=8)
            table.add_column("说明")
            _DESC = {
                "yfinance":      "Yahoo Finance (免费)",
                "akshare":       "AkShare A股 (免费)",
                "tushare":       "Tushare Pro (需Token)",
                "fred":          "美联储经济数据 (免费)",
                "edgar":         "SEC EDGAR 财报 (免费)",
                "alpha_vantage": "Alpha Vantage (免费Key)",
                "world_bank":    "世界银行 (免费)",
            }
            for name, cls in _SOURCE_REGISTRY.items():
                try:
                    src = cls()
                    configured = src.is_configured()
                    status = "[green]✓ 就绪[/green]" if configured else "[dim]✗ 未配置[/dim]"
                    needs_key = "是" if cls.requires_key else "否"
                    markets = ", ".join(getattr(cls, "markets", []))
                except Exception:
                    status, needs_key, markets = "[red]错误[/red]", "?", "?"
                table.add_row(name, markets, needs_key, status, _DESC.get(name, ""))
            console.print(table)
            console.print("  [dim]/datasource config — 配置文件路径[/dim]")
        else:
            for name, cls in _SOURCE_REGISTRY.items():
                src = cls()
                print(f"  {name}: {'ready' if src.is_configured() else 'not configured'}")

    # ---- News command ----
    # ── /file 多格式文件分析命令 ──────────────────────────────────────────────
    # ── /project — Claude Code style project folder analysis ─────────────────
    # ---- Vision / image input command ----

    def cmd_vision(self, args: str):
        """Load an image for visual analysis in the next message: /vision <path>"""
        from pathlib import Path as _Path
        import base64 as _b64

        # Check that the current model supports vision before loading
        _curr_model = self.terminal.config.get("model", "")
        if _curr_model and _HAS_MODEL_CAP:
            _vcap = get_model_capability(_curr_model)
            if not _vcap.vision:
                _warn = (
                    f"[yellow]⚠[/yellow]  当前模型 [bold]{_curr_model}[/bold] 不支持图片输入。\n"
                    f"[dim]支持视觉的模型：llama3.2:11b · gemma3 · llava · qwen2-vl · moondream[/dim]"
                )
                if HAS_RICH:
                    console.print(Panel(_warn, border_style="yellow", box=rich_box.ROUNDED, padding=(0, 1)))
                else:
                    print(f"Warning: model {_curr_model} does not support vision input.")
                return

        path_str = args.strip().strip("\"'")
        if not path_str:
            msg = "Usage: /vision <image_path>  (e.g. /vision ~/Desktop/chart.png)"
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return

        path = _Path(path_str).expanduser().resolve()
        if not path.exists():
            _print_error(f"File not found: {path}", "vision")
            return

        suffix = path.suffix.lstrip(".").lower()
        mime_map = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "webp": "image/webp",
        }
        mime = mime_map.get(suffix)
        if not mime:
            _print_error(
                f"Unsupported image type: .{suffix}",
                "vision — supported: .png .jpg .jpeg .gif .webp",
            )
            return

        try:
            data = _b64.b64encode(path.read_bytes()).decode()
        except OSError as e:
            _print_error(f"Cannot read image: {e}", "vision")
            return

        self.terminal._pending_image = {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{data}"},
        }
        size_kb = path.stat().st_size // 1024
        if HAS_RICH:
            console.print(Panel(
                f"[green]✓[/green] [dim]{path.name}[/dim]  [dim]{size_kb} KB · {mime}[/dim]\n"
                f"[dim]Image queued — ask your question now[/dim]",
                border_style="dim",
                box=rich_box.ROUNDED,
                padding=(0, 1),
            ))
        else:
            print(f"Image loaded: {path.name} ({size_kb} KB) — send your question now")

    # ---- Browser command ----

    async def cmd_browser(self, args: str):
        """Open a URL in a headless browser.
        Usage:
          /browser <url>              — fetch page text + links
          /browser screenshot <url>  — capture visual screenshot + queue for vision
        """
        if not _HAS_COMPUTER_USE:
            _print_error(
                "computer_use_tools not available.",
                "Install: pip install playwright mss pyautogui pillow && playwright install chromium",
            )
            return
        from computer_use_tools import _tool_browser_navigate, _tool_browser_screenshot

        parts = args.strip().split(maxsplit=1)
        if not parts:
            if HAS_RICH:
                console.print("[dim]Usage: /browser <url>  or  /browser screenshot <url>[/dim]")
            return

        if parts[0].lower() == "screenshot" and len(parts) > 1:
            url = parts[1].strip()
            if HAS_RICH:
                with console.status(f"[dim]Screenshotting {url[:60]}…[/dim]", spinner="dots"):
                    result = _tool_browser_screenshot({"url": url})
            else:
                result = _tool_browser_screenshot({"url": url})
            if result.get("success"):
                d = result["data"]
                # Set pending image so next question sees the screenshot
                from computer_use_tools import pop_pending_vision_image
                b64 = pop_pending_vision_image()
                if b64:
                    self.terminal._pending_image = {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    }
                if HAS_RICH:
                    console.print(Panel(
                        f"[green]✓[/green]  [bold]{d.get('title','')[:60]}[/bold]\n"
                        f"[dim]{url}  ·  {d.get('size_kb', 0)} KB[/dim]\n"
                        f"[dim]Screenshot queued — ask your question now[/dim]",
                        border_style="dim", box=rich_box.ROUNDED, padding=(0, 1),
                    ))
                else:
                    print(f"Screenshot ready ({d.get('size_kb', 0)} KB) — send your question")
            else:
                _print_error(result.get("error", "Screenshot failed"), "browser screenshot")
        else:
            url = parts[0].strip()
            if HAS_RICH:
                with console.status(f"[dim]Opening {url[:60]}…[/dim]", spinner="dots"):
                    result = _tool_browser_navigate({"url": url})
            else:
                result = _tool_browser_navigate({"url": url})
            if result.get("success"):
                d = result["data"]
                title = d.get("title", "")
                text = d.get("text", "")[:2000]
                links = d.get("links", [])[:5]
                engine = d.get("engine", "")
                if HAS_RICH:
                    link_str = "\n".join(f"  {l}" for l in links) if links else "  (none)"
                    console.print(Panel(
                        f"[bold]{title[:80]}[/bold]  [dim]({engine})[/dim]\n\n"
                        f"{text}\n\n[dim]Links:[/dim]\n{link_str}",
                        border_style="dim", box=rich_box.ROUNDED, padding=(0, 1),
                        title=f"[dim]{url[:60]}[/dim]", title_align="left",
                    ))
                else:
                    print(f"Title: {title}\n{text[:500]}")
            else:
                _print_error(result.get("error", "Navigation failed"), "browser")

    # ---- Screenshot command ----

    async def cmd_screenshot(self, args: str):
        """Capture desktop screenshot and queue for vision analysis.
        Usage: /screenshot [monitor_number]
        """
        if not _HAS_COMPUTER_USE:
            _print_error(
                "computer_use_tools not available.",
                "Install: pip install mss pillow",
            )
            return
        from computer_use_tools import _tool_computer_screenshot, pop_pending_vision_image

        monitor = int(args.strip()) if args.strip().isdigit() else 1
        if HAS_RICH:
            with console.status("[dim]Capturing screen…[/dim]", spinner="dots"):
                result = _tool_computer_screenshot({"monitor": monitor})
        else:
            result = _tool_computer_screenshot({"monitor": monitor})

        if result.get("success"):
            d = result["data"]
            b64 = pop_pending_vision_image()
            if b64:
                self.terminal._pending_image = {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                }
            if HAS_RICH:
                console.print(Panel(
                    f"[green]✓[/green]  [dim]{d['width']}×{d['height']}  ·  {d['size_kb']} KB[/dim]\n"
                    f"[dim]Screenshot queued — ask your question now[/dim]",
                    border_style="dim", box=rich_box.ROUNDED, padding=(0, 1),
                ))
            else:
                print(f"Screenshot {d['width']}×{d['height']} ({d['size_kb']} KB) — send your question")
        else:
            _print_error(result.get("error", "Screenshot failed"), "screenshot")

    # ---- Config command ----

    def cmd_input(self, args: str):
        """Configure the interactive input UI."""
        raw = args.strip().lower()
        cfg = self.terminal.config
        valid_styles = {"panel", "box", "plain"}
        valid_themes = {"auto", "dark", "light"}

        def _save_and_show(message: str) -> None:
            save_config(cfg)
            if HAS_RICH:
                console.print(f"[green]✓[/green] {message}")
                console.print(
                    f"  [dim]style[/dim] {cfg.get('input_style', 'panel')}  "
                    f"[dim]theme[/dim] {cfg.get('input_theme', 'auto')}"
                )
            else:
                print(message)
                print(f"  style {cfg.get('input_style', 'panel')}  theme {cfg.get('input_theme', 'auto')}")

        if not raw or raw in {"status", "show"}:
            style = cfg.get("input_style", "panel")
            theme = cfg.get("input_theme", "auto")
            if HAS_RICH:
                console.print(Panel(
                    f"[bold]style[/bold]  {style}\n"
                    f"[bold]theme[/bold]  {theme}\n\n"
                    "[dim]Use[/dim] /input panel [dim]for the Codex-style input block[/dim]\n"
                    "[dim]Use[/dim] /input theme auto [dim]to follow the terminal/system theme[/dim]",
                    title="Input UI",
                    border_style="dim",
                    box=rich_box.ROUNDED,
                    padding=(0, 1),
                ))
            else:
                print(f"input style: {style}")
                print(f"input theme: {theme}")
                print("Usage: /input panel|box|plain | /input theme auto|dark|light")
            return

        if raw == "reset":
            cfg["input_style"] = "panel"
            cfg["input_theme"] = "auto"
            _save_and_show("input UI reset to panel · auto")
            return

        parts = raw.split()
        if parts[0] == "theme":
            if len(parts) != 2 or parts[1] not in valid_themes:
                msg = "Usage: /input theme auto|dark|light"
                console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                return
            cfg["input_theme"] = parts[1]
            _save_and_show(f"input theme set to {parts[1]}")
            return

        if parts[0] in valid_themes and len(parts) == 1:
            cfg["input_theme"] = parts[0]
            _save_and_show(f"input theme set to {parts[0]}")
            return

        if parts[0] in valid_styles and len(parts) == 1:
            cfg["input_style"] = parts[0]
            _save_and_show(f"input style set to {parts[0]}")
            return

        msg = "Usage: /input panel|box|plain | /input theme auto|dark|light | /input reset"
        console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
    # ---- Context command ----

    def cmd_context(self, args: str):
        """Show current AI context: model, conversation length, token usage, project context."""
        cfg = self.terminal.config
        conv = self.terminal.conversation
        conv_len = len(conv)
        model_id = cfg.get("model", "qwen2.5:7b")
        thinking = cfg.get("thinking_mode", "auto")
        has_auth = bool(cfg.get("auth_token"))
        local_mode = cfg.get("local_mode", False)

        # Rough token estimate: ~3 chars per token
        total_chars = sum(len(m.get("content", "")) for m in conv)
        est_tokens = total_chars // 3
        max_ctx = get_model_cfg(model_id).get("num_ctx", 16384)
        ctx_pct = min(100, int(est_tokens / max_ctx * 100))
        ctx_color = "green" if ctx_pct < 60 else ("yellow" if ctx_pct < 85 else "red")

        if HAS_RICH:
            console.print()
            console.print("[bold]Current Context[/bold]")
            console.print()
            console.print(f"  [dim]{'Model':<20s}[/dim]{model_id}")
            console.print(f"  [dim]{'Provider':<20s}[/dim]{'[green]Local (Ollama)[/green]' if local_mode else 'AWS → Ollama fallback'}")
            console.print(f"  [dim]{'Thinking':<20s}[/dim]{thinking}")
            console.print(f"  [dim]{'Messages':<20s}[/dim]{conv_len}")
            console.print(f"  [dim]{'Est. tokens':<20s}[/dim][{ctx_color}]{est_tokens:,} / {max_ctx:,} ({ctx_pct}%)[/{ctx_color}]")
            console.print(f"  [dim]{'Authenticated':<20s}[/dim]{'yes' if has_auth else 'no'}")
            console.print(f"  [dim]{'Session':<20s}[/dim]{self.terminal.session_id}")
            console.print(f"  [dim]{'Project context':<20s}[/dim]{'loaded' if _PROJECT_CONTEXT else 'none'}")
            wl = cfg.get("watchlist", [])
            if wl:
                console.print(f"  [dim]{'Watchlist':<20s}[/dim]{', '.join(wl)}")
            if ctx_pct >= 80:
                console.print(f"\n  [yellow]⚠ Context {ctx_pct}% full — use /compact to free space[/yellow]")
            console.print()
        else:
            print(f"  Model: {model_id}  ({'local' if local_mode else 'aws'})")
            print(f"  Messages: {conv_len}  Tokens: ~{est_tokens:,}/{max_ctx:,} ({ctx_pct}%)")
            print(f"  Session: {self.terminal.session_id}")


# ── 经营权共创平台：Agent 输出辅助函数（模块级，SlashCommands 内外均可用）────────────


def _print_realty_result(result, agent_name: str):
    """格式化打印 realty Agent 结果"""
    _SIGNAL_LABELS = {
        "BUY": "[green]推荐/正常[/green]",
        "STRONG_BUY": "[bold green]强烈推荐[/bold green]",
        "HOLD": "[yellow]需观察[/yellow]",
        "SELL": "[red]警示[/red]",
        "STRONG_SELL": "[bold red]极高风险[/bold red]",
    }
    if not HAS_RICH:
        print(f"\n[{agent_name}] Signal: {result.signal}  Confidence: {result.confidence:.0%}")
        print(result.analysis)
        return

    console.print()
    console.print(f"  [bold]{agent_name.upper().replace('_',' ')}[/bold]"
                  f"  {_SIGNAL_LABELS.get(result.signal, result.signal)}"
                  f"  [dim]置信度 {result.confidence:.0%}[/dim]")
    console.print()
    for pt in (result.key_points or []):
        console.print(f"    • {pt}")
    if result.analysis:
        console.print()
        text = result.analysis[:1200] + ("…" if len(result.analysis) > 1200 else "")
        console.print(f"  [dim]{text}[/dim]")
    console.print()


def _print_risk_scan(data: dict):
    """格式化打印风险扫描结果"""
    if not HAS_RICH:
        print(f"Risk scan: {data.get('overall_level','?')} "
              f"(score={data.get('risk_score',0)})")
        for alert in data.get("alerts", []):
            print(f"  [{alert['level']}] {alert['desc']}")
        return

    level = data.get("overall_level", "未知")
    score = data.get("risk_score", 0)
    color = {"低": "green", "中": "yellow", "高": "red", "极高": "bold red"}.get(level, "white")
    console.print()
    console.print(f"  风险等级: [{color}]{level}[/{color}]  "
                  f"风险分值: {score}  "
                  f"预警项: {data.get('alert_count',0)}")
    console.print()
    for alert in data.get("alerts", []):
        ac = {"低": "dim", "中": "yellow", "高": "red", "极高": "bold red"}.get(
            alert["level"], "white")
        console.print(f"    [{ac}][{alert['level']}][/{ac}] {alert['desc']}")
    if data.get("suggestion"):
        console.print(f"\n  [dim]建议: {data['suggestion']}[/dim]")
    console.print()


def _p(msg: str, style: str = ""):
    """快速打印辅助（rich 可用时带样式）"""
    if HAS_RICH:
        tag = {"dim": "dim", "error": "red", "ok": "green"}.get(style, style)
        console.print(f"[{tag}]{msg}[/{tag}]" if tag else msg)
    else:
        print(msg)


# ============================================================================
# Main Terminal — Claude Code-like REPL
# ============================================================================

class ArtheraTerminal:
    """Interactive REPL inspired by Claude Code CLI."""

    def __init__(self, config: dict):
        self.config = config
        _sync_write_policy(config)  # ensure module-level policy matches loaded config
        self.api_url = config.get("api_url", DEFAULT_CONFIG["api_url"])
        self.conversation: List[dict] = []
        self.running = True
        self.session_id = config.get("last_session_id") or str(uuid.uuid4())[:8]
        self.session_mgr = SessionManager()
        # JSONL session store: crash-safe, append-per-turn
        try:
            from apps.cli.session_jsonl import JsonlSessionStore
            self._jsonl_store: Optional[Any] = JsonlSessionStore()
            self._jsonl_store.init_session(self.session_id)
        except Exception:
            self._jsonl_store = None
        # Fire SessionStart hook
        if _HAS_JSON_HOOKS:
            try:
                _fire_json_hook("SessionStart", session_id=self.session_id, hooks=_JSON_HOOKS)
            except Exception:
                pass
        _run_event_hook("session_start", {"ARIA_SESSION": self.session_id})
        # Refresh project context at session start (pick up ARIA.md changes)
        _refresh_project_context()
        self.pending_plan: List[str] = []
        self.last_plan_results: List[dict] = []
        self.runtime_trace = RuntimeTrace()
        self.tool_executor = ToolExecutor(
            LOCAL_TOOLS,
            hook=_run_hook,
            trace=self.runtime_trace,
            config=self.config,
        )
        self.cancel_event: Optional[asyncio.Event] = None
        self._streaming = False
        self._last_provider = ""   # last successful provider ("" = no message sent yet)
        self._actual_model: Optional[str] = None  # actual Ollama model in use (may differ from config)
        self._ollama_alive = False                # set by print_header / health check
        self._installed_models: set = set()       # installed Ollama models (from header detection)
        self._auto_healed_from: Optional[str] = None  # original model if auto-paired at startup

        # ── Session-level telemetry (like Claude Code's /cost) ──────────
        import time as _time_mod
        self._session_start: float = _time_mod.time()
        self._session_input_tokens: int = 0   # prompt tokens this session
        self._session_output_tokens: int = 0  # completion tokens this session
        self._session_thinking_tokens: int = 0
        self._session_turns: int = 0           # number of exchange pairs
        self._last_response: str = ""          # last assistant message text (for /copy)
        self._forks: List[dict] = []           # forked conversation snapshots
        self._pending_image: Optional[dict] = None  # pending vision content block
        # ── Multi-file analysis session ──────────────────────────────────────
        try:
            from file_analysis_tools import FileSession
            self._file_session: Optional[Any] = FileSession()
        except ImportError:
            self._file_session = None

        # ── Project folder analysis session (Claude Code style) ──────────────
        self._project_session: Optional[Any] = None  # set by /project load
        self._project_ctx_injected: bool = False

        # ── ariarc: project-level context injection ──────────────────────
        self.ariarc: Optional[Any] = None
        if _HAS_ARIARC:
            try:
                self.ariarc = get_ariarc()
                if self.ariarc.found:
                    logger.info("ariarc loaded from %s", self.ariarc.source_path)
            except Exception as _exc:
                logger.debug("ariarc load error: %s", _exc)

        # ── MCP registry placeholder (started async in run_interactive) ──
        self._mcp_registry: Optional[Any] = None
        self._mcp_started = False

        # ── Global user memory ────────────────────────────────────────────
        try:
            from memory_manager import MemoryManager
            self.memory_mgr: Optional[Any] = MemoryManager()
        except Exception:
            self.memory_mgr = None

        self.commands = SlashCommands(self)

        # Setup input — prefer prompt_toolkit, fallback to readline.
        # Skip interactive input setup entirely in non-interactive mode (-p flag)
        # to avoid prompt_toolkit emitting "Warning: Input is not a terminal".
        self._pt_session = None
        self._pt_completer = None
        self._pt_history = None
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

        _interactive = sys.stdin.isatty()
        # Task list state — toggled by Ctrl+T
        self._task_list_visible = False
        self._task_list: list[dict] = []
        # Transcript / tool-call log — toggled by Ctrl+O
        self._transcript_log: list[str] = []
        self._transcript_visible = False
        # Session recap: timestamp of last completed AI turn
        self._last_turn_ts: float = 0.0

        if HAS_PT and _interactive:
            self._pt_completer = AriaPTCompleter(
                self.commands.commands, SKILLS, config.get("watchlist", []),
            )
            self._pt_history = FileHistory(str(HISTORY_FILE))
            _placeholder = (
                [("class:placeholder", "Ask Aria, edit files, run commands, or /help")]
                if config.get("input_style", "panel") == "box"
                else HTML('<style fg="#888888">Ask Aria · @ file  !cmd  /help</style>')
            )
            _kb = self._build_keybindings()
            self._pt_session = PromptSession(
                history=self._pt_history,
                completer=self._pt_completer,
                complete_while_typing=True,
                style=build_aria_pt_style(config.get("input_theme", "auto")),
                placeholder=_placeholder,
                key_bindings=_kb,
            )
        elif _interactive:
            try:
                if HISTORY_FILE.exists():
                    readline.read_history_file(str(HISTORY_FILE))
                readline.set_history_length(1000)
                completer = ArtheraCompleter(
                    list(self.commands.commands.keys()),
                    SKILLS,
                    config.get("watchlist", []),
                )
                readline.set_completer(completer.complete)
                readline.parse_and_bind("tab: complete")
                readline.set_completer_delims(" ")
            except Exception:
                pass

    def print_header(self):
        # Resolve current model info
        current_id  = self.config.get("model", "qwen2.5:7b")

        # ── 模型自动配对（现实优先）─────────────────────────────────────────
        # 检测本机已安装的 Ollama 模型；若配置模型未安装，自动配对到最优
        # 可用模型并持久化配置（与运行时 fallback 共用同一选择逻辑）。
        self._auto_healed_from: Optional[str] = None   # 原配置模型（仅本次显示用）
        self._ollama_alive = False
        self._installed_models: set = set()
        try:
            _rm, _ = detect_ollama_models_rich(
                self.config.get("ollama_url", "http://localhost:11434"))
            self._installed_models = {_x["name"] for _x in _rm}
            self._ollama_alive = bool(self._installed_models)
        except Exception:
            pass
        if self._installed_models and current_id not in self._installed_models:
            _resolved = _pick_best_installed_model(self._installed_models, current_id)
            if _resolved:
                self._auto_healed_from = current_id
                current_id = _resolved
                self.config["model"] = _resolved
                self._actual_model = None   # config now matches reality
                try:
                    save_config(self.config)
                except Exception:
                    pass

        current_key = next((k for k, v in MODELS.items() if v["id"] == current_id), None)
        _default_m  = MODELS.get("qwen7b") or MODELS.get("qwen-fast") or next(iter(MODELS.values()))
        m = MODELS.get(current_key, _default_m) if current_key else _default_m
        cwd = os.getcwd()
        # Shorten home directory to ~
        home = os.path.expanduser("~")
        if cwd.startswith(home):
            cwd = "~" + cwd[len(home):]
        wl = self.config.get("watchlist", [])
        tool_count = len(ARIA_TOOLS) + len(LOCAL_TOOLS)
        skill_count = len(SKILLS)

        # Watchlist string
        wl_str = ""
        if wl:
            wl_str = ", ".join(wl[:5])
            if len(wl) > 5:
                wl_str += f" +{len(wl) - 5}"

        _badge = m.get("badge", "")
        _runtime = "cloud" if _badge == "Cloud" or "cloud" in current_id.lower() else "local"
        _banner_mode = self.config.get("banner", "full")  # full | compact | off
        _mascot = "[bold #C08050]▣[/bold #C08050]"

        if _banner_mode == "off":
            return   # Silent startup for scripts / automation

        if HAS_RICH:
            console.print()

            _ui_lang = self.config.get("ui_lang", "en") or "en"
            if _banner_mode == "compact":
                _model_label = f"{m['name']} {m['version']}" if current_key else current_id
                from ui.banner import render_compact_banner as _rcb
                try:
                    from apps.cli.update_check import get_update_notice as _gun
                    _update_notice = _gun(wait_ms=1200)
                except Exception:
                    _update_notice = None
                _rcb(
                    version=__version__,
                    model_label=_model_label,
                    runtime=_runtime,
                    cwd=cwd,
                    control_status_rich=self._control_status_label(rich=True),
                    tool_count=tool_count,
                    update_notice=_update_notice,
                    console=console,
                    has_rich=HAS_RICH,
                    lang=_ui_lang,
                )
            else:
                _model_label = f"{m['name']} {m['version']}" if current_key else current_id
                try:
                    from apps.cli.i18n import t as _i18n_t
                    _lite_word  = _i18n_t("lite", lang=_ui_lang)
                    _cloud_word = _i18n_t("cloud", lang=_ui_lang)
                    _local_word = _i18n_t("local", lang=_ui_lang)
                except Exception:
                    _lite_word, _cloud_word, _local_word = "lite", "cloud", "local"
                if _badge == "Fast":
                    _rt_label = f"{_model_label}  [dim]{_lite_word}[/dim]"
                elif _badge == "Cloud":
                    _rt_label = f"{_model_label}  [dim]{_cloud_word}[/dim]"
                else:
                    _rt_label = f"{_model_label}  [dim]{_local_word}[/dim]"

                _best_id = (MODELS.get("qwen7b") or {}).get("id", "qwen2.5:7b")
                from ui.banner import render_full_banner as _rfb, render_try_hints as _rth
                try:
                    from apps.cli.update_check import get_update_notice as _gun
                    _update_notice = _gun(wait_ms=1200)
                except Exception:
                    _update_notice = None
                _rfb(
                    version=__version__,
                    rt_label=_rt_label,
                    cwd=cwd,
                    control_status_rich=self._control_status_label(rich=True),
                    ollama_status_rich=self._ollama_status_label(rich=True),
                    tool_count=tool_count,
                    skill_count=skill_count,
                    auto_healed_from=self._auto_healed_from or "",
                    current_id=current_id,
                    badge=_badge,
                    installed_models=frozenset(self._installed_models),
                    best_lite_id=_best_id,
                    update_notice=_update_notice,
                    console=console,
                    has_rich=HAS_RICH,
                    rich_box=rich_box,
                    lang=_ui_lang,
                )
                _rth(console, HAS_RICH, lang=_ui_lang)
                if not self.config.get("first_run_seen"):
                    self.config["first_run_seen"] = True
                    save_config(self.config)
        else:
            if _banner_mode != "off":
                from ui.banner import render_full_banner as _rfb
                _rfb(
                    version=__version__,
                    rt_label=_runtime,
                    cwd=cwd,
                    control_status_rich=self._control_status_label(),
                    ollama_status_rich=self._ollama_status_label(),
                    tool_count=tool_count,
                    skill_count=skill_count,
                    console=console,
                    has_rich=HAS_RICH,
                    rich_box=rich_box,
                )

    def _privacy_status_label(self, rich: bool = False) -> str:
        from ui.banner import privacy_status_label as _psl
        _lang = self.config.get("ui_lang", "en") or "en"
        return _psl(self.config, rich=rich, lang=_lang)

    def _control_status_label(self, rich: bool = False) -> str:
        from ui.banner import control_status_label as _csl
        _lang = self.config.get("ui_lang", "en") or "en"
        return _csl(self.config, rich=rich, lang=_lang)

    def _ollama_status_label(self, rich: bool = False) -> str:
        from ui.banner import ollama_status_label as _osl
        _lang = self.config.get("ui_lang", "en") or "en"
        return _osl(
            getattr(self, "_ollama_alive", False),
            getattr(self, "_installed_models", set()) or set(),
            self.config,
            rich=rich,
            lang=_lang,
        )

    def _status_line(self) -> str:
        current_id = self.config.get("model", "qwen2.5:7b")
        # If Ollama switched to a different model, show the actual running model
        display_id = self._actual_model or current_id
        model_name = display_id  # fallback: raw model ID
        for k, v in MODELS.items():
            if v["id"] == display_id:
                model_name = v["name"].replace("Aria ", "")
                break
            # also match by actual model ID (e.g. gpt-oss:120b-cloud)
            if v["id"] == current_id and self._actual_model is None:
                model_name = v["name"].replace("Aria ", "")
                break
        # If actual_model differs from config, append a ⚑ warning marker
        _mismatch = (self._actual_model is not None and self._actual_model != current_id)
        if _mismatch:
            model_name = f"{self._actual_model} ⚑"
        # Determine runtime label
        _lp = self._last_provider or ""
        _model_badge = next(
            (v.get("badge", "") for v in MODELS.values() if v["id"] == current_id), ""
        )
        if _lp == "ollama":
            runtime = "local"
        elif _lp in ("deepseek", "openai", "anthropic", "groq", "dashscope", "together"):
            runtime = "cloud"
        elif _model_badge == "Cloud" or "cloud" in current_id.lower():
            runtime = "cloud"
        elif not _lp:
            runtime = "local" if getattr(self, "_ollama_alive", False) else "—"
        else:
            runtime = "cloud"
        # Context source tags
        _ctx_tags = []
        if getattr(self, "_project_session", None):
            _ctx_tags.append(f"proj:{self._project_session.name}")
        elif getattr(self, "_file_session", None) and self._file_session.get_active():
            _ctx_tags.append(f"file:{self._file_session.get_active().filename}")
        _ctx = f"  ·  {_ctx_tags[0]}" if _ctx_tags else ""
        privacy = "share" if bool(self.config.get("data_sharing", False)) else "local-only"
        permission = self.config.get("permission_mode", "workspace-write")
        return f"aria  ·  {runtime}  ·  {permission}  ·  {privacy}{_ctx}"

    async def send_message(self, message: str, system_override: Optional[str] = None):
        """Send message to Aria AI with agentic tool loop, smart fallback, markdown."""
        # Store optional system prompt override (used by /file analyze)
        self._system_override = system_override
        # Fire prompt_submit hook (Claude Code: UserPromptSubmit)
        _run_event_hook("prompt_submit", {
            "ARIA_MESSAGE":  message[:500],
            "ARIA_SESSION":  self.session_id,
            "ARIA_PROVIDER": self._last_provider,
        })
        # Attach pending image block if /vision was used before this message
        if self._pending_image is not None:
            user_content = [
                {"type": "text", "text": message},
                self._pending_image,
            ]
            self._pending_image = None
        elif (self._file_session is not None and
              self._file_session.get_active() is not None):
            # Inject loaded-file context as a text block before the user question.
            # Only inject for the FIRST message after /file load (tracked via flag),
            # then keep the file in system prompt for follow-up turns.
            _fc = self._file_session.get_active()
            _fc_ctx = self._file_session.build_context_block(max_chars=14_000)
            user_content = f"[文件上下文已加载: {_fc.filename}]\n\n{message}"
            # Persist file context in system prompt so follow-up questions work
            if not hasattr(self, "_file_ctx_injected") or not self._file_ctx_injected:
                self._file_ctx_injected = True
                # Pre-pend file content to the very first user message
                user_content = (f"以下是用户上传的文件内容，请在回答时参考：\n{_fc_ctx}\n\n"
                                f"---用户问题---\n{message}")
        elif self._project_session is not None:
            # Inject project context for the first message, then rely on history
            _ps = self._project_session
            user_content = f"[项目已加载: {_ps.name}]\n\n{message}"
            if not self._project_ctx_injected:
                self._project_ctx_injected = True
                _pc_ctx = _ps.build_llm_context(max_chars=14_000)
                user_content = (
                    f"以下是已加载的项目信息，请在完成任务时参考：\n{_pc_ctx}\n\n"
                    f"---用户请求---\n{message}"
                )
        else:
            user_content = message
            self._file_ctx_injected = False  # reset when no file loaded
            self._project_ctx_injected = False  # reset when no project loaded
        self.conversation.append({"role": "user", "content": user_content})

        # ── 路由决策：支持工具调用的模型走 LLM+tool call，否则走确定性路由 ──
        # 支持 function calling 的模型（Claude / GPT-4 class / qwen-72b+）能自己
        # 识别公司名 → ticker 并调 get_market_data，不需要硬编码字典。
        # 本地小模型（<14B）工具调用不稳定，保留确定性路由作降级。
        _curr_model_id = self.config.get("model", "")
        _model_has_tools = False
        if _HAS_MODEL_CAP:
            try:
                _mc = get_model_capability(_curr_model_id)
                _model_has_tools = bool(_mc.tool_calls and _mc.context_window >= 8192)
            except Exception:
                pass

        # ── Broker setup intent: intercept before LLM / deterministic routing ──
        if _is_broker_setup_intent(message):
            _btype = _detect_broker_type(message)
            if HAS_RICH:
                from apps.cli.utils.market_detect import _BROKER_SETUP_NAMES
                _display = _BROKER_SETUP_NAMES.get(_btype, ("",))[0] if _btype else ""
                _label = f"  正在启动{_display}配置向导…" if _display else "  正在启动券商配置向导…"
                console.print(f"\n[bold]Aria[/bold]  [dim]{_label}[/dim]\n")
            await self.commands._cmd_broker_add(_btype)
            return

        deterministic: dict = {"success": False}
        if not _model_has_tools:
            # Deterministic path: only for models that can't reliably do function calling
            deterministic = _try_handle_broker_query(message)
        if not deterministic.get("success"):
            # Real-estate / housing queries get their own deterministic handler so they
            # never accidentally inherit a stock ticker from session history.
            deterministic = _try_handle_realty_query(message)
        if not deterministic.get("success"):
            # Market snapshot always uses deterministic renderer — even tool-capable models
            # produce N/A when they try to parse injected data themselves.
            deterministic = _try_handle_market_snapshot_analysis(
                message, history=self.conversation[:-1])
        if not deterministic.get("success"):
            deterministic = _try_handle_stock_chart_analysis(message)
        if deterministic.get("success") or _is_stock_chart_analysis_request(message):
            final_text = deterministic.get("response", "")
            if not final_text:
                final_text = f"市场分析未完成：{deterministic.get('error', '未知错误')}"
            if HAS_RICH:
                console.print()
                console.print("[bold]Aria[/bold]")
                console.print()
                console.print(Markdown(_strip_latex(final_text)))
                # User-friendly footer: show data source(s) instead of internal routing label
                _tools = deterministic.get("tools_used", [])
                _tool_label = {
                    "market_snapshot": "市场快照",
                    "stock_chart":     "图表分析",
                    "broker_query":    "账户数据",
                    "realty_query":    "房地产数据",
                }.get(_tools[0], _tools[0]) if _tools else "本地分析"
                _rate_limited = deterministic.get("rate_limited", False)
                _rl_note = "  [yellow]⚠ 数据源限流[/yellow]" if _rate_limited else ""
                console.print(f"\n[dim]{_tool_label} · 本内容不构成投资建议[/dim]{_rl_note}\n")
                console.print(Rule(style="dim"))
            else:
                print("\nAria\n")
                print(final_text)
                print(f"\n市场快照 · 本内容不构成投资建议\n")
            self.conversation.append({"role": "assistant", "content": final_text})
            return

        model = self.config.get("model", "qwen2.5:7b")
        thinking_mode = self.config.get("thinking_mode", "auto")
        auth_token = self.config.get("auth_token")
        user_context = _build_user_context(self.config)
        self.cancel_event = asyncio.Event()
        self._streaming = True
        set_robot_state(RobotState.THINKING)
        _esc_watcher.start(self.cancel_event)

        # Context pressure warning — only once per session when > 85% full
        _est_tokens = sum(len(m.get("content", "")) for m in self.conversation) // 3
        _max_ctx    = get_model_cfg(self.config.get("model", "qwen2.5:7b")).get("num_ctx", 16384)
        from ui.render.output import print_context_warning as _pcw
        _pcw(_est_tokens, _max_ctx, console=console, has_rich=HAS_RICH,
             session_id=getattr(self, "session_id", ""))

        if HAS_RICH:
            console.print()
        start_time = time.time()

        # --- Dynamic max_rounds: scale with task complexity ---
        # Simple queries get 10 rounds; complex multi-step tasks get up to 20.
        _task_complexity_signals = (
            len(message) > 120 or
            any(kw in message for kw in (
                "然后", "接着", "最后", "步骤", "并且", "同时",
                "and then", "step", "finally", "after that", "next",
                "完整", "全面", "详细", "系统", "comprehensive", "complete",
            ))
        )
        max_rounds = 20 if _task_complexity_signals else 10

        # --- Task decomposition for complex multi-step requests ---
        # For long or multi-step messages, ask the AI to produce a plan first,
        # then inject it as context so the agentic loop follows a clear path.
        _DECOMP_THRESHOLD = 150   # chars
        _decomp_plan: str = ""
        if (
            len(message) > _DECOMP_THRESHOLD and
            _task_complexity_signals and
            not any(message.startswith(p) for p in ("/", "!"))  # not a slash command
        ):
            _decomp_prompt = (
                "Break the following user request into a numbered step-by-step execution plan "
                "(max 8 steps, one line each). Be concrete and tool-aware. "
                "Output ONLY the numbered list, nothing else.\n\n"
                f"Request: {message[:600]}"
            )
            try:
                _plan_result = await stream_ollama(
                    self.config.get("ollama_url", "http://localhost:11434"),
                    _decomp_prompt,
                    history=[],
                    model=self.config.get("model", "qwen2.5:7b"),
                    enable_tools=False,
                )
                if _plan_result.get("success") and _plan_result.get("response"):
                    _decomp_plan = _plan_result["response"].strip()
            except Exception:
                pass  # decomposition is best-effort

        # Inject plan as a prefix to the first turn's message so the AI
        # follows the decomposed steps rather than free-forming the approach.
        current_message = message
        if _decomp_plan:
            current_message = (
                f"[执行计划]\n{_decomp_plan}\n\n"
                f"[用户请求]\n{message}"
            )
        turn_state = AgentTurnState(provider="aws")
        provider = turn_state.provider
        token_count = 0
        thinking_tokens = 0

        for round_num in range(max_rounds):
            response_text = ""
            thinking_shown = False
            thinking_start = None
            thinking_finished = False
            thinking_preview_buf: list = []  # accumulate preview chars
            streamed_any = False

            if round_num == 0:
                if HAS_RICH:
                    console.print("\n[bold]Aria[/bold]")
                else:
                    print("\nAria")

            # Progressive markdown rendering via Rich Live
            _live_display = [None]
            # Spinner shown before first token arrives
            _spinner = [None]
            _first_token_received = [False]
            _token_start_time = [None]
            # Throttle Live.update() to prevent terminal flooding (max once per 80ms)
            _last_live_update = [0.0]
            _LIVE_UPDATE_INTERVAL = 0.08  # seconds
            # Force plain-print mode for Ollama streams: Live.update() causes the
            # entire accumulated response to reprint on every token in embedded
            # terminals (Electron/Arthera) that report is_terminal=True but cannot
            # handle cursor-up ANSI sequences correctly.
            _use_plain_print  = [False]
            # Batch-render mode (Ollama only): accumulate tokens silently while
            # spinner runs, then render the COMPLETE response with Rich Markdown
            # after streaming ends.  Avoids per-token LaTeX-buffering issues where
            # "$$" is split across two single-"$" tokens (model-dependent tokenisation)
            # causing raw \frac, \sum, \; to leak into output.
            _use_batch_render = [False]
            # LaTeX streaming buffer: accumulate tokens between \( and \) or $$ and $$
            # so that _strip_latex gets the COMPLETE expression, not fragments.
            _latex_buf = [""]      # mutable for closure
            _in_latex  = [False]   # True while inside an open LaTeX block

            def _flush_latex_buf() -> str:
                """Process and return the accumulated LaTeX buffer, then clear it."""
                raw = _latex_buf[0]
                _latex_buf[0] = ""
                _in_latex[0]  = False
                return _strip_latex(raw) if raw.strip() else raw

            def _start_spinner():
                if HAS_RICH and _spinner[0] is None and not _first_token_received[0]:
                    _spinner[0] = console.status(
                        "[dim]思考中… [/dim][dim italic]esc 取消[/dim italic]",
                        spinner="dots", spinner_style="dim")
                    _spinner[0].__enter__()

            def _stop_spinner():
                if _spinner[0] is not None:
                    try:
                        _spinner[0].__exit__(None, None, None)
                    except Exception:
                        pass
                    _spinner[0] = None

            def _stop_live(discard: bool = False):
                """Stop Live display if active.

                discard=True: silently discard the current Live content without
                rendering it to the terminal. Use this before triggering a
                fallback stream so that the same content isn't printed twice
                (once by Live.stop() and once by the fallback's plain-print path).
                """
                _stop_spinner()
                if _live_display[0]:
                    try:
                        if discard:
                            # Replace renderable with empty text so Live.stop()
                            # does not paint stale content onto the terminal.
                            try:
                                from rich.text import Text as _RichText
                                _live_display[0].update(_RichText(""))
                                _live_display[0].refresh()
                            except Exception:
                                pass
                        _live_display[0].stop()
                    except Exception:
                        pass
                    _live_display[0] = None
                elif _first_token_received[0] and not discard:
                    # Plain-print mode: ensure cursor is on a new line.
                    # Skip for batch-render — no tokens were printed to stdout,
                    # so no newline is needed here.
                    if not _use_batch_render[0]:
                        print(flush=True)

            _start_spinner()

            def on_token(token):
                nonlocal response_text, streamed_any, thinking_shown, thinking_start, thinking_finished, token_count
                # Stop spinner on first token — UNLESS batch-render mode (Ollama),
                # where the spinner keeps running throughout generation so the user
                # knows work is in progress.
                if not _first_token_received[0]:
                    _first_token_received[0] = True
                    _token_start_time[0] = time.time()
                    set_robot_state(RobotState.STREAMING)
                    if not _use_batch_render[0]:
                        _stop_spinner()
                # Filter out Ollama special tokens
                if "<|im_start|>" in token or "<|im_end|>" in token:
                    token = token.replace("<|im_start|>", "").replace("<|im_end|>", "")
                    if not token.strip():
                        return
                # Filter out model meta-annotation artifacts (small-model hallucinations)
                # e.g. "(注释：请使用实际注入的数据进行回答)" that the model should never output
                _META_ARTIFACTS = (
                    "(注释：", "（注释：", "(提示：", "（提示：",
                    "请使用实际注入的数据", "请使用实际数据", "实际注入的数据",
                    "[system]", "[/system]", "[INST]", "[/INST]",
                )
                if any(a in token for a in _META_ARTIFACTS):
                    # Strip the artifact from token; if nothing left, skip entirely
                    import re as _re_tok
                    token = _re_tok.sub(
                        r'\(注[释释]：[^)）]*[)）]|（注[释释]：[^)）]*[)）]'
                        r'|\(提示：[^)）]*[)）]|（提示：[^)）]*[)）]'
                        r'|请使用实际(?:注入的)?数据[^。\n]*'
                        r'|\[/?(?:system|INST)\]',
                        '', token
                    )
                    if not token.strip():
                        return
                # Finalize thinking display on first content token
                if thinking_shown and not thinking_finished:
                    thinking_finished = True
                    _stop_spinner()
                    elapsed_t = time.time() - thinking_start if thinking_start else 0
                    t_info = f"Thought for {elapsed_t:.1f}s"
                    if thinking_tokens > 0:
                        t_info += f" · {thinking_tokens:,} tokens"
                    if HAS_RICH:
                        # \r clears the live thinking counter line before printing
                        import sys as _sys
                        _sys.stdout.write("\r\033[K")  # CR + erase-to-end-of-line
                        _sys.stdout.flush()
                        console.print(f"  [dim]{t_info}[/dim]")
                        # Optional thinking preview (config: "thinking_preview": true)
                        if self.config.get("thinking_preview") and thinking_preview_buf:
                            preview_text = "".join(thinking_preview_buf)[:280].strip()
                            if len("".join(thinking_preview_buf)) > 280:
                                preview_text += "…"
                            console.print(f"  [dim italic]{preview_text}[/dim italic]")
                    else:
                        print(f"\r  {t_info}")
                # ── Batch-render mode (Ollama) ────────────────────────────────
                # Accumulate the raw token without any per-token processing.
                # The spinner keeps running; the COMPLETE response is rendered
                # with Rich Markdown + _strip_latex after the stream finishes.
                # This correctly handles "$$" split across two single-"$" tokens
                # (model-dependent tokenisation) that would otherwise bypass the
                # LaTeX buffer and leak raw \frac / \sum / \; into output.
                if _use_batch_render[0]:
                    response_text += token
                    streamed_any = True
                    token_count += 1
                    return
                # ── LaTeX buffering ───────────────────────────────────────────
                # Accumulate tokens between LaTeX delimiters (\(...\) or $$...$$)
                # so _strip_latex sees the COMPLETE expression, not fragments.
                # Inline `$...$` is NOT buffered to avoid false positives on dollar
                # signs in financial text ("price is $192").
                _OPEN_DELIMS  = (r"\(", r"\[", "$$")
                _CLOSE_DELIMS = (r"\)", r"\]", "$$")

                if not _in_latex[0]:
                    # Check if token OPENS a LaTeX block
                    _opens = any(d in token for d in _OPEN_DELIMS)
                    if _opens:
                        _in_latex[0] = True
                        _latex_buf[0] = token
                        # Check if it also CLOSES in the same token
                        _tail = token
                        for _od, _cd in zip(_OPEN_DELIMS, _CLOSE_DELIMS):
                            if _od in _tail:
                                _after = _tail[_tail.index(_od) + len(_od):]
                                if _cd in _after:
                                    # Complete block in one token — process immediately
                                    token = _flush_latex_buf()
                                    break
                        else:
                            # Block opened but not closed — keep buffering, don't print yet
                            response_text += _latex_buf[0]  # accumulate raw in response_text
                            streamed_any = True
                            token_count += 1
                            return
                    else:
                        # Normal token — strip and print
                        token = _strip_latex(token)
                else:
                    # Already inside a LaTeX block — keep buffering
                    _latex_buf[0] += token
                    _closes = any(d in token for d in _CLOSE_DELIMS)
                    if _closes:
                        # Block complete — process the whole accumulated buffer
                        token = _flush_latex_buf()
                    else:
                        # Still open — accumulate in response_text but don't print
                        response_text += token
                        streamed_any = True
                        token_count += 1
                        return
                # ─────────────────────────────────────────────────────────────

                response_text += token
                streamed_any = True
                token_count += 1
                # Streaming output: use Rich.Live ONLY when the terminal
                # supports ANSI cursor control (is_terminal=True and NOT dumb)
                # AND we are not in forced plain-print mode.
                # In dumb/pipe mode, or when streaming from a local Ollama model,
                # every Live.update() reprints the full block — producing the
                # cascading-echo bug — so fall back to incremental plain print.
                _can_live = (
                    HAS_RICH
                    and not _use_plain_print[0]
                    and getattr(console, "is_terminal", False)
                    and not getattr(console, "is_dumb_terminal", True)
                )
                if _can_live:
                    now = time.time()
                    _md = Markdown(_strip_latex(response_text))
                    if _live_display[0] is None:
                        _live_display[0] = Live(
                            _md, console=console,
                            refresh_per_second=12,
                            vertical_overflow="visible",
                        )
                        _live_display[0].start()
                        _last_live_update[0] = now
                    elif now - _last_live_update[0] >= _LIVE_UPDATE_INTERVAL:
                        _live_display[0].update(_md)
                        _last_live_update[0] = now
                else:
                    # Plain incremental output — works in all terminals / pipes
                    print(token, end="", flush=True)

            def on_thinking(content):
                nonlocal thinking_shown, thinking_start, thinking_tokens
                if not thinking_shown:
                    _stop_spinner()  # stop generic spinner
                    thinking_start = time.time()
                    thinking_shown = True
                thinking_tokens += 1
                # Live elapsed counter — update every 30 tokens (~0.5s)
                if thinking_tokens % 30 == 1:
                    elapsed = time.time() - thinking_start
                    import sys as _sys
                    _sys.stdout.write(
                        f"\r  \033[2mthinking...  {elapsed:.1f}s  "
                        f"({thinking_tokens} tokens)\033[0m    "
                    )
                    _sys.stdout.flush()
                # Accumulate up to 300 chars for optional preview
                if len("".join(thinking_preview_buf)) < 300:
                    thinking_preview_buf.append(content)

            def on_tool_call(tool, params):
                nonlocal thinking_shown, thinking_start, thinking_finished, thinking_tokens
                # Finalize thinking display before tool call
                if thinking_shown and not thinking_finished:
                    thinking_finished = True
                    elapsed_t = time.time() - thinking_start if thinking_start else 0
                    t_info = f"Thought for {elapsed_t:.1f}s"
                    if thinking_tokens > 0:
                        t_info += f" · {thinking_tokens:,} tokens"
                    if HAS_RICH:
                        import sys as _sys
                        _sys.stdout.write("\r\033[K")  # CR + erase-to-end-of-line
                        _sys.stdout.flush()
                        console.print(f"  [dim]{t_info}[/dim]")
                        if self.config.get("thinking_preview") and thinking_preview_buf:
                            preview_text = "".join(thinking_preview_buf)[:280].strip()
                            if len("".join(thinking_preview_buf)) > 280:
                                preview_text += "…"
                            console.print(f"  [dim italic]{preview_text}[/dim italic]")
                    else:
                        print(f"\r  {t_info}")
                _print_tool_call(tool, params if isinstance(params, dict) else {})

            def on_tool_result(tool, summary):
                # Track tool calls in transcript log (Ctrl+O viewer)
                import time as _t
                _ts = _t.strftime("%H:%M:%S")
                _entry = f"[{_ts}] {tool}: {str(summary)[:100]}"
                self._transcript_log.append(_entry)
                if len(self._transcript_log) > 100:
                    self._transcript_log = self._transcript_log[-100:]
                # Parse TaskCreate/TaskUpdate results into task list (Ctrl+T)
                if tool in ("TaskCreate", "TaskUpdate") and isinstance(summary, dict):
                    _tid = summary.get("id") or summary.get("task_id")
                    _title = summary.get("title", "")
                    _status = summary.get("status", "pending")
                    if _tid:
                        _existing = next((t for t in self._task_list if t.get("id") == _tid), None)
                        if _existing:
                            _existing["status"] = _status
                            if _title:
                                _existing["title"] = _title
                        else:
                            self._task_list.append({"id": _tid, "title": _title, "status": _status})

            _prev_provider = self._last_provider or "local"

            def on_status(state, msg):
                if state == "fallback":
                    # Parse "from → to" from msg when possible, otherwise show as-is
                    import re as _re_status
                    m = _re_status.search(r"(?:from\s+)?(\w+)\s*(?:→|->|to)\s*(\w+)", msg or "", _re_status.I)
                    if m:
                        _from, _to = m.group(1), m.group(2)
                    else:
                        _from  = _prev_provider
                        _to    = "cloud"
                    reason = msg or ""
                    from ui.render.output import print_fallback_toast as _pft
                    _pft(_from, _to, reason, console=console, has_rich=HAS_RICH)

            # Route: local_mode → Ollama directly; otherwise AWS first → Ollama fallback
            local_mode = self.config.get("local_mode", False)
            if local_mode:
                _use_plain_print[0]  = True
                _use_batch_render[0] = True   # accumulate silently → Rich render at end
                _sys_ov = getattr(self, "_system_override", None)
                self._system_override = None
                result = await stream_ollama(
                    self.config.get("ollama_url", "http://localhost:11434"),
                    current_message, self.conversation,
                    model=model, on_token=on_token, on_thinking=on_thinking,
                    on_tool_call=on_tool_call, on_tool_result=on_tool_result,
                    cancel_event=self.cancel_event,
                    enable_tools=True,
                    system_override=_sys_ov,
                )
                provider = "ollama"
                self._last_provider = "ollama"
            else:
                # Pass system_override through user_context for cloud path
                _cloud_uctx = dict(user_context or {})
                _so = getattr(self, "_system_override", None)
                if _so:
                    _cloud_uctx["system_role_override"] = _so
                    self._system_override = None
                result = await stream_chat(
                    self.api_url, current_message, self.conversation,
                    model=model, thinking_mode=thinking_mode,
                    user_context=_cloud_uctx or user_context, auth_token=auth_token,
                    on_token=on_token, on_thinking=on_thinking,
                    on_tool_call=on_tool_call, on_tool_result=on_tool_result,
                    on_status=on_status, cancel_event=self.cancel_event,
                )
                # 响应质量检测：success=True 但返回占位符/空响应 → 同样 fallback
                def _is_placeholder_response(r: dict) -> bool:
                    resp = r.get("response", "")
                    if not resp or len(resp) < 20:
                        return True
                    # 后端已知占位模板
                    _placeholders = (
                        "欢迎使用 Aria AI 金融助手",
                        "这是一个需要详细解释的概念。请稍后重试",
                        "Welcome to Aria",
                        "请提供更具体的问题",
                        "I'm here to help with financial",
                    )
                    return any(p in resp for p in _placeholders)

                # If backend failed OR returned placeholder, fallback chain:
                # Ollama (if running) → DeepSeek cloud → OpenAI → error
                _should_fallback = (
                    (not result.get("success") and not result.get("cancelled"))
                    or _is_placeholder_response(result)
                )
                if _should_fallback:
                    # Discard any in-progress Live display without rendering it —
                    # the fallback will stream fresh content.  Rendering here would
                    # cause the same response to appear twice (once from the Live
                    # final-render and once from the fallback's plain-print path).
                    _stop_live(discard=True)
                    # Also reset streaming state so the fallback starts fresh
                    response_text = ""
                    streamed_any = False
                    _first_token_received[0] = False

                    # ── 1. 查询 Ollama 实际安装列表 ───────────────────────────
                    # NOTE: Use aiohttp with trust_env=False to bypass HTTP_PROXY
                    # environment variable — urllib.request can fail for localhost
                    # even when NO_PROXY=localhost,127.0.0.1 is set.
                    import json as _json
                    ollama_url      = self.config.get("ollama_url", "http://localhost:11434")
                    _ollama_up      = False
                    _ollama_models  = set()   # {"qwen2.5:7b", "gpt-oss:120b-cloud", ...}
                    try:
                        import aiohttp as _aiohttp
                        async with _aiohttp.ClientSession(
                            trust_env=False,  # ignore HTTP_PROXY / NO_PROXY
                            connector=_aiohttp.TCPConnector(ssl=False)
                        ) as _sess:
                            async with _sess.get(
                                f"{ollama_url}/api/tags",
                                timeout=_aiohttp.ClientTimeout(total=3)
                            ) as _resp:
                                if _resp.status == 200:
                                    _tags = await _resp.json()
                                    _ollama_up = True
                                    _ollama_models = {m["name"] for m in _tags.get("models", [])}
                    except Exception:
                        # Fallback: try urllib with explicit no-proxy
                        try:
                            import urllib.request as _ur
                            _proxy_handler = _ur.ProxyHandler({})  # bypass all proxies
                            _opener = _ur.build_opener(_proxy_handler)
                            _tags_resp = _opener.open(f"{ollama_url}/api/tags", timeout=3)
                            _tags = _json.loads(_tags_resp.read())
                            _ollama_up = True
                            _ollama_models = {m["name"] for m in _tags.get("models", [])}
                        except Exception:
                            pass

                    # 优先使用用户选定的模型；若未安装则按能力顺序降级
                    # （选择逻辑与启动预检共用 _pick_best_installed_model）
                    _ollama_model = None
                    if _ollama_up:
                        _ollama_model = _pick_best_installed_model(_ollama_models, model)

                    if _ollama_model:
                        _switched = _ollama_model != model
                        self._actual_model = _ollama_model  # record for header display
                        if _switched:
                            # 配置的模型未安装，已自动切换 — 用 Panel 明确告知用户
                            if HAS_RICH:
                                console.print(Panel(
                                    f"[yellow]⚠ 配置模型 [bold]{model}[/bold] 未安装\n"
                                    f"[/yellow][dim]已自动切换至 [bold]{_ollama_model}[/bold]（本地可用）\n"
                                    f"安装配置模型：[bold]ollama pull {model}[/bold][/dim]",
                                    border_style="yellow",
                                    box=rich_box.ROUNDED,
                                    padding=(0, 1),
                                ))
                            else:
                                print(f"  ⚠ 配置模型 {model} 未安装，已切换至 {_ollama_model}")
                        else:
                            # 含 "cloud" 字样说明是通过 Ollama 运行的云端命名模型
                            if "cloud" in _ollama_model.lower() and HAS_RICH:
                                console.print(f"  [dim]Ollama · {_ollama_model}[/dim]")
                        _use_plain_print[0]  = True   # disable Live for Ollama
                        _use_batch_render[0] = True   # accumulate silently → Rich render at end
                        _sys_ov = getattr(self, "_system_override", None)
                        self._system_override = None  # consume before call
                        result = await stream_ollama(
                            ollama_url, current_message, self.conversation,
                            model=_ollama_model, on_token=on_token,
                            on_thinking=on_thinking,
                            on_tool_call=on_tool_call, on_tool_result=on_tool_result,
                            cancel_event=self.cancel_event, enable_tools=True,
                            system_override=_sys_ov,
                        )
                        provider = "ollama"
                        self._last_provider = "ollama"

                    else:
                        # ── 2. Ollama 无模型或未运行 → 尝试云端 provider ─────
                        if _ollama_up and not _ollama_models:
                            # Ollama 在但没有任何模型
                            _tip = "Ollama 已运行但未安装任何模型。运行: ollama pull qwen2.5:7b"
                            if HAS_RICH:
                                console.print(f"  [yellow]{_tip}[/yellow]")
                            else:
                                print(f"  {_tip}")
                        elif not _ollama_up:
                            if HAS_RICH:
                                console.print("  [dim]Ollama 未运行，尝试云端...[/dim]")
                            else:
                                print("  Ollama 未运行，尝试云端...")

                        try:
                            from providers.llm.registry import stream_cloud_fallback
                            _cloud_avail = True
                        except ImportError:
                            _cloud_avail = False

                        if _cloud_avail:
                            result = await stream_cloud_fallback(
                                current_message, self.conversation,
                                on_token=on_token,
                                cancel_event=self.cancel_event,
                            )
                            provider = result.get("provider", "cloud")
                            self._last_provider = provider
                        else:
                            # ── 3. 彻底无可用 provider ────────────────────────
                            _stop_live()
                            result = {"success": False, "error": "no_provider",
                                      "response": "", "cancelled": False}

            # Stop Live display before handling results
            _stop_live()

            if result.get("cancelled"):
                turn_state.append_response(response_text)
                break

            if not result.get("success"):
                set_robot_state(RobotState.ERROR)
                error_presentation = AgentErrorPresentation.from_error(result.get("error", "Unknown error"))
                console.print() if HAS_RICH else print()
                if error_presentation.use_generic_error_prefix:
                    _print_error(error_presentation.lines[0])
                else:
                    for idx, ln in enumerate(error_presentation.lines):
                        if HAS_RICH:
                            style = "bold yellow" if idx == 0 and len(error_presentation.lines) > 1 else "yellow"
                            console.print(f"  [{style}]{ln}[/{style}]")
                        else:
                            print(f"  {ln}")
                console.print() if HAS_RICH else print()
                break

            turn_state.provider = provider
            turn_state.apply_model_result(result, response_text)
            provider = turn_state.provider
            self._last_provider = turn_state.provider

            # --- Agentic tool loop ---
            pending = result.get("tool_calls_pending", [])
            if not pending:
                # Semantic exit: AI returned text without tool calls.
                # Additionally check if the response contains an explicit
                # "task complete" signal even after a tool-heavy sequence.
                break

            # Warn on final available round so user sees progress
            if round_num == max_rounds - 1:
                if HAS_RICH:
                    console.print(
                        f"  [yellow]⚠ 已达最大工具轮次 ({max_rounds}). "
                        f"若任务未完成，请继续对话或用 /compact 释放上下文。[/yellow]"
                    )
                else:
                    print(f"  ⚠ Max rounds ({max_rounds}) reached.")

            # ── Parallel tool dispatch ─────────────────────────────────────────
            # Read-only / remote tools run concurrently via asyncio.gather().
            # Write / edit / shell tools are serialised to avoid race conditions.
            async def _remote_tool_runner(tool_name: str, tool_params: dict) -> dict:
                return await execute_aria_tool(
                    self.api_url,
                    tool_name,
                    tool_params,
                    auth_token=auth_token,
                )

            _parallel_done = await run_parallel_tools(
                pending,
                self.tool_executor,
                remote_runner=_remote_tool_runner,
                hook=_run_hook,
            )

            # Execute pending tools: local tools first, then remote Aria tools
            tool_turn = ToolTurnPlan(pending=pending, parallel_done=_parallel_done)
            tool_batch = tool_turn.batch
            _activity_results: list = []   # accumulate (name, result, elapsed, params) for group render

            for task in tool_turn.tasks():
                # Check if user cancelled (ESC / Ctrl+C) between tool executions
                if self.cancel_event and self.cancel_event.is_set():
                    tool_batch.cancel()
                    break

                tool_name = task.tool_name
                tool_params = task.params

                # Note: _print_tool_call already called by on_tool_call during streaming

                # If this tool was already executed in the parallel batch, reuse result
                if task.has_parallel_result:
                    tr = task.parallel_result
                    tool_batch.add_result(tool_name, tr, _format_tool_summary)
                    _activity_results.append((tool_name, tr, 0.0, task.params))
                    continue

                # Ask user confirmation for destructive local tools
                if tool_name in _CONFIRM_TOOLS:
                    _stop_live()
                    try:
                        _cfg_policy = self.config.get("command_policy", "safe")
                        approval = _confirm_tool_execution_decision(
                            tool_name,
                            tool_params,
                            config_policy=_cfg_policy,
                        )
                        _apply_tool_approval(tool_params, approval)
                        if not approval.approved:
                            tool_batch.cancel()
                            from ui.render.output import print_tool_blocked as _ptb
                            _ptb(tool_name, "用户取消", console=console, has_rich=HAS_RICH)
                            break
                        # If user chose "Allow & set balanced", persist to config
                        if approval.upgrade_policy:
                            tool_params.pop("_upgrade_policy", None)
                            self.config["command_policy"] = "balanced"
                            try:
                                save_config(self.config)
                                if HAS_RICH:
                                    console.print("  [dim]策略已升级为 balanced 并保存[/dim]")
                            except Exception:
                                pass
                    except KeyboardInterrupt:
                        tool_batch.cancel()
                        break

                try:
                    async def _serial_remote_runner(_tool_name: str, _tool_params: dict) -> dict:
                        return await execute_aria_tool(
                            self.api_url,
                            _tool_name,
                            _tool_params,
                            auth_token=auth_token,
                        )

                    progress_label = task.progress_label(len(pending))
                    _slow_local = {"write_file", "edit_file", "run_command", "search_code"}
                    if HAS_RICH and (tool_name not in LOCAL_TOOLS or tool_name in _slow_local):
                        spinner_label = "" if tool_name in LOCAL_TOOLS else f"[dim]{progress_label}[/dim]"
                        with console.status(spinner_label, spinner="dots", spinner_style="dim"):
                            tr, tool_elapsed = await run_serial_tool(
                                tool_name,
                                tool_params,
                                self.tool_executor,
                                remote_runner=_serial_remote_runner,
                                hook=_run_hook,
                            )
                    else:
                        if tool_name not in LOCAL_TOOLS:
                            print(progress_label, end="", flush=True)
                        tr, tool_elapsed = await run_serial_tool(
                            tool_name,
                            tool_params,
                            self.tool_executor,
                            remote_runner=_serial_remote_runner,
                            hook=_run_hook,
                        )
                except KeyboardInterrupt:
                    tool_batch.cancel()
                    break

                _activity_results.append((tool_name, tr, tool_elapsed, tool_params))
                tool_batch.add_result(tool_name, tr, _format_tool_summary, elapsed=tool_elapsed)

            # ── Render tool results as Activity group or single-line ───────────
            if _activity_results:
                from ui.render.output import print_tool_activity_group as _ptag
                _ptag(
                    _activity_results,
                    console=console,
                    has_rich=HAS_RICH,
                    rich_box=rich_box,
                    print_finance_fn=_print_finance_result,
                    bot_mode=_ARIA_BOT_MODE,
                )

            # User cancelled during tool execution
            turn_state.add_tool_time(tool_batch.elapsed_total)
            if tool_batch.cancelled:
                result = {"success": True, "cancelled": True}
                break

            assistant_message, user_message, followup = tool_batch.build_next_turn(turn_state.total_response)
            self.conversation.append(assistant_message)
            self.conversation.append(user_message)
            current_message = followup
            turn_state.reset_response()

        # --- End of agentic loop ---
        _esc_watcher.stop()
        self._streaming = False
        set_robot_state(RobotState.DONE)
        elapsed = time.time() - start_time

        # ── Unified cancellation path ──────────────────────────────────────────
        # All cancel sources (model cancel, ESC between tools, KeyboardInterrupt)
        # converge here via result["cancelled"]=True.  A single AgentTurnResult
        # carries partial text and timing so callers see a consistent shape.
        if result.get("cancelled"):
            _stop_live()
            turn_result = turn_state.build_cancelled_result(
                elapsed=elapsed,
                token_count=token_count,
                thinking_tokens=thinking_tokens,
            )
            if HAS_RICH:
                # Batch-render mode (Ollama): tokens were silently accumulated.
                # Render whatever was generated before the cancel so the user
                # can see partial output rather than a blank screen.
                if turn_result.final_text and _use_batch_render[0]:
                    _stop_spinner()
                    console.print(Markdown(_strip_latex(turn_result.final_text)))
                console.print("\n[dim]Cancelled[/dim]")
                console.print(Rule(style="dim"))
            else:
                if turn_result.final_text:
                    print(turn_result.final_text)
                print("\n  (cancelled)")
            if turn_result.final_text:
                self.conversation.append(
                    {"role": "assistant", "content": turn_result.final_text}
                )
            return

        if result.get("success") and not result.get("cancelled"):
            turn_result = turn_state.build_result(
                elapsed=elapsed,
                fallback_response=result.get("response", ""),
                token_count=token_count,
                thinking_tokens=thinking_tokens,
            )
            final_text = turn_result.final_text

            # Flush any unclosed LaTeX buffer (e.g. stream cut off mid-formula).
            # This only matters for the non-batch plain-print path; in batch-render
            # mode the full raw response is rendered below anyway.
            if _in_latex[0] and _latex_buf[0]:
                _leftover = _flush_latex_buf()
                final_text = (final_text or "") + _leftover
                if _use_plain_print[0] and not _use_batch_render[0]:
                    print(_leftover, end="", flush=True)

            # Stop progressive Live display (final state stays in terminal)
            _stop_live()

            # ── Render final response ──────────────────────────────────────
            if _use_batch_render[0] and final_text and HAS_RICH:
                # Ollama batch-render: spinner was kept running during generation.
                # Stop it and render the COMPLETE response through Rich Markdown +
                # _strip_latex in one pass.  This correctly handles:
                #   • "$$" split across two single-"$" tokens (tokeniser-dependent)
                #   • All LaTeX spacing commands (\; \, \quad etc.)
                #   • Markdown headings, bold, tables
                _stop_spinner()
                _render_answer_block(final_text)
            elif token_count == 0 and final_text and HAS_RICH:
                # Non-streamed response (e.g. complete() API path): render markdown.
                _render_answer_block(final_text)

            self.conversation.append({"role": "assistant", "content": final_text})
            import time as _time_ts
            self._last_turn_ts = _time_ts.time()

            # Metadata line — detailed stats
            metadata = turn_result.metadata
            prompt_t = metadata.prompt_tokens
            completion_t = metadata.completion_tokens
            think_t = metadata.thinking_tokens

            if HAS_RICH:
                copy_hint = "  [dim]/copy[/dim]" if self._last_response else ""
                console.print(f"\n[dim]{' · '.join(metadata.parts)}[/dim]{copy_hint}")
                # One-time warning if first response and input tokens are very high
                # (>2000 for a short message suggests a heavy system prompt)
                _is_first_turn = (self._session_turns == 0)
                if _is_first_turn and prompt_t > 2000:
                    _sys_est = metadata.system_prompt_estimate(message)
                    if _sys_est > 1500:
                        console.print(
                            f"[dim]  ℹ 系统提示词约 {_sys_est:,} tokens，"
                            f"较长的对话会较快占满上下文。"
                            f"可用 /compact 压缩历史，或用 /clear 重置。[/dim]"
                )
                console.print(Rule(style="dim"))
            else:
                print(f"\n{' · '.join(metadata.parts)}\n")

            # ── Accumulate session-level usage stats (for /cost) ──────────
            self._session_input_tokens  += prompt_t or 0
            self._session_output_tokens += completion_t or 0
            self._session_thinking_tokens += think_t or 0
            self._session_turns += 1
            self._last_response = final_text   # for /copy

            # Fire response_done lifecycle hooks (shell + JSON)
            _run_event_hook("response_done", {
                "ARIA_RESPONSE":  (final_text or "")[:500],
                "ARIA_PROVIDER":  turn_result.provider,
                "ARIA_TOKENS":    str((prompt_t or 0) + (completion_t or 0)),
                "ARIA_SESSION":   self.session_id,
            })
            if _HAS_JSON_HOOKS:
                try:
                    _fire_json_hook(
                        "ResponseDone",
                        response=(final_text or "")[:500],
                        session_id=self.session_id,
                        hooks=_JSON_HOOKS,
                    )
                except Exception:
                    pass

            # Auto-capture user preferences / facts expressed in this turn
            try:
                from memory_manager import auto_capture_from_turn as _acft, MemoryManager as _MM
                _acft(message, final_text or "", _MM())
            except Exception:
                pass

            # Trim conversation history to prevent unbounded growth
            if len(self.conversation) > 40:
                self.conversation = self.conversation[-40:]

            # Auto-warn when context approaching limit; auto-compact at 95%
            _est = sum(len(m.get("content", "")) for m in self.conversation) // 3
            _mkey = resolve_model_key(self.config.get("model", "qwen2.5:7b"))
            _default_m2 = MODELS.get("qwen7b") or MODELS.get("qwen-fast") or next(iter(MODELS.values()))
            _max = MODELS.get(_mkey, _default_m2).get("num_ctx", 16384)
            _pct = min(100, int(_est / _max * 100))
            if _pct >= 95:
                # Auto-compact: silently summarise and truncate
                try:
                    await self.commands._smart_compact_async(silent=True)
                except Exception:
                    # Fallback: hard trim
                    self.conversation = self.conversation[-8:]
                if HAS_RICH:
                    console.print("  [dim]↩ Auto-compacted context (was 95%+ full)[/dim]")
            elif _pct >= 75 and HAS_RICH:
                _color = "yellow" if _pct < 90 else "red"
                console.print(
                    f"  [{_color}]⚠ Context {_pct}% full "
                    f"({_est:,}/{_max:,} tokens) — /compact to free space[/{_color}]"
                )

            # Auto-save session (JSON + JSONL dual write)
            if self.config.get("auto_save_sessions"):
                try:
                    self.session_mgr.save_session(self.session_id, self.conversation)
                except Exception:
                    pass
                # JSONL: append only the two new messages (user + assistant) for crash safety
                if self._jsonl_store is not None:
                    try:
                        self._jsonl_store.append_message(self.session_id, "user", message)
                        if final_text:
                            self._jsonl_store.append_message(self.session_id, "assistant", final_text)
                    except Exception:
                        pass

            # Auto-extract preference signals into global memory
            if self.memory_mgr and final_text:
                try:
                    from memory_manager import extract_preference_signal
                    _sig = extract_preference_signal(message, final_text)
                    if _sig:
                        self.memory_mgr.append("user_profile", _sig, title="User Profile")
                except Exception:
                    pass

    def _build_keybindings(self):
        """Build prompt_toolkit KeyBindings for REPL shortcuts."""
        kb = _PTKeyBindings()

        @kb.add("s-tab")
        def _cycle_permission(event):
            """Shift+Tab → cycle permission mode."""
            cur = _ACTIVE_PERMISSION_MODE[0]
            try:
                idx = _PERMISSION_CYCLE.index(cur)
            except ValueError:
                idx = 0
            nxt = _PERMISSION_CYCLE[(idx + 1) % len(_PERMISSION_CYCLE)]
            _ACTIVE_PERMISSION_MODE[0] = nxt
            self.config["permission_mode"] = nxt
            label = {"read-only": "🔒 read-only", "workspace-write": "✏️  workspace-write", "full-access": "⚡ full-access"}.get(nxt, nxt)
            event.app.current_buffer.text = ""
            # Print inline so user sees the change immediately
            import sys as _sys
            _sys.stderr.write(f"\r  Mode → {label}                \n")
            _sys.stderr.flush()

        @kb.add("escape", "t")
        def _toggle_thinking(event):
            """Alt+T → toggle thinking mode."""
            cur = self.config.get("thinking", False)
            self.config["thinking"] = not cur
            state = "ON" if not cur else "OFF"
            import sys as _sys
            _sys.stderr.write(f"\r  Thinking → {state}             \n")
            _sys.stderr.flush()

        @kb.add("escape", "p")
        def _switch_model(event):
            """Alt+P → insert /model into prompt buffer."""
            buf = event.app.current_buffer
            if not buf.text:
                buf.text = "/model "
                buf.cursor_position = len(buf.text)

        @kb.add("c-l")
        def _redraw(event):
            """Ctrl+L → clear and redraw screen."""
            event.app.renderer.clear()

        @kb.add("c-o")
        def _toggle_transcript(event):
            """Ctrl+O → show/hide recent tool call log."""
            self._transcript_visible = not self._transcript_visible
            if self._transcript_visible and self._transcript_log:
                import sys as _sys
                _sys.stderr.write("\n")
                for line in self._transcript_log[-20:]:
                    _sys.stderr.write(f"  {line}\n")
                _sys.stderr.write("  [Ctrl+O to close]\n\n")
                _sys.stderr.flush()
            else:
                self._transcript_visible = False

        @kb.add("c-t")
        def _toggle_tasklist(event):
            """Ctrl+T → show/hide task list."""
            self._task_list_visible = not self._task_list_visible
            if self._task_list_visible and self._task_list:
                import sys as _sys
                _sys.stderr.write("\n  📋 Tasks:\n")
                icons = {"pending": "○", "in_progress": "◉", "completed": "✓", "failed": "✗"}
                for t in self._task_list:
                    icon = icons.get(t.get("status", "pending"), "○")
                    _sys.stderr.write(f"    {icon} {t.get('title', '')}\n")
                _sys.stderr.write("\n")
                _sys.stderr.flush()

        return kb

    def _bottom_toolbar(self):
        """Bottom toolbar content for prompt_toolkit."""
        model_label, cwd, privacy, est_tokens, max_ctx = self._bottom_toolbar_parts()
        ctx_color = "#606060" if est_tokens / max_ctx < 0.6 else (
            "#aa8800" if est_tokens / max_ctx < 0.85 else "#cc4444"
        )
        perm = _ACTIVE_PERMISSION_MODE[0]
        perm_color = {"read-only": "#888800", "workspace-write": "#606060", "full-access": "#cc4444"}.get(perm, "#606060")
        perm_short = {"read-only": "ro", "workspace-write": "rw", "full-access": "full"}.get(perm, perm)
        # PR / git branch info
        _branch = ""
        try:
            import subprocess as _sp
            _b = _sp.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                  stderr=_sp.DEVNULL, timeout=1).decode().strip()
            if _b and _b != "HEAD":
                _branch = f" ⎇ {_b}"
        except Exception:
            pass
        # Task list indicator
        _tasks = ""
        if self._task_list:
            _done = sum(1 for t in self._task_list if t.get("status") == "completed")
            _total = len(self._task_list)
            _tasks = f" · ✓{_done}/{_total}"
        return HTML(
            f'<style fg="#C08050">{model_label}</style>'
            f'<style fg="#8a8a8a"> · {cwd}{_branch}{_tasks} · </style>'
            f'<style fg="{perm_color}">{perm_short}</style>'
            f'<style fg="#8a8a8a"> · {privacy} · /help · </style>'
            f'<style fg="{ctx_color}">{est_tokens:,}/{max_ctx:,}</style>'
        )

    def _bottom_toolbar_plain(self) -> str:
        model_label, cwd, privacy, est_tokens, max_ctx = self._bottom_toolbar_parts()
        return f"{model_label} · {cwd} · {privacy} · /help · esc · {est_tokens:,}/{max_ctx:,}"

    def _bottom_toolbar_parts(self):
        from ui.banner import bottom_toolbar_parts as _btp
        return _btp(self.conversation, self.config, self._actual_model, get_model_cfg)

    async def _startup_health_check(self):
        """Async Ollama + cloud connectivity probe displayed after the header."""
        if not HAS_RICH:
            return
        try:
            import aiohttp as _aio
            parts = []
            ollama_url = self.config.get("ollama_url", "http://localhost:11434")
            try:
                async with _aio.ClientSession() as s:
                    async with s.get(
                        f"{ollama_url}/api/tags",
                        timeout=_aio.ClientTimeout(total=2),
                    ) as r:
                        if r.status == 200:
                            _tags = await r.json()
                            _n = len(_tags.get("models", []))
                            self._ollama_alive = True
                            parts.append(
                                f"[dim]Ollama · {_n} models[/dim]"
                                if _n else "[dim]Ollama[/dim]"
                            )
                        else:
                            parts.append("[dim]Ollama offline[/dim]")
            except Exception:
                parts.append("[dim]Ollama offline[/dim]")

            # Cloud provider check (only if API key is set)
            if self.config.get("auth_token") or os.getenv("ANTHROPIC_API_KEY"):
                parts.append("[dim]Cloud[/dim]")

            # Auto-connect default broker from ~/.arthera/brokers.json
            if _HAS_BROKERS:
                try:
                    _reg = _get_broker_registry()
                    _broker = _reg.connect_default()
                    if _broker:
                        parts.append(f"[dim]{_broker.label} · account connected[/dim]")
                except Exception as _be:
                    logger.debug("Auto-connect broker failed: %s", _be)

            # Global memory fact count
            if getattr(self, "memory_mgr", None):
                try:
                    _mcount = self.memory_mgr.fact_count()
                    if _mcount:
                        parts.append(f"[dim]memory {_mcount} facts[/dim]")
                except Exception:
                    pass

            # Broker connection shown in banner status; log remainder for debug
            if parts:
                logger.debug("startup health: %s", "  ".join(parts))
            # Broker connection shown separately (not in 5-row banner to keep it compact)
            for p in parts:
                if "account connected" in p and HAS_RICH:
                    console.print(f"  {p}")
        except ImportError:
            pass

    async def _alert_watchdog(self):
        """Background task: check price alerts every 30s and fire notifications."""
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        while self.running:
            await _asyncio.sleep(30)
            if not self.running:
                break
            try:
                from data_analysis_tools import check_alerts
                result = await loop.run_in_executor(None, check_alerts)
                triggered = result.get("triggered", [])
                if triggered:
                    for alrt in triggered:
                        sym = alrt.get("symbol", "")
                        cur = alrt.get("triggered_price", "")
                        if HAS_RICH:
                            console.print(
                                f"\n[bold yellow]⚡ 预警触发[/bold yellow] "
                                f"[cyan]{sym}[/cyan] → {cur}",
                                highlight=False,
                            )
                        try:
                            from notification_tools import send_alert_notification
                            await loop.run_in_executor(None, send_alert_notification, alrt)
                        except Exception as _ne:
                            logger.debug("Alert notification failed: %s", _ne)
            except Exception as _we:
                logger.debug("Alert watchdog error: %s", _we)

    async def run_interactive(self):
        """Run the interactive REPL loop."""
        self.print_header()
        await self._startup_health_check()

        # ── Start MCP servers (non-blocking background task) ─────────────
        if _HAS_MCP and not self._mcp_started:
            self._mcp_started = True
            async def _start_mcp():
                global _mcp_registry
                try:
                    from mcp_client import MCPToolRegistry
                    self._mcp_registry = MCPToolRegistry()
                    results = await self._mcp_registry.start_all()
                    if results:
                        n = self._mcp_registry.register_into(LOCAL_TOOLS, LOCAL_TOOL_SCHEMAS)
                        _mcp_registry = self._mcp_registry
                        if n and HAS_RICH:
                            console.print(f"  [dim]MCP: {n} tools from {len(results)} server(s)[/dim]")
                except Exception as _exc:
                    logger.debug("MCP startup error: %s", _exc)
            asyncio.create_task(_start_mcp())

        # ── Start plugin hot-reload watcher ───────────────────────────────
        if _HAS_PLUGIN:
            global _plugin_watcher
            if _plugin_watcher is None:
                try:
                    _plugin_watcher = PluginWatcher(LOCAL_TOOLS, LOCAL_TOOL_SCHEMAS)
                    asyncio.create_task(_plugin_watcher.start())
                except Exception:
                    pass

        # ── Start alert watchdog (30s background price-alert checker) ─────
        asyncio.create_task(self._alert_watchdog())

        while self.running:
            try:
                if self._pt_session:
                    if self.config.get("input_style", "panel") == "panel":
                        from ui import PanelInputConfig, run_panel_input
                        set_robot_state(RobotState.IDLE)
                        _ml, _cwd, _priv, _etok, _mctx = self._bottom_toolbar_parts()
                        _skills_n = len(SKILLS)
                        _tools_n = len(LOCAL_TOOLS)
                        _ollama_st = ""
                        if getattr(self, "_ollama_alive", False):
                            _om = len(getattr(self, "_installed_models", set()) or [])
                            _ollama_st = f"ollama {_om}m" if _om else "ollama ●"
                        user_input = await asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: run_panel_input(
                                completer=self._pt_completer,
                                history=self._pt_history,
                                config=PanelInputConfig(
                                    theme=self.config.get("input_theme", "auto"),
                                    model_label=_ml,
                                    cwd=_cwd,
                                    privacy=_priv,
                                    est_tokens=_etok,
                                    max_tokens=_mctx,
                                    tools_count=_tools_n,
                                    skills_count=_skills_n,
                                    ollama_status=_ollama_st,
                                ),
                            ),
                        )
                    else:
                        user_input = await asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: self._pt_session.prompt(
                                [("class:prompt", "> ")]
                                if self.config.get("input_style", "panel") == "box"
                                else [("class:prompt", "> ")],
                                bottom_toolbar=self._bottom_toolbar,
                            ),
                        )
                    user_input = user_input.strip()
                elif HAS_RICH:
                    user_input = console.input("[dim]>[/dim] ").strip()
                else:
                    user_input = input("> ").strip()

                if not user_input:
                    continue

                # ── Session recap: show summary if away for 3+ minutes ─────────
                import time as _time
                _now = _time.time()
                if self._last_turn_ts and (_now - self._last_turn_ts) > 180 and self.conversation:
                    _recap_turns = len(self.conversation)
                    if _recap_turns >= 6:
                        _last_ai = next(
                            (m["content"][:120] for m in reversed(self.conversation)
                             if m.get("role") == "assistant" and m.get("content")), None
                        )
                        if _last_ai:
                            _gap = int((_now - self._last_turn_ts) / 60)
                            if HAS_RICH:
                                console.print(
                                    f"  [dim]↩ 回到会话（{_gap}分钟前）— "
                                    f"{_last_ai[:80]}…[/dim]"
                                )
                self._last_turn_ts = _now

                # ── ! prefix: Shell mode ─────────────────────────────────────
                # Run shell command directly, add output to conversation context
                if user_input.startswith("!"):
                    shell_cmd = user_input[1:].strip()
                    if shell_cmd:
                        import subprocess as _subp
                        if HAS_RICH:
                            console.print(f"  [dim]$ {shell_cmd}[/dim]")
                        try:
                            _result = _subp.run(
                                shell_cmd, shell=True, capture_output=True,
                                text=True, timeout=30,
                            )
                            _out = (_result.stdout + _result.stderr).strip()
                            if _out:
                                if HAS_RICH:
                                    console.print(f"[dim]{_out}[/dim]")
                                else:
                                    print(_out)
                                # Inject into conversation context as user observation
                                self.conversation.append({
                                    "role": "user",
                                    "content": f"[shell $ {shell_cmd}]\n{_out}",
                                })
                            # Update shell autocomplete history
                            if self._pt_completer and hasattr(self._pt_completer, "add_shell_history"):
                                self._pt_completer.add_shell_history(shell_cmd)
                        except _subp.TimeoutExpired:
                            if HAS_RICH:
                                console.print("[yellow]  Command timed out (30s)[/yellow]")
                        except Exception as _se:
                            if HAS_RICH:
                                console.print(f"[red]  Error: {_se}[/red]")
                    continue

                # Multi-line input: start with """ to enter multi-line mode
                if user_input == '"""' or user_input.startswith('"""'):
                    lines = []
                    if user_input != '"""':
                        lines.append(user_input[3:])  # Text after opening """
                    if HAS_RICH:
                        console.print("[dim]  Multi-line mode (type \"\"\" to end)[/dim]")
                    else:
                        print('  Multi-line mode (type """ to end)')
                    while True:
                        try:
                            line = input("  ... ")
                            if line.strip() == '"""':
                                break
                            lines.append(line)
                        except (EOFError, KeyboardInterrupt):
                            break
                    user_input = "\n".join(lines).strip()
                    if not user_input:
                        continue

                if user_input.lower() in ("exit", "quit", "q"):
                    if self.conversation and self.config.get("auto_save_sessions"):
                        self.session_mgr.save_session(self.session_id, self.conversation)
                        self.config["last_session_id"] = self.session_id
                        if self._jsonl_store is not None:
                            try:
                                self._jsonl_store.flush_meta(self.session_id)
                            except Exception:
                                pass
                    # Fire SessionEnd hooks
                    if _HAS_JSON_HOOKS:
                        try:
                            _fire_json_hook("SessionEnd", session_id=self.session_id, hooks=_JSON_HOOKS)
                        except Exception:
                            pass
                    _run_event_hook("session_end", {"ARIA_SESSION": self.session_id})
                    if HAS_RICH:
                        console.print("[dim]Goodbye[/dim]")
                    else:
                        print("Goodbye")
                    break

                if self.commands.is_command(user_input):
                    await self.commands.execute(user_input)
                    continue

                # ── Top-level command router (quant CLI style) ─────────────────
                # Intercepts bare keywords like "analyze AAPL" → /analyze AAPL
                # so users don't need to type the slash for common quant workflows.
                _routed = await try_top_level_route(user_input, self.commands)
                if _routed:
                    continue

                # Auto memory trigger: "记住 X" / "remember that X" → silent /note
                _mem_fact = _check_memory_trigger(user_input)
                if _mem_fact:
                    self.commands.cmd_note(_mem_fact)

                await self.send_message(user_input)

            except KeyboardInterrupt:
                _esc_watcher.stop()
                if self._streaming and self.cancel_event:
                    self.cancel_event.set()
                    self._streaming = False
                    if HAS_RICH:
                        console.print("\n[dim]Cancelled[/dim]")
                    else:
                        print("\n  (cancelled)")
                else:
                    if HAS_RICH:
                        console.print("\n[dim]Press ESC to cancel · Ctrl+D to exit[/dim]")
                    else:
                        print("\nESC to cancel · Ctrl+D to exit")
            except EOFError:
                break

        save_config(self.config)

    async def run_prompt(self, prompt: str, json_output: bool = False,
                         fmt: str = "table", output_file: str = None, quiet: bool = False):
        """Run a single prompt (non-interactive / pipe mode)."""
        model = self.config.get("model", "qwen2.5:7b")
        thinking_mode = self.config.get("thinking_mode", "auto")
        auth_token = self.config.get("auth_token")
        user_context = _build_user_context(self.config)

        local_mode = self.config.get("local_mode", False)

        # Dispatch slash commands in -p mode (same as interactive REPL loop).
        # Without this, /memory /note /init /review are sent to the LLM as plain text.
        _stripped_prompt = prompt.strip()
        if self.commands.is_command(_stripped_prompt):
            await self.commands.execute(_stripped_prompt)
            return

        # Auto-inject referenced local file contents before the LLM call (-p mode)
        _file_inject = _try_inject_file_paths(prompt)
        if _file_inject:
            prompt = _file_inject + prompt

        _curr_model_id_p = self.config.get("model", "")
        _model_has_tools_p = False
        if _HAS_MODEL_CAP:
            try:
                _mc_p = get_model_capability(_curr_model_id_p)
                _model_has_tools_p = bool(_mc_p.tool_calls and _mc_p.context_window >= 8192)
            except Exception:
                pass

        # ── Broker setup intent: intercept before LLM / deterministic routing ──
        if _is_broker_setup_intent(prompt):
            _btype_p = _detect_broker_type(prompt)
            if HAS_RICH:
                from apps.cli.utils.market_detect import _BROKER_SETUP_NAMES
                _display_p = _BROKER_SETUP_NAMES.get(_btype_p, ("",))[0] if _btype_p else ""
                _label_p = f"  正在启动{_display_p}配置向导…" if _display_p else "  正在启动券商配置向导…"
                console.print(f"\n[bold]Aria[/bold]  [dim]{_label_p}[/dim]\n")
            await self.commands._cmd_broker_add(_btype_p)
            return

        deterministic: dict = {"success": False}
        if not _model_has_tools_p:
            deterministic = _try_handle_broker_query(prompt)
        if not deterministic.get("success"):
            deterministic = _try_handle_market_snapshot_analysis(prompt)
        if not deterministic.get("success"):
            deterministic = _try_handle_stock_chart_analysis(prompt)
        if deterministic.get("success") or _is_stock_chart_analysis_request(prompt):
            result = deterministic
        else:
            # Spinner for terminal usage: gives visual feedback while the model generates.
            # Only starts when we actually need to call the LLM (not for deterministic responses).
            _prompt_spinner = None
            if HAS_RICH and sys.stdout.isatty():
                try:
                    _prompt_spinner = console.status("", spinner="dots", spinner_style="dim")
                    _prompt_spinner.__enter__()
                except Exception:
                    _prompt_spinner = None
            try:
                if local_mode:
                    result = await stream_ollama(
                        self.config.get("ollama_url", "http://localhost:11434"),
                        prompt, [], model=model,
                    )
                else:
                    # Try AWS, fallback to Ollama
                    result = await stream_chat(
                        self.api_url, prompt, [],
                        model=model, thinking_mode=thinking_mode,
                        user_context=user_context, auth_token=auth_token,
                    )
                    if not result.get("success"):
                        result = await stream_ollama(
                            self.config.get("ollama_url", "http://localhost:11434"),
                            prompt, [], model=model,
                        )
            finally:
                if _prompt_spinner is not None:
                    try:
                        _prompt_spinner.__exit__(None, None, None)
                    except Exception:
                        pass

        # Execute any pending tool calls (write_file / run_command) generated by
        # the code-block fallback in stream_ollama.  This makes -p mode behave
        # the same as interactive mode for code generation tasks.
        pending = result.get("tool_calls_pending", [])
        if pending and result.get("success"):
            for tc in pending:
                tool_name  = tc.get("tool", "")
                tool_params = tc.get("params", {})
                if tool_name in LOCAL_TOOLS:
                    fn = LOCAL_TOOLS[tool_name][0]
                    tr = fn(tool_params)
                    if not quiet:
                        if tool_name == "write_file":
                            _path = tool_params.get("path", "")
                            _status = "Created" if tr.get("success") else "Failed"
                            msg = f"{_status}: {_path}"
                            print(msg if not HAS_RICH else msg, file=sys.stderr)
                        elif tool_name == "run_command":
                            _out = tr.get("data", {}).get("stdout", "") or tr.get("error", "")
                            if _out:
                                print(_out[:2000])

        if json_output or fmt == "json":
            content = json.dumps(result, ensure_ascii=False, indent=2)
        elif fmt == "csv":
            content = f"role,content\nassistant,\"{result.get('response', '').replace(chr(34), chr(34)+chr(34))}\""
        elif fmt == "md":
            content = f"# Aria Code AI Response\n\n{result.get('response', '')}\n"
        else:
            content = result.get("response", "") if result.get("success") else f"Error: {result.get('error', 'Unknown')}"

        # Output routing
        if output_file:
            with open(output_file, "w") as f:
                f.write(content)
            if not quiet:
                console.print(f"[green]Saved to {output_file}[/green]" if HAS_RICH
                              else f"Saved: {output_file}")
        else:
            if not result.get("success") and fmt == "table":
                print(f"Error: {result.get('error', 'Unknown')}", file=sys.stderr)
                sys.exit(1)
            # In default (table) format: render with Rich Markdown when output is
            # a terminal.  This gives properly formatted headings, bold, and tables
            # in interactive use.  When piped/redirected, fall back to plain text
            # for scripting compatibility.
            if HAS_RICH and fmt == "table" and sys.stdout.isatty() and result.get("success"):
                console.print(Markdown(_strip_latex(content)))
            else:
                print(content)

    async def run_watch(self, command_fn, interval: int, cmd_args: str):
        """Run a command repeatedly with interval (like Unix watch)."""
        try:
            while True:
                if not self.config.get("_quiet"):
                    os.system("clear" if os.name == "posix" else "cls")
                    ts = datetime.now().strftime("%H:%M:%S")
                    if HAS_RICH:
                        console.print(f"[dim]Every {interval}s | {ts} | Ctrl+C to stop[/dim]\n")
                    else:
                        print(f"Every {interval}s | {ts} | Ctrl+C to stop\n")

                await command_fn(cmd_args)

                await asyncio.sleep(interval)
        except (KeyboardInterrupt, asyncio.CancelledError):
            if HAS_RICH:
                console.print("\n[dim]Watch stopped[/dim]")
            else:
                print("\nStopped")


# ============================================================================
# CLI Entry Point
# ============================================================================

async def main():
    parser = argparse.ArgumentParser(
        prog="aria-code",
        description="Aria Code — Quantitative Investment Terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                  # Interactive REPL
  %(prog)s --resume                         # Resume last session
  %(prog)s -p "Analyze AAPL technicals"     # Single query
  %(prog)s quote AAPL MSFT                  # Quick quote
  %(prog)s backtest momentum SPY            # Strategy backtest
  %(prog)s --thinking                       # Enable thinking mode
  %(prog)s -p "AAPL PE ratio" --json        # JSON output
  %(prog)s -p "分析AAPL" --output report.md  # Save to file
  %(prog)s -p "报价" --format csv --quiet    # CSV, data only
  %(prog)s quote AAPL --watch 30             # Refresh every 30s
  echo "AAPL MSFT" | %(prog)s -p "比较"      # Unix pipe
        """
    )

    parser.add_argument("--version", "-V", action="version", version=f"aria-code {__version__}")
    parser.add_argument("-p", "--prompt", help="Single prompt (non-interactive)")
    parser.add_argument("--model", help="AI model: sonata|prelude|sonata-thinking|prelude-thinking or full Ollama ID")
    parser.add_argument("--thinking", action="store_true", help="Enable thinking mode")
    parser.add_argument("--json", action="store_true", help="JSON output (with -p)")
    parser.add_argument("--format", choices=["table", "json", "csv", "md"], default="table",
                        help="Output format (default: table)")
    parser.add_argument("--output", "-o", help="Save output to file")
    parser.add_argument("--quiet", "-q", action="store_true", help="Quiet mode (data only, no UI)")
    parser.add_argument("--watch", "-w", type=int, metavar="SECS", help="Refresh interval in seconds")
    parser.add_argument("--url", help="Backend API URL")
    parser.add_argument("--local", action="store_true", help="Local-only mode: skip AWS, use Ollama directly")
    parser.add_argument("--no-banner", action="store_true", help="Skip startup banner (same as --banner off)")
    parser.add_argument("--banner", choices=["full", "compact", "off"], help="Banner mode: full|compact|off")
    parser.add_argument("--resume", action="store_true", help="Resume last session")
    parser.add_argument("--session", help="Load specific session ID")
    parser.add_argument(
        "--dangerously-skip-permissions",
        action="store_true",
        help="Skip ALL tool confirmation prompts this session (use in trusted scripts)",
    )
    parser.add_argument(
        "--allow-tools",
        metavar="TOOLS",
        help="Comma-separated tools to auto-allow this session, e.g. write_file,edit_file",
    )
    parser.add_argument("command", nargs="?", help="Direct command (quote, backtest, etc.)")
    parser.add_argument("args", nargs="*", help="Command arguments")

    args = parser.parse_args()

    config = load_config()

    # ── Start background update check (non-blocking, daemon thread) ──────────
    try:
        from apps.cli.update_check import start_update_check
        _ui_lang_early = config.get("ui_lang", "en") or "en"
        start_update_check(__version__, lang=_ui_lang_early)
    except Exception:
        pass

    # Apply syntax theme from config (P3)
    global _SYNTAX_THEME
    _SYNTAX_THEME = config.get("syntax_theme", "monokai")

    # Apply CLI overrides
    if args.model:
        mkey = resolve_model_key(args.model)
        config["model"] = MODELS[mkey]["id"] if mkey in MODELS else args.model
    if getattr(args, "local", False):
        config["local_mode"] = True
    if getattr(args, "no_banner", False):
        config["banner"] = "off"
    elif getattr(args, "banner", None):
        config["banner"] = args.banner
    if args.thinking:
        config["thinking_mode"] = "thinking"
    if args.url:
        config["api_url"] = args.url

    # --dangerously-skip-permissions: bypass all confirmation prompts this session
    if getattr(args, "dangerously_skip_permissions", False):
        global _auto_approve_session
        _auto_approve_session = True
        if HAS_RICH:
            console.print("[yellow dim]⚠ 所有工具确认已跳过 (--dangerously-skip-permissions)[/yellow dim]")
        else:
            print("⚠ All tool confirmations skipped")

    # --allow-tools: pre-populate per-tool session allow list
    if getattr(args, "allow_tools", None):
        for _t in args.allow_tools.split(","):
            _t = _t.strip()
            if _t:
                _session_always_allow.add(_t)
        if HAS_RICH:
            console.print(f"[dim]Auto-allowed tools: {', '.join(sorted(_session_always_allow))}[/dim]")

    terminal = ArtheraTerminal(config)

    # Resume session
    if args.resume or args.session:
        sid = args.session or config.get("last_session_id")
        if sid:
            data = terminal.session_mgr.load_session(sid)
            if data:
                terminal.conversation = data.get("messages", [])
                terminal.session_id = data["id"]
                title = data.get("metadata", {}).get("title", "Untitled")
                n = len(terminal.conversation)
                if HAS_RICH:
                    console.print(f"[green]Resumed: {title} ({n} messages)[/green]")
                else:
                    print(f"Resumed: {title} ({n} msgs)")

    # Shared output flags
    fmt = args.format if hasattr(args, 'format') else "table"
    output_file = args.output if hasattr(args, 'output') else None
    quiet = args.quiet if hasattr(args, 'quiet') else False
    watch_interval = args.watch if hasattr(args, 'watch') else None

    # Store quiet flag for watch mode
    terminal.config["_quiet"] = quiet

    # Unix pipe: read stdin if not a TTY and prepend to prompt
    piped_input = ""
    if not sys.stdin.isatty():
        piped_input = sys.stdin.read().strip()
        if piped_input and args.prompt:
            args.prompt = f"Context data:\n{piped_input}\n\nUser request: {args.prompt}"
        elif piped_input and not args.prompt:
            args.prompt = piped_input

    # Mode 1: Single prompt
    if args.prompt:
        if watch_interval:
            await terminal.run_watch(
                lambda _: terminal.run_prompt(args.prompt, json_output=args.json, fmt=fmt, output_file=output_file, quiet=quiet),
                watch_interval, ""
            )
        else:
            await terminal.run_prompt(args.prompt, json_output=args.json, fmt=fmt, output_file=output_file, quiet=quiet)
        return

    # Mode 2: Direct command
    if args.command:
        cmd = args.command.lower()
        cmd_args = " ".join(args.args)

        # Build the command function for potential watch wrapping
        async def run_direct_cmd(_):
            await dispatch_direct_command(
                terminal,
                cmd,
                cmd_args,
                json_output=args.json,
                fmt=fmt,
                output_file=output_file,
                quiet=quiet,
            )

        if watch_interval and is_watchable_direct_command(cmd):
            await terminal.run_watch(run_direct_cmd, watch_interval, cmd_args)
        else:
            await run_direct_cmd(None)
        return

    # Mode 3: Interactive REPL (default)
    await terminal.run_interactive()


# ── Football helper functions (module-level, used by cmd_football) ────────────

def _football_standings(league: str) -> None:
    from rich.table import Table
    from rich import box as rich_box
    from rich.panel import Panel
    try:
        from football_data_client import get_standings, LEAGUE_NAMES, _resolve_league
    except ImportError:
        console.print("[red]football_data_client.py 未找到[/red]")
        return

    console.print(f"[dim]获取 {league.upper()} 积分榜…[/dim]")
    data = get_standings(league)
    if not data:
        comp = _resolve_league(league)
        console.print(
            f"[yellow]无法获取数据。请设置 FOOTBALL_DATA_API_KEY:[/yellow]\n"
            f"  1. 访问 football-data.org 免费注册\n"
            f"  2. 在 ~/.aria/.env 中添加:\n"
            f"     [cyan]FOOTBALL_DATA_API_KEY=your_key_here[/cyan]"
        )
        return

    t = Table(
        title=f"[bold]{data['league_name']}[/bold]  {data['season_start'][:4]}/{data['season_end'][:4]}",
        box=rich_box.SIMPLE,
        show_header=True,
        header_style="bold",
        padding=(0, 1),
    )
    t.add_column("#",    width=3,  justify="right")
    t.add_column("球队", width=22)
    t.add_column("场",   width=4,  justify="right")
    t.add_column("胜",   width=3,  justify="right", style="green")
    t.add_column("平",   width=3,  justify="right", style="yellow")
    t.add_column("负",   width=3,  justify="right", style="red")
    t.add_column("进/失", width=7, justify="right")
    t.add_column("净胜", width=5,  justify="right")
    t.add_column("积分", width=5,  justify="right", style="bold cyan")
    t.add_column("近5场", width=7)

    for row in data["table"]:
        gd = row["gd"]
        gd_str = f"+{gd}" if gd > 0 else str(gd)
        form_colored = ""
        for c in (row.get("form") or ""):
            if c == "W":
                form_colored += "[green]W[/green]"
            elif c == "L":
                form_colored += "[red]L[/red]"
            elif c == "D":
                form_colored += "[yellow]D[/yellow]"
            else:
                form_colored += c
        t.add_row(
            str(row["pos"]),
            row["team"],
            str(row["played"]),
            str(row["w"]),
            str(row["d"]),
            str(row["l"]),
            f"{row['gf']}/{row['ga']}",
            gd_str,
            str(row["pts"]),
            form_colored,
        )

    console.print(t)


def _football_fixtures(league: str, days: int = 7) -> None:
    from rich.table import Table
    from rich import box as rich_box
    try:
        from football_data_client import get_fixtures, LEAGUE_NAMES, _resolve_league
    except ImportError:
        console.print("[red]football_data_client.py 未找到[/red]")
        return

    comp = _resolve_league(league)
    league_label = LEAGUE_NAMES.get(comp, comp)
    console.print(f"[dim]获取 {league_label} 未来 {days} 天赛程…[/dim]")

    matches = get_fixtures(league, days)
    if matches is None:
        console.print("[yellow]无法获取数据。请检查 FOOTBALL_DATA_API_KEY 设置。[/yellow]")
        return
    if not matches:
        console.print(f"[dim]未来 {days} 天内暂无赛事[/dim]")
        return

    t = Table(
        title=f"[bold]{league_label}[/bold]  未来 {days} 天赛程",
        box=rich_box.SIMPLE,
        show_header=True,
        header_style="bold",
        padding=(0, 1),
    )
    t.add_column("日期(UTC)", width=11)
    t.add_column("主队",      width=22)
    t.add_column("",          width=3,  justify="center")
    t.add_column("客队",      width=22)
    t.add_column("轮次",      width=5,  justify="right")

    for m in matches:
        t.add_row(
            m["date"],
            m["home"],
            "vs",
            m["away"],
            str(m["matchday"] or m.get("stage", "")),
        )

    console.print(t)


def _football_team(team: str, league: str = "pl") -> None:
    from rich.table import Table
    from rich import box as rich_box
    from rich.panel import Panel
    try:
        from football_data_client import get_team_stats
    except ImportError:
        console.print("[red]football_data_client.py 未找到[/red]")
        return

    console.print(f"[dim]获取 {team} 近期数据 ({league.upper()})…[/dim]")
    stats = get_team_stats(league, team)
    if not stats:
        console.print("[yellow]无法获取球队数据。请检查球队名称和联赛代码。[/yellow]")
        return

    form_colored = ""
    for c in stats["form"]:
        if c == "W":   form_colored += "[bold green]W[/bold green]"
        elif c == "L": form_colored += "[bold red]L[/bold red]"
        elif c == "D": form_colored += "[bold yellow]D[/bold yellow]"

    summary = (
        f"[bold]{stats['team']}[/bold]  |  近 {stats['last_n']} 场\n\n"
        f"  战绩:    {stats['w']}胜  {stats['d']}平  {stats['l']}负\n"
        f"  进球:    {stats['gf']}球  (场均 {stats['avg_gf']})\n"
        f"  失球:    {stats['ga']}球  (场均 {stats['avg_ga']})\n"
        f"  主场进球: 场均 {stats['home_avg_gf']}\n"
        f"  客场进球: 场均 {stats['away_avg_gf']}\n"
        f"  近5场:   {form_colored}"
    )
    console.print(Panel(summary, title="[bold green]⚽ 球队状态[/bold green]", border_style="green"))

    t = Table(box=rich_box.SIMPLE, show_header=True, padding=(0, 1))
    t.add_column("日期",     width=10)
    t.add_column("主队",     width=20)
    t.add_column("比分",     width=7, justify="center", style="bold")
    t.add_column("客队",     width=20)
    t.add_column("",         width=3)

    for r in stats["recent"]:
        result_style = {"W": "green", "D": "yellow", "L": "red"}.get(r["result"], "dim")
        t.add_row(
            r["date"],
            r["home"],
            r["score"],
            r["away"],
            f"[{result_style}]{r['result']}[/{result_style}]",
        )
    console.print(t)


def _football_h2h(t1: str, t2: str, league: str = "pl") -> None:
    from rich.table import Table
    from rich import box as rich_box
    from rich.panel import Panel
    try:
        from football_data_client import get_head_to_head
    except ImportError:
        console.print("[red]football_data_client.py 未找到[/red]")
        return

    console.print(f"[dim]获取 {t1} vs {t2} 历史对决 ({league.upper()})…[/dim]")
    data = get_head_to_head(t1, t2, league)
    if not data:
        console.print("[yellow]未找到历史对决记录。[/yellow]")
        return

    summary = (
        f"[bold]{data['team1']}[/bold] vs [bold]{data['team2']}[/bold]  |  共 {data['total']} 场\n\n"
        f"  {data['team1']} 胜: [green]{data['team1_wins']}[/green]\n"
        f"  平局:         [yellow]{data['draws']}[/yellow]\n"
        f"  {data['team2']} 胜: [red]{data['team2_wins']}[/red]"
    )
    console.print(Panel(summary, title="[bold]⚽ 历史交锋[/bold]", border_style="dim"))

    t = Table(box=rich_box.SIMPLE, show_header=True, padding=(0, 1))
    t.add_column("日期", width=10)
    t.add_column("主队", width=22)
    t.add_column("比分", width=7, justify="center", style="bold cyan")
    t.add_column("客队", width=22)

    for m in data["matches"]:
        t.add_row(m["date"], m["home"], m["score"], m["away"])
    console.print(t)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
        sys.exit(0)
