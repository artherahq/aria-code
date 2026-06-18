# Memory

- **Project**: Aria Code
- **Stack**: Python CLI agent workspace
- **Entry**: aria_cli.py
- **Purpose**: Command-driven local agent runtime with tools, skills, providers, trace, export, and project memory.
- **Conventions**:
  - Keep CLI output stable and avoid leaking local paths in normal UI.
  - Prefer thin CLI entry points plus command mixins for implementation.
  - Treat turn envelopes, traces, exports, and provider health as shared contracts.
  - Keep generated code and user artifacts in the user's workspace unless the user asks otherwise.

## Memory Layers
- Project ARIA.md describes this repository and overrides the global profile when both apply.
- `~/.arthera/ARIA.md` is the user profile and carries cross-project preferences.
- Session history and traces stay ephemeral unless exported explicitly.

## Operational Rules
- Use `/doctor` or the health views before trusting external providers.
- Keep generated code and user artifacts out of the source tree unless the user asks otherwise.
- Prefer deterministic service output envelopes for CLI, trace, and export surfaces.
- Keep long sessions compacted and avoid relying on chat history as the only source of truth.

## Workflow Notes
- Use `/init` to refresh project context after substantial structural changes.
- Use `/memory add` for durable project facts and `/memory profile` for user preferences.
- Keep business workflows, diagnostics, and UI surfaces split into focused modules.
