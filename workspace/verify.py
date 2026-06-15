"""Verification planning for Aria Code workspaces."""

from __future__ import annotations

import json
import pathlib
import shlex
import subprocess
from dataclasses import dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class VerificationPlan:
    commands: List[str]
    reason: str


class VerificationPlanner:
    """Infer focused verification commands from changed files and project files."""

    def __init__(self, root: str | pathlib.Path = ".") -> None:
        self.root = pathlib.Path(root).expanduser().resolve()

    def infer(self, paths: Iterable[str] | None = None) -> VerificationPlan:
        changed = [p for p in (paths or []) if p]
        if not changed:
            changed = self._git_changed_files()
        commands: List[str] = []
        reasons: List[str] = []

        py_files = [p for p in changed if p.endswith(".py")]
        if py_files:
            quoted = " ".join(shlex.quote(p) for p in py_files[:20])
            commands.append(f"python3 -m py_compile {quoted}")
            reasons.append("Python files changed")
            if self._has_any("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini") or (self.root / "tests").exists():
                commands.append("python3 -m pytest -q")

        frontend_files = [
            p for p in changed
            if p.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css"))
        ]
        if frontend_files and (self.root / "package.json").exists():
            scripts = self._package_scripts()
            reasons.append("Node/TypeScript files changed")
            if "test" in scripts:
                commands.append("npm test")
            if "build" in scripts:
                commands.append("npm run build")
            elif (self.root / "tsconfig.json").exists():
                commands.append("npx tsc --noEmit")

        if not commands:
            if (self.root / "pyproject.toml").exists() or (self.root / "tests").exists():
                commands.append("python3 -m pytest -q")
                reasons.append("Python project detected")
            elif (self.root / "package.json").exists():
                scripts = self._package_scripts()
                if "test" in scripts:
                    commands.append("npm test")
                    reasons.append("Node project detected")
                elif "build" in scripts:
                    commands.append("npm run build")
                    reasons.append("Node project detected")

        deduped = list(dict.fromkeys(commands))
        reason = "; ".join(dict.fromkeys(reasons)) if reasons else "No focused check inferred"
        return VerificationPlan(deduped, reason)

    def _has_any(self, *names: str) -> bool:
        return any((self.root / name).exists() for name in names)

    def _package_scripts(self) -> dict:
        try:
            data = json.loads((self.root / "package.json").read_text(encoding="utf-8"))
            return data.get("scripts", {}) if isinstance(data.get("scripts", {}), dict) else {}
        except Exception:
            return {}

    def _git_changed_files(self) -> List[str]:
        try:
            proc = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        except Exception:
            pass
        try:
            proc = subprocess.run(
                ["git", "status", "--short"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if proc.returncode != 0:
                return []
            files = []
            for line in proc.stdout.splitlines():
                if not line.strip():
                    continue
                files.append(line[3:].strip())
            return files
        except Exception:
            return []
