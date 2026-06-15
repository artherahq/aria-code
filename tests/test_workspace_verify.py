import json
import tempfile
import unittest
from pathlib import Path

from workspace import VerificationPlanner


class VerificationPlannerTests(unittest.TestCase):
    def test_python_file_gets_compile_and_pytest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            plan = VerificationPlanner(root).infer(["app.py"])
            self.assertIn("python3 -m py_compile app.py", plan.commands)
            self.assertIn("python3 -m pytest -q", plan.commands)

    def test_node_file_uses_package_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"scripts": {"test": "vitest run", "build": "vite build"}}),
                encoding="utf-8",
            )
            plan = VerificationPlanner(root).infer(["src/App.tsx"])
            self.assertIn("npm test", plan.commands)
            self.assertIn("npm run build", plan.commands)

    def test_no_paths_falls_back_to_project_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
            plan = VerificationPlanner(root).infer([])
            self.assertEqual(plan.commands, ["python3 -m pytest -q"])


if __name__ == "__main__":
    unittest.main()
