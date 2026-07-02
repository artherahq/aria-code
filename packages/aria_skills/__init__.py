"""Skill registry facade."""

from .registry import SkillSpec, builtin_skill_specs
from .catalog import (
    CatalogInstallResult,
    CatalogSource,
    catalog_clone_command,
    default_catalog_home,
    install_catalog,
    parse_catalog_source,
)
from .loader import (
    LoadedSkill,
    SkillActivation,
    SkillActivationTrace,
    SkillPolicy,
    activate_external_skills,
    build_skill_prompt_block,
    default_skill_roots,
    discover_external_skills,
    recent_skill_activation_traces,
    select_external_skills,
    skill_tree_sha256,
)

__all__ = [
    "CatalogInstallResult",
    "CatalogSource",
    "LoadedSkill",
    "SkillActivation",
    "SkillActivationTrace",
    "SkillPolicy",
    "SkillSpec",
    "activate_external_skills",
    "build_skill_prompt_block",
    "builtin_skill_specs",
    "catalog_clone_command",
    "default_catalog_home",
    "default_skill_roots",
    "discover_external_skills",
    "install_catalog",
    "parse_catalog_source",
    "recent_skill_activation_traces",
    "select_external_skills",
    "skill_tree_sha256",
]
