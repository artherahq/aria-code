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


if __name__ == "__main__":
    unittest.main()
