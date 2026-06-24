"""licensing.py — feature entitlement gate for the open-core model.

FREE BY DEFAULT. The BSL-licensed CLI shell works fully with no license. Premium
features (the compiled quant engine, hosted services) call ``has_feature`` /
``require_feature`` to gate access.

A license key here is an *entitlement* token, not the mechanism that protects
IP — IP protection comes from shipping the engine COMPILED (see
tools/build_quant_engine.py). This module just unlocks features and supports a
paid tier, with optional tamper-evident HMAC signatures.

License sources (first match wins):
  1. ARIA_LICENSE_KEY env var  — raw key string (treated as the "pro" tier)
  2. ~/.arthera/license.json   — {"key","tier","features":[...],"exp":"YYYY-MM-DD","sig"?}

If ARIA_LICENSE_PUBKEY (an HMAC secret distributed with the build) is set, the
license ``sig`` MUST verify or the license is rejected. Without it, licenses are
accepted unsigned (development / self-host).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

# Features that are ALWAYS free (the BSL shell). Premium features are anything
# not listed here; they require a license that grants them.
FREE_FEATURES: frozenset[str] = frozenset({
    "chat", "market_data", "market_history", "backtest", "portfolio_backtest",
    "broker_read", "broker_trade", "paper_trade", "file_analysis", "web_search",
    "alerts", "reports", "strategy_vault",
})

_LICENSE_PATHS = [
    Path.home() / ".arthera" / "license.json",
    Path.home() / ".aria" / "license.json",
]


@dataclass
class License:
    key: str = ""
    tier: str = "free"
    features: List[str] = field(default_factory=list)
    exp: str = ""                 # "YYYY-MM-DD"; empty = no expiry
    valid: bool = True
    reason: str = ""

    @property
    def expired(self) -> bool:
        if not self.exp:
            return False
        try:
            return datetime.strptime(self.exp, "%Y-%m-%d").date() < date.today()
        except ValueError:
            return False


def _signing_secret() -> str:
    return str(os.getenv("ARIA_LICENSE_PUBKEY", "") or "").strip()


def _verify_sig(payload: dict) -> bool:
    """Verify the license HMAC signature when a verification key is configured."""
    secret = _signing_secret()
    if not secret:
        return True  # unsigned mode (dev / self-host)
    sig = str(payload.get("sig") or "")
    body = {k: payload[k] for k in sorted(payload) if k != "sig"}
    expected = hmac.new(secret.encode(), json.dumps(body, sort_keys=True).encode(),
                        hashlib.sha256).hexdigest()
    return bool(sig) and hmac.compare_digest(sig, expected)


def _load_license() -> License:
    env_key = str(os.getenv("ARIA_LICENSE_KEY", "") or "").strip()
    if env_key:
        return License(key=env_key, tier="pro", features=["*"], valid=True)
    for p in _LICENSE_PATHS:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            return License(valid=False, reason=f"license.json 解析失败: {e}")
        if not _verify_sig(data):
            return License(valid=False, reason="license 签名校验失败")
        lic = License(
            key=str(data.get("key", "")),
            tier=str(data.get("tier", "pro")),
            features=list(data.get("features", []) or []),
            exp=str(data.get("exp", "") or ""),
        )
        if lic.expired:
            lic.valid = False
            lic.reason = f"license 已于 {lic.exp} 过期"
        return lic
    return License(tier="free")


# Cached singleton; refresh() re-reads (e.g. after the user installs a key).
_CACHE: Optional[License] = None


def current_license(refresh: bool = False) -> License:
    global _CACHE
    if _CACHE is None or refresh:
        _CACHE = _load_license()
    return _CACHE


def has_feature(name: str) -> bool:
    """True if a feature is available (free, or granted by a valid license)."""
    if name in FREE_FEATURES:
        return True
    lic = current_license()
    if not lic.valid:
        return False
    return "*" in lic.features or name in lic.features


def require_feature(name: str) -> tuple[bool, str]:
    """Gate a premium feature. Returns (allowed, user_message)."""
    if has_feature(name):
        return True, ""
    lic = current_license()
    if not lic.valid and lic.reason:
        return False, f"功能 '{name}' 需要有效授权：{lic.reason}"
    return False, (f"功能 '{name}' 属于专业版。请配置 ARIA_LICENSE_KEY 或 "
                   f"~/.arthera/license.json 解锁（当前: {lic.tier} 版）。")


def license_status() -> dict:
    """Snapshot for a /license command."""
    lic = current_license()
    return {
        "tier": lic.tier,
        "valid": lic.valid,
        "expired": lic.expired,
        "exp": lic.exp,
        "reason": lic.reason,
        "features": lic.features,
        "signed_mode": bool(_signing_secret()),
    }
