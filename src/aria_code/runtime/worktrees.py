"""Git worktree isolation for write-capable background agents."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class WorktreeError(RuntimeError):
    """Raised when an isolated worktree cannot be created safely."""


@dataclass(frozen=True)
class WorktreeSpec:
    task_id: str
    repository: str
    path: str
    branch: str
    base_revision: str


@dataclass(frozen=True)
class WorktreeApplyResult:
    paths: tuple[str, ...]
    tracked_paths: tuple[str, ...]
    untracked_paths: tuple[str, ...]


class WorktreeManager:
    """Create task-specific worktrees without changing the caller's cwd."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _git(*args: str, cwd: Path) -> str:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=str(cwd),
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise WorktreeError(f"Unable to run git: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise WorktreeError(detail or f"git {' '.join(args)} failed")
        return completed.stdout.strip()

    @classmethod
    def repository_root(cls, workspace: Path | str) -> Path:
        path = Path(workspace).expanduser().resolve()
        output = cls._git("rev-parse", "--show-toplevel", cwd=path)
        return Path(output).resolve()

    @classmethod
    def is_clean(cls, repository: Path | str) -> bool:
        repo = Path(repository).expanduser().resolve()
        return not cls._git("status", "--porcelain", "--untracked-files=all", cwd=repo)

    def create(
        self,
        *,
        task_id: str,
        workspace: Path | str,
        require_clean: bool = True,
    ) -> WorktreeSpec:
        repository = self.repository_root(workspace)
        if require_clean and not self.is_clean(repository):
            raise WorktreeError(
                "The repository has uncommitted changes. Commit or stash them before "
                "starting a write-capable isolated subagent."
            )
        base_revision = self._git("rev-parse", "HEAD", cwd=repository)
        branch = f"aria/task-{task_id}"
        destination = (self.root / repository.name / task_id).resolve()
        if destination.exists():
            raise WorktreeError(f"Worktree path already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._git(
                "worktree",
                "add",
                "-b",
                branch,
                str(destination),
                base_revision,
                cwd=repository,
            )
        except Exception:
            if destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            raise
        return WorktreeSpec(
            task_id=task_id,
            repository=str(repository),
            path=str(destination),
            branch=branch,
            base_revision=base_revision,
        )

    def remove(self, spec: WorktreeSpec, *, force: bool = False) -> None:
        repository = Path(spec.repository)
        path = Path(spec.path)
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(path))
        self._git(*args, cwd=repository)

    def delete_branch(self, spec: WorktreeSpec, *, force: bool = False) -> None:
        flag = "-D" if force else "-d"
        self._git("branch", flag, spec.branch, cwd=Path(spec.repository))

    @classmethod
    def diff(cls, spec: WorktreeSpec) -> str:
        worktree = Path(spec.path)
        diff_stat = cls._git("diff", "--stat", spec.base_revision, cwd=worktree)
        untracked = cls._git("ls-files", "--others", "--exclude-standard", cwd=worktree)
        parts = [part for part in (diff_stat, untracked) if part]
        return "\n".join(parts)

    @staticmethod
    def _git_binary(*args: str, cwd: Path, input_data: bytes | None = None) -> bytes:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=str(cwd),
                input=input_data,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise WorktreeError(f"Unable to run git: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).decode("utf-8", errors="replace").strip()
            raise WorktreeError(detail or f"git {' '.join(args)} failed")
        return completed.stdout

    def apply(self, spec: WorktreeSpec) -> WorktreeApplyResult:
        """Apply an isolated worktree's unstaged result to a clean main worktree."""
        repository = Path(spec.repository)
        worktree = Path(spec.path)
        if not self.is_clean(repository):
            raise WorktreeError(
                "The target workspace changed after the subagent started. "
                "Commit or stash those changes before applying the task result."
            )
        target_revision = self._git("rev-parse", "HEAD", cwd=repository)
        if target_revision != spec.base_revision:
            raise WorktreeError(
                "The target branch advanced after the subagent started. "
                "Review or rebase the task worktree before applying it."
            )

        tracked, untracked = self.changed_paths(spec)
        if not tracked and not untracked:
            raise WorktreeError("The subagent worktree contains no changes to apply.")

        for relative in untracked:
            destination = repository / relative
            if destination.exists():
                raise WorktreeError(
                    f"Refusing to overwrite an existing untracked path: {destination}"
                )

        patch = self._git_binary(
            "diff", "--binary", spec.base_revision, cwd=worktree
        )
        if patch:
            self._git_binary("apply", "--check", "-", cwd=repository, input_data=patch)

        copied: list[Path] = []
        patch_applied = False
        try:
            if patch:
                self._git_binary("apply", "-", cwd=repository, input_data=patch)
                patch_applied = True
            for relative in untracked:
                source = worktree / relative
                destination = repository / relative
                if source.is_symlink():
                    raise WorktreeError(f"Refusing to copy an untracked symlink: {source}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                copied.append(destination)
        except Exception:
            for destination in reversed(copied):
                if destination.exists():
                    destination.unlink()
            if patch_applied:
                self._git_binary("apply", "--reverse", "-", cwd=repository, input_data=patch)
            raise

        paths = tuple(dict.fromkeys([*tracked, *untracked]))
        return WorktreeApplyResult(paths, tracked, untracked)

    def changed_paths(self, spec: WorktreeSpec) -> tuple[tuple[str, ...], tuple[str, ...]]:
        worktree = Path(spec.path)
        tracked_text = self._git(
            "diff", "--name-only", spec.base_revision, cwd=worktree
        )
        tracked = tuple(path for path in tracked_text.splitlines() if path)
        untracked_text = self._git(
            "ls-files", "--others", "--exclude-standard", cwd=worktree
        )
        untracked = tuple(path for path in untracked_text.splitlines() if path)
        return tracked, untracked
