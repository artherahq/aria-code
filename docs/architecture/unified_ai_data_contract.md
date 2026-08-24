# Unified AI and Data Contract

This is the migration contract for Aria Code, Arthera Desktop, Arthera Web,
and Arthera iOS. It intentionally describes product behavior, rather than a
client-specific implementation. Every surface calls the same gateway; clients
do not call data vendors, LLM providers, or MCP servers directly.

## One Gateway, Three Surfaces

```text
Aria Code / Desktop / Web / iOS
             |
             v
      Arthera API gateway
        |       |       |
     chat     data    actions
        |       |       |
   model +    approved  preview -> confirm -> audit
   tools      sources
```

The shared gateway owns authentication, session identity, model selection,
tool policy, streaming, source provenance, retention, and audit records.
The client owns rendering, local drafts, accessibility, and explicit user
actions. A client must never receive a provider secret or an Apify token.

## Conversation API

Use one request envelope for the chat, research, and coding workspaces.
Legacy aliases may be accepted at the edge during migration but are never
emitted by new clients.

```json
{
  "session_id": "uuid",
  "message": {"id": "uuid", "role": "user", "content": [{"type": "text", "text": "Research AAPL news"}]},
  "mode": "research",
  "surface": "ios",
  "model": {"id": "auto", "effort": "auto"},
  "context": {"workspace_id": "optional", "locale": "zh-CN", "attachments": []},
  "capabilities": ["web_research", "market_data"]
}
```

`mode` is one of `chat`, `research`, or `code`. `surface` is one of
`aria_code`, `desktop`, `web`, or `ios`; it is telemetry, not an authorization
mechanism. The server derives the user from authentication rather than trusting
`user_id` in JSON.

The streaming endpoint emits Server-Sent Events with a stable public schema:

| Event | Required payload | Meaning |
| --- | --- | --- |
| `status` | `state`, `label` | Short visible progress, never hidden reasoning |
| `delta` | `message_id`, `text` | Append-only assistant text |
| `tool` | `call_id`, `name`, `status`, `summary` | Tool activity with a user-safe summary |
| `source` | `source` | A source/provenance item available for citation |
| `approval_required` | `approval` | A guarded action awaits an explicit decision |
| `final` | `message`, `sources`, `usage`, `trace_id` | Completed response |
| `error` | `code`, `message`, `retryable` | Safe failure presentation |

All clients render the same states: idle, composing, streaming, awaiting
approval, complete, cancelled, and failed. This provides the calm composer,
streaming answer, collapsible tool activity, source chips, and explicit
approval affordance users expect from leading AI products without copying their
branding or proprietary UI.

## Data and Apify Policy

Apify is an optional research connector. It may provide public web documents,
search results, or other approved Actor output. It is not an authoritative
price, execution, account, or identity source.

Every retrieved item entering research context must include:

```json
{
  "kind": "web_document",
  "title": "string",
  "url": "https://...",
  "publisher": "string",
  "retrieved_at": "RFC 3339 timestamp",
  "provider": "apify",
  "actor_id": "owner/actor",
  "run_id": "optional provider run id",
  "content_hash": "sha256",
  "license_or_terms": "known | unknown",
  "quality": {"status": "ok | partial | unavailable", "warnings": []}
}
```

The gateway allowlists Actors and output fields, rate-limits requests, strips
secrets, stores raw output in a restricted provenance store, and returns only
the normalized records. Actor selection, price, target-site terms, consent,
and personal-data handling are reviewed before enablement. Research data never
goes directly into a backtest or a trade decision without its source, timestamp,
and quality status.

### Deployment configuration

Apify is off until the **Arthera API deployment**, not an app client, has all
of the following environment variables. `APIFY_WEB_RESEARCH_ACTOR` is a single
reviewed `owner/actor` identifier; it is not client input. The actor's supported
fields must be listed in `APIFY_WEB_RESEARCH_ALLOWED_INPUT_KEYS`.

```text
APIFY_TOKEN=server-only-secret
APIFY_WEB_RESEARCH_ACTOR=owner/approved-research-actor
APIFY_WEB_RESEARCH_ALLOWED_INPUT_KEYS=query,urls,startUrls,maxResults
APIFY_WEB_RESEARCH_MAX_CHARGE_USD=1
```

The gateway caps returned items at 10 and a run's charge budget at USD 10 even
when the environment is misconfigured. Keep the operational budget much lower
for interactive chat. The current synchronous integration has a 120-second
network timeout; longer research should migrate to an asynchronous job and a
webhook before it is exposed in the interactive UI.

## Migration Order

1. Make `/api/v2/chat/react` accept and emit this envelope while preserving the
   existing routes as compatibility adapters.
2. Route desktop, web, iOS, and Aria Code through that endpoint; remove each
   client-specific request model after parity tests pass.
3. Put Apify behind a gateway-owned `web_research` tool, initially with only
   Actor discovery, documentation, and the approved web-browser Actor.
4. Unify design tokens (color, type, spacing, radius, motion, states) in a
   versioned token package; each client consumes translated platform tokens.
5. Add cross-client contract fixtures for SSE, cancellation, approvals, source
   citations, and inaccessible-provider failures.

## Aria Code opt-in

Aria Code remains local-first. To route its cloud turn through the same ReAct
gateway used by Desktop and iOS, configure a server URL and enable the explicit
opt-in below. This does not send local project content unless the normal Aria
Code context policy has already selected it for that turn.

```yaml
# ~/.aria/providers.yaml or project .aria.yaml
backend_chat: true
api_url: https://api.example.com
arthera_react_gateway: true
```

The CLI sends `surface: aria_code` and defaults to `mode: code`. Set
`workspace_mode` in the existing user context when a chat or research turn is
intended instead. Leaving `arthera_react_gateway` unset preserves the legacy
stream endpoint and offline/local model workflows.
