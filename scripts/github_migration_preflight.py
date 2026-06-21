#!/usr/bin/env python3
"""Preflight checks before moving Aria Code to the Arthera GitHub organization."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ORG = os.environ.get("ARTHERA_GITHUB_ORG", "Arthera")
EXPECTED_REPO = os.environ.get("ARIA_GITHUB_REPO", "aria-code")
BRANCH_PATTERN = re.compile(
    r"^(main|develop|dev|feature/.+|fix/.+|refactor/.+|chore/.+|docs/.+|release/v.+|codex/.+)$"
)


def git(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def line(status: str, message: str) -> None:
    print(f"{status:5} {message}")


def tracked(path: str) -> bool:
    return git("ls-files", "--error-unmatch", path).returncode == 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Warn instead of fail when the working tree has uncommitted changes.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    failures: list[str] = []
    warnings: list[str] = []

    if not (ROOT / ".git").exists():
        failures.append("not a git repository")

    branch = git("branch", "--show-current").stdout.strip()
    if branch:
        if BRANCH_PATTERN.match(branch):
            line("ok", f"branch name: {branch}")
        else:
            warnings.append(f"branch '{branch}' does not match the enterprise branch model")
    else:
        failures.append("detached HEAD or unknown branch")

    status = git("status", "--porcelain").stdout.strip()
    if status:
        message = "working tree has uncommitted changes"
        if args.allow_dirty:
            warnings.append(message)
        else:
            failures.append(message)
    else:
        line("ok", "working tree clean")

    remotes = git("remote", "-v").stdout.strip().splitlines()
    if remotes:
        for remote in remotes:
            line("info", remote)
    else:
        failures.append("no git remotes configured")

    target_fragment = f"github.com/{EXPECTED_ORG}/{EXPECTED_REPO}"
    has_arthera_remote = any(target_fragment.lower() in remote.lower() for remote in remotes)
    if has_arthera_remote:
        line("ok", f"Arthera remote detected: {EXPECTED_ORG}/{EXPECTED_REPO}")
    else:
        warnings.append(
            f"Arthera remote not configured yet; expected URL contains {target_fragment}"
        )

    upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream.returncode == 0:
        upstream_name = upstream.stdout.strip()
        counts = git("rev-list", "--left-right", "--count", f"{upstream_name}...HEAD")
        if counts.returncode == 0:
            behind, ahead = counts.stdout.strip().split()
            if ahead != "0":
                warnings.append(f"branch is ahead of upstream by {ahead} commits")
            if behind != "0":
                warnings.append(f"branch is behind upstream by {behind} commits")
            line("info", f"upstream: {upstream_name} (ahead {ahead}, behind {behind})")
    else:
        warnings.append("current branch has no upstream")

    required_files = [
        ".github/workflows/ci.yml",
        "README.md",
        "CONTRIBUTING.md",
        "docs/operations/github_enterprise_migration.md",
    ]
    for rel in required_files:
        if (ROOT / rel).exists():
            line("ok", f"required file exists: {rel}")
        else:
            failures.append(f"missing required file: {rel}")

    secret_like = [".env", ".env.local", "id_rsa", "id_ed25519"]
    for rel in secret_like:
        if tracked(rel):
            failures.append(f"secret-like file is tracked: {rel}")
    line("ok", "no tracked root .env/private key files found")

    ci_text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    for branch_name in ("main", "develop", "codex/**", "feature/**"):
        if branch_name not in ci_text:
            warnings.append(f"CI workflow does not explicitly include branch pattern: {branch_name}")

    docs_with_personal_urls = []
    for rel in ("README.md", "README_CN.md", "CONTRIBUTING.md", "pyproject.toml"):
        path = ROOT / rel
        if path.exists() and "github.com/Cinsoul" in path.read_text(encoding="utf-8", errors="ignore"):
            docs_with_personal_urls.append(rel)
    if docs_with_personal_urls:
        warnings.append(
            "personal GitHub URLs still present before cutover: "
            + ", ".join(docs_with_personal_urls)
        )

    for warning in warnings:
        line("warn", warning)
    for failure in failures:
        line("fail", failure)

    if failures:
        print("\nPreflight failed. Fix failures before pushing to the Arthera repository.")
        return 1

    print("\nPreflight passed with warnings." if warnings else "\nPreflight passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
