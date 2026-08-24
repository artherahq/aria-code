import importlib.util
import json
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "aria_code" / "packages" / "aria_services" / "provider_health.py"
SPEC = importlib.util.spec_from_file_location("aria_provider_health", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

ProviderHealthRegistry = MODULE.ProviderHealthRegistry
classify_provider_error = MODULE.classify_provider_error


class PublicServiceStatusTests(unittest.TestCase):
    def test_empty_registry_has_neutral_status(self):
        status = ProviderHealthRegistry().public_status("market_data")

        self.assertEqual(status.state, "unknown")
        self.assertEqual(status.label, "市场数据")
        self.assertFalse(status.can_retry)

    def test_degraded_status_hides_provider_and_error_details(self):
        registry = ProviderHealthRegistry()
        registry.mark_success("local market")
        registry.mark_issue(
            classify_provider_error(
                "Vendor Secret",
                RuntimeError("api key=private-token"),
            )
        )

        status = registry.public_status("market_data")
        serialized = json.dumps(status.to_dict(), ensure_ascii=False)

        self.assertEqual(status.state, "degraded")
        self.assertIn("自动切换", status.message)
        self.assertNotIn("Vendor Secret", serialized)
        self.assertNotIn("private-token", serialized)

    def test_auth_failure_suggests_connection_settings(self):
        registry = ProviderHealthRegistry()
        registry.mark_issue(classify_provider_error("Broker", RuntimeError("401 Unauthorized")))

        status = registry.public_status("ai")

        self.assertEqual(status.state, "unavailable")
        self.assertIn("连接设置", status.message)
        self.assertFalse(status.can_retry)


if __name__ == "__main__":
    unittest.main()
