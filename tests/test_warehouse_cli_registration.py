from types import SimpleNamespace

import aria_code.aria_cli


def test_warehouse_command_is_available_in_the_slash_command_registry():
    commands = aria_cli.SlashCommands(SimpleNamespace(config={}))
    handler, description = commands.commands["/warehouse"]
    assert handler.__name__ == "cmd_warehouse"
    assert "Read-only" in description
