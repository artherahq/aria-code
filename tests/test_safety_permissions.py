import unittest

from safety import PermissionService, evaluate_command_policy, normalize_command


class SafetyPermissionTests(unittest.TestCase):
    def test_normalize_command_uses_shell_quoting(self):
        self.assertEqual(normalize_command("python script.py"), "python3 script.py")
        self.assertEqual(normalize_command("pip install rich"), "pip3 install rich")

    def test_read_only_blocks_medium_commands(self):
        decision = PermissionService(mode="read-only", command_policy="balanced").evaluate_command("pytest -q")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.risk, "medium")

    def test_network_can_be_disabled(self):
        decision = evaluate_command_policy("curl https://example.com", "full", network_enabled=False)
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.network)
        self.assertIn("Network command blocked", decision.reason)

    def test_tool_permissions(self):
        svc = PermissionService(mode="workspace-write")
        self.assertTrue(svc.evaluate_tool("read_file").allowed)
        write = svc.evaluate_tool("write_file")
        self.assertTrue(write.allowed)
        self.assertTrue(write.requires_approval)
        unknown = svc.evaluate_tool("unknown_tool")
        self.assertFalse(unknown.allowed)

    def test_full_policy_allows_high_risk_but_marks_risk(self):
        decision = evaluate_command_policy("git push origin main", "full")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.risk, "high")


if __name__ == "__main__":
    unittest.main()
