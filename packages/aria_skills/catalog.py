"""Safe installation helpers for GitHub-hosted portable skill catalogs."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Callable, Sequence

from .loader import LoadedSkill, discover_external_skills


_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


@dataclass(frozen=True)
class CatalogSource:
    owner: str
    repository: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repository}"

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.full_name}.git"


@dataclass(frozen=True)
class CatalogInstallResult:
    source: CatalogSource
    destination: Path
    revision: str
    skills: tuple[LoadedSkill, ...]


def parse_catalog_source(value: str) -> CatalogSource:
    text = str(value or "").strip()
    for prefix in ("https://github.com/", "http://github.com/"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    text = text.removesuffix(".git").strip("/")
    if not _REPOSITORY_RE.fullmatch(text):
        raise ValueError("catalog must be a GitHub owner/repository path")
    owner, repository = text.split("/", 1)
    if owner in {".", ".."} or repository in {".", ".."}:
        raise ValueError("catalog path traversal is not allowed")
    return CatalogSource(owner=owner, repository=repository)


def default_catalog_home() -> Path:
    configured = os.getenv("ARIA_SKILL_CATALOG_HOME", "")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".arthera" / "skill-catalogs").resolve()


def catalog_clone_command(
    source: CatalogSource,
    destination: Path,
    ref: str = "",
) -> list[str]:
    if ref and not _REF_RE.fullmatch(ref):
        raise ValueError("invalid catalog ref")
    command = ["git", "clone", "--depth", "1", "--filter=blob:none"]
    command.extend([source.clone_url, str(destination)])
    return command


def install_catalog(
    source_value: str,
    *,
    ref: str = "",
    catalog_home: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> CatalogInstallResult:
    source = parse_catalog_source(source_value)
    home = (catalog_home or default_catalog_home()).expanduser().resolve()
    destination = home / source.owner / source.repository
    if destination.exists():
        raise FileExistsError(
            f"catalog already installed at {destination}; remove or update it explicitly"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = catalog_clone_command(source, destination, ref)
    try:
        runner(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if ref:
            runner(
                ["git", "fetch", "--depth", "1", "origin", ref],
                cwd=destination,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            runner(
                ["git", "checkout", "--detach", "FETCH_HEAD"],
                cwd=destination,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        skills_root = destination / "skills"
        skills = discover_external_skills([skills_root])
        if not skills:
            raise ValueError("catalog contains no discoverable SKILL.md entries")
        invalid = [
            skill.qualified_name for skill in skills
            if skill.integrity != "verified"
        ]
        if invalid:
            raise ValueError(
                "catalog integrity verification failed for: " + ", ".join(invalid)
            )
        revision_result = runner(
            ["git", "rev-parse", "HEAD"],
            cwd=destination,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        # The destination did not exist before this call, so it is safe to
        # remove a partial clone or a catalog that failed verification.
        if destination.exists():
            shutil.rmtree(destination)
        raise
    return CatalogInstallResult(
        source=source,
        destination=destination,
        revision=str(revision_result.stdout or "").strip(),
        skills=tuple(skills),
    )
