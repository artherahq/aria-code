"""Tests for aria_cli._pick_best_installed_model — the model-selection logic
shared by startup preflight and the runtime Ollama-fallback path. Pure,
deterministic, and previously untested despite being on a high-blast-radius
path (a bug here silently changes which model actually answers the user).
"""

import unittest

from aria_cli import _pick_best_installed_model


class PickBestInstalledModelTests(unittest.TestCase):
    def test_empty_installed_returns_none(self):
        self.assertIsNone(_pick_best_installed_model(set(), preferred="qwen2.5:7b"))
        self.assertIsNone(_pick_best_installed_model([], preferred=""))

    def test_exact_preferred_match_wins_even_if_lower_priority(self):
        # "llama3.2:3b" is far down the priority list, but an exact match on
        # `preferred` short-circuits the whole fallback-prefix search.
        installed = {"llama3.2:3b", "qwen3:8b"}
        self.assertEqual(
            _pick_best_installed_model(installed, preferred="llama3.2:3b"),
            "llama3.2:3b",
        )

    def test_preferred_not_installed_falls_back_to_priority_order(self):
        installed = {"llama3.1:8b", "qwen2.5:7b"}
        # qwen2.5:7b outranks llama3.1:8b in _MODEL_FALLBACK_PREFIXES
        self.assertEqual(
            _pick_best_installed_model(installed, preferred="qwen3:30b-a3b"),
            "qwen2.5:7b",
        )

    def test_no_preferred_uses_priority_order(self):
        installed = {"mistral:7b", "qwen3:8b", "phi4:14b"}
        self.assertEqual(_pick_best_installed_model(installed, preferred=""), "qwen3:8b")

    def test_prefix_match_uses_startswith_not_exact(self):
        # "gpt-oss" is a bare prefix in the list; any tagged variant matches.
        installed = {"gpt-oss:20b"}
        self.assertEqual(_pick_best_installed_model(installed), "gpt-oss:20b")

    def test_multiple_matches_for_same_prefix_pick_alphabetically_first(self):
        # Both start with "gpt-oss"; alphabetical order picks 120b over 20b
        # ('1' < '2') — documenting actual behavior, not a "smarter" choice.
        installed = {"gpt-oss:20b", "gpt-oss:120b-cloud"}
        self.assertEqual(_pick_best_installed_model(installed), "gpt-oss:120b-cloud")

    def test_no_prefix_matches_falls_back_to_alphabetically_first(self):
        installed = {"zzz-custom-model", "aaa-custom-model"}
        self.assertEqual(_pick_best_installed_model(installed), "aaa-custom-model")

    def test_preferred_empty_string_is_treated_as_not_preferred(self):
        installed = {"qwen2.5:7b"}
        self.assertEqual(_pick_best_installed_model(installed, preferred=""), "qwen2.5:7b")

    def test_accepts_set_or_list_input(self):
        self.assertEqual(_pick_best_installed_model(["qwen2.5:7b"]), "qwen2.5:7b")
        self.assertEqual(_pick_best_installed_model({"qwen2.5:7b"}), "qwen2.5:7b")


if __name__ == "__main__":
    unittest.main()
