"""Reusable health checks for Aria Code installations."""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import sys
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
        check_status = "ok" if status == "ok" else "warn" if row.get("last_error_category") != "auth" else "err"
        suggestion = "Wait for cooldown or switch provider/API key." if cooldown else ""
        if row.get("last_error_category") == "auth":
            suggestion = "Check provider API key with /apikey list or /apikey set."
        checks.append(_check(f"data_provider:{provider}", check_status, detail, suggestion))
    return checks


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
    cwd = (cwd or Path.cwd()).expanduser()
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

    try:
        from artifacts import artifact_root

        root = artifact_root()
        writable, detail = _is_writable(root)
        checks.append(_check("artifact_root", "ok" if writable else "err", detail, "Set ARIA_ARTIFACT_ROOT to a writable folder."))
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
