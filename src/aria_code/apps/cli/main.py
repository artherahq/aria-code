"""Installed console entrypoint for Aria Code.

The terminal implementation lives in ``aria_cli`` as an async ``main()``. A
console script (declared in pyproject ``[project.scripts]`` as
``aria-code = "apps.cli.main:main"``) must be a *synchronous* callable, so this
shim wraps the coroutine in ``asyncio.run`` — mirroring the
``if __name__ == "__main__"`` block in ``aria_cli.py``. Calling the async
``aria_cli.main`` directly from a console script would only create a coroutine
that is never awaited.
"""

from __future__ import annotations

import asyncio
import sys


def main() -> None:
    """Synchronous entry point for the ``aria-code`` command."""
    from aria_code.aria_cli import main as _async_main

    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        print()
        sys.exit(0)


if __name__ == "__main__":
    # Lets `python -m aria_code.apps.cli.main` launch the CLI directly — the
    # path install.sh and the npm launcher use, since they invoke the venv's
    # python rather than relying on the pip-generated `aria-code` console
    # script. Without this guard, `-m` only imports the module and exits
    # immediately: `main` is defined but never called.
    main()


__all__ = ["main"]
