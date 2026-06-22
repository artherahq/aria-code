# Security Policy

Aria Code is a local-first agent with filesystem, shell, market-data, and
optional broker integrations. Security reports should focus on issues that can
expose credentials, bypass approval boundaries, execute unintended commands, or
mis-handle broker actions.

## Supported Versions

Security fixes target the latest `main` branch and the latest tagged release.
Older development branches are not maintained after their changes are merged or
closed.

## Reporting a Vulnerability

Do not open a public issue for suspected vulnerabilities. Report privately to
the Arthera maintainers with:

- affected version or commit;
- operating system and install method;
- reproduction steps;
- expected impact;
- logs with secrets redacted.

If GitHub private vulnerability reporting is enabled for the repository, use it.
Otherwise contact the repository owner through the Arthera GitHub organization.

## Credential Handling

Never commit real API keys, broker credentials, tokens, `.env` files, private
certificates, or production config files. Commit only templates such as
`.env.example`, `.env.daemon.template`, and `config/*.example.*`.

## Broker Safety

Live broker execution must remain opt-in. Real broker adapters should default to
read-only, require explicit preview/confirmation boundaries, and write audit
events for previews, rejections, and executions.
