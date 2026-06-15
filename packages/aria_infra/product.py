"""Product identity for Aria Code inside the Arthera product line."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List


@dataclass(frozen=True)
class ProductIdentity:
    company: str
    product: str
    package_name: str
    version: str
    description: str
    product_family: List[str]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def aria_code_identity(version: str = "3.0.0") -> ProductIdentity:
    return ProductIdentity(
        company="Arthera",
        product="Aria Code",
        package_name="aria-code",
        version=version,
        description="Local-first coding and quantitative research agent by Arthera.",
        product_family=[
            "Aria Code",
            "Aria Gateway",
            "Arthera Quant Engine",
            "Arthera Broker Bridge",
            "Arthera ML/LLM",
            "Arthera Reporting",
        ],
    )
