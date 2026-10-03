"""A fresh install must work the first time someone uses it.

Found by installing v0.56.0 from PyPI into an empty virtualenv, the way a new
user would, and running it:

  - The first prompt failed. DEFAULT_MODEL is google/gemini-2.5-pro, but its
    client, google-genai, was only the optional `google` extra. The comment
    above that extra said "a default install that cannot import this has no
    working model at all" — and it stayed optional.
  - `aria-code doctor` ended in "1 errors" on every clean install, for
    akshare, which pyproject has always declared as the optional `cn` extra.
  - `--help` introduced the tool as "Quantitative Investment Terminal" while
    PyPI and npm described something else.
  - Every install path in the README pointed at the `artherahq` GitHub
    organisation, which had been renamed to `artheras`. GitHub redirects the
    old name — but only until someone registers it, and it is unclaimed. From
    then on `curl … | sh` would run their installer, and the SHA-256 check
    would not help, because the checksum file would come from their release
    too.

Each of these is cheap to assert and was invisible to the suite before.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys
import unittest

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "aria_code"
for _p in (str(ROOT / "src"), str(SRC), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _dist(requirement: str) -> str:
    """'google-genai>=1.0.0; python_version>="3.10"' -> 'google-genai'."""
    return re.split(r"[<>=!~;\[ ]", requirement.strip(), maxsplit=1)[0].lower().replace("_", "-")


CORE = {_dist(r) for r in PYPROJECT["dependencies"]}
EXTRAS = {name: {_dist(r) for r in reqs} for name, reqs in PYPROJECT["optional-dependencies"].items()}

# The client each provider prefix needs. Only providers that can be the
# shipped default need to be here; the test below reads which one it is.
PROVIDER_CLIENT = {"google": "google-genai"}

# doctor checks modules by import name; pyproject lists distributions.
IMPORT_TO_DIST = {
    "aiohttp": "aiohttp", "rich": "rich", "prompt_toolkit": "prompt-toolkit",
    "requests": "requests", "pandas": "pandas", "numpy": "numpy",
    "yfinance": "yfinance", "google.genai": "google-genai", "akshare": "akshare",
}


def _default_model() -> str:
    tree = ast.parse((SRC / "apps" / "cli" / "bootstrap.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "DEFAULT_MODEL" for t in node.targets)):
            call = node.value  # os.getenv("ARIA_DEFAULT_MODEL", "<default>")
            return ast.literal_eval(call.args[1])
    raise AssertionError("DEFAULT_MODEL not found in bootstrap.py")


class TheDefaultModelWorksOnAFreshInstall(unittest.TestCase):
    def test_the_default_models_client_is_a_core_dependency(self) -> None:
        model = _default_model()
        provider = model.split("/", 1)[0] if "/" in model else "ollama"
        client = PROVIDER_CLIENT.get(provider)
        if client is None:
            self.skipTest(f"no client mapping for default provider {provider!r}")
        self.assertIn(
            client, CORE,
            f"the default model is {model}, which needs {client}, but {client} "
            f"is not in [project].dependencies — a plain `pip install aria-code` "
            f"cannot answer a prompt",
        )

    def test_requirements_txt_carries_it_too(self) -> None:
        # The container installs from requirements.txt with --no-deps, so
        # pyproject alone does not reach it.
        lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        dists = {_dist(l) for l in lines if l.strip() and not l.lstrip().startswith("#")}
        self.assertIn("google-genai", dists)


class DoctorTellsTheTruthAboutDependencies(unittest.TestCase):
    def setUp(self) -> None:
        from aria_code import doctor
        self.doctor = doctor

    def test_what_doctor_calls_required_is_a_core_dependency(self) -> None:
        for module, _purpose in self.doctor._iter_required_modules():
            with self.subTest(module=module):
                self.assertIn(
                    IMPORT_TO_DIST[module], CORE,
                    f"doctor reports {module} as required, so its absence is an "
                    f"error — but pyproject does not install it by default",
                )

    def test_what_doctor_calls_optional_names_a_real_extra(self) -> None:
        for module, _purpose, extra in self.doctor._iter_optional_modules():
            with self.subTest(module=module):
                self.assertIn(extra, EXTRAS, f"{extra!r} is not an extra in pyproject")
                self.assertIn(IMPORT_TO_DIST[module], EXTRAS[extra])

    def test_a_missing_optional_extra_is_not_an_error(self) -> None:
        original = self.doctor._has_module
        self.doctor._has_module = lambda name: name != "akshare"  # type: ignore[assignment]
        try:
            report = self.doctor.run_doctor({})
        finally:
            self.doctor._has_module = original  # type: ignore[assignment]
        row = next(c for c in report.checks if c.name == "package:akshare")
        self.assertNotEqual(row.status, "err")
        self.assertIn("aria-code[cn]", row.suggestion)

    def test_has_module_survives_a_missing_parent_package(self) -> None:
        # find_spec("x.y") raises, rather than returning None, when "x" is
        # absent; doctor must report that as missing, not crash.
        self.assertFalse(self.doctor._has_module("no_such_parent_pkg.child"))


class TheProductDescribesItselfOneWay(unittest.TestCase):
    def test_help_matches_the_package_description(self) -> None:
        cli = (SRC / "aria_cli.py").read_text(encoding="utf-8")
        match = re.search(r'description="Aria Code — ([^"]+)"', cli)
        self.assertIsNotNone(match, "argparse description not found")
        self.assertEqual(match.group(1), PYPROJECT["description"])


class NothingInstallsFromTheOldOrganisation(unittest.TestCase):
    """The `artherahq` name is unclaimed; anything pointing there can be hijacked."""

    # Files that tell a user, a package manager or the running tool where to
    # fetch from. Historical documents and the changelog may name the old org.
    SHIPPED = (
        "README.md", "README_CN.md", "CONTRIBUTING.md", "bootstrap.sh",
        "pyproject.toml", "npm/package.json",
        "scripts/install.sh", "scripts/install.ps1",
        ".github/workflows/publish.yml",
    )
    OLD_ORG = re.compile(r"(github\.com|githubusercontent\.com)/artherahq/|@artherahq/|\"@artherahq\"")

    def test_install_paths_and_package_metadata(self) -> None:
        for rel in self.SHIPPED:
            with self.subTest(file=rel):
                hits = [f"{n}: {l.strip()}"
                        for n, l in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1)
                        if self.OLD_ORG.search(l)]
                self.assertEqual(hits, [], f"{rel} still fetches from artherahq:\n" + "\n".join(hits))

    def test_runtime_code(self) -> None:
        hits = []
        for path in SRC.rglob("*.py"):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if self.OLD_ORG.search(line) or re.search(r'["\']artherahq/', line) \
                        or re.search(r'=\s*"artherahq"', line) or re.search(r'or "artherahq"', line):
                    hits.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
        self.assertEqual(hits, [], "runtime code still targets the old org:\n" + "\n".join(hits))


if __name__ == "__main__":
    unittest.main()
