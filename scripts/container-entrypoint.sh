#!/bin/sh
# Cloud Run supplies PORT. Local CLI containers do not.
set -eu

if [ -n "${PORT:-}" ]; then
  exec python3 -m aria_code.aria_relay_server
fi

exec aria-code "$@"
