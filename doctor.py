"""Reusable health checks for Aria Code installations."""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str = ""
    suggestion: str = ""


@dataclass(frozen=True)
class DoctorReport:
    checks: List[DoctorCheck] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for check in self.checks if check.status == "ok")

    @property
    def warnings(self) -> int:
        return sum(1 for check in self.checks if check.status == "warn")

    @property
    def errors(self) -> int:
        return sum(1 for check in self.checks if check.status == "err")

    @property
    def status(self) -> str:
        if self.errors:
            return "err"
        if self.warnings:
            return "warn"
        return "ok"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "passed": self.passed,
            "warnings": self.warnings,
            "errors": self.errors,
            "checks": [check.__dict__ for check in self.checks],
        }


def _check(name: str, status: str, detail: str = "", suggestion: str = "") -> DoctorCheck:
    return DoctorCheck(name=name, status=status, detail=detail, suggestion=suggestion)


def _format_age(seconds: float | int | None) -> str:
    if not seconds or seconds <= 0:
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{sec:02d}s"
    hours, rem = divmod(minutes, 60)
    return f"{hours}h{rem:02d}m"


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _is_writable(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".aria-doctor-", dir=path, delete=True) as handle:
            handle.write(b"ok")
        return True, str(path)
    except Exception as exc:
        return False, str(exc)


def _is_path_ready(path: Path, *, expect_file: bool = False) -> tuple[str, str]:
    """Read-only path readiness check for runtime/config/cache diagnostics."""
    try:
        path = path.expanduser()
        if path.exists():
            if expect_file and not path.is_file():
                return "err", f"{path} exists but is not a file"
            if not expect_file and not path.is_dir():
                return "err", f"{path} exists but is not a directory"
            writable_target = path.parent if expect_file else path
            if os.access(writable_target, os.W_OK):
                return "ok", str(path)
            return "warn", f"{path} exists but may not be writable"
        parent = path.parent
        if parent.exists() and os.access(parent, os.W_OK):
            return "warn", f"{path} not created yet; parent writable"
        return "warn", f"{path} not found"
    except Exception as exc:
        return "err", str(exc)


def _capture_cmd(cmd: list[str], timeout: float = 2.0) -> tuple[int, str]:
    try:
        result = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        return result.returncode, (result.stdout or result.stderr or "").strip()
    except Exception as exc:
        return 1, str(exc)


def _expand_home(raw: str | None) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if value == "~":
        return str(Path.home())
    if value.startswith("~/") or value.startswith("~\\"):
        return str(Path.home() / value[2:])
    return value


def _npm_config_home() -> tuple[str, str]:
    for key in ("npm_config_aria_code_home", "npm_config_aria_home", "npm_config_ariacode_home"):
        value = _expand_home(os.getenv(key))
        if value:
            return str(Path(value).expanduser().resolve()), f"env:{key}"
    npm = shutil.which("npm")
    if not npm:
        return "", ""
    code, out = _capture_cmd([npm, "config", "get", "aria-code:home"], timeout=1.5)
    value = _expand_home(out)
    if code == 0 and value and value.lower() not in ("undefined", "null"):
        return str(Path(value).expanduser().resolve()), "npm-config:aria-code:home"
    return "", ""


def _platform_data_dir() -> Path:
    system = platform.system().lower()
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / "Aria Code"
    if system == "windows":
        return Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "AriaCode"
    return Path(os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "aria-code"


def _platform_config_dir() -> Path:
    system = platform.system().lower()
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / "Aria Code"
    if system == "windows":
        return Path(os.getenv("APPDATA") or (Path.home() / "AppData" / "Roaming")) / "AriaCode"
    return Path(os.getenv("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "aria-code"


def _platform_cache_dir() -> Path:
    system = platform.system().lower()
    if system == "darwin":
        return Path.home() / "Library" / "Caches" / "Aria Code"
    if system == "windows":
        return Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "AriaCode" / "Cache"
    return Path(os.getenv("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "aria-code"


def _resolve_runtime_paths() -> dict[str, Any]:
    legacy = Path.home() / ".aria-code"
    source = ""
    install_raw = os.getenv("ARIA_HOME") or os.getenv("ARIA_CODE_HOME") or ""
    if install_raw:
        install_dir = Path(_expand_home(install_raw)).expanduser().resolve()
        source = "env:ARIA_HOME" if os.getenv("ARIA_HOME") else "env:ARIA_CODE_HOME"
    else:
        npm_home, npm_source = _npm_config_home()
        if npm_home:
            install_dir = Path(npm_home)
            source = npm_source
        elif legacy.exists():
            install_dir = legacy
            source = "legacy-existing"
        else:
            install_dir = _platform_data_dir()
            source = "platform-default"

    config_dir = Path(_expand_home(os.getenv("ARIA_CONFIG_DIR"))).expanduser().resolve() if os.getenv("ARIA_CONFIG_DIR") else _platform_config_dir()
    cache_dir = Path(_expand_home(os.getenv("ARIA_CACHE_DIR"))).expanduser().resolve() if os.getenv("ARIA_CACHE_DIR") else _platform_cache_dir()
    info_file = install_dir / ".npm-install-info.json"
    config_info_file = config_dir / "install.json"
    legacy_info_file = legacy / ".npm-install-info.json"
    info_candidates = []
    for candidate in (info_file, config_info_file, legacy_info_file):
        if candidate not in info_candidates:
            info_candidates.append(candidate)
    return {
        "install_dir": install_dir,
        "install_dir_source": source,
        "legacy_install_dir": legacy,
        "venv_dir": install_dir / ".venv",
        "venv_py": install_dir / ".venv" / ("Scripts/python.exe" if platform.system().lower() == "windows" else "bin/python"),
        "aria_cli": install_dir / "aria_cli.py",
        "config_dir": config_dir,
        "cache_dir": cache_dir,
        "info_file": info_file,
        "config_info_file": config_info_file,
        "legacy_info_file": legacy_info_file,
        "info_candidates": info_candidates,
    }


def npm_runtime_checks(*, cwd: Optional[Path] = None) -> List[DoctorCheck]:
    """Return npm launcher/runtime path diagnostics."""
    checks: List[DoctorCheck] = []
    paths = _resolve_runtime_paths()
    cwd = (cwd or Path.cwd()).expanduser().resolve()
    source_cli = cwd / "aria_cli.py"
    source_venv_py = cwd / ".venv" / ("Scripts/python.exe" if platform.system().lower() == "windows" else "bin/python")
    using_source_checkout = source_cli.is_file() and not paths["aria_cli"].is_file()

    node = shutil.which("node")
    if node:
        code, version = _capture_cmd([node, "--version"], timeout=1.5)
        checks.append(_check("npm_runtime:node", "ok" if code == 0 else "warn", f"{node} {version}".strip()))
    else:
        checks.append(_check("npm_runtime:node", "warn", "node not found", "Install Node.js if you use the npm launcher."))

    npm = shutil.which("npm")
    if npm:
        _code, version = _capture_cmd([npm, "--version"], timeout=1.5)
        prefix_code, prefix = _capture_cmd([npm, "config", "get", "prefix"], timeout=1.5)
        root_code, root = _capture_cmd([npm, "root", "-g"], timeout=1.5)
        detail = f"{npm} v{version or '?'}"
        if prefix_code == 0 and prefix:
            detail += f"; prefix={prefix}"
        if root_code == 0 and root:
            detail += f"; root={root}"
        checks.append(_check("npm_runtime:npm", "ok", detail))
    else:
        checks.append(_check("npm_runtime:npm", "warn", "npm not found", "Install Node.js/npm if you use npm install -g aria-code."))

    install_status, install_detail = _is_path_ready(paths["install_dir"])
    install_suggestion = ""
    if using_source_checkout:
        install_suggestion = f"Set ARIA_HOME={cwd} when launching through npm, or run npm install -g aria-code."
    elif not paths["aria_cli"].exists():
        install_suggestion = "Run npm install -g aria-code or set ARIA_HOME to the cloned repo."
    checks.append(_check(
        "npm_runtime:install_dir",
        "warn" if using_source_checkout else ("ok" if paths["aria_cli"].exists() else install_status),
        (
            f"{install_detail}; source={paths['install_dir_source']}"
            + (f"; source_checkout={cwd}" if using_source_checkout else "")
        ),
        install_suggestion,
    ))

    actual_cli = paths["aria_cli"] if paths["aria_cli"].is_file() else (source_cli if source_cli.is_file() else paths["aria_cli"])
    checks.append(_check(
        "npm_runtime:aria_cli",
        "ok" if actual_cli.is_file() else "err",
        str(actual_cli),
        "Repair with: node $(npm root -g)/aria-code/scripts/postinstall.js" if not actual_cli.is_file() else "",
    ))

    actual_venv_py = paths["venv_py"] if paths["venv_py"].is_file() else (source_venv_py if source_venv_py.is_file() else paths["venv_py"])
    checks.append(_check(
        "npm_runtime:venv",
        "ok" if actual_venv_py.is_file() else "warn",
        str(actual_venv_py),
        "Run npm repair/update-engine or reinstall dependencies." if not actual_venv_py.is_file() else "",
    ))

    info_hit = next((p for p in paths["info_candidates"] if p.is_file()), None)
    checks.append(_check(
        "npm_runtime:install_info",
        "ok" if info_hit else "warn",
        str(info_hit) if info_hit else "none found; checked " + ", ".join(str(p) for p in paths["info_candidates"]),
        "Run npm repair/update-engine to rewrite install metadata." if not info_hit else "",
    ))

    config_status, config_detail = _is_path_ready(paths["config_dir"])
    cache_status, cache_detail = _is_path_ready(paths["cache_dir"])
    checks.append(_check("npm_runtime:config_dir", config_status, config_detail))
    checks.append(_check("npm_runtime:cache_dir", cache_status, cache_detail))
    return checks


def _check_ollama(url: str, timeout: float = 1.5) -> DoctorCheck:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{url.rstrip('/')}/api/tags", timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        models = [str(model.get("name", "")) for model in data.get("models", []) if model.get("name")]
        if models:
            return _check("ollama", "ok", f"{len(models)} models: {', '.join(models[:4])}")
        return _check("ollama", "warn", "running but no models installed", "ollama pull qwen2.5-coder:7b")
    except Exception as exc:
        return _check("ollama", "warn", f"not reachable at {url}: {exc}", "Start Ollama or configure a cloud provider.")


def provider_health_checks(snapshot: Optional[List[Dict[str, Any]]] = None) -> List[DoctorCheck]:
    """Convert data provider health state into doctor checks."""
    if snapshot is None:
        try:
            from packages.aria_services.provider_health import GLOBAL_PROVIDER_HEALTH

            snapshot = GLOBAL_PROVIDER_HEALTH.snapshot()
        except Exception:
            snapshot = []

    if not snapshot:
        return [
            _check(
                "data_provider_health",
                "warn",
                "no provider calls recorded in this session",
                "Run /quote, /ta, /analyze, or /report to populate provider health.",
            )
        ]

    checks: List[DoctorCheck] = []
    for row in snapshot:
        provider = str(row.get("provider") or "provider")
        status = str(row.get("status") or "unknown")
        failures = int(row.get("failures") or 0)
        cooldown = bool(row.get("cooldown_active"))
        remaining = int(row.get("cooldown_remaining_seconds") or 0)
        detail = status
        if failures:
            detail += f", failures={failures}"
        if cooldown:
            detail += f", cooldown={remaining}s"
        if row.get("last_error"):
            detail += f", last={row.get('last_error')}"
        last_success = _format_age((time.time() - float(row.get("last_success_at"))) if row.get("last_success_at") else None)
        if last_success:
            detail += f", last_ok={last_success}"
        check_status = "ok" if status == "ok" else "warn" if row.get("last_error_category") != "auth" else "err"
        suggestion = "Wait for cooldown or switch provider/API key." if cooldown else ""
        if row.get("last_error_category") == "auth":
            suggestion = "Check provider API key with /apikey list or /apikey set."
        checks.append(_check(f"data_provider:{provider}", check_status, detail, suggestion))
    return checks


def provider_health_summary(snapshot: Optional[List[Dict[str, Any]]] = None) -> DoctorCheck:
    """Summarise provider health into one dashboard-style check."""
    if snapshot is None:
        try:
            from packages.aria_services.provider_health import GLOBAL_PROVIDER_HEALTH

            snapshot = GLOBAL_PROVIDER_HEALTH.snapshot()
        except Exception:
            snapshot = []
    try:
        from packages.aria_services.provider_health import summarize_provider_health

        summary = summarize_provider_health(snapshot)
        return _check("provider_health_summary", summary.status, summary.detail, summary.suggestion)
    except Exception:
        if not snapshot:
            return _check(
                "provider_health_summary",
                "warn",
                "no provider calls recorded in this session",
                "Run /quote, /ta, /analyze, or /report to populate provider health.",
            )
        total = len(snapshot)
        return _check(
            "provider_health_summary",
            "warn",
            f"{total} providers",
            "Run /doctor --network or inspect /apikey.",
        )


def _iter_required_modules() -> Iterable[tuple[str, str]]:
    yield "aiohttp", "async HTTP"
    yield "rich", "terminal UI"
    yield "prompt_toolkit", "interactive input"
    yield "requests", "HTTP client"
    yield "pandas", "dataframes"
    yield "numpy", "numeric processing"
    yield "yfinance", "US/HK/global market data"
    yield "akshare", "China market data"


def run_doctor(
    config: Optional[Dict[str, Any]] = None,
    *,
    cwd: Optional[Path] = None,
    check_network: bool = False,
) -> DoctorReport:
    """Run local-first diagnostics without mutating user configuration."""

    config = config or {}
    cwd = (cwd or Path.cwd()).expanduser().resolve()
    checks: List[DoctorCheck] = []

    pyver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 10):
        checks.append(_check("python", "ok", f"{pyver} on {platform.system()}"))
    else:
        checks.append(_check("python", "err", f"{pyver} is unsupported", "Install Python 3.10 or newer."))

    for module, purpose in _iter_required_modules():
        if _has_module(module):
            checks.append(_check(f"package:{module}", "ok", purpose))
        else:
            checks.append(_check(f"package:{module}", "err", f"{purpose} missing", f"pip install {module}"))

    for module in ("data_service", "artifacts", "report_generator", "backtest_report"):
        checks.append(
            _check(f"module:{module}", "ok" if _has_module(module) else "err", "importable" if _has_module(module) else "missing from install")
        )

    checks.extend(npm_runtime_checks(cwd=cwd))

    try:
        from artifacts import artifact_root, artifact_summary

        root = artifact_root()
        writable, detail = _is_writable(root)
        checks.append(_check("artifact_root", "ok" if writable else "err", detail, "Set ARIA_ARTIFACT_ROOT to a writable folder."))
        summary = artifact_summary(root)
        total = int(summary.get("total") or 0)
        total_size = int(summary.get("total_size_bytes") or 0)
        by_kind = summary.get("by_kind") or {}
        detail_bits = [f"{total} artifacts", f"{total_size} bytes"]
        if by_kind:
            detail_bits.extend(f"{kind}={count}" for kind, count in list(by_kind.items())[:4])
        checks.append(
            _check(
                "artifact_inventory",
                "ok" if total else "warn",
                ", ".join(detail_bits),
                "Run /artifacts stats or generate a report to populate local outputs.",
            )
        )
    except Exception as exc:
        checks.append(_check("artifact_root", "err", str(exc)))

    config_dir = Path.home() / ".arthera"
    config_file = config_dir / "config.json"
    if config_file.exists():
        checks.append(_check("config", "ok", str(config_file)))
    else:
        checks.append(_check("config", "warn", f"{config_file} not found", "Run aria-code once or use /config set key=value."))

    data_sharing = bool(config.get("data_sharing", False))
    feedback_upload = bool(config.get("feedback_upload", False))
    privacy_detail = f"data_sharing={data_sharing}, feedback_upload={feedback_upload}"
    checks.append(_check("privacy", "ok", privacy_detail))

    try:
        from datasources.router import DataRouter

        sources = DataRouter().list_sources()
        configured = [src["name"] for src in sources if src.get("configured")]
        missing = [src["name"] for src in sources if src.get("needs_key") and not src.get("configured")]
        status = "ok" if configured else "warn"
        detail = f"configured: {', '.join(configured) or 'none'}"
        suggestion = f"optional keys missing: {', '.join(missing)}" if missing else ""
        checks.append(_check("datasources", status, detail, suggestion))
    except Exception as exc:
        checks.append(_check("datasources", "warn", str(exc)))

    checks.append(provider_health_summary())
    checks.extend(provider_health_checks())

    if check_network:
        checks.append(_check_ollama(str(config.get("ollama_url") or "http://localhost:11434")))
    else:
        checks.append(_check("ollama", "warn", "network check skipped", "Run /doctor --network to verify local Ollama."))

    if (cwd / ".ariarc").exists():
        checks.append(_check("project_config", "ok", str(cwd / ".ariarc")))
    else:
        checks.append(_check("project_config", "warn", ".ariarc not found", "Optional: add .ariarc for project-local settings."))

    return DoctorReport(checks=checks)


def format_doctor_plain(report: DoctorReport) -> str:
    marks = {"ok": "OK", "warn": "WARN", "err": "ERR"}
    lines = ["Aria Code doctor"]
    for check in report.checks:
        suffix = f" — {check.detail}" if check.detail else ""
        if check.suggestion:
            suffix += f" ({check.suggestion})"
        lines.append(f"{marks.get(check.status, check.status.upper()):<4} {check.name}{suffix}")
    lines.append(f"{report.passed} passed · {report.warnings} warnings · {report.errors} errors")
    return "\n".join(lines)
