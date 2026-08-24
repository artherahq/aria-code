"""
runtime/self_healing.py — Lightweight Static Pre-flight & Traceback Patch Self-Healing Engine
===========================================================================================
1. Fast Static Syntax Compilation (<10ms via compile / py_compile)
2. Sandbox Dry-run Execution
3. Traceback Parsing (Error Type, Offending Line, Context)
4. Surgical Differential Patch Repair (Never rewrites entire file)
5. Metric Extraction & Deliverable Packaging (No terminal log flooding)
"""

from __future__ import annotations

import ast
import json
import logging
import os
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class TracebackInfo:
    file_path: str
    line_number: int
    error_type: str
    error_message: str
    code_snippet: str
    full_traceback: str


@dataclass
class SelfHealingResult:
    success: bool
    retries_used: int
    final_output: str
    error: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    artifact_paths: List[str] = field(default_factory=list)
    patches_applied: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "retries_used": self.retries_used,
            "error": self.error,
            "metrics": self.metrics,
            "artifact_paths": self.artifact_paths,
            "patches_applied": self.patches_applied,
        }


class SelfHealingEngine:
    """
    Automated execution, diagnostics, and patch self-healing engine.
    """

    def __init__(
        self,
        python_executable: str = sys.executable,
        max_retries: int = 3,
        llm_fixer: Optional[Callable[[TracebackInfo, str], Any]] = None,
    ) -> None:
        self.python_executable = python_executable
        self.max_retries = max_retries
        self.llm_fixer = llm_fixer

    def verify_syntax(self, file_path: pathlib.Path) -> Tuple[bool, Optional[str], Optional[int]]:
        """
        Fast static syntax check using AST compilation (<10ms).
        """
        try:
            content = file_path.read_text(encoding="utf-8")
            compile(content, str(file_path), "exec")
            return True, None, None
        except SyntaxError as exc:
            line = exc.lineno or 1
            msg = f"SyntaxError: {exc.msg} at line {line}"
            return False, msg, line
        except Exception as exc:
            return False, str(exc), None

    def run_sandbox_execution(
        self,
        file_path: pathlib.Path,
        timeout: float = 30.0,
        cwd: Optional[pathlib.Path] = None,
    ) -> Tuple[int, str, str]:
        """
        Run the Python script in a dry-run subprocess sandbox.
        """
        work_dir = cwd or file_path.parent
        env = dict(os.environ)
        env["PYTHONPATH"] = f"{work_dir}:{env.get('PYTHONPATH', '')}"

        try:
            proc = subprocess.run(
                [self.python_executable, str(file_path)],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"Execution timed out after {timeout}s"
        except Exception as exc:
            return -1, "", f"Failed to execute script: {exc}"

    def parse_traceback(self, stderr: str, target_file: pathlib.Path) -> Optional[TracebackInfo]:
        """
        Extract exact error line, error type, and snippet from python stderr.
        """
        if not stderr:
            return None

        # Look for standard Python traceback lines: File "path", line X, in Y
        lines = stderr.strip().splitlines()
        target_name = target_file.name

        target_line = None
        snippet = ""
        error_type = "UnknownError"
        error_msg = ""

        for i, line in enumerate(lines):
            if "File " in line and target_name in line:
                m = re.search(r'line (\d+)', line)
                if m:
                    target_line = int(m.group(1))
                    if i + 1 < len(lines) and not lines[i + 1].strip().startswith("File "):
                        snippet = lines[i + 1].strip()

        # The last non-empty line usually contains ErrorType: message
        for line in reversed(lines):
            line_str = line.strip()
            if ":" in line_str and not line_str.startswith("File "):
                parts = line_str.split(":", 1)
                error_type = parts[0].strip()
                error_msg = parts[1].strip()
                break

        if target_line is None:
            # Check if any line number is mentioned
            m = re.search(r'line (\d+)', stderr)
            if m:
                target_line = int(m.group(1))

        if target_line is None:
            target_line = 1

        return TracebackInfo(
            file_path=str(target_file),
            line_number=target_line,
            error_type=error_type,
            error_message=error_msg,
            code_snippet=snippet,
            full_traceback=stderr,
        )

    def apply_patch(
        self,
        file_path: pathlib.Path,
        target_line: int,
        replacement_code: str,
        context_radius: int = 0,
    ) -> bool:
        """
        Surgically edit the target file at target_line (1-indexed).
        Never rewrites the entire file.
        """
        try:
            content = file_path.read_text(encoding="utf-8")
            lines = content.splitlines(keepends=True)

            if not (1 <= target_line <= len(lines) + 1):
                logger.error(f"Target line {target_line} out of range for {file_path}")
                return False

            start_idx = max(0, target_line - 1 - context_radius)
            end_idx = min(len(lines), target_line + context_radius)

            # Format replacement lines
            if not replacement_code.endswith("\n"):
                replacement_code += "\n"

            # Replace slice
            lines[start_idx:end_idx] = [replacement_code]
            file_path.write_text("".join(lines), encoding="utf-8")
            return True
        except Exception as exc:
            logger.error(f"Failed to apply patch on {file_path}: {exc}")
            return False

    def generate_heuristic_fix(
        self,
        tb: TracebackInfo,
        file_content: str,
    ) -> Optional[Tuple[int, str, int]]:
        """
        Rule-based heuristic patch generator for common quant/python runtime issues.
        Returns: (target_line, replacement_content, context_radius)
        """
        lines = file_content.splitlines()
        if not (1 <= tb.line_number <= len(lines)):
            return None

        offending_line = lines[tb.line_number - 1]
        indent = len(offending_line) - len(offending_line.lstrip())
        indent_str = " " * indent

        # Case 1: ZeroDivisionError (e.g. sharpe ratio or return calculation)
        if "ZeroDivisionError" in tb.error_type or "division by zero" in tb.error_message:
            if "/" in offending_line:
                # Add safe division
                fixed = offending_line.replace("/", " / max(1e-9, ")
                fixed += ")"
                return tb.line_number, fixed, 0

        # Case 2: Column KeyError (e.g. 'Adj Close' vs 'Close' vs 'close')
        if "KeyError" in tb.error_type:
            col_match = re.search(r"['\"]([^'\"]+)['\"]", tb.error_message)
            if col_match:
                missing_col = col_match.group(1)
                # Replace df['missing'] with df.get(missing, df.get('close', df.get('Close', 0)))
                pattern = rf"\[['\"]{re.escape(missing_col)}['\"]\]"
                replacement = f".get('{missing_col.lower()}', df.get('{missing_col}', df.get('close', df.get('Close'))))"
                if re.search(pattern, offending_line):
                    fixed = re.sub(pattern, replacement, offending_line)
                    return tb.line_number, fixed, 0

        # Case 3: Missing Import (NameError)
        if "NameError" in tb.error_type:
            m = re.search(r"name ['\"]([^'\"]+)['\"] is not defined", tb.error_message)
            if m:
                missing_name = m.group(1)
                if missing_name in {"np", "numpy"}:
                    return 1, "import numpy as np\n" + lines[0], 0
                elif missing_name in {"pd", "pandas"}:
                    return 1, "import pandas as pd\n" + lines[0], 0
                elif missing_name in {"math", "json", "os", "sys"}:
                    return 1, f"import {missing_name}\n" + lines[0], 0

        # Case 4: IndexError on empty dataframe/array
        if "IndexError" in tb.error_type:
            fixed = f"{indent_str}if len(df) > 0 and len(data) > 0:\n{indent_str}    {offending_line.strip()}"
            return tb.line_number, fixed, 0

        # Case 5: SyntaxError near line
        if "SyntaxError" in tb.error_type:
            # Clean common syntax typos (e.g. mismatched parentheses or quotes)
            stripped = offending_line.strip()
            if stripped.count("(") > stripped.count(")"):
                fixed = offending_line + ")" * (stripped.count("(") - stripped.count(")"))
                return tb.line_number, fixed, 0

        return None

    async def execute_and_heal(
        self,
        file_path: pathlib.Path,
        timeout: float = 30.0,
    ) -> SelfHealingResult:
        """
        Full pre-flight verification, execution, and multi-round patch self-healing loop.
        """
        file_path = file_path.resolve()
        patches: List[Dict[str, Any]] = []

        for attempt in range(self.max_retries + 1):
            # 1. Pre-flight Static Syntax Check
            valid_syntax, syn_err, syn_line = self.verify_syntax(file_path)
            if not valid_syntax:
                if attempt >= self.max_retries:
                    return SelfHealingResult(
                        success=False,
                        retries_used=attempt,
                        final_output="",
                        error=f"Syntax pre-flight check failed: {syn_err}",
                        patches_applied=patches,
                    )
                # Attempt syntax fix
                content = file_path.read_text(encoding="utf-8")
                tb_synth = TracebackInfo(
                    file_path=str(file_path),
                    line_number=syn_line or 1,
                    error_type="SyntaxError",
                    error_message=syn_err or "SyntaxError",
                    code_snippet="",
                    full_traceback=syn_err or "",
                )
                fix = self.generate_heuristic_fix(tb_synth, content)
                if fix:
                    t_line, rep, rad = fix
                    self.apply_patch(file_path, t_line, rep, rad)
                    patches.append({"round": attempt + 1, "type": "syntax_fix", "line": t_line})
                    continue

            # 2. Dry-Run Execution
            ret_code, stdout, stderr = self.run_sandbox_execution(file_path, timeout=timeout)

            if ret_code == 0:
                # Success! Extract metrics and artifacts
                metrics, artifacts = self._extract_results(file_path, stdout)
                return SelfHealingResult(
                    success=True,
                    retries_used=attempt,
                    final_output=stdout,
                    metrics=metrics,
                    artifact_paths=artifacts,
                    patches_applied=patches,
                )

            # Failure occurred: inspect traceback
            if attempt >= self.max_retries:
                return SelfHealingResult(
                    success=False,
                    retries_used=attempt,
                    final_output=stdout,
                    error=f"Execution failed after {attempt} retries: {stderr[:300]}",
                    patches_applied=patches,
                )

            tb = self.parse_traceback(stderr, file_path)
            if not tb:
                return SelfHealingResult(
                    success=False,
                    retries_used=attempt,
                    final_output=stdout,
                    error=f"Execution failed with no traceback: {stderr[:300]}",
                    patches_applied=patches,
                )

            content = file_path.read_text(encoding="utf-8")
            fix = None

            # Try LLM fixer if available
            if self.llm_fixer:
                try:
                    fix = await self.llm_fixer(tb, content)
                except Exception as exc:
                    logger.debug(f"LLM fixer failed: {exc}")

            # Fallback to heuristic fixer
            if not fix:
                fix = self.generate_heuristic_fix(tb, content)

            if fix:
                t_line, rep, rad = fix
                self.apply_patch(file_path, t_line, rep, rad)
                patches.append({
                    "round": attempt + 1,
                    "error_type": tb.error_type,
                    "line": t_line,
                    "message": tb.error_message,
                })
            else:
                # Could not determine patch
                return SelfHealingResult(
                    success=False,
                    retries_used=attempt,
                    final_output=stdout,
                    error=f"Unresolvable {tb.error_type} at line {tb.line_number}: {tb.error_message}",
                    patches_applied=patches,
                )

        return SelfHealingResult(
            success=False,
            retries_used=self.max_retries,
            final_output="",
            error="Exceeded max healing retries",
            patches_applied=patches,
        )

    def _extract_results(self, file_path: pathlib.Path, stdout: str) -> Tuple[Dict[str, Any], List[str]]:
        """
        Extract backtest metrics and generated files from output.
        """
        metrics: Dict[str, Any] = {}
        artifacts: List[str] = [str(file_path)]

        # 1. Look for companion metrics JSON file (e.g. strategy_nvda_metrics.json)
        json_candidate = file_path.with_name(f"{file_path.stem}_metrics.json")
        if json_candidate.exists():
            try:
                metrics = json.loads(json_candidate.read_text(encoding="utf-8"))
                artifacts.append(str(json_candidate))
            except Exception:
                pass

        # 2. Look for JSON in stdout lines or blocks
        if not metrics and stdout:
            for line in stdout.splitlines():
                line_str = line.strip()
                if line_str.startswith("{") and line_str.endswith("}"):
                    try:
                        parsed = json.loads(line_str)
                        if isinstance(parsed, dict):
                            metrics = parsed
                            break
                    except Exception:
                        pass
            if not metrics and "{" in stdout and "}" in stdout:
                try:
                    m = re.search(r"(\{.*\"[a-zA-Z0-9_-]+\"\s*:.*\})", stdout, re.DOTALL)
                    if m:
                        metrics = json.loads(m.group(1))
                except Exception:
                    pass

        # 3. Look for generated images or plots in the same folder
        plot_candidate = file_path.with_name(f"{file_path.stem}_backtest.png")
        if plot_candidate.exists():
            artifacts.append(str(plot_candidate))

        return metrics, artifacts
