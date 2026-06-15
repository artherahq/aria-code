"""Direct command dispatcher for non-interactive CLI entrypoints."""

from __future__ import annotations

import inspect
from typing import Any

from apps.cli.commands.catalog import DIRECT_COMMAND_MAP, WATCHABLE_DIRECT_COMMANDS


def is_watchable_direct_command(command: str) -> bool:
    return command.strip().lower() in WATCHABLE_DIRECT_COMMANDS


async def dispatch_direct_command(
    terminal: Any,
    command: str,
    command_args: str = "",
    *,
    json_output: bool = False,
    fmt: str = "table",
    output_file: str | None = None,
    quiet: bool = False,
) -> bool:
    """Dispatch a direct CLI command.

    Returns True when the command was handled by a known direct command handler.
    Unknown direct commands fall back to the normal prompt path and return False.
    """

    cmd = command.strip().lower()
    spec = DIRECT_COMMAND_MAP.get(cmd)
    if spec is None:
        await terminal.run_prompt(
            f"{command} {command_args}".strip(),
            json_output=json_output,
            fmt=fmt,
            output_file=output_file,
            quiet=quiet,
        )
        return False

    handler = getattr(terminal.commands, spec.method_name)
    result = handler(command_args)
    if inspect.isawaitable(result):
        await result
    return True

