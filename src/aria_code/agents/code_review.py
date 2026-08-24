"""Deterministic, read-only code review agent.

The agent deliberately reviews caller-supplied text only.  It never resolves a
path, invokes a shell, or changes a workspace, so it can be used safely from
both the CLI and a Google ADK function tool.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, Dict, Iterable, List

from .base import AgentResult, BaseAgent


@dataclass(frozen=True)
class CodeReviewFinding:
    """A stable, evidence-bearing static-review finding."""

    severity: str
    rule: str
    message: str
    line: int | None
    evidence: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "rule": self.rule,
            "message": self.message,
            "line": self.line,
            "evidence": self.evidence,
        }


class CodeReviewAgent(BaseAgent):
    """Find high-signal correctness and security hazards without an LLM."""

    name = "code_review"
    description = "Read-only deterministic code review for user-supplied snippets and diffs"
    MAX_SOURCE_CHARS = 48_000
    _SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    _SECRET_ASSIGNMENT = re.compile(
        r"\b(?:api[_-]?key|secret|token|password)\b\s*=\s*['\"][^'\"]{8,}['\"]",
        re.IGNORECASE,
    )
    _TOKEN_LITERAL = re.compile(r"\b(?:(?:ghp|github_pat)_[A-Za-z0-9_-]{12,}|sk-[A-Za-z0-9_-]{12,})\b", re.IGNORECASE)
    _SHELL_TRUE = re.compile(r"\b(?:subprocess\.[A-Za-z_]+|os\.system)\b.*\bshell\s*=\s*True", re.IGNORECASE)
    _EVAL_EXEC = re.compile(r"\b(?:eval|exec)\s*\(")
    _PICKLE = re.compile(r"\bpickle\.loads?\s*\(")
    _UNSAFE_YAML = re.compile(r"\byaml\.load\s*\(")
    _TLS_DISABLED = re.compile(r"\brequests\.[A-Za-z_]+\s*\(.*\bverify\s*=\s*False", re.IGNORECASE)

    @staticmethod
    def _safe_filename(filename: str) -> str:
        name = PurePath(str(filename or "snippet.py")).name
        return name[:160] or "snippet.py"

    @classmethod
    def _append(
        cls,
        findings: List[CodeReviewFinding],
        severity: str,
        rule: str,
        message: str,
        line: int | None,
        evidence: str,
    ) -> None:
        if rule == "hardcoded-secret":
            evidence = "<credential value redacted>"
        findings.append(
            CodeReviewFinding(
                severity=severity,
                rule=rule,
                message=message,
                line=line,
                evidence=evidence.strip()[:240],
            )
        )

    @classmethod
    def _scan_lines(cls, lines: Iterable[str], findings: List[CodeReviewFinding]) -> None:
        for number, line in enumerate(lines, start=1):
            if cls._SECRET_ASSIGNMENT.search(line) or cls._TOKEN_LITERAL.search(line):
                cls._append(
                    findings, "high", "hardcoded-secret",
                    "疑似硬编码凭据；改用环境变量或受管密钥，并立即轮换已暴露的值。",
                    number, line,
                )
            if cls._SHELL_TRUE.search(line):
                cls._append(
                    findings, "high", "shell-true",
                    "shell=True 会把不可信输入交给 shell 解释；改用参数列表和严格的输入校验。",
                    number, line,
                )
            if cls._EVAL_EXEC.search(line):
                cls._append(
                    findings, "high", "dynamic-execution",
                    "eval/exec 可执行不可信文本；使用受限解析器或显式分发表。",
                    number, line,
                )
            if cls._PICKLE.search(line):
                cls._append(
                    findings, "high", "unsafe-deserialization",
                    "pickle 反序列化不可信数据可执行任意代码；改用 JSON 或签名的可信工件。",
                    number, line,
                )
            if cls._UNSAFE_YAML.search(line) and "Loader=" not in line:
                cls._append(
                    findings, "high", "unsafe-yaml-load",
                    "yaml.load 未指定安全 Loader；使用 yaml.safe_load。",
                    number, line,
                )
            if cls._TLS_DISABLED.search(line):
                cls._append(
                    findings, "high", "tls-verification-disabled",
                    "TLS 证书校验被关闭；除受控测试外不得使用 verify=False。",
                    number, line,
                )

    @classmethod
    def review_source(
        cls,
        source: str,
        *,
        filename: str = "snippet.py",
        language: str = "python",
        is_diff: bool = False,
    ) -> List[CodeReviewFinding]:
        """Review source text or an already-provided diff without touching disk."""
        if not isinstance(source, str) or not source.strip():
            return [CodeReviewFinding("medium", "empty-input", "没有可审查的代码内容。", None, "")]
        if len(source) > cls.MAX_SOURCE_CHARS:
            return [CodeReviewFinding(
                "medium", "input-truncated",
                f"审查输入超过 {cls.MAX_SOURCE_CHARS} 字符；请按文件或提交拆分后复查。",
                None, "",
            )]

        findings: List[CodeReviewFinding] = []
        lines = source.splitlines()
        scan_lines = [line[1:] for line in lines if line.startswith("+") and not line.startswith("+++")] if is_diff else lines
        cls._scan_lines(scan_lines, findings)

        inferred_python = language.lower() in {"py", "python"} or cls._safe_filename(filename).endswith(".py")
        if inferred_python and not is_diff:
            try:
                tree = ast.parse(source, filename=cls._safe_filename(filename))
            except SyntaxError as exc:
                cls._append(
                    findings, "critical", "python-syntax-error",
                    "Python 代码无法解析，运行前会失败。",
                    exc.lineno, (exc.text or "").strip(),
                )
            else:
                for node in ast.walk(tree):
                    if isinstance(node, ast.ExceptHandler) and node.type is None:
                        cls._append(
                            findings, "medium", "bare-except",
                            "裸 except 会吞掉系统退出和中断；至少捕获 Exception，并记录或处理错误。",
                            node.lineno, "except:",
                        )

        unique = {(item.rule, item.line, item.evidence): item for item in findings}
        return sorted(unique.values(), key=lambda item: (cls._SEVERITY_ORDER[item.severity], item.line or 0, item.rule))

    @classmethod
    def format_findings(cls, findings: List[CodeReviewFinding]) -> str:
        if not findings:
            return "确定性检查未发现已覆盖规则中的问题；仍需执行测试并进行语义审查。"
        rows = []
        for finding in findings:
            location = f"第 {finding.line} 行" if finding.line else "输入级"
            rows.append(f"[{finding.severity.upper()}] {location} {finding.rule}: {finding.message}")
        return "\n".join(rows)

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        source = str(data.get("source") or data.get("content") or data.get("diff") or "")
        filename = self._safe_filename(str(data.get("filename") or symbol or "snippet.py"))
        findings = self.review_source(
            source,
            filename=filename,
            language=str(data.get("language") or "python"),
            is_diff=bool(data.get("is_diff")),
        )
        return AgentResult(
            agent=self.name,
            symbol=filename,
            analysis=self.format_findings(findings),
            confidence=0.99 if source else 0.0,
            signal="HOLD",
            key_points=[f"{item.severity}: {item.rule}" for item in findings[:8]],
            data_used={"filename": filename, "finding_count": len(findings)},
            degraded=False,
            provenance=["deterministic_static_review"],
            limitations=["只审查调用方提供的文本；不会读取文件、执行命令或运行测试。"],
        )
