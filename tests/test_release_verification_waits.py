"""The post-release check must wait out registry lag, and still catch a gap.

v0.58.0 was published completely — dispatcher, ten platform packages, PyPI —
and "Verify the release actually shipped" failed it anyway: npm answered "no
such version" for a dispatcher written moments before. A release that fails
when nothing is wrong teaches everyone to ignore the check that exists to
catch v0.45.0-style tags pointing at nothing.

This runs the step's real script under GitHub's shell flags, with npm, curl
and sleep replaced by stubs that report a package as absent for its first N
queries.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"


def _script() -> str:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for step in doc["jobs"]["verify-published"]["steps"]:
        if step.get("name") == "The tagged version must exist on PyPI and npm":
            return step["run"]
    raise AssertionError("verification step not found")


class VerificationWaitsForTheRegistries(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "npm").mkdir()
        (self.tmp / "npm" / "package.json").write_text(json.dumps({
            "optionalDependencies": {"@artheras/aria-code-linux-x64": "9.9.9",
                                     "@artheras/aria-code-mcp-linux-x64": "9.9.9"}}))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.state = self.tmp / "state"
        self.state.mkdir()
        # absent-for: how many queries each spec misses before it appears;
        # "never" means it never does.
        for name, body in {
            "npm": '#!/bin/bash\nspec="$2"; f="$STATE/$(echo "$spec" | tr "/@" "__")"\n'
                   'n=$(cat "$f.count" 2>/dev/null || echo 0); echo $((n+1)) > "$f.count"\n'
                   'want=$(cat "$f.absent" 2>/dev/null || echo 0)\n'
                   '[ "$want" = never ] && exit 1\n[ "$n" -ge "$want" ] && { echo 9.9.9; exit 0; }\nexit 1\n',
            "curl": '#!/bin/bash\nwant=$(cat "$STATE/pypi.absent" 2>/dev/null || echo 0)\n'
                    'n=$(cat "$STATE/pypi.count" 2>/dev/null || echo 0); echo $((n+1)) > "$STATE/pypi.count"\n'
                    '[ "$want" = never ] && exit 22\n[ "$n" -ge "$want" ] && exit 0\nexit 22\n',
            "sleep": '#!/bin/bash\necho slept >> "$STATE/sleeps"\n',
        }.items():
            path = self.bin / name
            path.write_text(body)
            path.chmod(0o755)

    def absent(self, spec: str, times) -> None:
        key = "pypi" if spec == "pypi" else spec.replace("/", "_").replace("@", "_")
        (self.state / f"{key}.absent").write_text(str(times))

    def run_step(self) -> subprocess.CompletedProcess[str]:
        node = shutil.which("node")
        env = {"PATH": f"{self.bin}:{os.path.dirname(node) if node else ''}:/usr/bin:/bin",
               "STATE": str(self.state), "INPUT_TAG": "v9.9.9", "GITHUB_REF_NAME": "v9.9.9",
               "NPM_RESULT": "success", "PYPI_RESULT": "success",
               "GITHUB_STEP_SUMMARY": str(self.tmp / "summary")}
        return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", _script()],
                              cwd=self.tmp, env=env, capture_output=True, text=True, timeout=60)

    def sleeps(self) -> int:
        f = self.state / "sleeps"
        return len(f.read_text().splitlines()) if f.exists() else 0

    def setUpNode(self) -> None:
        if not shutil.which("node"):
            self.skipTest("node is needed to read the dispatcher's pins")

    def test_everything_present_passes_without_waiting(self) -> None:
        self.setUpNode()
        proc = self.run_step()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.sleeps(), 0)

    def test_a_dispatcher_that_appears_late_passes(self) -> None:
        """The v0.58.0 case."""
        self.setUpNode()
        self.absent("@artheras/aria-code@9.9.9", 2)
        self.absent("pypi", 1)
        proc = self.run_step()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.sleeps(), 2)

    def test_a_package_that_never_appears_fails_and_is_named(self) -> None:
        self.setUpNode()
        self.absent("@artheras/aria-code-mcp-linux-x64@9.9.9", "never")
        proc = self.run_step()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no @artheras/aria-code-mcp-linux-x64@9.9.9", proc.stdout)
        self.assertNotIn("no @artheras/aria-code-linux-x64@9.9.9", proc.stdout)
        self.assertEqual(self.sleeps(), 10, "should give up after the retry budget")

    def test_present_packages_are_not_asked_again(self) -> None:
        self.setUpNode()
        self.absent("@artheras/aria-code-mcp-linux-x64@9.9.9", 3)
        self.assertEqual(self.run_step().returncode, 0)
        count = self.state / "_artheras_aria-code-linux-x64_9.9.9.count"
        self.assertEqual(count.read_text().strip(), "1")


if __name__ == "__main__":
    unittest.main()
