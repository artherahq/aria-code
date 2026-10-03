"""Offline checks for the dependency-free Unix installer."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"


class NativeInstallerTest(unittest.TestCase):
    def run_installer(self, *, valid_checksum: bool) -> tuple[subprocess.CompletedProcess[str], Path]:
        root = Path(tempfile.mkdtemp(prefix="aria-installer-test-"))
        self.addCleanup(shutil.rmtree, root)
        fake_bin = root / "fake-bin"
        fake_bin.mkdir()
        release = root / "release"
        release.mkdir()
        binary = release / "aria-code-macos-arm64"
        binary.write_text("#!/bin/sh\n[ \"$1\" = --version ] && echo v0.55.0\n", encoding="utf-8")
        binary.chmod(0o755)
        digest = hashlib.sha256(binary.read_bytes()).hexdigest() if valid_checksum else "0" * 64
        (release / "SHA256SUMS").write_text(f"{digest}  {binary.name}\n", encoding="utf-8")
        (fake_bin / "uname").write_text(
            "#!/bin/sh\ncase \"$1\" in -s) echo Darwin ;; -m) echo arm64 ;; esac\n",
            encoding="utf-8",
        )
        (fake_bin / "curl").write_text(
            '#!/bin/sh\nwhile [ "$1" != "-o" ]; do shift; done\n'
            'out=$2; shift 2; url=$1\ncp "$ARIA_TEST_RELEASE/${url##*/}" "$out"\n',
            encoding="utf-8",
        )
        for command in ("uname", "curl"):
            (fake_bin / command).chmod(0o755)
        env = os.environ.copy()
        env.update(
            HOME=str(root),
            SHELL="/bin/zsh",
            PATH=f"{fake_bin}:{env['PATH']}",
            ARIA_TEST_RELEASE=str(release),
            ARIA_CODE_VERSION="v0.55.0",
        )
        result = subprocess.run(["/bin/sh", str(INSTALLER)], env=env, text=True, capture_output=True)
        return result, root

    def test_installs_verified_binary_and_updates_future_path(self) -> None:
        result, root = self.run_installer(valid_checksum=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((root / ".local/bin/aria-code").is_file())
        aria = root / ".local/bin/aria"
        self.assertTrue(aria.is_file())
        self.assertEqual(subprocess.run([str(aria), "code", "--version"], text=True, capture_output=True).stdout.strip(), "v0.55.0")
        self.assertIn('export PATH="$HOME/.local/bin:$PATH"', (root / ".zprofile").read_text())

    def test_rejects_checksum_mismatch_before_installing(self) -> None:
        result, root = self.run_installer(valid_checksum=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum mismatch", result.stderr)
        self.assertFalse((root / ".local/bin/aria-code").exists())
        self.assertFalse((root / ".local/bin/aria").exists())


if __name__ == "__main__":
    unittest.main()
