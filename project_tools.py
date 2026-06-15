"""
project_tools.py — 项目文件夹分析引擎
========================================
提供 Claude Code / Codex 同等能力：
  - 递归扫描项目目录，构建文件树
  - 自动检测项目类型（Python / Node / Go / Rust / Java / …）
  - 智能读取关键文件注入 LLM 上下文
  - 跨文件 grep / 符号索引
  - Git 状态集成
  - 按 token 预算动态裁剪上下文

ProjectSession 生命周期：
  1. scan(path)         — 扫描目录，建立索引
  2. build_llm_context  — 生成注入 system prompt 的上下文块
  3. get_file / grep    — LLM 工具调用时按需读取
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ── 忽略规则 ──────────────────────────────────────────────────────────────────

_IGNORE_DIRS: Set[str] = {
    ".git", ".hg", ".svn",
    "node_modules", ".pnp",
    "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    ".venv", "venv", "env", ".env",
    "dist", "build", "out", "target", ".next", ".nuxt",
    "coverage", ".coverage", "htmlcov",
    ".idea", ".vscode", ".DS_Store",
    "eggs", "*.egg-info",
}

_IGNORE_FILE_PATTERNS: List[str] = [
    "*.pyc", "*.pyo", "*.pyd",
    "*.so", "*.dll", "*.dylib",
    "*.class", "*.jar",
    "*.min.js", "*.min.css",
    "*.map",
    "*.lock",           # package-lock.json 例外：内容有用，但体积太大
    "*.log",
    ".DS_Store", "Thumbs.db",
    "*.bin", "*.exe", "*.whl", "*.tar.gz", "*.zip",
    "*.jpg", "*.jpeg", "*.png", "*.gif", "*.webp", "*.ico",
    "*.mp4", "*.mp3", "*.pdf",
]

_BINARY_EXTENSIONS: Set[str] = {
    ".pyc",".pyo",".pyd",".so",".dll",".dylib",".class",".jar",
    ".bin",".exe",".whl",".tar",".gz",".zip",".rar",
    ".jpg",".jpeg",".png",".gif",".bmp",".webp",".ico",
    ".mp4",".mp3",".wav",".avi",".mkv",
    ".pdf",".doc",".xls",".ppt",
}

# ── 项目类型指纹 ──────────────────────────────────────────────────────────────

_PROJECT_SIGNATURES: List[Tuple[str, List[str], str]] = [
    # (类型名, 指纹文件列表, 主入口候选)
    ("Python",      ["pyproject.toml", "setup.py", "requirements.txt"],  "main.py"),
    ("Node.js",     ["package.json"],                                     "index.js"),
    ("TypeScript",  ["tsconfig.json"],                                    "src/index.ts"),
    ("Go",          ["go.mod"],                                           "main.go"),
    ("Rust",        ["Cargo.toml"],                                       "src/main.rs"),
    ("Java",        ["pom.xml", "build.gradle"],                         "src/main/java"),
    ("Kotlin",      ["build.gradle.kts"],                                 "src/main/kotlin"),
    ("Ruby",        ["Gemfile"],                                          "main.rb"),
    ("PHP",         ["composer.json"],                                    "index.php"),
    ("C#",          ["*.csproj", "*.sln"],                                "Program.cs"),
    ("C/C++",       ["CMakeLists.txt", "Makefile"],                       "main.c"),
    ("Swift",       ["Package.swift"],                                    "Sources/main.swift"),
    ("Dart/Flutter",["pubspec.yaml"],                                     "lib/main.dart"),
    ("Elixir",      ["mix.exs"],                                          "lib/main.ex"),
]

# 关键配置文件（权重高，一定要读）
_KEY_CONFIG_FILES: List[str] = [
    "README.md", "README.rst", "README.txt",
    "pyproject.toml", "setup.py", "requirements.txt",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "Makefile",
    ".env.example", ".env.sample",
    "docker-compose.yml", "Dockerfile",
    ".github/workflows",  # CI
]

# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class FileNode:
    rel_path: str
    abs_path: str
    size:     int
    ext:      str
    is_dir:   bool = False
    language: str  = ""


@dataclass
class ProjectSession:
    """Loaded project state — persisted in ArtheraTerminal._project_session."""
    root:         str            = ""
    name:         str            = ""
    project_type: str            = ""
    languages:    List[str]      = field(default_factory=list)
    files:        List[FileNode] = field(default_factory=list)
    key_contents: Dict[str, str] = field(default_factory=dict)  # rel_path → text
    git_info:     Dict[str, Any] = field(default_factory=dict)
    stats:        Dict[str, Any] = field(default_factory=dict)
    _tree_cache:  str            = field(default="", repr=False)

    # ── Scan ──────────────────────────────────────────────────────────────────

    def scan(self, path: str, max_files: int = 2000) -> "ProjectSession":
        root = Path(path).expanduser().resolve()
        if not root.exists():
            raise FileNotFoundError(f"路径不存在: {root}")
        if not root.is_dir():
            raise ValueError(f"不是目录: {root}")

        self.root = str(root)
        self.name = root.name
        self.files = []
        self._tree_cache = ""

        # Walk directory
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune ignored dirs in-place
            dirnames[:] = [
                d for d in dirnames
                if d not in _IGNORE_DIRS
                and not d.startswith(".")
                or d in {".github", ".gitlab"}
            ]
            # Also skip dirs matching patterns
            dirnames[:] = [
                d for d in dirnames
                if not any(fnmatch.fnmatch(d, p) for p in ["*.egg-info", "*.dist-info"])
            ]

            for fname in filenames:
                if len(self.files) >= max_files:
                    break
                if any(fnmatch.fnmatch(fname, p) for p in _IGNORE_FILE_PATTERNS):
                    continue
                fpath = Path(dirpath) / fname
                try:
                    size = fpath.stat().st_size
                except OSError:
                    continue
                ext  = fpath.suffix.lower()
                if ext in _BINARY_EXTENSIONS:
                    continue
                rel = str(fpath.relative_to(root))
                self.files.append(FileNode(
                    rel_path=rel,
                    abs_path=str(fpath),
                    size=size,
                    ext=ext,
                    language=_ext_to_lang(ext),
                ))

        # Detect project type
        root_names = {f.rel_path for f in self.files}
        self.project_type = "Unknown"
        detected_langs: List[str] = []
        for ptype, fingerprints, _ in _PROJECT_SIGNATURES:
            for fp in fingerprints:
                if "*" in fp:
                    if any(fnmatch.fnmatch(r, fp) for r in root_names):
                        self.project_type = ptype
                        detected_langs.append(ptype)
                        break
                elif fp in root_names:
                    self.project_type = ptype
                    detected_langs.append(ptype)
                    break
        # Language frequency from extensions
        lang_counts: Dict[str, int] = {}
        for f in self.files:
            if f.language:
                lang_counts[f.language] = lang_counts.get(f.language, 0) + 1
        top_langs = sorted(lang_counts, key=lang_counts.get, reverse=True)[:4]
        self.languages = list(dict.fromkeys(detected_langs + top_langs))[:5]

        # Stats
        total_lines = 0
        for f in self.files:
            if f.size < 500_000 and f.ext not in _BINARY_EXTENSIONS:
                try:
                    total_lines += Path(f.abs_path).read_text(errors="replace").count("\n")
                except Exception:
                    pass
        self.stats = {
            "total_files": len(self.files),
            "total_lines": total_lines,
            "total_size_kb": sum(f.size for f in self.files) // 1024,
            "languages": lang_counts,
        }

        # Read key files
        self.key_contents = {}
        self._load_key_files(root)

        # Git info
        self.git_info = _get_git_info(str(root))

        return self

    def _load_key_files(self, root: Path, max_chars_each: int = 4000):
        """Read README + config files into key_contents."""
        for rel in _KEY_CONFIG_FILES:
            candidate = root / rel
            if candidate.is_file():
                try:
                    text = candidate.read_text(errors="replace")[:max_chars_each]
                    self.key_contents[rel] = text
                except Exception:
                    pass

        # Auto-detect main entry point
        for _, fingerprints, entry in _PROJECT_SIGNATURES:
            for fp in fingerprints:
                if "*" in fp:
                    continue
                if (root / fp).exists():
                    ep = root / entry
                    if ep.is_file() and entry not in self.key_contents:
                        try:
                            self.key_contents[entry] = ep.read_text(errors="replace")[:4000]
                        except Exception:
                            pass
                    break

    # ── Tree rendering ─────────────────────────────────────────────────────────

    def get_tree(self, max_lines: int = 80) -> str:
        """Return ASCII file tree string."""
        if self._tree_cache:
            return self._tree_cache

        root = Path(self.root)
        # Build nested dict
        tree: Dict[str, Any] = {}
        for f in self.files:
            parts = Path(f.rel_path).parts
            node = tree
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = f.size

        lines: List[str] = [f"{self.name}/"]
        _render_tree(tree, lines, "", max_lines)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines.append(f"  … (仅显示前 {max_lines} 条，共 {len(self.files)} 个文件)")
        result = "\n".join(lines)
        self._tree_cache = result
        return result

    # ── LLM context ───────────────────────────────────────────────────────────

    def build_llm_context(self, max_chars: int = 16_000) -> str:
        """Build the system-prompt context block injected for every message."""
        parts: List[str] = []
        budget = max_chars

        # Header
        git_branch = self.git_info.get("branch", "")
        header = (
            f"=== 已加载项目: {self.name} ===\n"
            f"路径: {self.root}\n"
            f"类型: {self.project_type}  |  语言: {', '.join(self.languages[:3])}\n"
            f"文件: {self.stats.get('total_files',0)} 个  "
            f"代码行: {self.stats.get('total_lines',0):,}  "
            f"大小: {self.stats.get('total_size_kb',0)} KB"
        )
        if git_branch:
            header += f"\nGit 分支: {git_branch}"
        if self.git_info.get("recent_commits"):
            commits = self.git_info["recent_commits"][:3]
            header += "\n最近提交:\n" + "\n".join(f"  {c}" for c in commits)
        parts.append(header)
        budget -= len(header)

        # File tree (cap at 3000 chars)
        tree_str = self.get_tree(max_lines=60)
        tree_block = f"\n--- 文件结构 ---\n{tree_str}"
        if len(tree_block) <= min(budget, 3000):
            parts.append(tree_block)
            budget -= len(tree_block)

        # Key file contents
        if budget > 1000:
            for rel, content in self.key_contents.items():
                if budget <= 500:
                    break
                snippet = content[:min(len(content), budget // max(1, len(self.key_contents)))]
                block = f"\n--- {rel} ---\n{snippet}"
                parts.append(block)
                budget -= len(block)

        # Tool capability hint
        tool_hint = (
            "\n--- 可用工具 ---\n"
            "你可以调用以下工具来完成任务：\n"
            "  read_file(path)            — 读取项目中任意文件\n"
            "  write_file(path, content)  — 创建或覆写文件\n"
            "  edit_file(path, old, new)  — 精确查找替换\n"
            "  search_code(pattern, path) — 跨文件正则搜索\n"
            "  list_files(path)           — 列出目录内容\n"
            "  run_command(command, cwd)  — 在项目目录执行 shell 命令\n"
            "所有路径相对于项目根目录，或使用绝对路径。\n"
            "执行任务时先规划步骤，再逐步调用工具完成。"
        )
        parts.append(tool_hint)

        return "\n".join(parts)

    # ── File reading (safe, within project root) ───────────────────────────────

    def read_file(self, rel_or_abs: str, max_chars: int = 40_000) -> Tuple[bool, str]:
        """Read a file within the project. Returns (ok, content_or_error)."""
        p = Path(rel_or_abs)
        if not p.is_absolute():
            p = Path(self.root) / rel_or_abs
        p = p.resolve()
        root = Path(self.root).resolve()
        if not str(p).startswith(str(root)):
            return False, f"安全限制：路径 {p} 在项目目录之外"
        if not p.exists():
            return False, f"文件不存在: {p}"
        if not p.is_file():
            return False, f"不是文件: {p}"
        try:
            content = p.read_text(errors="replace")[:max_chars]
            return True, content
        except Exception as e:
            return False, str(e)

    # ── Grep ──────────────────────────────────────────────────────────────────

    def grep(self, pattern: str, glob: str = "**/*",
             max_results: int = 60) -> List[Dict[str, Any]]:
        """Regex search across project files."""
        results: List[Dict[str, Any]] = []
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return [{"error": f"正则表达式错误: {e}"}]

        root = Path(self.root)
        for f in self.files:
            if len(results) >= max_results:
                break
            if f.ext in _BINARY_EXTENSIONS or f.size > 2_000_000:
                continue
            if glob != "**/*":
                if not fnmatch.fnmatch(f.rel_path, glob):
                    continue
            try:
                lines = Path(f.abs_path).read_text(errors="replace").splitlines()
                for i, line in enumerate(lines, 1):
                    if rx.search(line):
                        results.append({
                            "file":    f.rel_path,
                            "line":    i,
                            "content": line.strip()[:200],
                        })
                        if len(results) >= max_results:
                            break
            except Exception:
                continue
        return results

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> Dict[str, Any]:
        lang_counts = self.stats.get("languages", {})
        top_langs = sorted(lang_counts, key=lang_counts.get, reverse=True)[:5]
        return {
            "name":         self.name,
            "root":         self.root,
            "type":         self.project_type,
            "languages":    top_langs,
            "total_files":  self.stats.get("total_files", 0),
            "total_lines":  self.stats.get("total_lines", 0),
            "total_size_kb":self.stats.get("total_size_kb", 0),
            "key_files":    list(self.key_contents.keys()),
            "git":          self.git_info,
        }


# ── Tree renderer ─────────────────────────────────────────────────────────────

def _render_tree(node: dict, lines: list, prefix: str, max_lines: int):
    items = sorted(node.items(), key=lambda kv: (isinstance(kv[1], dict), kv[0]))
    for i, (name, val) in enumerate(items):
        if len(lines) >= max_lines:
            return
        is_last = (i == len(items) - 1)
        connector = "└── " if is_last else "├── "
        if isinstance(val, dict):
            lines.append(f"{prefix}{connector}{name}/")
            ext_prefix = prefix + ("    " if is_last else "│   ")
            _render_tree(val, lines, ext_prefix, max_lines)
        else:
            size_str = _fmt_size(val)
            lines.append(f"{prefix}{connector}{name}  [{size_str}]")


def _fmt_size(size: int) -> str:
    if size < 1024:     return f"{size}B"
    if size < 1048576:  return f"{size//1024}KB"
    return f"{size//1048576}MB"


# ── Extension → language ──────────────────────────────────────────────────────

_EXT_LANG: Dict[str, str] = {
    ".py":"Python", ".pyx":"Python", ".pyi":"Python",
    ".js":"JavaScript", ".jsx":"JavaScript", ".mjs":"JavaScript",
    ".ts":"TypeScript", ".tsx":"TypeScript",
    ".go":"Go",
    ".rs":"Rust",
    ".java":"Java", ".kt":"Kotlin", ".kts":"Kotlin",
    ".rb":"Ruby",
    ".php":"PHP",
    ".cs":"C#",
    ".c":"C", ".h":"C", ".cpp":"C++", ".cc":"C++", ".hpp":"C++",
    ".swift":"Swift",
    ".dart":"Dart",
    ".ex":"Elixir", ".exs":"Elixir",
    ".html":"HTML", ".htm":"HTML",
    ".css":"CSS", ".scss":"CSS", ".sass":"CSS", ".less":"CSS",
    ".json":"JSON", ".jsonl":"JSON",
    ".yaml":"YAML", ".yml":"YAML",
    ".toml":"TOML",
    ".md":"Markdown", ".mdx":"Markdown",
    ".sh":"Shell", ".bash":"Shell", ".zsh":"Shell",
    ".sql":"SQL",
    ".tf":"Terraform", ".hcl":"Terraform",
    ".proto":"Protobuf",
    ".r":"R", ".R":"R",
    ".lua":"Lua",
    ".vim":"VimScript",
}

def _ext_to_lang(ext: str) -> str:
    return _EXT_LANG.get(ext, "")


# ── Git integration ───────────────────────────────────────────────────────────

def _get_git_info(root: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {}
    if not (Path(root) / ".git").exists():
        return info

    def _git(*args, capture=True) -> str:
        try:
            r = subprocess.run(
                ["git", *args], cwd=root,
                capture_output=capture, text=True, timeout=5
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    info["branch"]   = _git("rev-parse", "--abbrev-ref", "HEAD")
    info["remote"]   = _git("remote", "get-url", "origin")
    status_out = _git("status", "--short")
    if status_out:
        changed = [l for l in status_out.splitlines() if l.strip()]
        info["changed_files"] = changed[:20]
        info["changed_count"] = len(changed)
    else:
        info["changed_files"] = []
        info["changed_count"] = 0
    log_out = _git("log", "--oneline", "-8")
    if log_out:
        info["recent_commits"] = log_out.splitlines()
    diff_stat = _git("diff", "--stat", "HEAD")
    if diff_stat:
        info["diff_stat"] = diff_stat[:800]
    return info


# ── Standalone helpers (called by /project commands) ─────────────────────────

def scan_project(path: str, max_files: int = 2000) -> ProjectSession:
    """Convenience: create + scan a new ProjectSession."""
    s = ProjectSession()
    s.scan(path, max_files=max_files)
    return s


def format_grep_results(results: List[Dict[str, Any]],
                         pattern: str, max_show: int = 40) -> str:
    """Format grep results for Rich console display."""
    if not results:
        return f'没有找到匹配 "{pattern}" 的内容'
    if results and "error" in results[0]:
        return results[0]["error"]
    lines = [f'搜索 "{pattern}" — {len(results)} 个匹配\n']
    cur_file = None
    for r in results[:max_show]:
        if r["file"] != cur_file:
            cur_file = r["file"]
            lines.append(f"\n  📄 {cur_file}")
        lines.append(f"    {r['line']:>4} │ {r['content']}")
    if len(results) > max_show:
        lines.append(f"\n  … 还有 {len(results) - max_show} 个结果")
    return "\n".join(lines)
