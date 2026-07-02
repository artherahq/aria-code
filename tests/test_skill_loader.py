import json
from pathlib import Path

from packages.aria_skills.loader import (
    activate_external_skills,
    build_skill_prompt_block,
    discover_external_skills,
    recent_skill_activation_traces,
    select_external_skills,
    skill_tree_sha256,
)


def _write_skill(
    root: Path,
    name: str = "equity-research-report",
    description: str = 'Build reports when asked for "全面分析报告" or "equity report".',
    folded: bool = False,
) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    path = folder / "SKILL.md"
    if folded:
        description_block = "description: >-\n  " + description.replace("\n", "\n  ")
    else:
        description_block = f"description: {description}"
    path.write_text(
        "---\n"
        f"name: {name}\n"
        f"{description_block}\n"
        "---\n\n"
        "# Workflow\n\nUse verified evidence and run completion gates.\n",
        encoding="utf-8",
    )
    return path


def _write_catalog(
    catalog: Path,
    *,
    plugin_name: str = "quant-research-skills",
    skill_name: str = "equity-research-report",
    locked_hash: str | None = None,
) -> Path:
    plugin_dir = catalog / ".claude-plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "marketplace.json").write_text(
        json.dumps({
            "metadata": {
                "version": "0.3.0",
                "repository": "https://github.com/artherahq/skills",
            },
            "plugins": [{
                "name": plugin_name,
                "version": "0.3.0",
                "repository": "https://github.com/artherahq/skills",
                "skills": [f"./skills/{skill_name}"],
            }],
        }),
        encoding="utf-8",
    )
    if locked_hash is not None:
        (plugin_dir / "skills.lock.json").write_text(
            json.dumps({
                "skills": {
                    f"{plugin_name}:{skill_name}": {"sha256": locked_hash},
                },
            }),
            encoding="utf-8",
        )
    return catalog / "skills"


def test_discovery_parses_folded_description_and_local_namespace(tmp_path):
    path = _write_skill(
        tmp_path,
        description='Trigger for "回测策略" and point-in-time validation.\nKeep original filings.',
        folded=True,
    )

    skills = discover_external_skills([tmp_path])

    assert len(skills) == 1
    assert skills[0].path == path
    assert skills[0].qualified_name == "local:equity-research-report"
    assert "Keep original filings" in skills[0].description


def test_marketplace_supplies_namespace_version_repository_and_lock(tmp_path):
    skills_root = tmp_path / "skills"
    path = _write_skill(skills_root)
    root = _write_catalog(tmp_path, locked_hash=skill_tree_sha256(path.parent))

    skill = discover_external_skills([root])[0]

    assert skill.qualified_name == "quant-research-skills:equity-research-report"
    assert skill.plugin_version == "0.3.0"
    assert skill.repository == "https://github.com/artherahq/skills"
    assert skill.integrity == "verified"


def test_generic_metadata_router_has_no_skill_name_hardcoding(tmp_path):
    skills_root = tmp_path / "skills"
    path = _write_skill(
        skills_root,
        name="custom-company-analysis",
        description='Use for "全面分析报告", "股票研报", and company analysis.',
    )
    root = _write_catalog(
        tmp_path,
        plugin_name="custom-research",
        skill_name="custom-company-analysis",
        locked_hash=skill_tree_sha256(path.parent),
    )
    skills = discover_external_skills([root])

    selected = select_external_skills("请给金盘科技做一份全面分析报告", skills)

    assert [skill.name for skill in selected] == ["custom-company-analysis"]


def test_unlocked_skill_requires_explicit_invocation(tmp_path):
    _write_skill(tmp_path, name="workspace-helper")
    skill = discover_external_skills([tmp_path])[0]

    automatic = activate_external_skills("请做全面分析报告", [skill])
    explicit = activate_external_skills("Use $workspace-helper", [skill])

    assert automatic.skills == ()
    assert automatic.traces[0].reason == (
        "automatic activation requires an integrity lock"
    )
    assert [item.qualified_name for item in explicit.skills] == [
        "local:workspace-helper"
    ]


def test_qualified_invocation_disambiguates_duplicate_skill_names(tmp_path):
    roots = []
    for plugin_name in ("plugin-a", "plugin-b"):
        catalog = tmp_path / plugin_name
        skills_root = catalog / "skills"
        _write_skill(skills_root, name="shared-skill")
        roots.append(_write_catalog(
            catalog,
            plugin_name=plugin_name,
            skill_name="shared-skill",
        ))
    skills = discover_external_skills(roots)

    ambiguous = select_external_skills("Use $shared-skill", skills)
    selected = select_external_skills("Use $plugin-b:shared-skill", skills)

    assert ambiguous == []
    assert [skill.qualified_name for skill in selected] == ["plugin-b:shared-skill"]


def test_integrity_mismatch_blocks_activation_and_records_trace(tmp_path):
    skills_root = tmp_path / "skills"
    _write_skill(skills_root)
    root = _write_catalog(tmp_path, locked_hash="0" * 64)
    skill = discover_external_skills([root])[0]

    activation = activate_external_skills(
        "Use $quant-research-skills:equity-research-report",
        [skill],
    )

    assert activation.skills == ()
    assert activation.traces[0].activated is False
    assert activation.traces[0].reason == "integrity mismatch"
    assert recent_skill_activation_traces(1)[0].qualified_name == skill.qualified_name


def test_prompt_includes_declared_policy_and_never_pre_authorizes_scripts(tmp_path):
    path = _write_skill(tmp_path)
    (path.parent / "skill-policy.json").write_text(
        json.dumps({
            "allowed_tools": ["get_market_data"],
            "permissions": ["network"],
            "scripts": {"execution": "approval", "network": False},
        }),
        encoding="utf-8",
    )
    skill = discover_external_skills([tmp_path])[0]

    block = activate_external_skills(
        "Use $equity-research-report",
        [skill],
    ).prompt_block

    assert "Active Skill: local:equity-research-report" in block
    assert "Allowed tools: get_market_data" in block
    assert "Bundled scripts are not pre-authorized" in block


def test_build_skill_prompt_block_uses_configured_catalog(tmp_path, monkeypatch):
    skills_root = tmp_path / "skills"
    path = _write_skill(skills_root)
    _write_catalog(tmp_path, locked_hash=skill_tree_sha256(path.parent))
    monkeypatch.setenv("ARIA_SKILLS_PATH", str(skills_root))

    block = build_skill_prompt_block(
        "$quant-research-skills:equity-research-report analyze a stock"
    )

    assert "Active Skill: quant-research-skills:equity-research-report" in block
    assert "Integrity: verified" in block
