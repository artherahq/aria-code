"""Core contracts shared by Aria packages."""

from .architecture import (
    ARCHITECTURE_SCHEMA_VERSION,
    ArchitectureLayer,
    LayerStatus,
    architecture_contract,
    architecture_gaps,
    architecture_layer_map,
    architecture_status_counts,
    list_architecture_layers,
    required_architecture_layer_names,
)
from .export import build_package_manifest, build_session_diagnostic_bundle, write_package_manifest
from .manifest import CapabilityManifest, PackageLink, PermissionLevel, ServiceKind

__all__ = [
    "ARCHITECTURE_SCHEMA_VERSION",
    "ArchitectureLayer",
    "CapabilityManifest",
    "LayerStatus",
    "PackageLink",
    "PermissionLevel",
    "ServiceKind",
    "architecture_contract",
    "architecture_gaps",
    "architecture_layer_map",
    "architecture_status_counts",
    "build_package_manifest",
    "build_session_diagnostic_bundle",
    "list_architecture_layers",
    "required_architecture_layer_names",
    "write_package_manifest",
]
