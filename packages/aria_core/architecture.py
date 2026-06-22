"""Declarative agent architecture contract for Aria Code.

The contract keeps Codex/Claude Code style boundaries explicit while the legacy
CLI is extracted into smaller packages. It is intentionally pure data so tests,
doctor checks, docs, and future UI views can share one source of truth.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Tuple


class LayerStatus(str, Enum):
    DONE = "done"
    PARTIAL = "partial"
    PLANNED = "planned"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ArchitectureLayer:
    name: str
    responsibility: str
    target_state: str
    current_state: str
    status: LayerStatus
    source_paths: Tuple[str, ...] = field(default_factory=tuple)
    depends_on: Tuple[str, ...] = field(default_factory=tuple)
    next_steps: Tuple[str, ...] = field(default_factory=tuple)
    blockers: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_complete(self) -> bool:
        return self.status == LayerStatus.DONE

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


ARCHITECTURE_SCHEMA_VERSION = "aria.agent-architecture.v1"


_ARCHITECTURE_LAYERS: Tuple[ArchitectureLayer, ...] = (
    ArchitectureLayer(
        name="launcher",
        responsibility="Stable executable entrypoint, runtime selection, and dependency bootstrap.",
        target_state="Shell entrypoints resolve the repo, use a controlled virtualenv, and never depend on the caller's random Python.",
        current_state="aria and aria-code bootstrap local dependencies; existing virtualenvs may still need runtime rebuild support.",
        status=LayerStatus.PARTIAL,
        source_paths=("aria-code", "install.sh"),
        next_steps=("Add a doctor check for Python version drift and a documented venv rebuild command.",),
    ),
    ArchitectureLayer(
        name="settings",
        responsibility="Configuration, secrets, model profiles, and permission policy resolution.",
        target_state="A single settings service resolves env, config files, CLI flags, and secrets without leaking credentials.",
        current_state="Settings are still split across CLI config, env vars, and feature-specific files.",
        status=LayerStatus.PLANNED,
        source_paths=("config.py", "settings_manager.py", "cloud_config.py"),
        next_steps=("Extract SettingsService and make launcher, CLI, daemon, brokers, and MCP use it.",),
    ),
    ArchitectureLayer(
        name="ui",
        responsibility="Terminal rendering, input UX, progress display, and artifact links.",
        target_state="A thin UI adapter renders compact, resumable, non-repetitive output with graceful plain-terminal fallback.",
        current_state="Terminal streaming and approval prompts now have a CLI runtime event consumer; Rich/prompt_toolkit remain optional and generated-file UX still needs hardening.",
        status=LayerStatus.PARTIAL,
        source_paths=("ui/", "apps/cli/commands/", "apps/cli/runtime_consumer.py"),
        next_steps=("Move generated-file open actions and remaining terminal panels behind a UI service.",),
    ),
    ArchitectureLayer(
        name="context",
        responsibility="Conversation memory, context compaction, task continuity, and artifact-backed summaries.",
        target_state="Context is automatically compacted before overflow, with recoverable task state and traceable artifacts.",
        current_state="ContextService owns pressure checks, local compaction, summary prompts, and resume envelopes; durable checkpoints are still pending.",
        status=LayerStatus.PARTIAL,
        source_paths=("packages/aria_services/context.py", "apps/cli/message_processing.py", "apps/cli/commands/session_ux_cmds.py"),
        next_steps=("Persist compaction checkpoints and expose context health through /doctor and support bundles.",),
        blockers=("Choose artifact schema and retention policy for compact/resume checkpoints.",),
    ),
    ArchitectureLayer(
        name="runtime",
        responsibility="Agent turn loop, planning, tool execution, retries, streaming, and interruption handling.",
        target_state="Runtime is separate from UI and business services, with typed tool calls, retries, cancellation, and traces.",
        current_state="A public packages.aria_sdk facade owns SDK-style query/result events, provider selection, streaming normalization, and the reusable runtime tool-turn loop; CLI rendering/approval is consumed through apps.cli.runtime_consumer. The CLI chat turn can now opt into run_agent via apps.cli.providers.runtime_bridge (config use_runtime_loop), with route-aware provider/fallback decisions in apps.cli.providers.chat_routing; send_message falls back to the inline loop on any error.",
        status=LayerStatus.PARTIAL,
        source_paths=("aria_cli.py", "runtime/", "packages/aria_sdk/", "apps/cli/runtime_consumer.py", "apps/cli/deterministic.py", "apps/cli/providers/"),
        depends_on=("settings", "tools", "safety", "context"),
        next_steps=("Verify the use_runtime_loop path in a live REPL, default it on, then retire the inline send_message loop so the turn runs entirely through run_agent.",),
    ),
    ArchitectureLayer(
        name="tools",
        responsibility="Tool registry, schemas, permissions, local commands, and MCP adapters.",
        target_state="All tools are manifests with input schema, permission level, owner service, and deterministic output shape.",
        current_state="Legacy tools can be converted to manifests; some direct command paths still bypass the registry.",
        status=LayerStatus.PARTIAL,
        source_paths=("packages/aria_tools/", "tools/"),
        depends_on=("safety",),
        next_steps=("Route new slash commands through tool/service manifests instead of direct CLI functions.",),
    ),
    ArchitectureLayer(
        name="services",
        responsibility="Product service boundaries for data, reports, brokers, skills, channels, and gateway.",
        target_state="Business logic lives behind services; CLI, daemon, MCP, and webhooks are adapters.",
        current_state="Service specs are registered, but several implementations still sit in legacy modules.",
        status=LayerStatus.PARTIAL,
        source_paths=("packages/aria_services/", "docs/architecture/service_boundaries.md"),
        depends_on=("settings", "runtime", "tools"),
        next_steps=("Move data, report, broker, and TradingView workflows behind service facades incrementally.",),
    ),
    ArchitectureLayer(
        name="mcp",
        responsibility="External package/tool integration through MCP and explicit adapters.",
        target_state="MCP servers expose typed tools, health, permissions, and connection status independent of CLI state.",
        current_state="Arthera Quant Engine bridge has manifests and doctor checks; lifecycle management still needs tightening.",
        status=LayerStatus.PARTIAL,
        source_paths=("packages/aria_mcp/",),
        depends_on=("tools", "services"),
        next_steps=("Add MCP reload/reconnect policy, tool provenance, and per-server failure isolation.",),
    ),
    ArchitectureLayer(
        name="safety",
        responsibility="Filesystem, shell, network, broker, and privacy guardrails.",
        target_state="Every risky action is classified, previewed when needed, audited, and blocked by default for live trading.",
        current_state="Broker preview/confirm exists, but shell/network/file policies are not yet one unified service.",
        status=LayerStatus.PARTIAL,
        source_paths=("safety/", "brokers/", "apps/cli/commands/broker_cmds.py"),
        depends_on=("settings", "tools"),
        next_steps=("Unify command policy, broker risk policy, and privacy controls under SafetyService.",),
    ),
    ArchitectureLayer(
        name="channels",
        responsibility="Daemon, webhook, TradingView alerts, Feishu, Telegram, and future external entrypoints.",
        target_state="Channels submit structured tasks to gateway/runtime and never call CLI internals directly.",
        current_state="TradingView URL/Pine generation exists; webhook-to-daemon routing is still incomplete.",
        status=LayerStatus.PLANNED,
        source_paths=("aria_daemon.py", "apps/channels/"),
        depends_on=("settings", "runtime", "services", "safety"),
        next_steps=("Implement channel registry and TradingView alert webhook flow through gateway.",),
    ),
    ArchitectureLayer(
        name="observability",
        responsibility="Doctor checks, traces, provider health, audit logs, and user-visible diagnostics.",
        target_state="Health checks explain missing services, degraded providers, unsafe configs, and incomplete architecture layers.",
        current_state="Provider and package doctor checks exist; this contract represents architecture coverage and the /architecture command renders it (layers, status, gaps, per-layer next steps; --gaps for outstanding work).",
        status=LayerStatus.PARTIAL,
        source_paths=("packages/aria_infra/doctor.py", "packages/aria_services/provider_health.py", "apps/cli/commands/diagnostic_ops_cmds.py"),
        depends_on=("services", "mcp", "safety"),
        next_steps=("Fold the architecture summary into /doctor and generated support bundles too.",),
    ),
)


def list_architecture_layers() -> List[ArchitectureLayer]:
    """Return the product architecture layers in dependency order."""

    return list(_ARCHITECTURE_LAYERS)


def architecture_layer_map() -> Dict[str, ArchitectureLayer]:
    return {layer.name: layer for layer in _ARCHITECTURE_LAYERS}


def required_architecture_layer_names() -> List[str]:
    return [layer.name for layer in _ARCHITECTURE_LAYERS]


def architecture_gaps() -> List[ArchitectureLayer]:
    return [layer for layer in _ARCHITECTURE_LAYERS if not layer.is_complete]


def architecture_status_counts() -> Dict[str, int]:
    counts = {status.value: 0 for status in LayerStatus}
    for layer in _ARCHITECTURE_LAYERS:
        counts[layer.status.value] += 1
    return counts


def architecture_contract() -> Dict[str, Any]:
    return {
        "schema_version": ARCHITECTURE_SCHEMA_VERSION,
        "layers": [layer.to_dict() for layer in _ARCHITECTURE_LAYERS],
        "status_counts": architecture_status_counts(),
    }
