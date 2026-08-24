"""SafetyService — one facade over Aria's three safety domains.

The architecture contract's safety layer calls for command policy, broker
risk policy, and privacy controls to be reachable through a single service
(registry spec ``safety`` → SafetyService, capabilities: command.policy,
privacy, audit, sandbox). The three implementations already exist and stay
where they are; this facade composes them behind one config-driven object so
launcher/daemon/CLI/MCP consumers stop reaching into three packages with
three different conventions:

  • command/tool policy   → safety.permissions.PermissionService
  • privacy controls      → privacy.PrivacySettings           (lazy import)
  • broker/trading risk   → brokers.trading policy helpers    (lazy import)

Cross-domain imports are deferred to call time, so importing ``safety``
never drags in broker or privacy modules (mirrors SettingsService's
injection-clean layering).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .permissions import PermissionDecision, PermissionService, classify_command_risk


class SafetyService:
    """Config-driven facade over command, privacy, and trading safety."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self._config: Dict[str, Any] = dict(config or {})
        self._permissions = self._build_permissions(self._config)

    # ── lifecycle ────────────────────────────────────────────────────────────

    @staticmethod
    def _build_permissions(config: Dict[str, Any]) -> PermissionService:
        return PermissionService(
            mode=config.get("permission_mode", "workspace-write"),
            command_policy=config.get("command_policy", "safe"),
            network_enabled=bool(config.get("network_enabled", True)),
        )

    def refresh(self, config: Dict[str, Any]) -> None:
        """Rebuild policy state after a config change (e.g. /config set)."""
        self._config = dict(config)
        self._permissions = self._build_permissions(self._config)

    # ── command / tool policy (capability: command.policy) ──────────────────

    @property
    def permissions(self) -> PermissionService:
        return self._permissions

    def evaluate_tool(self, tool_name: str, params: Optional[Dict[str, Any]] = None) -> PermissionDecision:
        return self._permissions.evaluate_tool(tool_name, params)

    def evaluate_command(self, command: str, policy: Optional[str] = None) -> PermissionDecision:
        return self._permissions.evaluate_command(command, policy)

    def classify_risk(self, command: str) -> str:
        return classify_command_risk(command)

    # ── privacy controls (capability: privacy) ──────────────────────────────

    def privacy(self):
        """Current PrivacySettings derived from config."""
        from privacy import PrivacySettings
        return PrivacySettings.from_config(self._config)

    # ── broker / trading risk (capability: audit — trading is the audited
    #    domain today; the jsonl audit sink lives with the trading policy) ───

    def trading_policy(self, broker_type: str = ""):
        """TradingPolicy resolved from config (paper/live, confirm, limits)."""
        from brokers.trading import policy_from_config
        return policy_from_config(self._config, broker_type)

    def trading_mode(self, broker_type: str = "") -> str:
        from brokers.trading import resolve_trading_mode
        return resolve_trading_mode(self._config, broker_type)

    def trading_dry_run(self) -> bool:
        """Global operational kill-switch (env ARIA_TRADING_DRY_RUN)."""
        from brokers.trading import global_dry_run
        return global_dry_run()
