"""Product-level service contracts for Aria Code.

These manifests describe stable service boundaries. They intentionally wrap the
current modules instead of moving code all at once, so the CLI can migrate away
from the large legacy entrypoint incrementally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from packages.aria_core import CapabilityManifest, PackageLink, PermissionLevel, ServiceKind


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    description: str
    owner_package: str
    module: str
    capabilities: List[str] = field(default_factory=list)
    permissions: List[PermissionLevel] = field(default_factory=list)
    required: bool = True
    migration_target: str = ""

    def manifest(self) -> CapabilityManifest:
        tags = ["service"]
        if self.required:
            tags.append("required")
        return CapabilityManifest(
            name=self.name,
            kind=ServiceKind.DATA if self.name == "data" else ServiceKind.SERVICE,
            description=self.description,
            capabilities=self.capabilities,
            permissions=self.permissions,
            package_link=PackageLink(
                package=self.owner_package,
                module=self.module,
                object_name=self.migration_target,
                notes="Facade over existing modules; migrate implementation behind this boundary.",
            ),
            tags=tags,
        )


def list_service_specs() -> List[ServiceSpec]:
    """Return stable service boundaries for the Aria Code product."""

    return [
        ServiceSpec(
            name="gateway",
            description="Local-first control plane for CLI, daemon, MCP, and channel entrypoints.",
            owner_package="packages.aria_services",
            module="apps.gateway",
            capabilities=["session.routing", "task.queue", "mcp.routing", "channel.routing"],
            permissions=[PermissionLevel.READ_ONLY, PermissionLevel.WORKSPACE_WRITE],
            migration_target="GatewayService",
        ),
        ServiceSpec(
            name="runtime",
            description="Agent turn loop, tool execution, approvals, traces, and parallel tool orchestration.",
            owner_package="runtime",
            module="runtime",
            capabilities=["agent.loop", "tool.execution", "approval.policy", "trace"],
            permissions=[PermissionLevel.READ_ONLY, PermissionLevel.WORKSPACE_WRITE],
            migration_target="AgentRuntime",
        ),
        ServiceSpec(
            name="data",
            description="Multi-source market data with timestamps, provider attribution, and stale-data flags.",
            owner_package="packages.aria_services",
            module="data_service",
            capabilities=["market.quote", "market.history", "technical.indicators", "data.quality"],
            permissions=[PermissionLevel.NETWORK],
            migration_target="MarketDataService",
        ),
        ServiceSpec(
            name="reports",
            description="Report, chart, and artifact generation with local workspace storage policies.",
            owner_package="packages.aria_services",
            module="report_generator",
            capabilities=["report.markdown", "report.html", "chart.render", "artifact.storage"],
            permissions=[PermissionLevel.READ_ONLY, PermissionLevel.WORKSPACE_WRITE],
            migration_target="ReportService",
        ),
        ServiceSpec(
            name="brokers",
            description="Broker account reads, paper trading, order previews, guarded execution, and audit trails.",
            owner_package="brokers",
            module="brokers",
            capabilities=[
                "broker.account",
                "broker.positions",
                "broker.orders",
                "broker.paper",
                "broker.trade_preview",
                "broker.confirmed_execution",
                "broker.audit",
            ],
            permissions=[PermissionLevel.BROKER_READ, PermissionLevel.BROKER_TRADE],
            migration_target="BrokerService",
        ),
        ServiceSpec(
            name="skills",
            description="Workflow recipes that bind tools, agents, data, reports, and brokers.",
            owner_package="packages.aria_skills",
            module="packages.aria_skills",
            capabilities=["skill.registry", "workflow.binding"],
            permissions=[PermissionLevel.READ_ONLY],
            migration_target="SkillRegistry",
        ),
        ServiceSpec(
            name="channels",
            description="Optional external entrypoints such as daemon, relay, Feishu, Telegram, and webhooks.",
            owner_package="apps.channels",
            module="apps.channels",
            capabilities=["channel.daemon", "channel.relay", "channel.feishu", "channel.telegram"],
            permissions=[PermissionLevel.NETWORK],
            required=False,
            migration_target="ChannelRegistry",
        ),
        ServiceSpec(
            name="safety",
            description="Sandbox, workspace permissions, command policy, privacy controls, and audit hooks.",
            owner_package="safety",
            module="safety",
            capabilities=["sandbox", "command.policy", "privacy", "audit"],
            permissions=[PermissionLevel.READ_ONLY],
            migration_target="SafetyService",
        ),
    ]


def service_map() -> Dict[str, ServiceSpec]:
    return {service.name: service for service in list_service_specs()}


def required_service_names() -> List[str]:
    return [service.name for service in list_service_specs() if service.required]
