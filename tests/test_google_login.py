"""Coverage for the CLI's RFC 8252 native-app Google sign-in flow.

The end-to-end path needs a real browser and network access, so these tests
cover the parts that do not: the loopback callback server, the handoff-code
validation, and the Google -> Firebase token exchange (with urlopen stubbed).
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from unittest import mock

from aria_code.apps.cli.google_login import (
    _CallbackResult,
    _free_port,
    _make_handler,
    exchange_google_token_for_firebase,
    run_google_login,
)


class FreePortTests(unittest.TestCase):
    def test_returns_a_bindable_loopback_port(self):
        port = _free_port()
        self.assertGreater(port, 0)
        # The port must be free again immediately after — a second caller
        # asking for one (as run_google_login does before starting its own
        # server) must not collide with a socket still held open.
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", port))


class CallbackHandlerTests(unittest.TestCase):
    """Drives the loopback handler with a real HTTP request."""

    def _serve_one(self, path_and_query: str, expected_path: str = "/callback"):
        import http.server

        result = _CallbackResult()
        server = http.server.HTTPServer(
            ("127.0.0.1", 0), _make_handler(result, expected_path)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            response = urllib.request.urlopen(
                f"http://127.0.0.1:{port}{path_and_query}", timeout=5
            )
            status = response.status
        finally:
            server.shutdown()
            server.server_close()
        return status, result

    def test_handoff_code_is_captured_and_acknowledged(self):
        status, result = self._serve_one("/callback?handoff_code=abc123")
        self.assertEqual(status, 200)
        self.assertTrue(result.received.is_set())
        self.assertEqual(result.params["handoff_code"], "abc123")

    def test_error_message_is_captured_with_a_400(self):
        import urllib.error

        try:
            self._serve_one("/callback?message=access_denied")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
        else:
            self.fail("expected the handler to answer 400 for a failed callback")

    def test_wrong_path_answers_404_and_does_not_signal(self):
        import urllib.error

        result = _CallbackResult()
        import http.server

        server = http.server.HTTPServer(("127.0.0.1", 0), _make_handler(result, "/callback"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/other", timeout=5)
                self.fail("expected 404")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 404)
            self.assertFalse(result.received.is_set())
        finally:
            server.shutdown()
            server.server_close()


class TokenExchangeTests(unittest.TestCase):
    def test_posts_the_google_id_token_and_returns_the_firebase_session(self):
        captured = {}

        class FakeResponse:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def read(self_inner):
                return json.dumps({"idToken": "fb-token", "email": "a@example.com"}).encode()

        def fake_urlopen(request, timeout=20):
            captured["url"] = request.full_url
            captured["body"] = request.data
            return FakeResponse()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = exchange_google_token_for_firebase("google-id-token")

        self.assertEqual(result["idToken"], "fb-token")
        self.assertIn("identitytoolkit.googleapis.com", captured["url"])
        self.assertIn(b"id_token=google-id-token", captured["body"])
        self.assertIn(b"providerId=google.com", captured["body"])


class RunGoogleLoginTests(unittest.TestCase):
    def test_raises_when_the_browser_cannot_be_opened(self):
        with mock.patch(
            "aria_code.apps.cli.google_login.webbrowser.open", return_value=False
        ):
            with self.assertRaises(RuntimeError) as ctx:
                run_google_login("http://api.example.com")
        self.assertIn("Could not open a browser", str(ctx.exception))

    def test_full_flow_returns_the_stored_credential_fields(self):
        # The browser step is stubbed to hit the loopback callback directly,
        # standing in for what a real browser redirect would do.
        def fake_open(url):
            import re

            port = int(re.search(r"port=(\d+)", url).group(1))
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/callback?handoff_code=hc123", timeout=5
            )
            return True

        def fake_post_json(url, payload, timeout=20):
            if "handoff/exchange" in url:
                self.assertEqual(payload, {"code": "hc123"})
                return {"data": {"token": "google-id-token", "uid": "u1"}}
            if "signInWithIdp" in url:
                return {
                    "idToken": "firebase-id-token",
                    "refreshToken": "refresh-1",
                    "expiresIn": "3600",
                    "localId": "u1",
                    "email": "person@example.com",
                    "displayName": "Person",
                }
            raise AssertionError(f"unexpected URL: {url}")

        with mock.patch(
            "aria_code.apps.cli.google_login.webbrowser.open", side_effect=fake_open
        ), mock.patch(
            "aria_code.apps.cli.google_login._post_json", side_effect=fake_post_json
        ):
            session = run_google_login("http://api.example.com")

        self.assertEqual(session["auth_token"], "firebase-id-token")
        self.assertEqual(session["refresh_token"], "refresh-1")
        self.assertEqual(session["user_id"], "u1")
        self.assertEqual(session["email"], "person@example.com")
        self.assertEqual(session["provider"], "google.com")

    def test_callback_error_message_is_raised(self):
        def fake_open(url):
            import re
            import urllib.error

            port = int(re.search(r"port=(\d+)", url).group(1))
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/callback?message=access_denied", timeout=5
                )
            except urllib.error.HTTPError:
                pass  # the handler answers 400 for a failed callback; a real
                # browser does not raise on that, it just renders the page.
            return True

        with mock.patch(
            "aria_code.apps.cli.google_login.webbrowser.open", side_effect=fake_open
        ):
            with self.assertRaises(RuntimeError) as ctx:
                run_google_login("http://api.example.com")
        self.assertIn("access_denied", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
