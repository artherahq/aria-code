"""Regression tests for reaching Google Cloud models at all.

The shipped default config pairs ``model="gemini-pro"`` with
``local_provider="ollama"``.  Both routing layers derived the backend from
``local_provider`` alone, so the flagship default model was never actually
called — every request went to Ollama instead.  The status line reading
"ollama" next to a Gemini model name was reporting that honestly.

Covered here:
  - a provider-qualified model id selects its own backend;
  - namespaced Ollama models ("hf.co/someone/qwen") are not mistaken for one;
  - the bare model name is what reaches the API, not the qualified id;
  - Vertex AI vs Gemini API-key credentials are auto-detected;
  - Google is registered as a provider and appears in the fallback chain.

Hermetic — no network, no model calls, no credentials required.
"""

import os
import unittest

from aria_code.apps.cli.providers.base import ConfiguredProvider
from aria_code.apps.cli.providers.chat_routing import first_round_route, model_provider

DEFAULT_CONFIG = {"local_provider": "ollama"}


class ModelProviderTests(unittest.TestCase):
    def test_qualified_cloud_ids_name_their_provider(self):
        self.assertEqual(model_provider("google/gemini-2.5-pro"), "google")
        self.assertEqual(model_provider("openai/gpt-4o"), "openai")

    def test_gemini_alias_normalises_to_google(self):
        self.assertEqual(model_provider("gemini/gemini-2.5-pro"), "google")

    def test_bare_ollama_names_name_no_provider(self):
        self.assertEqual(model_provider("qwen2.5:7b"), "")
        self.assertEqual(model_provider("gpt-oss:120b-cloud"), "")

    def test_namespaced_ollama_model_is_not_a_provider(self):
        # A slash is not proof of a provider; this is a host-namespaced model
        # served by Ollama and must keep routing locally.
        self.assertEqual(model_provider("hf.co/someone/qwen"), "")


class FirstRoundRouteTests(unittest.TestCase):
    def test_gemini_is_not_routed_to_ollama(self):
        # The exact default-config pairing that silently sent Gemini to Ollama.
        self.assertEqual(
            first_round_route("google/gemini-2.5-pro", DEFAULT_CONFIG, "https://x"),
            "configured",
        )

    def test_local_provider_still_governs_bare_names(self):
        self.assertEqual(first_round_route("qwen2.5:7b", DEFAULT_CONFIG, "https://x"), "ollama")
        self.assertEqual(
            first_round_route("hf.co/someone/qwen", DEFAULT_CONFIG, "https://x"), "ollama"
        )

    def test_existing_routing_contracts_hold(self):
        self.assertEqual(first_round_route("openai/gpt-4", {}, "https://x"), "configured")
        self.assertEqual(first_round_route("gpt-oss:120b-cloud", {}, "https://x"), "ollama")
        self.assertEqual(
            first_round_route("loaded", {"local_provider": "lmstudio"}, "https://x"),
            "configured",
        )

    def test_forced_backend_still_wins(self):
        self.assertEqual(
            first_round_route("google/gemini-2.5-pro", {"backend_chat": True}, "https://x"),
            "cloud",
        )


class ConfiguredProviderTests(unittest.TestCase):
    def test_backend_comes_from_the_model_id(self):
        provider = ConfiguredProvider(DEFAULT_CONFIG, "google/gemini-2.5-pro")
        self.assertEqual(provider.backend, "google")

    def test_vendor_prefix_is_stripped_before_the_api_call(self):
        # "google/gemini-2.5-pro" would 404 against Google's endpoint, and the
        # registry path re-prefixes the name into "google/google/...".
        provider = ConfiguredProvider(DEFAULT_CONFIG, "google/gemini-2.5-pro")
        self.assertEqual(provider.model, "gemini-2.5-pro")

    def test_bare_model_names_are_untouched(self):
        provider = ConfiguredProvider(DEFAULT_CONFIG, "qwen2.5:7b")
        self.assertEqual(provider.backend, "ollama")
        self.assertEqual(provider.model, "qwen2.5:7b")

    def test_namespaced_ollama_model_keeps_its_full_name(self):
        provider = ConfiguredProvider(DEFAULT_CONFIG, "hf.co/someone/qwen")
        self.assertEqual(provider.backend, "ollama")
        self.assertEqual(provider.model, "hf.co/someone/qwen")


class VertexCredentialDetectionTests(unittest.TestCase):
    """use_vertexai defaulted to True, failing for API-key-only developers."""

    ENV_KEYS = (
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_GENAI_USE_VERTEXAI",
    )

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self.ENV_KEYS}
        for key in self.ENV_KEYS:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _use_vertex(self, config=None):
        from aria_code.apps.cli.providers.vertexai_stream import VertexAIProvider

        return VertexAIProvider("gemini-2.5-pro", config or {})._use_vertex()

    def test_api_key_only_uses_the_api_endpoint(self):
        os.environ["GEMINI_API_KEY"] = "test-key"
        self.assertFalse(self._use_vertex())

    def test_project_selects_vertex(self):
        os.environ["GOOGLE_CLOUD_PROJECT"] = "test-project"
        self.assertTrue(self._use_vertex())

    def test_project_wins_when_both_are_present(self):
        os.environ["GOOGLE_CLOUD_PROJECT"] = "test-project"
        os.environ["GEMINI_API_KEY"] = "test-key"
        self.assertTrue(self._use_vertex())

    def test_explicit_config_overrides_detection(self):
        os.environ["GOOGLE_CLOUD_PROJECT"] = "test-project"
        self.assertFalse(self._use_vertex({"use_vertexai": False}))

    def test_explicit_env_flag_overrides_detection(self):
        os.environ["GOOGLE_CLOUD_PROJECT"] = "test-project"
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "0"
        self.assertFalse(self._use_vertex())

    def test_api_key_is_read_from_env(self):
        from aria_code.apps.cli.providers.vertexai_stream import VertexAIProvider

        os.environ["GEMINI_API_KEY"] = "from-env"
        self.assertEqual(VertexAIProvider("gemini-2.5-pro", {})._api_key(), "from-env")

    def test_config_api_key_wins_over_env(self):
        from aria_code.apps.cli.providers.vertexai_stream import VertexAIProvider

        os.environ["GEMINI_API_KEY"] = "from-env"
        provider = VertexAIProvider("gemini-2.5-pro", {"api_key": "from-config"})
        self.assertEqual(provider._api_key(), "from-config")


class ProviderRegistryTests(unittest.TestCase):
    def test_google_is_a_registered_provider(self):
        from aria_code.providers.llm.registry import _PROVIDER_CLASSES

        self.assertIn("google", _PROVIDER_CLASSES)
        self.assertIn("gemini", _PROVIDER_CLASSES)

    def test_google_participates_in_the_fallback_chain(self):
        from aria_code.providers.llm.registry import _DEFAULT_FALLBACK_CHAIN

        self.assertIn("google", [name for name, _, _ in _DEFAULT_FALLBACK_CHAIN])

    def test_google_provider_reads_the_standard_env_keys(self):
        from aria_code.providers.llm.base import ProviderConfig
        from aria_code.providers.llm.openai_compat import GoogleProvider

        saved = os.environ.get("GEMINI_API_KEY")
        os.environ["GEMINI_API_KEY"] = "test-key"
        try:
            provider = GoogleProvider(
                ProviderConfig(name="google", model="gemini-2.5-flash")
            )
            self.assertEqual(provider.config.api_key, "test-key")
            self.assertIn("googleapis.com", provider.base_url)
        finally:
            if saved is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = saved


if __name__ == "__main__":
    unittest.main()
