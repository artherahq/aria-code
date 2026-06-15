import os
import tempfile
import unittest
from pathlib import Path

from workspace import WorkspaceFiles, WorkspaceSecurity


class WorkspaceFilesTests(unittest.TestCase):
    def test_security_blocks_system_paths(self):
        security = WorkspaceSecurity()
        self.assertFalse(security.is_safe_path("/etc/passwd"))
        self.assertFalse(security.is_safe_path("/dev/null"))

    def test_read_list_and_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "alpha.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
            (root / "beta.txt").write_text("alpha beta\n", encoding="utf-8")

            files = WorkspaceFiles(WorkspaceSecurity(cwd=root))
            read = files.read_file(str(root / "alpha.py"))
            self.assertIn("def alpha", read.content)
            self.assertEqual(read.lines, 2)

            listing = files.list_files(str(root), "*.py")
            self.assertEqual(listing["count"], 1)
            self.assertEqual(listing["items"][0]["name"], "alpha.py")

            search = files.search_code("return", str(root), "**/*.py")
            self.assertEqual(search["count"], 1)
            self.assertEqual(search["matches"][0]["file"], "alpha.py")

    def test_symlink_to_blocked_root_is_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "etc_link"
            try:
                os.symlink("/etc", link)
            except OSError:
                return
            files = WorkspaceFiles(WorkspaceSecurity(cwd=tmp))
            with self.assertRaises(PermissionError):
                files.read_file(str(link / "passwd"))


if __name__ == "__main__":
    unittest.main()
