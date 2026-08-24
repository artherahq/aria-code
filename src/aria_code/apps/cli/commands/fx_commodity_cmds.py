"""FxCommodityCommandsMixin — /crypto, /forex, /commodity.

Method bodies use aria_cli module globals (self.context.console, self.context.has_rich, _get__HAS_MDC(),
_get_mdc), bound at import time by
aria_cli._rebind_mixin_globals(FxCommodityCommandsMixin). They also call
self._run_in_executor / self._run_parallel / self._fetch_and_display_finance,
which are resolved through the SlashCommands MRO at call time (defined
elsewhere on the class), not module globals.
"""

from __future__ import annotations


import json
import asyncio
import datetime
import time
import shlex
from typing import Dict, Any, Optional

def _get__HAS_MDC():
    from aria_cli import _HAS_MDC as val
    return val
def _get_mdc(*args, **kwargs):
    from aria_cli import _get_mdc as fn
    return fn(*args, **kwargs)

import json
import asyncio
import datetime
import time
import shlex
import sys
import os
from typing import Dict, Any, Optional


import json
import asyncio
import datetime
import time
import shlex
import sys
import os
from typing import Dict, Any, Optional


class FxCommodityCommandsMixin:
    """Mixin: crypto, forex, and commodity quote commands."""

    async def cmd_crypto(self, args: str):
        """Crypto data: /crypto BTC ETH  ·  /crypto account (read-only balance)"""
        # ── Read-only account view: /crypto account [exchange] ───────────────
        if args.strip().lower().split()[:1] == ["account"]:
            _parts = args.strip().split()
            _exch = _parts[1].lower() if len(_parts) > 1 else "binance"
            if not _get__HAS_MDC():
                self.context.console.print("[yellow]market_data_client 不可用[/yellow]" if self.context.has_rich else "unavailable")
                return
            acct = await self._run_in_executor(
                lambda: _get_mdc().crypto_account(_exch)
            )
            if not acct.get("success"):
                _err = acct.get("error", "")
                if _err == "no_api_key":
                    msg = (f"未配置 {_exch.upper()} 只读 API key。\n"
                           f"  设置环境变量：{_exch.upper()}_API_KEY 和 {_exch.upper()}_SECRET\n"
                           f"  [dim]建议用「只读」权限的 key — Aria 永不下单[/dim]")
                else:
                    msg = f"读取失败：{_err}"
                self.context.console.print(f"  [yellow]{msg}[/yellow]" if self.context.has_rich else msg)
                return
            holdings = acct.get("holdings", [])
            if self.context.has_rich:
                self.context.console.print(f"\n  [bold]{_exch.capitalize()} 账户[/bold]  "
                              f"[dim]只读 · {len(holdings)} 个资产[/dim]")
                for h in holdings[:15]:
                    self.context.console.print(f"    {h['asset']:<8} [bold]{h['amount']:,.6g}[/bold]"
                                  f"  [dim]可用 {h['free']:,.6g}[/dim]")
            else:
                print(f"{_exch} account ({len(holdings)} assets):")
                for h in holdings[:15]:
                    print(f"  {h['asset']:<8} {h['amount']:.6g}")
            return

        symbols = args.upper().split() if args else ["BTC"]
        if self.context.has_rich:
            self.context.console.print()
        for sym in symbols:
            # yfinance crypto symbol: BTC → BTC-USD, ETH → ETH-USD
            yf_sym = sym + "-USD" if not sym.endswith("-USD") and "/" not in sym else sym
            await self._fetch_and_display_finance(
                "get_crypto_data", {"symbol": sym},
                label=sym, mdc_fallback_symbol=yf_sym
            )
        if self.context.has_rich:
            self.context.console.print()

    async def cmd_forex(self, args: str):
        """Forex rates: /forex EUR/USD USD/CNY (with yfinance fallback)"""
        pairs = args.upper().split() if args else ["EUR/USD"]
        if self.context.has_rich:
            self.context.console.print()
        for pair in pairs:
            # yfinance forex symbol: EUR/USD → EURUSD=X
            yf_pair = pair.replace("/", "") + "=X"
            await self._fetch_and_display_finance(
                "get_forex_data", {"pair": pair},
                label=pair, mdc_fallback_symbol=yf_pair
            )
        if self.context.has_rich:
            self.context.console.print()

    async def cmd_commodity(self, args: str):
        """Commodities: /commodity gold oil silver (parallel fetch)"""
        items = args.lower().split() if args else ["gold"]
        await self._run_parallel(
            "get_commodities_data",
            [{"commodity": c} for c in items],
            label_fn=lambda p: f"commodity {p['commodity']}",
        )
