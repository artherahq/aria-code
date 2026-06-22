## Summary

-

## Validation

-

## Risk

-

## Checklist

- [ ] No secrets, tokens, real `.env` files, or broker credentials are included.
- [ ] User-facing behavior is covered by tests or manually verified.
- [ ] CLI changes keep `aria_cli.py` as an adapter and place reusable logic under `apps/`, `runtime/`, or `packages/`.
- [ ] Broker/live-trading changes preserve preview, confirmation, and audit boundaries.
