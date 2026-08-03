from pathlib import Path
import json
import shutil
import subprocess

import pytest

from packages.aria_skills.catalog import (
    CatalogSource,
    catalog_clone_command,
    install_catalog,
    parse_catalog_source,
)
from packages.aria_skills.loader import skill_tree_sha256


@pytest.mark.parametrize(
    "value",
    (
        "artherahq/skills",
        "https://github.com/artherahq/skills",
        "https://github.com/artherahq/skills.git",
    ),
)
def test_parse_catalog_source_accepts_arthera_github_routes(value):
    source = parse_catalog_source(value)

    assert source.full_name == "artherahq/skills"
    assert source.clone_url == "https://github.com/artherahq/skills.git"


@pytest.mark.parametrize(
    "value",
    ("", "skills", "https://example.com/artherahq/skills", "../skills", "a/b/c"),
)
def test_parse_catalog_source_rejects_non_github_or_ambiguous_routes(value):
    with pytest.raises(ValueError):
        parse_catalog_source(value)


def test_catalog_clone_command_uses_argument_array_and_optional_ref(tmp_path):
    command = catalog_clone_command(
        CatalogSource("artherahq", "skills"),
        tmp_path / "skills",
        ref="v0.3.0",
    )

    assert command == [
        "git",
        "clone",
        "--depth",
        "1",
        "--filter=blob:none",
        "https://github.com/artherahq/skills.git",
        str(tmp_path / "skills"),
    ]


def test_catalog_clone_command_rejects_shell_metacharacters(tmp_path):
    with pytest.raises(ValueError):
        catalog_clone_command(
            CatalogSource("artherahq", "skills"),
            tmp_path / "skills",
            ref="main;rm -rf /",
        )


def test_install_catalog_accepts_only_a_verified_cloned_catalog(tmp_path):
    source_catalog = tmp_path / "source"
    skill = source_catalog / "skills" / "sample-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: sample-skill\ndescription: Use for sample validation.\n---\n\n# Sample\n",
        encoding="utf-8",
    )
    plugin_dir = source_catalog / ".claude-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "marketplace.json").write_text(
        json.dumps({
            "metadata": {"version": "1.0.0"},
            "plugins": [{
                "name": "sample-plugin",
                "skills": ["./skills/sample-skill"],
            }],
        }),
        encoding="utf-8",
    )
    (plugin_dir / "skills.lock.json").write_text(
        json.dumps({
            "skills": {
                "sample-plugin:sample-skill": {"sha256": skill_tree_sha256(skill)},
            },
        }),
        encoding="utf-8",
    )

    def fake_runner(command, **kwargs):
        if command[1] == "clone":
            shutil.copytree(source_catalog, Path(command[-1]))
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, "abc123def456\n", "")

    result = install_catalog(
        "artherahq/skills",
        catalog_home=tmp_path / "installed",
        runner=fake_runner,
    )

    assert result.revision == "abc123def456"
    assert [skill.qualified_name for skill in result.skills] == [
        "sample-plugin:sample-skill"
    ]
    assert result.skills[0].integrity == "verified"


def test_install_catalog_fetches_and_detaches_requested_ref(tmp_path):
    source_catalog = tmp_path / "source"
    skill = source_catalog / "skills" / "sample-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: sample-skill\ndescription: Use explicitly.\n---\n\n# Skill\n",
        encoding="utf-8",
    )
    plugin_dir = source_catalog / ".claude-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "marketplace.json").write_text(
        json.dumps({
            "plugins": [{
                "name": "sample-plugin",
                "skills": ["./skills/sample-skill"],
            }],
        }),
        encoding="utf-8",
    )
    (plugin_dir / "skills.lock.json").write_text(
        json.dumps({
            "skills": {
                "sample-plugin:sample-skill": {
                    "sha256": skill_tree_sha256(skill),
                },
            },
        }),
        encoding="utf-8",
    )
    commands = []

    def fake_runner(command, **kwargs):
        commands.append(command)
        if command[1] == "clone":
            shutil.copytree(source_catalog, Path(command[-1]))
        stdout = "abc123def456\n" if command[1:3] == ["rev-parse", "HEAD"] else ""
        return subprocess.CompletedProcess(command, 0, stdout, "")

    result = install_catalog(
        "artherahq/skills",
        ref="abc123def456",
        catalog_home=tmp_path / "installed",
        runner=fake_runner,
    )

    assert result.revision == "abc123def456"
    assert ["git", "fetch", "--depth", "1", "origin", "abc123def456"] in commands
    assert ["git", "checkout", "--detach", "FETCH_HEAD"] in commands


def test_install_catalog_removes_unverified_partial_clone(tmp_path):
    source_catalog = tmp_path / "source"
    skill = source_catalog / "skills" / "unlocked-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: unlocked-skill\ndescription: Use explicitly.\n---\n\n# Skill\n",
        encoding="utf-8",
    )

    def fake_runner(command, **kwargs):
        shutil.copytree(source_catalog, Path(command[-1]))
        return subprocess.CompletedProcess(command, 0, "", "")

    catalog_home = tmp_path / "installed"
    with pytest.raises(ValueError, match="integrity verification failed"):
        install_catalog(
            "artherahq/skills",
            catalog_home=catalog_home,
            runner=fake_runner,
        )

    assert not (catalog_home / "artherahq" / "skills").exists()
