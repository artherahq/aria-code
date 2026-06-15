# CLI App

Target role: terminal UI and command parsing only.

The CLI should depend on these services:

- `gateway`: session routing and task orchestration.
- `runtime`: agent loop and tool execution.
- `ui`: terminal rendering and input.
- `reports`: local artifact and report output.

Migration rule: new slash commands should call package services instead of
adding more business logic to `aria_cli.py`.
