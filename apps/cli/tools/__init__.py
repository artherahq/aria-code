"""apps/cli/tools — stateless tool implementations extracted from aria_cli.py."""
from .file_tools import tool_read_file, tool_list_files, tool_search_code, tool_glob
from .context import ToolContext
from .system_tools import tool_run_command, tool_web_fetch, tool_github
from .notebook_tools import (
    tool_glob as tool_glob_nb,
    tool_notebook_read,
    tool_notebook_edit,
)
from .market_tools import (
    tool_get_market_data,
    tool_get_market_history,
    tool_broker_query,
    tool_broker_order,
)
from .write_tools import tool_write_file, tool_edit_file

__all__ = [
    "ToolContext",
    # file tools (stateless)
    "tool_read_file",
    "tool_list_files",
    "tool_search_code",
    "tool_glob",
    # write / edit tools (use lazy imports to avoid circular dep)
    "tool_write_file",
    "tool_edit_file",
    # system tools
    "tool_run_command",
    "tool_web_fetch",
    "tool_github",
    # notebook tools
    "tool_notebook_read",
    "tool_notebook_edit",
    # market / broker tools
    "tool_get_market_data",
    "tool_get_market_history",
    "tool_broker_query",
    "tool_broker_order",
]
