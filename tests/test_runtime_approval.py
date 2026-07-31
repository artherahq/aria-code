import unittest

from runtime import ApprovalDecision, apply_approval_decision


class RuntimeApprovalTests(unittest.TestCase):
    def test_apply_approval_decision_injects_command_fields(self):
        params = {"command": "pytest"}
        decision = ApprovalDecision.allow(policy="balanced", user_approved=True)

        returned = apply_approval_decision(params, decision)

        self.assertIs(returned, params)
        self.assertEqual(params["policy"], "balanced")
        self.assertTrue(params["user_approved"])
        self.assertNotIn("_upgrade_policy", params)

    def test_apply_approval_decision_marks_policy_upgrade(self):
        params = {"command": "npm test"}
        decision = ApprovalDecision.allow(
            policy="balanced",
            user_approved=True,
            upgrade_policy=True,
        )

        apply_approval_decision(params, decision)

        self.assertEqual(params["policy"], "balanced")
        self.assertTrue(params["user_approved"])
        self.assertTrue(params["_upgrade_policy"])

    def test_deny_decision_has_no_execution_side_effects(self):
        params = {"path": "x.py"}
        decision = ApprovalDecision.deny("user denied")

        apply_approval_decision(params, decision)

        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "user denied")
        self.assertEqual(params, {"path": "x.py"})

    def test_scoped_approval_metadata_is_typed_and_immutable(self):
        decision = ApprovalDecision.allow(
            policy="balanced",
            user_approved=True,
            tool_scope="write_file",
            command_prefix=("python3", "/tmp/report.py"),
        )

        self.assertEqual(decision.tool_scope, "write_file")
        self.assertEqual(decision.command_prefix, ("python3", "/tmp/report.py"))

    def test_cli_command_prefix_scope_matches_similar_commands_only(self):
        import aria_cli

        aria_cli._session_command_prefixes.clear()
        prefix = aria_cli._command_approval_prefix("python3 '/tmp/report.py' --format md")
        self.assertEqual(prefix, ("python3", "/tmp/report.py"))

        aria_cli._apply_tool_approval(
            {},
            ApprovalDecision.allow(
                policy="balanced",
                user_approved=True,
                command_prefix=prefix,
            ),
        )

        self.assertTrue(aria_cli._command_matches_session_prefix("python3 /tmp/report.py --format html"))
        self.assertFalse(aria_cli._command_matches_session_prefix("python3 /tmp/other.py"))
        aria_cli._session_command_prefixes.clear()


if __name__ == "__main__":
    unittest.main()
