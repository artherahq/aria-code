"""Simple lifecycle shell hooks for the CLI adapter."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Mapping


def run_event_hook(
    event: str,
    *,
    config_dir: Path,
    cwd: Path | None = None,
    env_extra: Mapping[str, str] | None = None,
    timeout: int = 10,
) -> None:
    """Run executable hook scripts for a lifecycle event.

    Script locations are intentionally simple for compatibility:
    `<config_dir>/hooks/<event>.sh` and `<cwd>/.aria/hooks/<event>.sh`.
    Hook failures are non-fatal.
    """
    root = cwd or Path.cwd()
    dirs = [
        config_dir / "hooks",
        root / ".aria" / "hooks",
    ]
    env = dict(os.environ)
    env["ARIA_EVENT"] = event
    if env_extra:
        env.update({str(k): str(v) for k, v in env_extra.items()})
    for hook_dir in dirs:
        script = hook_dir / f"{event}.sh"
        if not script.exists() or script.stat().st_size <= 0:
            continue
        try:
            subprocess.run(
                [str(script)],
                env=env,
                timeout=timeout,
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            continue
