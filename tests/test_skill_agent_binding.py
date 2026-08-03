"""A skill may declare which specialist agents it orchestrates.

Two separate concerns are tested here:

1. Parsing/rendering — the loader reads ``agents`` out of skill-policy.json and
   surfaces it in the prompt block. Runs always, against a fixture.
2. Referential integrity — every agent name declared by the *real* installed
   catalog actually exists in the agent registry. This is the check that
   catches the failure this binding invites: a skill declaring ``fundemental``
   (typo), or an agent being renamed without the catalog following. Nothing
   crashes in that case — the skill just silently loses a specialist it thinks
   it has — so it needs a test rather than a runtime error. Skipped when no
   external catalog is installed, per the repo convention that optional
   dependencies skip rather than fail.
"""

import json
import os
from pathlib import Path

import pytest

from packages.aria_skills.loader import (
    activate_external_skills,
    default_skill_roots,
    discover_external_skills,
)


def _write_skill(root: Path, name: str, policy: dict | None = None) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Test skill for {name} binding.\n---\n\n"
        f"# {name}\n\nDo the thing.\n",
        encoding="utf-8",
    )
    if policy is not None:
        (folder / "skill-policy.json").write_text(json.dumps(policy), encoding="utf-8")
    return folder


def test_declared_agents_are_parsed(tmp_path):
    _write_skill(tmp_path, "with-agents", {"agents": ["fundamental", "risk"]})
    skill = discover_external_skills([tmp_path])[0]
    assert skill.policy.agents == ("fundamental", "risk")


def test_agents_default_to_empty_when_absent(tmp_path):
    _write_skill(tmp_path, "no-agents", {"permissions": ["read-only"]})
    skill = discover_external_skills([tmp_path])[0]
    assert skill.policy.agents == ()


def test_missing_policy_file_still_loads(tmp_path):
    _write_skill(tmp_path, "no-policy", None)
    skill = discover_external_skills([tmp_path])[0]
    assert skill.policy.agents == ()


def test_declared_agents_reach_the_prompt(tmp_path):
    _write_skill(tmp_path, "with-agents", {"agents": ["fundamental", "risk"]})
    skills = discover_external_skills([tmp_path])
    block = activate_external_skills(f"${skills[0].name}", skills).prompt_block
    assert "Specialist agents: fundamental, risk" in block


def test_prompt_is_explicit_when_none_declared(tmp_path):
    # "none declared" rather than an omitted line: a skill that orchestrates no
    # specialists should say so, not leave the reader guessing whether the
    # field was simply dropped.
    _write_skill(tmp_path, "no-agents", {"permissions": ["read-only"]})
    skills = discover_external_skills([tmp_path])
    block = activate_external_skills(f"${skills[0].name}", skills).prompt_block
    assert "Specialist agents: none declared" in block


def _installed_catalog_skills():
    if not any(Path(r).is_dir() for r in default_skill_roots()):
        return []
    return discover_external_skills()


@pytest.mark.skipif(
    not any(Path(r).is_dir() for r in default_skill_roots()),
    reason="no external skill catalog installed (set ARIA_SKILLS_PATH)",
)
def test_installed_catalog_declares_only_real_agents():
    from agents.registry import get_registry

    known = {row.get("name") for row in get_registry().list()}
    assert known, "agent registry is empty — cannot validate skill bindings"

    unknown: dict[str, list[str]] = {}
    for skill in _installed_catalog_skills():
        missing = [a for a in skill.policy.agents if a not in known]
        if missing:
            unknown[skill.qualified_name] = missing

    assert not unknown, (
        f"skills declare agents that do not exist in the registry: {unknown}. "
        f"Known agents: {sorted(known)}"
    )
