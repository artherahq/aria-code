"""Tests for the repo map.

The behaviours pinned here are the ones that decide whether the map is
navigation or noise: what it refuses to walk, whose names it ranks up, and
that it never raises on a file it cannot parse.
"""

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from aria_code.runtime import RepoMap, Symbol, extract_symbols
from aria_code.runtime.repo_map import (
    clear_cache,
    tool_find_symbol,
    tool_repo_map,
)


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


class ExtractSymbolsTests(unittest.TestCase):
    def test_python_captures_classes_functions_and_methods(self):
        symbols = extract_symbols(
            textwrap.dedent("""
                class Engine:
                    def start(self):
                        pass
                    async def stop(self):
                        pass

                def helper():
                    pass
            """),
            "python",
        )
        by_name = {s.name: s for s in symbols}
        self.assertEqual(by_name["Engine"].kind, "class")
        self.assertEqual(by_name["start"].parent, "Engine")
        self.assertEqual(by_name["start"].qualified, "Engine.start")
        self.assertEqual(by_name["stop"].kind, "async def")
        self.assertEqual(by_name["helper"].parent, "")

    def test_python_captures_module_level_constants(self):
        symbols = extract_symbols("REGISTRY = {}\nlowercase = 1\nX = 2\n", "python")
        names = {s.name for s in symbols}
        self.assertIn("REGISTRY", names)
        self.assertNotIn("lowercase", names)  # not a public registry-style name
        self.assertNotIn("X", names)          # too short to be informative

    def test_unparseable_python_yields_nothing_rather_than_raising(self):
        self.assertEqual(extract_symbols("def broken(:\n", "python"), [])

    def test_typescript_declarations(self):
        symbols = extract_symbols(
            textwrap.dedent("""
                export interface Props { a: string }
                export type Handler = () => void;
                export class Widget {}
                export const render = (x) => x;
                export default function boot() {}
            """),
            "typescript",
        )
        names = {s.name for s in symbols}
        self.assertEqual(names, {"Props", "Handler", "Widget", "render", "boot"})

    def test_go_and_rust_declarations(self):
        go = {s.name for s in extract_symbols(
            "type Server struct {}\nfunc (s *Server) Serve() {}\nfunc New() {}\n", "go")}
        self.assertEqual(go, {"Server", "Serve", "New"})

        rust = {s.name for s in extract_symbols(
            "pub struct Cache;\npub async fn load() {}\ntrait Store {}\n", "rust")}
        self.assertEqual(rust, {"Cache", "load", "Store"})

    def test_a_call_is_not_read_as_a_declaration(self):
        # `register(function () {})` mid-line must not register a symbol.
        symbols = extract_symbols("  register(function () { return 1; });\n", "javascript")
        self.assertEqual(symbols, [])

    def test_unknown_language_yields_nothing(self):
        self.assertEqual(extract_symbols("anything", "brainfuck"), [])


class RepoMapScanTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        clear_cache()

    def test_vendor_directories_are_never_walked(self):
        _write(self.root, "app.py", "class Real: pass\n")
        _write(self.root, "node_modules/pkg/index.js", "export class Vendored {}\n")
        _write(self.root, ".venv/lib/dep.py", "class Dependency: pass\n")
        _write(self.root, "__pycache__/stale.py", "class Stale: pass\n")

        repo = RepoMap(self.root).build()

        self.assertIn("Real", repo.defs)
        self.assertNotIn("Vendored", repo.defs)
        self.assertNotIn("Dependency", repo.defs)
        self.assertNotIn("Stale", repo.defs)

    def test_oversized_files_are_skipped(self):
        _write(self.root, "big.py", "class Huge: pass\n" + ("# pad\n" * 5000))
        repo = RepoMap(self.root, max_file_bytes=200).build()
        self.assertNotIn("Huge", repo.defs)

    def test_widely_referenced_file_outranks_a_leaf(self):
        _write(self.root, "core.py", "class TransactionLedger:\n    pass\n")
        _write(self.root, "leaf.py", "class NobodyCallsThis:\n    pass\n")
        for i in range(4):
            _write(self.root, f"user{i}.py", "from core import TransactionLedger\n")

        repo = RepoMap(self.root).build()
        ranked = [e.path for e in repo.ranked()]
        self.assertLess(ranked.index("core.py"), ranked.index("leaf.py"))

    def test_short_names_do_not_win_the_ranking(self):
        # `run` is referenced from everywhere and means nothing.
        _write(self.root, "noise.py", "def run():\n    pass\n")
        _write(self.root, "real.py", "class SettlementReconciler:\n    pass\n")
        for i in range(6):
            _write(self.root, f"c{i}.py", "run()\nSettlementReconciler()\n")

        repo = RepoMap(self.root).build()
        ranked = [e.path for e in repo.ranked()]
        self.assertLess(ranked.index("real.py"), ranked.index("noise.py"))

    def test_a_name_defined_everywhere_contributes_nothing(self):
        for i in range(7):
            _write(self.root, f"mod{i}.py", "def configure():\n    pass\n")
        _write(self.root, "caller.py", "configure()\n")

        repo = RepoMap(self.root).build()
        self.assertEqual(repo._effective_refs("configure"), 0)

    def test_focus_promotes_the_files_the_caller_already_cares_about(self):
        _write(self.root, "hub.py", "class Hub:\n    pass\n")
        for i in range(4):
            _write(self.root, f"u{i}.py", "from hub import Hub\n")
        _write(self.root, "scratch.py", "class Scratch:\n    pass\n")

        repo = RepoMap(self.root).build()
        ranked = [e.path for e in repo.ranked(focus=["scratch"])]
        self.assertEqual(ranked[0], "scratch.py")

    def test_rebuild_reuses_unchanged_files_and_sees_new_ones(self):
        _write(self.root, "a.py", "class Alpha: pass\n")
        repo = RepoMap(self.root).build()
        cached = repo.files["a.py"]

        _write(self.root, "b.py", "class Beta: pass\n")
        repo.build()

        self.assertIs(repo.files["a.py"], cached)   # untouched file not reparsed
        self.assertIn("Beta", repo.defs)


class RepoMapRenderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        clear_cache()

    def test_render_respects_its_budget(self):
        for i in range(60):
            _write(self.root, f"m{i}.py", "".join(
                f"class Klass{i}_{j}:\n    pass\n" for j in range(30)
            ))
        repo = RepoMap(self.root).build()
        rendered = repo.render(budget_chars=2500)

        self.assertLessEqual(len(rendered), 2800)   # header + footer overhead
        self.assertIn("omitted", rendered)

    def test_budget_is_spread_across_files_not_spent_on_one(self):
        for i in range(40):
            _write(self.root, f"m{i}.py", "".join(
                f"class Klass{i}_{j}:\n    pass\n" for j in range(40)
            ))
        repo = RepoMap(self.root).build()
        rendered = repo.render(budget_chars=4000)

        files_shown = sum(1 for line in rendered.splitlines() if line.endswith(".py"))
        self.assertGreater(files_shown, 8)

    def test_top_level_definitions_are_listed_before_methods(self):
        _write(self.root, "svc.py", textwrap.dedent("""
            class Service:
                def alpha(self): pass
                def beta(self): pass
                def gamma(self): pass
                def delta(self): pass
            class Helper:
                pass
        """))
        repo = RepoMap(self.root).build()
        listed = [s.qualified for s in repo.notable(repo.files["svc.py"], 2)]
        self.assertEqual(sorted(listed), ["Helper", "Service"])

    def test_empty_repo_renders_an_explanation_not_a_crash(self):
        self.assertIn("empty", RepoMap(self.root).build().render())


class FindSymbolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        clear_cache()
        _write(self.root, "ledger.py", "\n\nclass SettlementLedger:\n    pass\n")
        _write(self.root, "caller.py", "from ledger import SettlementLedger\n")
        self.repo = RepoMap(self.root).build()

    def test_definition_is_returned_with_file_and_line(self):
        found = self.repo.find_symbol("SettlementLedger")
        self.assertEqual(found["definitions"][0]["file"], "ledger.py")
        self.assertEqual(found["definitions"][0]["line"], 3)
        self.assertEqual(found["referenced_by"], ["caller.py"])

    def test_a_near_miss_suggests_instead_of_returning_nothing(self):
        found = self.repo.find_symbol("SettlementLedge")
        self.assertEqual(found["definitions"], [])
        self.assertIn("SettlementLedger", found["did_you_mean"])

    def test_a_true_miss_is_empty_not_an_error(self):
        found = self.repo.find_symbol("ZzzNotHere")
        self.assertEqual(found["definitions"], [])
        self.assertEqual(found["did_you_mean"], [])

    def test_empty_name_is_reported(self):
        self.assertIn("error", self.repo.find_symbol("  "))


class RepoMapToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        clear_cache()
        _write(self.root, "svc.py", "class OrderService:\n    pass\n")

    def test_repo_map_tool_returns_a_map_and_a_summary(self):
        result = tool_repo_map({"path": str(self.root)})
        self.assertTrue(result["success"])
        self.assertIn("OrderService", result["data"]["map"])
        self.assertEqual(result["data"]["files"], 1)

    def test_find_symbol_tool_requires_a_name(self):
        result = tool_find_symbol({"path": str(self.root)})
        self.assertFalse(result["success"])
        self.assertIn("name", result["error"])

    def test_find_symbol_tool_locates_the_definition(self):
        result = tool_find_symbol({"name": "OrderService", "path": str(self.root)})
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["definitions"][0]["file"], "svc.py")

    def test_a_bad_path_fails_as_a_tool_result_not_an_exception(self):
        result = tool_repo_map({"path": str(self.root / "does-not-exist")})
        # A missing directory yields an empty map, not a raised error.
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["files"], 0)


if __name__ == "__main__":
    unittest.main()
