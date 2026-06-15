# Channel Apps

Target role: adapters for non-terminal entrypoints.

Examples:

- relay server/client;
- Feishu bot;
- Telegram bot;
- webhooks;
- future desktop or browser UI.

Each channel should translate inbound messages into gateway requests and render
gateway responses back to the channel. It should not bypass safety, runtime, or
artifact policies.
