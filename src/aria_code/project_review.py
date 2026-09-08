"""Safe, read-only review of user-supplied source archives.

The reviewer never executes project code, installs dependencies, follows
symlinks, or reads files outside the extraction root.  It produces a bounded
deterministic baseline that an optional LLM layer can enrich later.
"""

from __future__ import annotations

import io
import json
import tarfile
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from aria_code.agents.code_review import CodeReviewAgent


MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_EXTRACTED_BYTES = 80 * 1024 * 1024
MAX_FILES = 2_000
MAX_REVIEW_FILES = 300
MAX_FILE_BYTES = 512 * 1024

_IGNORED_PARTS = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "dist", "build",
    ".next", ".venv", "venv", "__pycache__", "coverage", "Pods",
}
_SENSITIVE_NAMES = {
    ".env", ".npmrc", ".pypirc", "credentials.json", "service-account.json",
    "id_rsa", "id_dsa",
}
_TEXT_EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".swift", ".kt", ".kts",
    ".java", ".go", ".rs", ".rb", ".php", ".cs", ".c", ".h", ".cpp",
    ".hpp", ".m", ".mm", ".sh", ".bash", ".zsh", ".sql", ".graphql",
    ".json", ".yaml", ".yml", ".toml", ".xml", ".html", ".css", ".scss",
    ".md", ".txt",
}
_LANGUAGE_BY_EXTENSION = {
    ".py": "Python", ".pyi": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".swift": "Swift", ".kt": "Kotlin",
    ".kts": "Kotlin", ".java": "Java", ".go": "Go", ".rs": "Rust", ".rb": "Ruby",
    ".php": "PHP", ".cs": "C#", ".c": "C", ".h": "C/C++", ".cpp": "C++",
    ".hpp": "C++", ".sql": "SQL", ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
}
_TEST_MARKERS = ("test", "tests", "spec", "__tests__")


class ProjectReviewError(ValueError):
    """Raised when an uploaded project violates the review boundary."""


@dataclass(frozen=True)
class ReviewFile:
    path: str
    content: str
    language: str


def _safe_member_path(raw: str) -> PurePosixPath:
    normalized = raw.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ProjectReviewError("Archive contains an unsafe path.")
    return path


def _should_ignore(path: PurePosixPath) -> bool:
    return any(part in _IGNORED_PARTS for part in path.parts)


def _is_sensitive(path: PurePosixPath) -> bool:
    name = path.name.lower()
    return name in _SENSITIVE_NAMES or name.startswith(".env.") or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}


def _decode_text(data: bytes) -> str | None:
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def _archive_entries(payload: bytes, filename: str) -> Iterable[tuple[str, bytes]]:
    lowered = filename.lower()
    stream = io.BytesIO(payload)
    if lowered.endswith(".zip"):
        try:
            with zipfile.ZipFile(stream) as archive:
                declared = [item for item in archive.infolist() if not item.is_dir()]
                if len(declared) > MAX_FILES or sum(max(item.file_size, 0) for item in declared) > MAX_EXTRACTED_BYTES:
                    raise ProjectReviewError("Expanded project exceeds the file-count or size limit.")
                for info in declared:
                    if info.is_dir():
                        continue
                    if info.file_size > MAX_FILE_BYTES or info.file_size < 0:
                        continue
                    # Unix symlink bit; links are never materialized or followed.
                    if (info.external_attr >> 16) & 0o170000 == 0o120000:
                        continue
                    yield info.filename, archive.read(info)
        except (zipfile.BadZipFile, RuntimeError) as exc:
            raise ProjectReviewError("Invalid or encrypted ZIP archive.") from exc
        return
    if lowered.endswith((".tar.gz", ".tgz", ".tar")):
        mode = "r:gz" if lowered.endswith((".tar.gz", ".tgz")) else "r:"
        try:
            with tarfile.open(fileobj=stream, mode=mode) as archive:
                declared = [item for item in archive.getmembers() if item.isfile()]
                if len(declared) > MAX_FILES or sum(max(item.size, 0) for item in declared) > MAX_EXTRACTED_BYTES:
                    raise ProjectReviewError("Expanded project exceeds the file-count or size limit.")
                for member in declared:
                    if not member.isfile() or member.issym() or member.islnk():
                        continue
                    if member.size > MAX_FILE_BYTES or member.size < 0:
                        continue
                    source = archive.extractfile(member)
                    if source is not None:
                        yield member.name, source.read()
        except tarfile.TarError as exc:
            raise ProjectReviewError("Invalid TAR archive.") from exc
        return
    raise ProjectReviewError("Only .zip, .tar, .tar.gz, and .tgz projects are supported.")


def _collect_files(payload: bytes, filename: str) -> tuple[list[ReviewFile], dict[str, Any]]:
    if not payload or len(payload) > MAX_ARCHIVE_BYTES:
        raise ProjectReviewError(f"Project archive must be between 1 byte and {MAX_ARCHIVE_BYTES} bytes.")
    files: list[ReviewFile] = []
    total_files = total_bytes = skipped_sensitive = skipped_binary = 0
    languages: Counter[str] = Counter()
    for raw_path, data in _archive_entries(payload, filename):
        path = _safe_member_path(raw_path)
        if _should_ignore(path):
            continue
        total_files += 1
        total_bytes += len(data)
        if total_files > MAX_FILES or total_bytes > MAX_EXTRACTED_BYTES:
            raise ProjectReviewError("Expanded project exceeds the file-count or size limit.")
        if _is_sensitive(path):
            skipped_sensitive += 1
            continue
        if len(data) > MAX_FILE_BYTES or path.suffix.lower() not in _TEXT_EXTENSIONS:
            skipped_binary += 1
            continue
        text = _decode_text(data)
        if text is None:
            skipped_binary += 1
            continue
        language = _LANGUAGE_BY_EXTENSION.get(path.suffix.lower(), "Text")
        languages[language] += max(text.count("\n") + 1, 1)
        if len(files) < MAX_REVIEW_FILES:
            files.append(ReviewFile(str(path), text, language))
    if not files:
        raise ProjectReviewError("No reviewable source or text files were found.")
    return files, {
        "archive_name": PurePosixPath(filename).name[:180],
        "total_files": total_files,
        "reviewed_files": len(files),
        "total_uncompressed_bytes": total_bytes,
        "skipped_sensitive_files": skipped_sensitive,
        "skipped_binary_or_large_files": skipped_binary,
        "languages": [{"name": name, "lines": lines} for name, lines in languages.most_common(8)],
        "truncated": len(files) >= MAX_REVIEW_FILES or total_files > len(files) + skipped_sensitive + skipped_binary,
    }


def _project_recommendations(files: list[ReviewFile], summary: dict[str, Any]) -> list[dict[str, str]]:
    paths = [item.path.lower() for item in files]
    names = {PurePosixPath(path).name for path in paths}
    recommendations: list[dict[str, str]] = []
    has_tests = any(any(marker in PurePosixPath(path).parts or marker in PurePosixPath(path).name for marker in _TEST_MARKERS) for path in paths)
    has_ci = any(path.startswith(".github/workflows/") or path.startswith(".gitlab-ci") for path in paths)
    has_readme = any(name.startswith("readme") for name in names)
    has_lockfile = bool(names & {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "uv.lock", "cargo.lock", "go.sum"})
    if not has_tests:
        recommendations.append({"priority": "high", "category": "testing", "message": "未发现测试目录或测试文件；先为核心业务路径补充可重复执行的单元与集成测试。"})
    if not has_ci:
        recommendations.append({"priority": "medium", "category": "delivery", "message": "未发现 CI 工作流；建议在合并前自动运行格式、静态检查、测试和依赖审计。"})
    if not has_readme:
        recommendations.append({"priority": "medium", "category": "documentation", "message": "未发现 README；补充本地启动、配置、测试、部署与安全边界说明。"})
    if not has_lockfile and any(name in names for name in {"package.json", "pyproject.toml", "cargo.toml", "go.mod"}):
        recommendations.append({"priority": "medium", "category": "dependencies", "message": "检测到依赖清单但未发现锁文件；应用项目应锁定可复现依赖版本。"})
    if summary.get("skipped_sensitive_files"):
        recommendations.append({"priority": "high", "category": "secrets", "message": "上传包包含敏感配置文件，审核时已跳过；确认这些文件未进入版本库并轮换可能暴露的凭据。"})
    if summary.get("truncated"):
        recommendations.append({"priority": "medium", "category": "coverage", "message": "项目超过单次审核覆盖上限；建议按服务、包或提交拆分后继续审核。"})
    return recommendations


def _product_assessment(files: list[ReviewFile]) -> dict[str, Any]:
    """Infer product shape from repository evidence, without executing it."""
    paths = [item.path.lower() for item in files]
    names = {PurePosixPath(path).name for path in paths}
    joined = "\n".join(item.content[:8_000] for item in files[:80]).lower()
    product_types: list[str] = []
    frameworks: list[str] = []

    evidence_map = {
        "web": ("package.json", "vite.config", "next.config", "src/app", "src/pages"),
        "mobile": ("project.pbxproj", "package.swift", "androidmanifest.xml", "pubspec.yaml"),
        "backend_api": ("fastapi", "django", "flask", "express(", "@restcontroller", "gin.default"),
        "cli": ("[project.scripts]", "console_scripts", "commander(", "argparse", "click.command"),
        "library": ("setup.py", "pyproject.toml", "package.json", "cargo.toml", "go.mod"),
    }
    for kind, markers in evidence_map.items():
        if any(marker in joined or any(marker in path for path in paths) or marker in names for marker in markers):
            product_types.append(kind)

    framework_markers = {
        "React": ("react", "react-dom"), "Next.js": ("next", "next.config"),
        "Vue": ("vue", "nuxt"), "SwiftUI": ("swiftui",), "FastAPI": ("fastapi",),
        "Django": ("django",), "Flask": ("flask",), "Express": ("express",),
        "Spring": ("springframework",), "Flutter": ("flutter", "pubspec.yaml"),
    }
    for framework, markers in framework_markers.items():
        if any(marker in joined or any(marker in path for path in paths) for marker in markers):
            frameworks.append(framework)

    dimensions = {
        "user_experience": any(Path(path).suffix in {".tsx", ".jsx", ".swift", ".html", ".vue"} for path in paths),
        "api_and_data": any(marker in joined for marker in ("fastapi", "express", "router", "@app.", "@router.", "graphql")),
        "authentication": any(marker in joined for marker in ("oauth", "jwt", "firebaseauth", "authentication", "authorization")),
        "testing": any(any(marker in path for marker in _TEST_MARKERS) for path in paths),
        "continuous_delivery": any(path.startswith(".github/workflows/") or ".gitlab-ci" in path for path in paths),
        "documentation": any(PurePosixPath(path).name.startswith("readme") for path in paths),
        "containerization": any(PurePosixPath(path).name in {"dockerfile", "docker-compose.yml", "compose.yaml"} for path in paths),
    }
    return {
        "product_types": product_types or ["software_project"],
        "frameworks": frameworks,
        "review_dimensions": dimensions,
        "evidence_basis": "manifest names, source imports, and repository paths",
    }


def review_project_archive(payload: bytes, filename: str) -> dict[str, Any]:
    """Return a bounded project report without executing any uploaded code."""
    files, summary = _collect_files(payload, filename)
    findings: list[dict[str, Any]] = []
    for item in files:
        source = item.content[: CodeReviewAgent.MAX_SOURCE_CHARS]
        for finding in CodeReviewAgent.review_source(
            source,
            filename=item.path,
            language=item.language,
        ):
            row = finding.to_dict()
            row["file"] = item.path
            findings.append(row)
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda row: (severity_order.get(str(row.get("severity")), 9), str(row.get("file")), row.get("line") or 0))
    counts = Counter(str(row["severity"]) for row in findings)
    recommendations = _project_recommendations(files, summary)
    product_assessment = _product_assessment(files)
    score = max(0, 100 - counts["critical"] * 25 - counts["high"] * 12 - counts["medium"] * 4 - counts["low"])
    return {
        "schema_version": "aria.project-review.v1",
        "status": "completed",
        "summary": {**summary, "score": score, "finding_counts": {level: counts[level] for level in ("critical", "high", "medium", "low")}},
        "product_assessment": product_assessment,
        "findings": findings[:500],
        "recommendations": recommendations,
        "limitations": [
            "只进行了只读静态审核；没有执行代码、安装依赖、访问网络或运行测试。",
            "自动审核不能替代人工安全审计、运行时验证或渗透测试。",
        ],
    }


def report_as_json(payload: bytes, filename: str) -> str:
    return json.dumps(review_project_archive(payload, filename), ensure_ascii=False, indent=2)
