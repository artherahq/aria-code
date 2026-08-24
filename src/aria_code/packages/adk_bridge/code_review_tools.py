"""Read-only code-review tool surface for Google ADK."""

from __future__ import annotations

from pathlib import PurePath
from typing import Any, Dict


class CodeReviewTools:
    """Review submitted source text without filesystem or shell capabilities."""

    def review_code(self, source: str, filename: str = "snippet.py", language: str = "python") -> Dict[str, Any]:
        """Return deterministic review findings for caller-provided code only.

        Args:
            source: Source code pasted by the user. File paths are never read.
            filename: Display name used to infer language; directory components are ignored.
            language: Source language, currently with additional syntax checks for Python.
        """
        # 惰性导入：agents/__init__.py 会连带拉起 registry 和各 financial agent，
        # 122 个模块、numpy 在内，约 260ms。放在模块级会让 packages.adk_bridge 整体
        # 变重，也会让 agents/ 侧的任何导入故障连累 MarketResearchTools——两个工具面
        # 本该互不影响。market_tools.py 对 DataService 用的是同一套惰性做法。
        from agents.code_review import CodeReviewAgent

        if not isinstance(source, str) or not source.strip():
            return {"success": False, "error": "Non-empty source text is required."}
        if len(source) > CodeReviewAgent.MAX_SOURCE_CHARS:
            return {
                "success": False,
                "error": f"Source exceeds the {CodeReviewAgent.MAX_SOURCE_CHARS}-character review limit; split it by file.",
            }

        safe_filename = PurePath(str(filename or "snippet.py")).name[:160] or "snippet.py"
        findings = CodeReviewAgent.review_source(source, filename=safe_filename, language=str(language or "python"))
        counts = {severity: 0 for severity in ("critical", "high", "medium", "low")}
        for finding in findings:
            counts[finding.severity] += 1
        return {
            "success": True,
            "filename": safe_filename,
            "language": str(language or "python")[:32],
            "findings": [finding.to_dict() for finding in findings],
            "counts": counts,
            "limitations": [
                "Static checks only; it did not read a file, execute code, run tests, or inspect dependencies.",
                "Review findings are not a security certification.",
            ],
        }
