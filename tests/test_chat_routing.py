import unittest

from apps.cli.providers.chat_routing import (
    first_round_route,
    force_backend,
    is_cloud_model,
    is_ollama_model,
    is_placeholder_response,
    normalize_provider_name,
    should_fallback,
)


class ChatRoutingTests(unittest.TestCase):
    def test_provider_aliases(self):
        self.assertEqual(normalize_provider_name("LM-Studio"), "lmstudio")
        self.assertEqual(normalize_provider_name("llama.cpp"), "llamacpp")
        self.assertEqual(normalize_provider_name("claude"), "anthropic")

    def test_model_kind(self):
        self.assertTrue(is_cloud_model("openai/gpt-4.5"))
        self.assertFalse(is_cloud_model("gpt-oss:120b-cloud"))
        self.assertTrue(is_ollama_model("deepseek-r1:14b"))
        self.assertFalse(is_ollama_model("anthropic/claude"))

    def test_force_backend(self):
        self.assertTrue(force_backend({"backend_chat": True}, "https://x"))
        self.assertFalse(force_backend({"backend_chat": True}, None))   # no api_url
        self.assertFalse(force_backend({"backend_chat": False}, "https://x"))

    def test_first_round_route(self):
        self.assertEqual(first_round_route("any", {"local_provider": "ollama"}, "https://x"), "ollama")
        self.assertEqual(first_round_route("openai/gpt-4", {}, "https://x"), "configured")
        self.assertEqual(first_round_route("gpt-oss:120b-cloud", {}, "https://x"), "ollama")
        self.assertEqual(
            first_round_route("loaded", {"local_provider": "lmstudio"}, "https://x"),
            "configured",
        )
        # ollama-named but backend_chat forces it → cloud
        self.assertEqual(
            first_round_route("gpt-oss:120b-cloud", {"backend_chat": True}, "https://x"), "cloud")

    def test_is_placeholder_response(self):
        self.assertTrue(is_placeholder_response("", 10))
        self.assertTrue(is_placeholder_response("short", 10))
        self.assertTrue(is_placeholder_response("x" * 100, token_count=1))  # canned, ~no tokens
        self.assertTrue(is_placeholder_response("stub help text here ok", 50,
                                                stub_detector=lambda r: True))
        self.assertFalse(is_placeholder_response("A real, sufficiently long answer.", 50))

    def test_should_fallback(self):
        ok = {"success": True, "cancelled": False}
        fail = {"success": False, "cancelled": False}
        cancelled = {"success": False, "cancelled": True}
        self.assertTrue(should_fallback("skip", ok, is_placeholder=False))         # skip always falls back
        self.assertTrue(should_fallback("cloud", fail, is_placeholder=False))      # failure
        self.assertTrue(should_fallback("cloud", ok, is_placeholder=True))         # placeholder
        self.assertFalse(should_fallback("cloud", cancelled, is_placeholder=False))  # user cancelled, don't re-run
        # the bug-free case: forced-backend round that genuinely succeeded must NOT fall back
        self.assertFalse(should_fallback("cloud", ok, is_placeholder=False))


if __name__ == "__main__":
    unittest.main()
