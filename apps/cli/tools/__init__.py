"""apps/cli/tools — stateless tool implementations extracted from aria_cli.py."""
from .file_tools import tool_read_file, tool_list_files, tool_search_code, tool_glob
from .context import ToolContext
from .system_tools import tool_run_command, tool_web_fetch, tool_github
from .notebook_tools import (
    tool_glob as tool_glob_nb,
    tool_notebook_read,
    tool_notebook_edit,
)
from .market_tools import tool_get_market_data, tool_broker_query, tool_broker_order

__all__ = [
    "ToolContext",
    # file tools (stateless)
    "tool_read_file",
    "tool_list_files",
    "tool_search_code",
    "tool_glob",
    # system tools
    "tool_run_command",
    "tool_web_fetch",
    "tool_github",
    # notebook tools
    "tool_notebook_read",
    "tool_notebook_edit",
    # market / broker tools
    "tool_get_market_data",
    "tool_broker_query",
    "tool_broker_order",
]
