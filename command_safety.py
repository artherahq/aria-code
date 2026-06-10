"""Command normalization, risk classification, and policy checks for Aria Code CLI."""

from __future__ import annotations

from dataclasses import dataclass


SAFE_POLICIES = {"safe", "balanced", "full"}


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    normalized_command: str
    policy: str
    risk: str
    reason: str = ""


def normalize_command(command: str) -> str:
    """Normalize common macOS command aliases used by models/users."""
    normalized = (command or "").strip()
    if normalized.startswith("python ") and not normalized.startswith("python3"):
        normalized = "python3" + normalized[6:]
    elif normalized == "python":
        normalized = "python3"

    if normalized.startswith("pip ") and not normalized.startswith("pip3"):
        normalized = "pip3" + normalized[3:]
    elif normalized == "pip":
        normalized = "pip3"

    return normalized


def classify_command_risk(command: str) -> str:
    """Classify command risk into low/medium/high."""
    normalized = f" {command.lower().strip()}"

    high_risk_patterns = [
        " rm ", " rm -", "chmod ", "chown ", "mkfs", "dd if=", "docker ", "kubectl ",
        "shutdown", "reboot", "systemctl ", "launchctl ", "passwd", "sudo ",
        "git push", "git reset --hard", "git checkout --", "mv ",
    ]
    medium_risk_prefixes = (
        "pip ", "pip3 ", "npm install", "npm i ", "npm run ", "python ", "python3 ",
        "pytest", "make ", "go test", "cargo test", "git commit", "git pull", "git merge",
    )
    low_risk_prefixes = (
        "ls", "pwd", "echo", "cat ", "head ", "tail ", "rg ", "find ", "git status",
        "git diff", "git log", "which ", "whoami", "date", "uname ", "env",
    )

    stripped = normalized.strip()
    if stripped.startswith(low_risk_prefixes):
        return "low"
    if stripped.startswith(medium_risk_prefixes):
        return "medium"
    if any(p in normalized for p in high_risk_patterns):
        return "high"
    return "medium"


def evaluate_command_policy(command: str, policy: str = "safe") -> PolicyDecision:
    """Return policy decision for command execution."""
    normalized = normalize_command(command)
    selected_policy = (policy or "safe").strip().lower()
    if selected_policy not in SAFE_POLICIES:
        selected_policy = "safe"

    risk = classify_command_risk(normalized)
    if selected_policy == "safe" and risk != "low":
        _extra = ""
        _norm_low = normalized.lower()
        if "pip3" in _norm_low or "pip " in _norm_low or _norm_low.startswith("pip"):
            _extra = (
                "\n\n💡 依赖安装提示：运行 `/config set command_policy=balanced` 后重试，"
                "或手动在终端执行该命令。不要重试相同命令！"
            )
        elif "python" in _norm_low and ("~/" in normalized or "/Desktop/" in normalized):
            _extra = (
                "\n\n💡 脚本执行提示：运行 `/config set command_policy=balanced` 后重试。"
                "不要重试相同命令！"
            )
        return PolicyDecision(
            allowed=False,
            normalized_command=normalized,
            policy=selected_policy,
            risk=risk,
            reason=(
                f"Command blocked by policy '{selected_policy}' (risk={risk}): {normalized}. "
                "Use /config set command_policy=balanced or /run --dry-run <cmd> first."
                + _extra
            ),
        )
    if selected_policy == "balanced" and risk == "high":
        return PolicyDecision(
            allowed=False,
            normalized_command=normalized,
            policy=selected_policy,
            risk=risk,
            reason=(
                f"Command blocked by policy '{selected_policy}' (risk={risk}): {normalized}. "
                "Use /config set command_policy=full only if you understand the risk."
            ),
        )

    return PolicyDecision(
        allowed=True,
        normalized_command=normalized,
        policy=selected_policy,
        risk=risk,
        reason="",
    )
