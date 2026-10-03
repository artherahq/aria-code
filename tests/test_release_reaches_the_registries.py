"""A tag must not be able to exist without the release behind it.

Four consecutive releases tagged a version and published nothing:

    v0.45.0, v0.46.0   the release workflow could not start (permissions)
    v0.47.0            the platform packages could not be assembled
    v0.48.0            npm rejected the platform packages

Every one of those was found by a person opening a workflow run and reading
it. That is not a control, and it is why four empty tags accumulated before
anyone noticed the first.

Two structural properties keep it from recurring, and both are asserted here
because both are easy to undo by accident:

1. A verification job runs after publishing and fails the release when the
   tagged version is not on the registries.

2. Publishing to PyPI does not depend on npm. v0.48.0's Python package was
   never published because an npm credential problem skipped the job that had
   nothing to do with npm.
"""

from __future__ import annotations

import pathlib
import unittest

import yaml

WORKFLOWS = pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows"
PUBLISH = WORKFLOWS / "publish.yml"
RELEASE = WORKFLOWS / "release-on-merge.yml"


def _jobs(path: pathlib.Path) -> dict:
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("jobs") or {}


def _needs(job: dict) -> list[str]:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


class ThePublishedVersionIsVerified(unittest.TestCase):
    def setUp(self) -> None:
        self.jobs = _jobs(PUBLISH)
        self.job = self.jobs.get("verify-published")
        self.assertIsNotNone(
            self.job,
            "publish.yml has no verify-published job; without it a tag can "
            "point at a version that was never published, which is how "
            "v0.45.0 through v0.48.0 happened",
        )

    def test_it_runs_after_both_publishers(self) -> None:
        self.assertEqual(sorted(_needs(self.job)), ["publish-npm", "publish-pypi"])

    def test_it_runs_even_when_a_publisher_failed(self) -> None:
        # The release where a publish job failed is precisely the one that
        # needs this to speak up, and `needs` alone would skip it there.
        self.assertIn("always()", str(self.job.get("if", "")))

    def test_it_checks_both_registries(self) -> None:
        script = "\n".join(str(s.get("run", "")) for s in self.job.get("steps") or [])
        with self.subTest(registry="pypi"):
            self.assertIn("pypi.org/pypi/aria-code", script)
        with self.subTest(registry="npm"):
            self.assertIn("@artheras/aria-code@", script)

    def test_a_missing_package_fails_the_release(self) -> None:
        script = "\n".join(str(s.get("run", "")) for s in self.job.get("steps") or [])
        self.assertIn("::error::", script)
        self.assertIn("exit 1", script,
                      "reporting a missing package without failing is the same "
                      "silence this job exists to break")

    def test_missing_platform_packages_fail_the_release(self) -> None:
        # npm silently skips unavailable optionalDependencies, so a published
        # dispatcher without its pinned binaries is not a working release.
        script = "\n".join(str(s.get("run", "")) for s in self.job.get("steps") or [])
        self.assertIn("manifest.optionalDependencies", script)
        self.assertIn('has no ${item#*:}, but $TAG was tagged', script)
        self.assertIn("MISSING=1", script)
        # That it waits out registry lag first, and still fails a real gap, is
        # run for real in tests/test_release_verification_waits.py.


# The job graph — that publishing no longer waits on the npm binaries, and
# that publish-npm still waits for its own platform packages — is asserted by
# tests/test_release_chain.py, which owns the chain's shape. Keeping it there
# means one owner per fact; two would rot.


class TheNpmPageDescribesTheCurrentProduct(unittest.TestCase):
    def test_the_two_manifests_agree(self) -> None:
        import ast
        import json
        import re

        root = WORKFLOWS.parents[1]
        npm = json.loads((root / "npm" / "package.json").read_text(encoding="utf-8"))
        project_section = (root / "pyproject.toml").read_text(encoding="utf-8").split("[project]", 1)[1].split("\n[", 1)[0]
        description = re.search(r'^description\s*=\s*(".*")\s*$', project_section, re.MULTILINE)
        self.assertIsNotNone(description)
        self.assertEqual(
            npm["description"], ast.literal_eval(description.group(1)),
            "the npm page's one-line pitch and PyPI's disagree; npm's said "
            "'AI-powered financial terminal' long after the project was "
            "repositioned, and it is the first line a visitor reads",
        )


if __name__ == "__main__":
    unittest.main()
