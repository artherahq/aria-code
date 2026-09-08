"""
Verification & Self-Repair Loop for Aria Code Agent.

Provides automated post-edit validation (type checking, linting, tests)
and formats high-signal repair directives for self-healing agent turns
mirroring Anthropic Claude Code and OpenAI agent harness standards.
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class VerificationCheckResult:
    """Result of a single automated verification command."""
    name: str
    command: str
    passed: bool
    exit_code: int
    output: str
    extracted_errors: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass
class VerificationSummary:
    """Consolidated report across all triggered verification checks."""
    all_passed: bool
    checks: List[VerificationCheckResult]
    repair_directive: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "all_passed": self.all_passed,
            "checks": [
                {
                    "name": c.name,
                    "command": c.command,
                    "passed": c.passed,
                    "exit_code": c.exit_code,
                    "extracted_errors": c.extracted_errors,
                    "duration": f"{c.duration_seconds:.2f}s",
                }
                for c in self.checks
            ],
            "repair_directive": self.repair_directive,
        }


def extract_key_errors(output: str, max_errors: int = 5) -> List[str]:
    """Extract actionable error lines and traceback fragments from check output."""
    lines = output.splitlines()
    errors: List[str] = []

    error_patterns = [
        re.compile(r"(?:error|exception|fail|failed|syntaxerror|typeerror|attributeerror|nameerror|importerror):.*", re.IGNORECASE),
        re.compile(r"^E\s+.*"), # Pytest failure line
        re.compile(r"^\s*File\s+\".*\",\s+line\s+\d+.*"), # Traceback frame
        re.compile(r".*:\d+:\d+:\s+error:.*"), # Mypy/TypeScript/Rust error line
    ]

    for line in lines:
        stripped = line.strip()
        if any(pat.search(stripped) for pat in error_patterns):
            if stripped not in errors and len(stripped) > 5:
                errors.append(stripped)
                if len(errors) >= max_errors:
                    break

    return errors


class VerificationLoop:
    """
    Automated verification orchestrator.
    Detects project type and runs fast checks after code changes.
    """

    def __init__(
        self,
        workspace_root: Optional[pathlib.Path | str] = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.workspace_root = pathlib.Path(workspace_root or os.getcwd()).resolve()
        self.timeout_seconds = timeout_seconds

    def detect_available_checks(self, modified_files: Optional[List[str]] = None) -> List[Tuple[str, List[str]]]:
        """Infer suitable verification commands based on project files."""
        checks: List[Tuple[str, List[str]]] = []
        root = self.workspace_root

        # Python project checks
        has_py = any(p.endswith(".py") for p in (modified_files or [])) or any(root.glob("*.py"))
        if has_py:
            # 1. Syntax check on modified files
            if modified_files:
                py_mods = [f for f in modified_files if f.endswith(".py") and os.path.exists(f)]
                if py_mods:
                    checks.append(("python-syntax", ["python3", "-m", "py_compile"] + py_mods[:5]))

            # 2. Pytest if present
            if (root / "pytest.ini").exists() or (root / "tests").is_dir():
                if shutil.which("pytest"):
                    checks.append(("pytest", ["pytest", "-q", "--maxfail=3"]))

        # Node/TypeScript checks
        if (root / "package.json").exists():
            npm_bin = shutil.which("npm")
            if npm_bin:
                # If package.json has test or typecheck
                checks.append(("npm-test", [npm_bin, "test", "--if-present"]))

        # Rust checks
        if (root / "Cargo.toml").exists() and shutil.which("cargo"):
            checks.append(("cargo-check", ["cargo", "check", "--quiet"]))

        # Go checks
        if (root / "go.mod").exists() and shutil.which("go"):
            checks.append(("go-test", ["go", "test", "./..."]))

        return checks

    def run_check(self, name: str, cmd: List[str]) -> VerificationCheckResult:
        """Run a single check command with timeout."""
        import time
        t0 = time.time()
        try:
            res = subprocess.run(
                cmd,
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            elapsed = time.time() - t0
            passed = (res.returncode == 0)
            output = res.stdout + ("\n" + res.stderr if res.stderr else "")
            errors = extract_key_errors(output) if not passed else []

            return VerificationCheckResult(
                name=name,
                command=" ".join(cmd),
                passed=passed,
                exit_code=res.returncode,
                output=output[:4000],
                extracted_errors=errors,
                duration_seconds=elapsed,
            )
        except subprocess.TimeoutExpired:
            return VerificationCheckResult(
                name=name,
                command=" ".join(cmd),
                passed=False,
                exit_code=-1,
                output=f"Verification timed out after {self.timeout_seconds}s",
                extracted_errors=["Timeout: Check took too long to complete"],
                duration_seconds=self.timeout_seconds,
            )
        except Exception as exc:
            return VerificationCheckResult(
                name=name,
                command=" ".join(cmd),
                passed=False,
                exit_code=-1,
                output=str(exc),
                extracted_errors=[f"Failed to execute check: {exc}"],
                duration_seconds=time.time() - t0,
            )

    def verify(self, modified_files: Optional[List[str]] = None) -> VerificationSummary:
        """Execute all detected checks and formulate a repair directive if any fail."""
        checks_to_run = self.detect_available_checks(modified_files)
        results: List[VerificationCheckResult] = []
        all_passed = True

        for name, cmd in checks_to_run:
            res = self.run_check(name, cmd)
            results.append(res)
            if not res.passed:
                all_passed = False
                break # Stop on first critical failure for fast feedback

        repair_directive = None
        if not all_passed:
            failed_check = next((c for c in results if not c.passed), None)
            if failed_check:
                err_text = "\n  - ".join(failed_check.extracted_errors) if failed_check.extracted_errors else failed_check.output[:500]
                repair_directive = (
                    f"⚠️ Automated verification `{failed_check.name}` failed with exit code {failed_check.exit_code}:\n"
                    f"Command: `{failed_check.command}`\n"
                    f"Errors detected:\n  - {err_text}\n\n"
                    f"Please review and fix these issues using `apply_patch` or `edit_file` before continuing."
                )

        return VerificationSummary(
            all_passed=all_passed,
            checks=results,
            repair_directive=repair_directive,
        )


# Set by procedural_trace.wire_trace_reporters() — None disables reporting
# entirely, and only that function should assign it, only after the user has
# explicitly opted in. See runtime/procedural_trace.py's docstring.
_TRACE_REPORTER: Optional[Callable[[Dict[str, Any], str], None]] = None


def set_trace_reporter(
    reporter: Optional[Callable[[Dict[str, Any], str], None]]
) -> None:
    """Register a best-effort execution-trace reporter for verification runs."""
    global _TRACE_REPORTER
    _TRACE_REPORTER = reporter


def tool_verify_changes(params: dict) -> dict:
    """Run automated post-edit verification and report a repair directive on failure.

    Tool function exposed to the LLM, following the same `params: dict -> dict`
    contract as `runtime/subagent.py`'s `tool_spawn_task` — a caller (or the
    model itself, once this is registered in the tool schema) asks "did my
    changes hold up" instead of assuming they did.

    Args:
        modified_files: paths changed in this turn (optional — an empty list
            still runs whole-project checks like pytest/npm test if present).
        workspace_root: project root to run checks from (defaults to cwd).
        timeout_seconds: per-check timeout (default 30s).
    """
    modified_files = params.get("modified_files") or []
    if not isinstance(modified_files, list):
        return {"success": False, "error": "modified_files must be a list of paths"}

    workspace_root = params.get("workspace_root")
    timeout_seconds = int(params.get("timeout_seconds") or 30)

    loop = VerificationLoop(workspace_root=workspace_root, timeout_seconds=timeout_seconds)
    summary = loop.verify(modified_files=modified_files)

    if _TRACE_REPORTER is not None:
        try:
            goal = (
                f"verify changes to {', '.join(modified_files[:5])}"
                if modified_files else "verify workspace"
            )
            _TRACE_REPORTER(summary.to_dict(), goal)
        except Exception:
            pass

    result = summary.to_dict()
    result["success"] = summary.all_passed
    return result


# Registered into aria_cli.py's LOCAL_TOOLS the same way SUBAGENT_TOOLS is
# (see the "Register subagent tools" block) — a dict of
# name -> (handler, short_description) plus the model-facing JSON schema.
VERIFY_TOOLS = {
    "verify_changes": (tool_verify_changes, "Run automated post-edit verification (syntax, tests, type checks) and get a repair directive if something broke"),
}

VERIFY_SCHEMAS = [
    {
        "name": "verify_changes",
        "description": (
            "Run automated post-edit verification on the current project: detects and runs "
            "the relevant checks (Python syntax + pytest, npm test, cargo check, go test) "
            "based on what's present. Call this after making code changes to confirm they "
            "actually hold up, instead of assuming an edit worked. On failure, returns a "
            "repair_directive naming exactly what broke."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "modified_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Paths changed in this turn. An empty list still runs whole-project checks (pytest/npm test) if present.",
                },
                "workspace_root": {
                    "type": "string",
                    "description": "Project root to run checks from. Defaults to the current working directory.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Per-check timeout in seconds. Default 30.",
                },
            },
            "required": [],
        },
    },
]
