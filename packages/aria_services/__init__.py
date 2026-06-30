"""Service boundary manifests for Aria Code."""

from .context import (
    ContextDecision,
    ContextPolicy,
    ContextService,
    ContextSummaryEnvelope,
    build_context_service,
)
from .provider_health import (
    GLOBAL_PROVIDER_HEALTH,
    ProviderHealthRegistry,
    ProviderIssue,
    ProviderState,
    classify_provider_error,
    summarize_provider_health,
)
from .references import (
    REFERENCE_KINDS,
    ContextReference,
    PreparedReferences,
    ReferenceKind,
    ReferencePolicy,
    ReferenceService,
    build_reference_service,
    iter_reference_tokens,
    reference_search_roots,
)
from .registry import ServiceSpec, list_service_specs, required_service_names, service_map
from .usage import ServiceUsageSpec, list_service_usage_specs, service_usage_map

__all__ = [
    "ContextDecision",
    "ContextPolicy",
    "ContextService",
    "ContextSummaryEnvelope",
    "ContextReference",
    "DataBundle",
    "DataService",
    "DataServiceResult",
    "GLOBAL_PROVIDER_HEALTH",
    "ProviderHealthRegistry",
    "ProviderIssue",
    "ProviderState",
    "PreparedReferences",
    "REFERENCE_KINDS",
    "ReferenceKind",
    "ReferencePolicy",
    "ReferenceService",
    "ServiceSpec",
    "ServiceUsageSpec",
    "build_context_service",
    "build_reference_service",
    "classify_provider_error",
    "list_service_specs",
    "list_service_usage_specs",
    "iter_reference_tokens",
    "reference_search_roots",
    "required_service_names",
    "service_map",
    "service_usage_map",
    "summarize_provider_health",
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
