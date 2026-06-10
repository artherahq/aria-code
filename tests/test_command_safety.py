import unittest

from command_safety import evaluate_command_policy, normalize_command
from plan_utils import parse_plan_steps


class CommandSafetyTests(unittest.TestCase):
    def test_normalize_python_and_pip_aliases(self):
        self.assertEqual(normalize_command("python script.py"), "python3 script.py")
        self.assertEqual(normalize_command("pip install rich"), "pip3 install rich")

    def test_safe_policy_blocks_non_low_risk(self):
        decision = evaluate_command_policy("pytest -q", "safe")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.risk, "medium")

    def test_balanced_policy_allows_medium_risk(self):
        decision = evaluate_command_policy("pytest -q", "balanced")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.risk, "medium")

    def test_balanced_policy_blocks_high_risk(self):
        decision = evaluate_command_policy("git push origin main", "balanced")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.risk, "high")

    def test_plan_parser_supports_arrow_and_semicolon(self):
        steps = parse_plan_steps("git status -> rg TODO . ; pytest -q")
        self.assertEqual(steps, ["git status", "rg TODO .", "pytest -q"])


if __name__ == "__main__":
    unittest.main()
