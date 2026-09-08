"""崩溃记录：本地优先，不向任何第三方发送。

问题：CLI 未捕获异常时只会打一个裸 traceback 就退出，什么都不留。用户来报
bug 时能提供的往往只有"它崩了"——版本、provider、走到哪一步、当时的配置
全部丢失，只能靠来回追问重建现场。

为什么不直接接 Sentry：aria-code 是 local-first 的终端工具，用户的堆栈里可能
带着文件路径、项目名、甚至提示词片段。默认把这些发到第三方服务器，跟这个
产品的定位相悖。这里的做法是**先在本地留全**，用户愿意时再自己把文件贴出来；
需要 Sentry 的部署可以自行在外层包一层，本模块不阻止。

写入 ~/.aria-code/crashes/（随 aria_home() 走，老用户仍落在 ~/.arthera/）。

脱敏是**尽力而为**，不是保证：环境变量里的密钥、明显的 key 形态会被打码，
但堆栈的局部变量里可能藏着任何东西。所以文件默认只在本地，且提示用户贴出前
自己看一眼。把这条说清楚，比假装"已完全脱敏"要诚实。
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = ["write_crash_report", "install_excepthook", "recent_crashes", "redact"]

_MAX_KEPT = 20

# 环境变量名里出现这些片段，值一律打码
_SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")

# 值本身长得像密钥的，即使变量名无辜也打码
_SECRET_VALUE_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|AIza[A-Za-z0-9_-]{30,}|[A-Za-z0-9_-]{32,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,})\b"
)


def redact(text: str) -> str:
    """给明显的密钥形态打码。尽力而为——见模块文档。"""
    return _SECRET_VALUE_RE.sub("<redacted>", text or "")


def _crash_dir() -> Path:
    try:
        from aria_code.packages.aria_core.paths import aria_home
        root = aria_home()
    except Exception:
        root = Path.home() / ".aria-code"
    return root / "crashes"


def _safe_env() -> Dict[str, str]:
    """只收跟 aria 相关的环境变量，且对疑似密钥打码。

    不收全量环境：那里面有用户的 PATH、代理地址、其它项目的凭证，跟这次崩溃
    多半无关，收进来只是扩大暴露面。
    """
    out: Dict[str, str] = {}
    for key, value in os.environ.items():
        if not any(p in key.upper() for p in ("ARIA", "ARTHERA", "OLLAMA", "LMSTUDIO")):
            continue
        if any(h in key.upper() for h in _SECRET_HINTS):
            out[key] = f"<set:{len(value)}chars>" if value else "<empty>"
        else:
            out[key] = redact(value)[:200]
    return out


def write_crash_report(
    exc: BaseException,
    *,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    """把一次崩溃写成本地 JSON。返回文件路径；写入失败返回 None。

    写入失败绝不抛异常——崩溃记录器自己再崩一次，只会把原始错误从屏幕上
    冲掉，让排查更难。
    """
    try:
        crash_dir = _crash_dir()
        crash_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = crash_dir / f"crash-{stamp}.json"

        try:
            from aria_code.aria_cli import __version__ as _ver
        except Exception:
            _ver = "unknown"

        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": _ver,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "error_type": type(exc).__name__,
            "error": redact(str(exc))[:1000],
            "traceback": redact("".join(traceback.format_exception(exc)))[-8000:],
            "argv": [redact(a) for a in sys.argv[:12]],
            "env": _safe_env(),
            "context": {k: redact(str(v))[:500] for k, v in (context or {}).items()},
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _prune(crash_dir)
        return path
    except Exception:
        return None


def _prune(crash_dir: Path, keep: int = _MAX_KEPT) -> None:
    """只保留最近 N 份。崩溃记录是排查素材，不是需要永久保存的资产；
    无上限增长的日志目录本身就会变成一个问题。"""
    try:
        files = sorted(crash_dir.glob("crash-*.json"), key=lambda p: p.stat().st_mtime)
        for stale in files[:-keep]:
            stale.unlink(missing_ok=True)
    except Exception:
        pass


def recent_crashes(limit: int = 5) -> List[Dict[str, Any]]:
    """最近几次崩溃的摘要，供 /doctor 之类的命令展示。"""
    out: List[Dict[str, Any]] = []
    try:
        files = sorted(_crash_dir().glob("crash-*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files[:limit]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                out.append({
                    "file": str(path),
                    "timestamp": data.get("timestamp", ""),
                    "error_type": data.get("error_type", ""),
                    "error": (data.get("error", "") or "")[:160],
                    "version": data.get("version", ""),
                })
            except Exception:
                continue
    except Exception:
        pass
    return out


def install_excepthook(*, context_provider=None) -> None:
    """接管未捕获异常：先落盘，再按原样打印。

    **保留原有输出**——用户和现有的排查习惯都依赖屏幕上那个 traceback，
    悄悄换掉只会让人以为程序静默失败了。这里只是在它之前多留一份记录。

    KeyboardInterrupt 不记：那是用户主动中断，不是故障。
    """
    previous = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            ctx = {}
            if context_provider is not None:
                try:
                    ctx = context_provider() or {}
                except Exception:
                    ctx = {}
            path = write_crash_report(exc_value, context=ctx)
            if path is not None:
                print(f"\n[crash report] {path}", file=sys.stderr)
        previous(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
