"""Optional discovery for the sibling Arthera monorepo packages."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ArtheraPackageMap:
    root: Path
    available: bool
    packages: Dict[str, Path] = field(default_factory=dict)
    mcp_servers: List[Path] = field(default_factory=list)
    tool_dirs: List[Path] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "root": str(self.root),
            "available": self.available,
            "packages": {name: str(path) for name, path in self.packages.items()},
            "mcp_servers": [str(path) for path in self.mcp_servers],
            "tool_dirs": [str(path) for path in self.tool_dirs],
        }


def discover_arthera_packages(root: Optional[Path] = None) -> ArtheraPackageMap:
    """Discover useful Arthera package entrypoints without importing them."""

    root = (root or Path.home() / "Desktop" / "Arthera" / "packages").expanduser()
    if not root.exists():
        return ArtheraPackageMap(root=root, available=False)

    names = ["contracts", "data", "ml", "monitoring", "quant_engine", "reporting"]
    packages = {name: root / name for name in names if (root / name).exists()}
    mcp_servers = sorted(root.glob("**/mcp_server.py"))
    tool_dirs = [
        path for path in [
            root / "quant_engine" / "tools",
            root / "ml" / "llm" / "tools",
            root / "ml" / "llm" / "skills",
        ]
        if path.exists()
    ]
    return ArtheraPackageMap(
        root=root,
        available=True,
        packages=packages,
        mcp_servers=mcp_servers,
        tool_dirs=tool_dirs,
    )
