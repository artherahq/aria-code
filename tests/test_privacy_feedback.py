import json
import tempfile
import unittest
from pathlib import Path

from privacy import FeedbackRecord, FeedbackStore, PrivacySettings


class PrivacyFeedbackTests(unittest.TestCase):
    def test_privacy_settings_default_to_local_only(self):
        settings = PrivacySettings.from_config({})

        self.assertFalse(settings.data_sharing)
        self.assertFalse(settings.feedback_upload)

    def test_privacy_settings_apply_to_config(self):
        config = {}
        settings = PrivacySettings(data_sharing=True, feedback_upload=True)

        returned = settings.apply_to_config(config)

        self.assertIs(returned, config)
        self.assertTrue(config["data_sharing"])
        self.assertTrue(config["feedback_upload"])

    def test_feedback_store_append_count_export_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(Path(tmp))
            record = FeedbackRecord.create(
                rating="positive",
                message="answer",
                comment="useful",
                model="qwen",
                session_id="session-1",
                message_index=2,
            )

            feedback_path = store.append(record)

            self.assertEqual(feedback_path, Path(tmp) / "feedback" / "feedback.jsonl")
            self.assertEqual(store.count(), 1)

            rows = list(store.iter_records())
            self.assertEqual(rows[0]["rating"], "positive")
            self.assertEqual(rows[0]["message"], "answer")
            self.assertFalse(rows[0]["shared"])

            export_path = store.export_jsonl()
            self.assertTrue(export_path.exists())
            exported = [json.loads(line) for line in export_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(exported[0]["comment"], "useful")

            deleted = store.delete_all()
            self.assertEqual(deleted, 1)
            self.assertEqual(store.count(), 0)

    def test_feedback_store_export_empty_file_when_no_feedback(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FeedbackStore(Path(tmp))

            export_path = store.export_jsonl()

            self.assertTrue(export_path.exists())
            self.assertEqual(export_path.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
