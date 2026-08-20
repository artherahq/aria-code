"""Tests for safety.service.SafetyService — the facade unifying command
policy, privacy controls, and broker/trading risk behind one config-driven
object (the contract's safety-layer next step). Pins delegation semantics so
the three underlying domains can't drift apart from the facade."""

import unittest

from safety import SafetyService


class CommandPolicyTests(unittest.TestCase):
    def test_safe_policy_blocks_high_risk_command(self):
        svc = SafetyService({"command_policy": "safe", "permission_mode": "workspace-write"})
        d = svc.evaluate_command("rm -rf /")
        self.assertFalse(d.allowed)

    def test_read_only_mode_blocks_write_tool(self):
        svc = SafetyService({"permission_mode": "read-only"})
        d = svc.evaluate_tool("write_file", {"path": "x"})
        self.assertFalse(d.allowed)

    def test_workspace_write_allows_write_tool_with_approval(self):
        svc = SafetyService({"permission_mode": "workspace-write"})
        d = svc.evaluate_tool("write_file", {"path": "x"})
        self.assertTrue(d.allowed)
        self.assertTrue(d.requires_approval)

    def test_network_disabled_blocks_network_command(self):
        svc = SafetyService({"network_enabled": False})
        d = svc.evaluate_command("curl https://example.com")
        self.assertFalse(d.allowed)
        self.assertTrue(d.network)

    def test_read_tool_always_allowed(self):
        svc = SafetyService({"permission_mode": "read-only"})
        d = svc.evaluate_tool("read_file", {"path": "x"})
        self.assertTrue(d.allowed)
        self.assertFalse(d.requires_approval)

    def test_classify_risk_delegates(self):
        svc = SafetyService()
        self.assertEqual(svc.classify_risk("ls -la"), "low")

    def test_refresh_rebuilds_policy_state(self):
        svc = SafetyService({"permission_mode": "read-only"})
        self.assertFalse(svc.evaluate_tool("write_file", {}).allowed)
        svc.refresh({"permission_mode": "workspace-write"})
        self.assertTrue(svc.evaluate_tool("write_file", {}).allowed)


class PrivacyTests(unittest.TestCase):
    def test_privacy_defaults_are_off(self):
        p = SafetyService().privacy()
        self.assertFalse(p.data_sharing)
        self.assertFalse(p.feedback_upload)

    def test_privacy_reflects_config(self):
        p = SafetyService({"data_sharing": True, "feedback_upload": True}).privacy()
        self.assertTrue(p.data_sharing)
        self.assertTrue(p.feedback_upload)


class TradingRiskTests(unittest.TestCase):
    def test_default_trading_mode_is_read_only(self):
        svc = SafetyService({})
        self.assertEqual(svc.trading_mode(), "read_only")

    def test_paper_mode_resolved_from_config(self):
        svc = SafetyService({"mode": "paper"})
        self.assertEqual(svc.trading_mode(), "paper")

    def test_trading_policy_defaults_require_confirm_and_no_live(self):
        pol = SafetyService({}).trading_policy()
        self.assertTrue(pol.require_confirm)
        self.assertFalse(pol.allow_live_trade)
        self.assertEqual(pol.mode, "read_only")

    def test_trading_policy_risk_limits_from_config(self):
        pol = SafetyService({"max_single_position_weight": 0.35, "allow_short": True}).trading_policy()
        self.assertAlmostEqual(pol.max_single_position_weight, 0.35)
        self.assertTrue(pol.allow_short)

    def test_importing_safety_does_not_import_brokers(self):
        # Run in a subprocess: asserting on a *fresh* interpreter's sys.modules
        # is the only way to check import-time layering without mutating this
        # process's module state (deleting/reloading modules here orphans other
        # tests' collection-time references and corrupts their monkeypatching).
        import subprocess
        import sys
        code = (
            "import sys; import safety; "
            "bad = [m for m in sys.modules if m.startswith('brokers') or m.startswith('privacy')]; "
            "sys.exit(1 if bad else 0)"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True)
        self.assertEqual(proc.returncode, 0,
                         "importing safety must not import brokers/privacy")


if __name__ == "__main__":
    unittest.main()
