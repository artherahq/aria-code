"""Tests for the channels layer first slice: the channel registry and the
TradingView alert → structured task adapter (aria.channel_task.v1). Pins the
authentication posture (fail-closed on wrong passphrase/HMAC, explicit
open_mode flag when no secret is configured) and the task shape the daemon
will hand to the gateway."""

import unittest
from unittest import mock

from apps.channels import CHANNEL_TASK_SCHEMA, channel_map, default_channels, enabled_channels
from apps.channels.tradingview import alert_to_task


class RegistryTests(unittest.TestCase):
    def test_default_channels_have_unique_names_and_directions(self):
        specs = default_channels()
        names = [s.name for s in specs]
        self.assertEqual(len(names), len(set(names)))
        for s in specs:
            self.assertIn(s.direction, ("inbound", "outbound", "both"))

    def test_tradingview_registered_as_inbound(self):
        tv = channel_map()["tradingview"]
        self.assertEqual(tv.direction, "inbound")
        self.assertIn("alerts.ingest", tv.capabilities)

    def test_enabled_via_config_key(self):
        enabled = enabled_channels({"telegram_bot_token": "x"}, env={})
        self.assertIn("telegram", [s.name for s in enabled])

    def test_enabled_via_env_key(self):
        enabled = enabled_channels({}, env={"ARIA_WEBHOOK_SECRET": "s3cret"})
        self.assertIn("tradingview", [s.name for s in enabled])

    def test_disabled_when_nothing_configured(self):
        self.assertEqual(enabled_channels({}, env={}), [])


class TradingViewTaskTests(unittest.TestCase):
    def test_open_mode_task_when_no_secret_configured(self):
        with mock.patch.dict("os.environ", {"ARIA_WEBHOOK_SECRET": ""}, clear=False):
            r = alert_to_task({"symbol": "NVDA", "action": "buy", "price": 190.5}, clock=lambda: 42.0)
        self.assertTrue(r["success"])
        task = r["task"]
        self.assertEqual(task["schema"], CHANNEL_TASK_SCHEMA)
        self.assertEqual(task["symbol"], "NVDA")
        self.assertEqual(task["action"], "BUY")
        self.assertEqual(task["received_at"], 42.0)
        self.assertFalse(task["verified"])
        self.assertTrue(task["open_mode"])
        self.assertIn("NVDA", task["prompt"])
        self.assertIn("Do not place any orders", task["prompt"])

    def test_wrong_passphrase_fails_closed(self):
        with mock.patch.dict("os.environ", {"ARIA_WEBHOOK_SECRET": "right"}, clear=False):
            r = alert_to_task({"symbol": "NVDA", "action": "sell", "passphrase": "wrong"})
        self.assertFalse(r["success"])
        self.assertIn("passphrase", r["error"])

    def test_correct_passphrase_marks_verified(self):
        with mock.patch.dict("os.environ", {"ARIA_WEBHOOK_SECRET": "right"}, clear=False):
            r = alert_to_task({"symbol": "AAPL", "action": "exit", "passphrase": "right"})
        self.assertTrue(r["success"])
        self.assertTrue(r["task"]["verified"])
        self.assertFalse(r["task"]["open_mode"])
        self.assertEqual(r["task"]["action"], "EXIT")

    def test_hmac_signature_path_never_open(self):
        body = b'{"symbol": "TSLA", "action": "buy"}'
        import hashlib, hmac as hmac_mod
        with mock.patch.dict("os.environ", {"ARIA_WEBHOOK_SECRET": "k"}, clear=False):
            good = hmac_mod.new(b"k", body, hashlib.sha256).hexdigest()
            ok = alert_to_task({"symbol": "TSLA", "action": "buy"}, raw_body=body, signature=f"sha256={good}")
            bad = alert_to_task({"symbol": "TSLA", "action": "buy"}, raw_body=body, signature="sha256=deadbeef")
        self.assertTrue(ok["success"])
        self.assertTrue(ok["task"]["verified"])
        self.assertFalse(bad["success"])
        self.assertIn("HMAC", bad["error"])

    def test_missing_symbol_rejected(self):
        with mock.patch.dict("os.environ", {"ARIA_WEBHOOK_SECRET": ""}, clear=False):
            r = alert_to_task({"action": "buy"})
        self.assertFalse(r["success"])
        self.assertIn("no symbol", r["error"])

    def test_dedup_key_stable_for_identical_alerts(self):
        with mock.patch.dict("os.environ", {"ARIA_WEBHOOK_SECRET": ""}, clear=False):
            a = alert_to_task({"symbol": "NVDA", "action": "buy", "price": 1, "time": "t1"})
            b = alert_to_task({"symbol": "NVDA", "action": "buy", "price": 1, "time": "t1"})
            c = alert_to_task({"symbol": "NVDA", "action": "buy", "price": 2, "time": "t1"})
        self.assertEqual(a["task"]["dedup_key"], b["task"]["dedup_key"])
        self.assertNotEqual(a["task"]["dedup_key"], c["task"]["dedup_key"])

    def test_compact_text_payload_parses(self):
        with mock.patch.dict("os.environ", {"ARIA_WEBHOOK_SECRET": ""}, clear=False):
            r = alert_to_task("NVDA buy")
        self.assertTrue(r["success"])
        self.assertEqual(r["task"]["action"], "BUY")


if __name__ == "__main__":
    unittest.main()
