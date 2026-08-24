"""Tests for packages.aria_services.settings.SettingsService — the concrete
implementation behind the registry's `settings` ServiceSpec. Mechanics were
extracted verbatim from apps/cli/config_store.py; these tests pin the merge,
normalization, persistence, and hook contracts so CLI behavior can't drift."""

import json
import unittest
import tempfile
from pathlib import Path

from aria_code.packages.aria_services.settings import NEVER_PERSIST, SettingsService


def _svc(tmp, defaults=None, **kw):
    root = Path(tmp)
    return SettingsService(
        config_dir=root,
        config_file=root / "config.json",
        sessions_dir=root / "sessions",
        defaults=defaults or {"model": "qwen2.5:7b", "ui_lang": "en"},
        **kw,
    )


class SettingsServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_first_load_uses_defaults_and_creates_dirs(self):
        svc = _svc(self.tmp)
        cfg = svc.load()
        self.assertEqual(cfg["model"], "qwen2.5:7b")
        self.assertTrue((Path(self.tmp) / "sessions").is_dir())

    def test_roundtrip_save_then_load(self):
        svc = _svc(self.tmp)
        svc.save({"model": "m1", "ui_lang": "zh", "local_provider": "ollama"})
        cfg = _svc(self.tmp).load()
        self.assertEqual(cfg["model"], "m1")
        self.assertEqual(cfg["ui_lang"], "zh")

    def test_saved_keys_merge_over_defaults(self):
        svc = _svc(self.tmp, defaults={"model": "d", "extra": 1, "ui_lang": "en"})
        svc.save({"model": "saved"})
        cfg = _svc(self.tmp, defaults={"model": "d", "extra": 1, "ui_lang": "en"}).load()
        self.assertEqual(cfg["model"], "saved")
        self.assertEqual(cfg["extra"], 1)  # default preserved

    def test_stale_model_prefix_resets_to_default(self):
        svc = _svc(self.tmp)
        svc.save({"model": "aria-opus:1", "local_provider": "ollama", "ui_lang": "en"})
        cfg = _svc(self.tmp).load()
        self.assertEqual(cfg["model"], "qwen2.5:7b")

    def test_local_provider_inferred_from_slash_model(self):
        svc = _svc(self.tmp)
        svc.save({"model": "openai/gpt-4.5", "ui_lang": "en"})
        cfg = _svc(self.tmp).load()
        self.assertEqual(cfg["local_provider"], "openai")

    def test_normalize_provider_hook_applied(self):
        svc = _svc(self.tmp)
        svc.save({"model": "m", "local_provider": "LM-Studio", "ui_lang": "en"})
        cfg = _svc(self.tmp, normalize_provider=lambda p: p.lower().replace("-", "")).load()
        self.assertEqual(cfg["local_provider"], "lmstudio")

    def test_conversation_history_never_persisted(self):
        svc = _svc(self.tmp)
        svc.save({"model": "m", "conversation_history": [{"role": "user"}], "ui_lang": "en"})
        raw = json.loads((Path(self.tmp) / "config.json").read_text())
        self.assertNotIn("conversation_history", raw)
        self.assertIn("conversation_history", NEVER_PERSIST)

    def test_on_loaded_hook_fires_with_merged_config(self):
        seen = []
        svc = _svc(self.tmp, on_loaded=seen.append)
        svc.load()
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["model"], "qwen2.5:7b")

    def test_get_set_snapshot(self):
        svc = _svc(self.tmp)
        self.assertEqual(svc.get("model"), "qwen2.5:7b")
        svc.set("theme", "dark")
        self.assertEqual(svc.get("theme"), "dark")
        # persisted immediately
        self.assertEqual(_svc(self.tmp).load().get("theme"), "dark")

    def test_corrupt_config_file_falls_back_to_defaults(self):
        (Path(self.tmp)).mkdir(exist_ok=True)
        (Path(self.tmp) / "config.json").write_text("{not json")
        cfg = _svc(self.tmp).load()
        self.assertEqual(cfg["model"], "qwen2.5:7b")

    def test_detect_lang_hook_used_when_lang_missing(self):
        svc = _svc(self.tmp, defaults={"model": "m", "ui_lang": ""})
        svc.save({"model": "m", "ui_lang": ""})
        cfg = _svc(self.tmp, defaults={"model": "m", "ui_lang": ""},
                   detect_lang=lambda: "zh").load()
        self.assertEqual(cfg["ui_lang"], "zh")


if __name__ == "__main__":
    unittest.main()
