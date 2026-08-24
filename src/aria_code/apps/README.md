# Aria Code Apps

`apps/` contains product entrypoints. Keep business logic in `packages/` and
make each app a thin adapter over service contracts.

- `cli/`: interactive terminal client.
- `daemon/`: local-first background gateway.
- `channels/`: external channel adapters such as relay, Feishu, Telegram, and webhooks.

Current implementation still lives mostly in root modules such as `aria_cli.py`.
New work should add service code behind `packages/` first, then migrate app
entrypoints here in small steps.
