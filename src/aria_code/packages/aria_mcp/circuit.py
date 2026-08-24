"""Per-server circuit breaker for MCP calls (pure, clock-injectable).

The contract's mcp-layer next step calls for per-server failure isolation and
a reconnect policy. Without it, a dead or wedged MCP server costs every call
its full request timeout (30s by default) forever — one bad server degrades
the whole tool loop for the rest of the session.

Classic three-state breaker:

  closed     → calls flow; consecutive failures are counted
  open       → calls fail fast (no subprocess I/O) until the cooldown elapses
  half_open  → after cooldown, exactly one probe call is allowed through;
               success closes the circuit, failure re-opens it for another
               cooldown

Pure state machine — no asyncio, no I/O; ``clock`` is injectable so tests
can drive time deterministically.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ServerCircuit:
    failure_threshold: int = 3
    cooldown_seconds: float = 60.0
    clock: Callable[[], float] = time.monotonic

    _failures: int = field(default=0, repr=False)
    _open_until: float = field(default=0.0, repr=False)
    _probing: bool = field(default=False, repr=False)

    @property
    def state(self) -> str:
        if self._failures < self.failure_threshold:
            return "closed"
        if self.clock() < self._open_until:
            return "open"
        return "half_open"

    @property
    def failures(self) -> int:
        return self._failures

    def seconds_until_probe(self) -> float:
        return max(0.0, self._open_until - self.clock())

    def allow_call(self) -> bool:
        """Whether a call may proceed now (half-open admits a single probe)."""
        s = self.state
        if s == "closed":
            return True
        if s == "open":
            return False
        # half_open: admit one probe at a time
        if self._probing:
            return False
        self._probing = True
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._open_until = 0.0
        self._probing = False

    def record_failure(self) -> None:
        self._failures += 1
        self._probing = False
        if self._failures >= self.failure_threshold:
            self._open_until = self.clock() + self.cooldown_seconds

    def describe(self) -> str:
        s = self.state
        if s == "open":
            return f"open ({self._failures} consecutive failures; probe in {self.seconds_until_probe():.0f}s)"
        if s == "half_open":
            return f"half_open ({self._failures} consecutive failures; probing)"
        return f"closed ({self._failures} recent failures)"
