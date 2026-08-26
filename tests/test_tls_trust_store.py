"""TLS must verify against the OS trust store, not only certifi.

A TLS-intercepting proxy presents its own CA. That CA lives in the system
keychain — which is why curl works on such a machine — while Python verifies
against certifi and fails every HTTPS call. Measured on a real machine:
oauth2.googleapis.com failed 5/5 from Python and returned 404 from curl, and
roughly a third of Vertex turns died fetching an OAuth token.
"""

import inspect
import ssl
import unittest

from aria_code.apps.cli.bootstrap import initialize_cli_environment, use_system_trust_store


class TrustStoreTests(unittest.TestCase):
    def test_injection_reports_whether_it_happened(self):
        self.assertIsInstance(use_system_trust_store(), bool)

    def test_it_is_applied_at_startup(self):
        source = inspect.getsource(initialize_cli_environment)
        self.assertIn("use_system_trust_store()", source)

    def test_a_default_context_still_verifies(self):
        # The tempting wrong fix is to disable verification, which accepts any
        # certificate at all. Whatever the trust source, verification stays on.
        use_system_trust_store()
        context = ssl.create_default_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_a_missing_truststore_degrades_rather_than_raising(self):
        import builtins

        real_import = builtins.__import__

        def refuse(name, *args, **kwargs):
            if name == "truststore":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = refuse
        try:
            self.assertFalse(use_system_trust_store())
        finally:
            builtins.__import__ = real_import

    def test_calling_it_twice_is_safe(self):
        use_system_trust_store()
        use_system_trust_store()
        self.assertTrue(ssl.create_default_context().check_hostname)


if __name__ == "__main__":
    unittest.main()
