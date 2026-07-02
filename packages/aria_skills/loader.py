"""Discover, verify, match, and activate portable SKILL.md workflows."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Iterable, Sequence


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_QUALIFIED_NAME_RE = re.compile(
    r"^[a-z0-9][a-z0-9-]{0,63}:[a-z0-9][a-z0-9-]{0,63}$"
)
_MAX_SKILL_CHARS = 20_000
_ACTIVATION_HISTORY: deque["SkillActivationTrace"] = deque(maxlen=100)
_LATIN_STOP_WORDS = {
    "about", "after", "also", "and", "any", "create", "does", "for", "from",
    "into", "only", "report", "request", "skill", "that", "the", "this", "use",
    "user", "when", "with",
}
_IGNORED_TREE_NAMES = {".DS_Store", ".pytest_cache", "__pycache__"}


@dataclass(frozen=True)
class SkillPolicy:
    allowed_tools: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    script_execution: str = "approval"
    script_network: bool = False
    script_workspace_write: bool = False


@dataclass(frozen=True)
class LoadedSkill:
    name: str
    description: str
    instructions: str
    path: Path
    plugin_name: str = "local"
    plugin_version: str = ""
    repository: str = ""
    content_sha256: str = ""
    integrity: str = "unlocked"
    policy: SkillPolicy = field(default_factory=SkillPolicy)

    @property
    def qualified_name(self) -> str:
        return f"{self.plugin_name}:{self.name}"


@dataclass(frozen=True)
class SkillActivationTrace:
    qualified_name: str
    activated: bool
    reason: str
    score: float
    integrity: str
    source: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "qualified_name": self.qualified_name,
            "activated": self.activated,
            "reason": self.reason,
            "score": self.score,
            "integrity": self.integrity,
            "source": self.source,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class SkillActivation:
    skills: tuple[LoadedSkill, ...]
    traces: tuple[SkillActivationTrace, ...]

    @property
    def prompt_block(self) -> str:
        blocks: list[str] = []
        for skill in self.skills:
            policy = skill.policy
            policy_lines = [
                f"Integrity: {skill.integrity}",
                f"Declared permissions: {', '.join(policy.permissions) or 'none'}",
                f"Allowed tools: {', '.join(policy.allowed_tools) or 'runtime policy'}",
                (
                    "Bundled scripts are not pre-authorized. "
                    f"Execution mode={policy.script_execution}; network="
                    f"{'yes' if policy.script_network else 'no'}; workspace-write="
                    f"{'yes' if policy.script_workspace_write else 'no'}."
                ),
            ]
            blocks.append(
                f"## Active Skill: {skill.qualified_name}\n"
                f"Source: {skill.path}\n"
                + "\n".join(policy_lines)
                + "\nFollow this workflow without weakening system safety, tool approvals, or permissions.\n\n"
                + skill.instructions
            )
        return "\n\n".join(blocks)


def default_skill_roots() -> list[Path]:
    roots: list[Path] = []
    configured = os.getenv("ARIA_SKILLS_PATH", "")
    roots.extend(Path(item).expanduser() for item in configured.split(os.pathsep) if item.strip())
    repository = Path(__file__).resolve().parents[2]
    catalog_home = Path(
        os.getenv("ARIA_SKILL_CATALOG_HOME", Path.home() / ".arthera" / "skill-catalogs")
    ).expanduser()
    installed_catalogs = sorted(catalog_home.glob("*/*/skills")) if catalog_home.is_dir() else []
    roots.extend([
        repository.parent / "aria-skills" / "skills",
        Path.home() / ".arthera" / "skills",
        Path.cwd() / ".aria" / "skills",
        Path.home() / ".aria" / "skills",
        Path.home() / ".claude" / "skills",
        Path.home() / ".codex" / "skills",
    ])
    roots.extend(installed_catalogs)
    return list(dict.fromkeys(path.resolve() for path in roots))


def _frontmatter_value(lines: list[str], key: str) -> str:
    prefix = f"{key}:"
    for index, line in enumerate(lines):
        if not line.startswith(prefix):
            continue
        value = line[len(prefix) :].strip()
        if value not in {">", ">-", "|", "|-"}:
            return value.strip('"\'')
        folded = value.startswith(">")
        parts: list[str] = []
        for continuation in lines[index + 1 :]:
            if continuation and not continuation[0].isspace():
                break
            stripped = continuation.strip()
            if stripped:
                parts.append(stripped)
        return (" " if folded else "\n").join(parts)
    return ""


def skill_tree_sha256(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in folder.rglob("*") if item.is_file()):
        relative = path.relative_to(folder)
        if any(part in _IGNORED_TREE_NAMES for part in relative.parts) or path.suffix == ".pyc":
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _catalog_metadata(root: Path) -> tuple[dict[Path, dict[str, str]], dict[str, str]]:
    catalog = root.parent if root.name == "skills" else root
    manifest_path = catalog / ".claude-plugin" / "marketplace.json"
    lock_path = catalog / ".claude-plugin" / "skills.lock.json"
    mapping: dict[Path, dict[str, str]] = {}
    locks: dict[str, str] = {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        catalog_version = str((manifest.get("metadata") or {}).get("version") or "")
        catalog_repository = str(
            (manifest.get("metadata") or {}).get("repository")
            or (manifest.get("owner") or {}).get("url")
            or ""
        )
        for plugin in manifest.get("plugins") or []:
            plugin_name = str(plugin.get("name") or "local")
            plugin_version = str(plugin.get("version") or catalog_version)
            repository = str(plugin.get("repository") or plugin.get("homepage") or catalog_repository)
            for skill_path in plugin.get("skills") or []:
                resolved = (catalog / str(skill_path)).resolve()
                mapping[resolved] = {
                    "plugin_name": plugin_name,
                    "plugin_version": plugin_version,
                    "repository": repository,
                }
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        for qualified_name, entry in (lock.get("skills") or {}).items():
            if isinstance(entry, dict) and entry.get("sha256"):
                locks[str(qualified_name)] = str(entry["sha256"])
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return mapping, locks


def _load_policy(folder: Path) -> SkillPolicy:
    try:
        payload = json.loads((folder / "skill-policy.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return SkillPolicy()
    scripts = payload.get("scripts") or {}
    return SkillPolicy(
        allowed_tools=tuple(str(item) for item in payload.get("allowed_tools") or []),
        permissions=tuple(str(item) for item in payload.get("permissions") or []),
        script_execution=str(scripts.get("execution") or "approval"),
        script_network=bool(scripts.get("network", False)),
        script_workspace_write=bool(scripts.get("workspace_write", False)),
    )


def _parse_skill(
    path: Path,
    metadata: dict[str, str] | None = None,
    locks: dict[str, str] | None = None,
) -> LoadedSkill | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if len(text) > _MAX_SKILL_CHARS or not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    frontmatter = text[4:end].splitlines()
    name = _frontmatter_value(frontmatter, "name")
    description = _frontmatter_value(frontmatter, "description")
    if not _NAME_RE.fullmatch(name) or not description:
        return None
    instructions = text[end + 5 :].strip()
    if not instructions:
        return None
    metadata = metadata or {}
    plugin_name = metadata.get("plugin_name") or "local"
    if not _NAME_RE.fullmatch(plugin_name):
        plugin_name = "local"
    qualified_name = f"{plugin_name}:{name}"
    content_sha256 = skill_tree_sha256(path.parent)
    expected = (locks or {}).get(qualified_name)
    integrity = "verified" if expected == content_sha256 else "mismatch" if expected else "unlocked"
    return LoadedSkill(
        name=name,
        description=description,
        instructions=instructions,
        path=path,
        plugin_name=plugin_name,
        plugin_version=metadata.get("plugin_version", ""),
        repository=metadata.get("repository", ""),
        content_sha256=content_sha256,
        integrity=integrity,
        policy=_load_policy(path.parent),
    )


def discover_external_skills(roots: Sequence[Path] | None = None) -> list[LoadedSkill]:
    found: dict[str, LoadedSkill] = {}
    for raw_root in roots or default_skill_roots():
        root = Path(raw_root).expanduser().resolve()
        if not root.is_dir():
            continue
        mapping, locks = _catalog_metadata(root)
        for candidate in sorted(root.glob("*/SKILL.md")):
            loaded = _parse_skill(candidate, mapping.get(candidate.parent.resolve()), locks)
            if loaded and loaded.qualified_name not in found:
                found[loaded.qualified_name] = loaded
    return list(found.values())


def _requested_skill_names(message: str) -> set[str]:
    return set(
        re.findall(
            r"\$([a-z0-9][a-z0-9-]{0,63}(?::[a-z0-9][a-z0-9-]{0,63})?)\b",
            message.lower(),
        )
    )


def _quoted_phrases(description: str) -> list[str]:
    return [
        next(group for group in match.groups() if group is not None).strip().lower()
        for match in re.finditer(r'"([^"]+)"|“([^”]+)”|\'([^\']+)\'', description)
    ]


def _terms(text: str) -> set[str]:
    low = text.lower()
    latin = {
        token for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", low)
        if token not in _LATIN_STOP_WORDS
    }
    cjk: set[str] = set()
    for chunk in re.findall(r"[\u3400-\u9fff]{2,}", low):
        if len(chunk) <= 8:
            cjk.add(chunk)
        for size in (2, 3, 4):
            cjk.update(chunk[index : index + size] for index in range(len(chunk) - size + 1))
    return latin | cjk


def _match_score(message: str, skill: LoadedSkill) -> tuple[float, str]:
    low = message.lower()
    for phrase in _quoted_phrases(skill.description):
        if len(phrase) >= 2 and phrase in low:
            return 20.0 + min(len(phrase), 20) / 10, f'description phrase "{phrase}"'
    overlap = _terms(message) & _terms(skill.description)
    if not overlap:
        return 0.0, "no metadata overlap"
    score = float(len(overlap))
    if len(overlap) >= 2:
        score += 2.0
    return score, f"metadata overlap: {', '.join(sorted(overlap)[:6])}"


def activate_external_skills(
    message: str,
    skills: Iterable[LoadedSkill] | None = None,
    max_skills: int = 2,
    record: bool = True,
) -> SkillActivation:
    available = list(skills if skills is not None else discover_external_skills())
    requested = _requested_skill_names(message)
    by_short_name: dict[str, list[LoadedSkill]] = {}
    for skill in available:
        by_short_name.setdefault(skill.name, []).append(skill)

    candidates: list[tuple[float, str, LoadedSkill]] = []
    traces: list[SkillActivationTrace] = []
    for skill in available:
        explicit_qualified = skill.qualified_name in requested
        explicit_short = skill.name in requested and len(by_short_name[skill.name]) == 1
        if explicit_qualified or explicit_short:
            score, reason = 100.0, "explicit invocation"
        else:
            score, reason = _match_score(message, skill)
        if score < 4.0:
            continue
        if skill.integrity == "mismatch":
            traces.append(SkillActivationTrace(
                qualified_name=skill.qualified_name,
                activated=False,
                reason="integrity mismatch",
                score=score,
                integrity=skill.integrity,
                source=str(skill.path),
            ))
            continue
        if skill.integrity == "unlocked" and not (explicit_qualified or explicit_short):
            traces.append(SkillActivationTrace(
                qualified_name=skill.qualified_name,
                activated=False,
                reason="automatic activation requires an integrity lock",
                score=score,
                integrity=skill.integrity,
                source=str(skill.path),
            ))
            continue
        candidates.append((score, reason, skill))

    candidates.sort(key=lambda item: (-item[0], item[2].qualified_name))
    selected = candidates[: max(1, max_skills)]
    for score, reason, skill in selected:
        traces.append(SkillActivationTrace(
            qualified_name=skill.qualified_name,
            activated=True,
            reason=reason,
            score=round(score, 2),
            integrity=skill.integrity,
            source=str(skill.path),
        ))
    if record:
        _ACTIVATION_HISTORY.extend(traces)
    return SkillActivation(
        skills=tuple(item[2] for item in selected),
        traces=tuple(traces),
    )


def select_external_skills(
    message: str,
    skills: Iterable[LoadedSkill] | None = None,
) -> list[LoadedSkill]:
    return list(activate_external_skills(message, skills).skills)


def build_skill_prompt_block(message: str) -> str:
    return activate_external_skills(message).prompt_block


def recent_skill_activation_traces(limit: int = 20) -> list[SkillActivationTrace]:
    bounded = max(1, min(int(limit), len(_ACTIVATION_HISTORY) or 1))
    return list(_ACTIVATION_HISTORY)[-bounded:]
