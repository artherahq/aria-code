"""Startup banner and status-label rendering for Aria Code.

All functions accept only primitive values (strings, dicts, ints, bools)
so aria_cli.py can do data gathering while this module owns all display.

Public surface
--------------
    render_compact_banner(...)   — one-line banner (banner=compact)
    render_full_banner(...)      — robot-face + grid panel (banner=full)
    render_try_hints(console)    — "try analyze AAPL · /help" line below panel
    privacy_status_label(...)
    control_status_label(...)
    ollama_status_label(...)
    bottom_toolbar_parts(...)
"""

from __future__ import annotations

import os
import shutil
from typing import Optional


def _t(key: str, lang: str) -> str:
    """Thin wrapper around i18n.t() — tolerates import failure."""
    try:
        from apps.cli.i18n import t as _translate
        return _translate(key, lang=lang)
    except Exception:
        _fallback = {
            "sharing_on": "sharing on", "local_only": "local-only",
            "network_on": "network on", "network_off": "network off",
            "privacy": "privacy", "ollama_online": "Ollama online",
            "ollama_offline": "Ollama offline", "cloud": "cloud",
            "local_first_agent": "local-first agent",
            "model": "model", "workspace": "workspace",
            "mode": "mode", "status": "status",
            "tools": "tools", "skills": "skills", "quant": "quant",
            "try": "try", "local": "local", "lite": "lite",
        }
        return _fallback.get(key, key)


# ── Status label helpers ───────────────────────────────────────────────────────

def privacy_status_label(config: dict, rich: bool = False, lang: str = "") -> str:
    sharing = bool(config.get("data_sharing", False))
    upload  = bool(config.get("feedback_upload", False))
    _lang   = lang or config.get("ui_lang", "en")
    if sharing and upload:
        label = _t("sharing_on", _lang)
        return _mark("accent", label) if rich else label
    return _t("local_only", _lang)


def control_status_label(config: dict, rich: bool = False, lang: str = "") -> str:
    _lang      = lang or config.get("ui_lang", "en")
    permission = config.get("permission_mode", "workspace-write")
    net_key    = "network_on" if bool(config.get("network_enabled", True)) else "network_off"
    network    = _t(net_key, _lang)
    priv_label = _t("privacy", _lang)
    privacy    = privacy_status_label(config, rich=rich, lang=_lang)
    return f"{permission} · {network} · {priv_label} {privacy}"


def ollama_status_label(
    ollama_alive: bool,
    installed_models: set,
    config: dict,
    rich: bool = False,
    lang: str = "",
) -> str:
    _lang = lang or config.get("ui_lang", "en")
    count = len(installed_models)
    has_cloud = bool(
        config.get("auth_token")
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
    )
    cloud_word = _t("cloud", _lang)
    cloud_rich = _mark("muted", f"· {cloud_word} ✓")
    cloud_tag = (
        f"  {cloud_rich}" if rich
        else f"  · {cloud_word} ✓"
    ) if has_cloud else ""
    model_word = _t("model_singular" if count == 1 else "model_plural", _lang)
    if ollama_alive:
        label = f"{_t('ollama_online', _lang)} · {count} {model_word}"
        return f"{label}{cloud_tag}"
    base = _t("ollama_offline", _lang)
    if has_cloud:
        return (f"{base}  {cloud_rich}" if rich else f"{base}  · {cloud_word} ✓")
    return base


def bottom_toolbar_parts(
    conversation: list,
    config: dict,
    actual_model: Optional[str],
    get_model_cfg_fn,
) -> tuple:
    """Return (model_label, cwd, privacy, est_tokens, max_ctx)."""
    est_tokens = sum(len(m.get("content", "")) for m in conversation) // 3
    mkey       = config.get("model", "qwen2.5:7b")
    max_ctx    = get_model_cfg_fn(mkey).get("num_ctx", 16384)
    cwd = os.getcwd()
    home = os.path.expanduser("~")
    if cwd.startswith(home):
        cwd = "~" + cwd[len(home):]
    model_label = actual_model or mkey
    if len(model_label) > 28:
        model_label = "…" + model_label[-27:]
    if len(cwd) > 34:
        cwd = "…" + cwd[-33:]
    privacy = "sharing" if bool(config.get("data_sharing", False)) else "local-only"
    return model_label, cwd, privacy, est_tokens, max_ctx


# ── Banner renderers ───────────────────────────────────────────────────────────

_MASCOT = "[bold #C08050]◉[/bold #C08050]"


def _is_light_theme() -> bool:
    try:
        from .robot import detect_theme
        return detect_theme() == "light"
    except Exception:
        return False


def _style_tag(style: str, text: str) -> str:
    if style == "dim":
        return f"[dim]{text}[/dim]"
    return f"[{style}]{text}[/{style}]"


def _banner_style(role: str) -> str:
    if _is_light_theme():
        return {
            "primary": "bold #1F2328",
            "muted": "#57606A",
            "subtle": "#6E7781",
            "dim": "#8C959F",
            "accent": "#9A6700",
        }.get(role, "#57606A")
    return {
        "primary": "bold",
        "muted": "dim",
        "subtle": "dim",
        "dim": "dim",
        "accent": "#C08050",
    }.get(role, "dim")


def _mark(role: str, text: str) -> str:
    return _style_tag(_banner_style(role), text)


def _normalize_dim_markup(markup: str) -> str:
    """Make caller-supplied [dim] markup readable in light terminals."""
    if not _is_light_theme():
        return markup
    return markup.replace("[dim]", "[#57606A]").replace("[/dim]", "[/#57606A]")


def render_compact_banner(
    *,
    version: str,
    model_label: str,
    runtime: str,       # "cloud" | "local"
    cwd: str,
    control_status_rich: str,
    tool_count: int,
    update_notice: Optional[str] = None,
    console,
    has_rich: bool,
    lang: str = "en",
) -> None:
    if not has_rich:
        print(f"  Aria Code v{version}  {model_label}  {cwd}")
        return
    _rt_word = _t("cloud", lang) if runtime == "cloud" else _t("local", lang)
    _rt = _mark("muted", _rt_word)
    _tools_word = _t("tools", lang)
    _mascot = _style_tag(f"bold {_banner_style('accent')}", "◉")
    console.print(
        f"  {_mascot} {_mark('primary', 'Aria Code')} {_mark('subtle', f'v{version}')}"
        f"  {_mark('dim', '·')} {model_label} {_rt}"
        f"  {_mark('dim', '·')} {_mark('muted', cwd)}"
    )
    console.print(_mark("muted", f"  {_normalize_dim_markup(control_status_rich)} · {tool_count} {_tools_word} · /help"))
    if update_notice:
        console.print(f"  {update_notice}")


def render_full_banner(
    *,
    version: str,
    rt_label: str,              # Rich markup: "GPT-OSS 120B  [dim]cloud[/dim]"
    cwd: str,
    control_status_rich: str,
    ollama_status_rich: str,
    tool_count: int,
    skill_count: int,
    auto_healed_from: str = "",
    current_id: str = "",
    badge: str = "",
    installed_models: frozenset = frozenset(),
    best_lite_id: str = "",     # model ID to suggest when lite badge + not installed
    update_notice: Optional[str] = None,
    console,
    has_rich: bool,
    rich_box,
    lang: str = "en",
) -> None:
    _lfa    = _t("local_first_agent", lang)
    _model  = _t("model", lang)
    _ws     = _t("workspace", lang)
    _tools  = _t("tools", lang)
    _tip    = _t("tip", lang)
    _amatch = _t("auto_matched", lang)

    if not has_rich:
        print(f"\n  Aria Code v{version}  {_lfa}")
        print(f"  {_model:<10}{rt_label}")
        print(f"  {_ws:<10}{cwd}")
        print(f"  {control_status_rich}")
        print(f"  {ollama_status_rich}")
        print("─" * 60)
        return

    from rich.table import Table
    from rich.text import Text

    # Left column: hand-tuned pixel mascot (pure text — works in every terminal,
    # no image deps). Keep it quiet; copper is reserved for state accents.
    from .robot import ROBOT_ROW_COUNT, get_robot_row

    _face = Text()
    for _idx in range(ROBOT_ROW_COUNT):
        for _style, _text in get_robot_row(2, _idx):
            _face.append(_text, style=_style)
        if _idx < ROBOT_ROW_COUNT - 1:
            _face.append("\n")

    # Right column: Claude Code-like essentials only. Operational detail is
    # available in the bottom toolbar and slash commands, so startup stays calm.
    _info_lines = [
        f"{_mark('primary', 'Aria Code')}  {_mark('subtle', f'v{version} · {_lfa}')}",
        f"{_normalize_dim_markup(rt_label)}",
        f"{_mark('muted', cwd)}",
    ]
    if auto_healed_from:
        _info_lines.append(
            f"{_mark('muted', f'⚙ {_amatch}')}  "
            f"[yellow]{auto_healed_from}[/yellow]"
            f" {_mark('dim', '→')} [bold]{current_id}[/bold]"
        )
    if badge == "Fast" and best_lite_id and best_lite_id not in installed_models:
        lite_word = _t("lite", lang)
        _info_lines.append(
            f"[yellow]{_tip}[/yellow]  {_mark('muted', f'{lite_word} model — ')}"
            f"[bold]ollama pull {best_lite_id}[/bold] {_mark('muted', f'for full {_tools}')}"
        )
    if update_notice:
        _info_lines.append(update_notice)

    _info = Text.from_markup("\n".join(_info_lines))

    _grid = Table.grid(padding=(0, 3))
    _grid.add_column(no_wrap=True, vertical="top")
    _grid.add_column(vertical="middle")
    _grid.add_row(_face, _info)

    console.print(_grid)


def render_try_hints(console, has_rich: bool, lang: str = "en") -> None:
    """Show natural-language examples that demonstrate LLM-native usage."""
    if not has_rich:
        return
    tcols = shutil.get_terminal_size((80, 24)).columns
    # Hints are natural language sentences — NOT slash commands.
    # The point: users should feel free to just type what they want.
    if lang == "zh":
        hints = [
            (_mark("accent", "宁德时代今天怎么样?"),          19),  # 9 CJK×2 + 1
            (_mark("accent", "帮我分析一下持仓风险"),          20),  # 10 CJK×2
            (_mark("accent", "生成今日A股晨报看板"),           19),  # 9 CJK×2 + "A"×1
            (_mark("subtle", "/help"),                         5),
        ]
    else:
        hints = [
            (_mark("accent", "How's NVDA this week?"),          18),
            (_mark("accent", "Analyze my portfolio risk"),       24),
            (_mark("accent", "Generate a morning brief HTML"),   30),
            (_mark("subtle", "/help"),                            5),
        ]
    sep   = f"  {_mark('dim', '·')}  "
    parts = []
    used  = 8
    for hint_rich, hint_len in hints:
        cost = hint_len + (5 if parts else 0)
        if used + cost <= tcols - 4:
            parts.append(hint_rich)
            used += cost
    _try_word = _t("try", lang)
    console.print(f"  {_mark('subtle', _try_word)}  " + sep.join(parts) + "\n")
