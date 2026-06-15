"""Service boundary manifests for Aria Code."""

from .provider_health import (
    GLOBAL_PROVIDER_HEALTH,
    ProviderHealthRegistry,
    ProviderIssue,
    ProviderState,
    classify_provider_error,
)
from .registry import ServiceSpec, list_service_specs, required_service_names, service_map

__all__ = [
    "DataBundle",
    "DataService",
    "DataServiceResult",
    "GLOBAL_PROVIDER_HEALTH",
    "ProviderHealthRegistry",
    "ProviderIssue",
    "ProviderState",
    "ServiceSpec",
    "classify_provider_error",
    "list_service_specs",
    "required_service_names",
    "service_map",
]


def __getattr__(name: str):
    if name in {"DataBundle", "DataService", "DataServiceResult"}:
        from .data import DataBundle, DataService, DataServiceResult

        return {
            "DataBundle": DataBundle,
            "DataService": DataService,
            "DataServiceResult": DataServiceResult,
        }[name]
    raise AttributeError(name)
