"""Typed, read-only ``@`` context references for Aria inputs.

``/`` selects an operation. ``@`` selects context for that operation or for a
natural-language request.  This service deliberately has no terminal or LLM
dependency so the CLI, SDK, and future clients share the same semantics.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence


@dataclass(frozen=True)
class ReferenceKind:
    name: str
    label_en: str
    label_zh: str
    filesystem: bool = False


REFERENCE_KINDS: tuple[ReferenceKind, ...] = (
    ReferenceKind("file", "File", "文件", filesystem=True),
    ReferenceKind("folder", "Folder", "目录", filesystem=True),
    ReferenceKind("asset", "Market asset", "市场资产"),
    ReferenceKind("portfolio", "Portfolio", "投资组合", filesystem=True),
    ReferenceKind("strategy", "Strategy", "策略", filesystem=True),
    ReferenceKind("dataset", "Dataset", "数据集", filesystem=True),
    ReferenceKind("run", "Research run", "研究运行", filesystem=True),
    ReferenceKind("report", "Report", "报告", filesystem=True),
)

_KIND_MAP = {item.name: item for item in REFERENCE_KINDS}
_REF_RE = re.compile(
    r"(?<![\w@])@(?:(?P<kind>[A-Za-z][A-Za-z0-9_-]*):)?"
    r"(?P<value>\"[^\"\n]+\"|'[^'\n]+'|[^\s@]+)"
)
_TRAILING_PUNCTUATION = ",;!?，。；！？)]}"
_ASSET_RE = re.compile(r"^[A-Za-z0-9^._=-]{1,32}$")
_RESOURCE_DIRS = {
    "portfolio": ("portfolios",),
    "strategy": ("strategies",),
    "dataset": ("datasets", "data"),
    "run": ("runs", "reports"),
    "report": ("reports", "generated"),
}
_RESOURCE_EXTENSIONS = ("", ".md", ".json", ".yaml", ".yml", ".toml", ".csv", ".html")


def reference_search_roots(kind: str, workspace: Path, output_root: Path | None = None) -> tuple[Path, ...]:
    """Return deterministic search roots used by resolution and completion."""
    roots: list[Path] = []
    for name in _RESOURCE_DIRS.get(kind, ()):
        roots.extend((workspace / ".aria" / name, workspace / name))
        if output_root:
            roots.append(output_root / name)
    return tuple(dict.fromkeys(roots))


@dataclass(frozen=True)
class ReferencePolicy:
    workspace: Path = field(default_factory=Path.cwd)
    output_root: Path | None = None
    allowed_roots: tuple[Path, ...] = ()

    def normalized(self) -> "ReferencePolicy":
        workspace = self.workspace.expanduser().resolve()
        output_root = self.output_root.expanduser().resolve() if self.output_root else None
        roots = [workspace]
        if output_root:
            roots.append(output_root)
        roots.extend(path.expanduser().resolve() for path in self.allowed_roots)
        return ReferencePolicy(
            workspace=workspace,
            output_root=output_root,
            allowed_roots=tuple(dict.fromkeys(roots)),
        )


@dataclass(frozen=True)
class ContextReference:
    raw: str
    kind: str
    value: str
    start: int
    end: int
    resolved_value: str = ""
    path: Path | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass(frozen=True)
class PreparedReferences:
    original_text: str
    expanded_text: str
    context_block: str
    references: tuple[ContextReference, ...]

    @property
    def errors(self) -> tuple[ContextReference, ...]:
        return tuple(ref for ref in self.references if ref.error)

    @property
    def prompt(self) -> str:
        if not self.context_block:
            return self.expanded_text
        return f"{self.expanded_text}\n\n{self.context_block}"


def iter_reference_tokens(text: str) -> Iterator[tuple[str, str, str, int, int]]:
    """Yield ``(raw, kind, value, start, end)`` while ignoring email addresses."""
    for match in _REF_RE.finditer(text):
        kind = (match.group("kind") or "file").lower()
        token = match.group("value")
        end = match.end()
        if token[:1] in {"\"", "'"} and token[-1:] == token[:1]:
            value = token[1:-1]
        else:
            trailing = _TRAILING_PUNCTUATION + ("." if kind not in {"file", "folder"} else "")
            value = token.rstrip(trailing)
            end -= len(token) - len(value)
        if value:
            yield text[match.start():end], kind, value, match.start(), end


class ReferenceService:
    """Resolve and prepare explicit context references without side effects."""

    def __init__(self, policy: ReferencePolicy | None = None):
        self.policy = (policy or ReferencePolicy()).normalized()

    def prepare(self, text: str) -> PreparedReferences:
        refs = tuple(self._resolve(*token) for token in iter_reference_tokens(text))
        expanded = text
        for ref in reversed(refs):
            if not ref.ok:
                continue
            replacement = self._replacement(ref)
            expanded = expanded[:ref.start] + replacement + expanded[ref.end:]

        blocks: list[str] = []
        for ref in refs:
            if not ref.ok:
                continue
            target = str(ref.path) if ref.path is not None else ref.resolved_value
            if ref.kind == "folder":
                action = "Inspect on demand with list_files or search_code."
            elif ref.kind == "asset":
                action = "Fetch on demand with get_market_data or get_market_history."
            else:
                action = "Inspect on demand with read_file or analyze_file."
            blocks.append(f"- {ref.kind}: {target}\n  {action}")

        context_block = ""
        if blocks:
            context_block = (
                "[Aria resource references - pointers only]\n"
                "No resource content is preloaded. Use the named tools before answering.\n"
                + "\n".join(blocks)
                + "\n[End Aria resource references]"
            )
        return PreparedReferences(text, expanded, context_block, refs)

    def _resolve(self, raw: str, kind: str, value: str, start: int, end: int) -> ContextReference:
        if kind not in _KIND_MAP:
            valid = ", ".join(item.name for item in REFERENCE_KINDS)
            return ContextReference(raw, kind, value, start, end, error=f"unknown reference type '{kind}' (use: {valid})")
        if kind == "asset":
            if not _ASSET_RE.fullmatch(value):
                return ContextReference(raw, kind, value, start, end, error=f"invalid market asset '{value}'")
            return ContextReference(raw, kind, value, start, end, resolved_value=value.upper())

        path = self._resolve_path(kind, value)
        if path is None:
            return ContextReference(raw, kind, value, start, end, error=f"{kind} not found: {value}")
        if not self._is_allowed(path):
            return ContextReference(raw, kind, value, start, end, path=path, error=f"reference is outside the allowed workspace: {value}")
        if kind == "folder" and not path.is_dir():
            return ContextReference(raw, kind, value, start, end, path=path, error=f"folder not found: {value}")
        if kind != "folder" and not path.is_file():
            return ContextReference(raw, kind, value, start, end, path=path, error=f"{kind} is not a file: {value}")

        return ContextReference(
            raw, kind, value, start, end,
            resolved_value=str(path), path=path,
        )

    def _resolve_path(self, kind: str, value: str) -> Path | None:
        if kind in {"file", "folder"}:
            candidate = Path(os.path.expandvars(value)).expanduser()
            if not candidate.is_absolute():
                candidate = self.policy.workspace / candidate
            try:
                return candidate.resolve()
            except OSError:
                return None

        for root in self._resource_roots(kind):
            for suffix in _RESOURCE_EXTENSIONS:
                candidate = root / f"{value}{suffix}"
                if candidate.exists():
                    return candidate.resolve()
            if not root.is_dir():
                continue
            target_names = {value.lower(), *(f"{value}{suffix}".lower() for suffix in _RESOURCE_EXTENSIONS if suffix)}
            seen = 0
            for candidate in root.rglob("*"):
                seen += 1
                if seen > 2_000:
                    break
                if candidate.is_file() and (candidate.name.lower() in target_names or candidate.stem.lower() == value.lower()):
                    return candidate.resolve()
        return None

    def _resource_roots(self, kind: str) -> Iterable[Path]:
        yield from reference_search_roots(kind, self.policy.workspace, self.policy.output_root)

    def _is_allowed(self, path: Path) -> bool:
        resolved = path.resolve()
        return any(resolved == root or root in resolved.parents for root in self.policy.allowed_roots)

    def _replacement(self, ref: ContextReference) -> str:
        if ref.kind == "asset":
            return ref.resolved_value
        if ref.path is not None:
            return shlex.quote(str(ref.path)) if " " in str(ref.path) else str(ref.path)
        return ref.resolved_value

def build_reference_service(
    *,
    workspace: Path | None = None,
    output_root: Path | None = None,
    allowed_roots: Sequence[Path] = (),
) -> ReferenceService:
    return ReferenceService(ReferencePolicy(
        workspace=workspace or Path.cwd(),
        output_root=output_root,
        allowed_roots=tuple(allowed_roots),
    ))
