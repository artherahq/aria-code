"""Describe how Aria packages should be exposed over MCP."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class MCPExposure:
    name: str
    target: str
    description: str
    read_only: bool = True

    def to_tool_descriptor(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "target": self.target,
            "read_only": self.read_only,
        }


def default_exposures() -> List[MCPExposure]:
    """Initial MCP server surface for Aria Code.

    These names are stable contracts. The server implementation can bind them
    to ToolRegistry/AgentRegistry without changing client-facing tool names.
    """

    return [
        MCPExposure("aria.market.quote", "tool:get_market_data", "Fetch quote and technical market snapshot."),
        MCPExposure("aria.agent.team", "agent:team", "Run multi-agent financial research."),
        MCPExposure("aria.report.generate", "tool:report_generate", "Fetch data + generate a full HTML research report artifact for a symbol.", read_only=False),
        MCPExposure("aria.backtest.run", "tool:backtest_run", "Run a historical strategy simulation (buy_hold or sma_cross) against real price history.", read_only=False),
        MCPExposure("aria.artifacts.list", "infra:artifacts", "List local generated artifacts."),
        MCPExposure("aria.broker.positions", "tool:broker_positions", "Read account info + positions for a configured broker."),
        MCPExposure("aria.broker.list_previews", "tool:broker_list_previews", "List recent order previews, including TradingView-triggered ones."),
        MCPExposure(
            "aria.broker.preview_order",
            "tool:broker_preview_order",
            "Build a risk-checked order preview. Never executes a trade — "
            "execution always requires a human to run /trade confirm in the aria-code terminal.",
            read_only=False,
        ),
        MCPExposure(
            "aria.report.chart",
            "tool:report_chart",
            "Generate a candlestick + moving-average chart PNG for a symbol.",
            read_only=False,
        ),
        MCPExposure(
            "aria.report.pdf",
            "tool:report_pdf",
            "Render Markdown text to a styled PDF report.",
            read_only=False,
        ),
        MCPExposure(
            "aria.report.canva_upload_asset",
            "tool:canva_upload_asset",
            "Upload a local image (e.g. a chart PNG from aria.report.chart) to Canva and "
            "return its asset_id, for use in aria.report.canva_design's data field. "
            "Requires /canva connect to have been run first.",
            read_only=False,
        ),
        MCPExposure(
            "aria.report.canva_design",
            "tool:canva_autofill",
            "Fill a Canva brand template with data and export the design draft. "
            "Requires /canva connect to have been run first.",
            read_only=False,
        ),
        MCPExposure("aria.report.docx", "tool:report_docx", "Render Markdown text to an editable Word document.", read_only=False),
        MCPExposure("aria.report.pptx", "tool:report_pptx", "Render Markdown text to an editable slide deck (one slide per heading).", read_only=False),
        MCPExposure("aria.figma.read_file", "tool:figma_read_file", "Read a Figma file's page/frame structure (depth-limited summary, not the full node tree). Read-only — Figma has no API for writing designs."),
        MCPExposure("aria.figma.comments", "tool:figma_comments", "List comments on a Figma file."),
    ]
