import unittest

from apps.cli.commands.diagnostic_ops_cmds import format_architecture_report
from packages.aria_core import architecture_status_counts, list_architecture_layers


class ArchitectureReportTests(unittest.TestCase):
    def setUp(self):
        self.layers = list_architecture_layers()
        self.counts = architecture_status_counts()

    def test_report_surfaces_runtime_next_step(self):
        text = "\n".join(format_architecture_report(self.layers, self.counts, rich=False))
        self.assertIn("runtime", text)
        # the runtime layer's documented next step is the run_agent cutover
        self.assertIn("run_agent", text)

    def test_every_layer_listed(self):
        text = "\n".join(format_architecture_report(self.layers, self.counts, rich=False))
        for layer in self.layers:
            self.assertIn(layer.name, text)

    def test_gaps_only_never_longer_than_full(self):
        full = format_architecture_report(self.layers, self.counts, rich=False)
        gaps = format_architecture_report(self.layers, self.counts, rich=False, gaps_only=True)
        self.assertLessEqual(len(gaps), len(full))

    def test_rich_markup_emitted(self):
        lines = format_architecture_report(self.layers, self.counts, rich=True)
        self.assertTrue(any("[bold]" in ln for ln in lines))


if __name__ == "__main__":
    unittest.main()
