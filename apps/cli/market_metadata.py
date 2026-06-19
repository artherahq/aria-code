"""Local market symbol metadata used to normalize provider output.

Providers occasionally omit display names or return a generic/default currency.
This module keeps deterministic exchange metadata close to the CLI so prompt
injection and tool responses do not ask the model to guess symbol identity.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class MarketAssetMeta:
    symbol: str
    short_label: str
    display_name: str
    currency: str
    exchange: str = ""


_SYMBOL_META: dict[str, MarketAssetMeta] = {
    "MC.PA": MarketAssetMeta(
        symbol="MC.PA",
        short_label="LVMH/路易威登",
        display_name="LVMH Moet Hennessy Louis Vuitton SE",
        currency="EUR",
        exchange="Euronext Paris",
    ),
    "RMS.PA": MarketAssetMeta(
        symbol="RMS.PA",
        short_label="Hermes/爱马仕",
        display_name="Hermes International SCA",
        currency="EUR",
        exchange="Euronext Paris",
    ),
    "KER.PA": MarketAssetMeta(
        symbol="KER.PA",
        short_label="Kering/开云集团",
        display_name="Kering SA",
        currency="EUR",
        exchange="Euronext Paris",
    ),
}

_SUFFIX_CURRENCY: tuple[tuple[str, str], ...] = (
    (".SS", "CNY"),
    (".SH", "CNY"),
    (".SZ", "CNY"),
    (".HK", "HKD"),
    (".PA", "EUR"),
    (".DE", "EUR"),
    (".MI", "EUR"),
    (".AS", "EUR"),
    (".BR", "EUR"),
    (".MC", "EUR"),
    (".LS", "EUR"),
    (".SW", "CHF"),
    (".L", "GBp"),
    (".TO", "CAD"),
    (".AX", "AUD"),
    (".T", "JPY"),
)


def normalize_market_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def market_asset_meta(symbol: str) -> MarketAssetMeta | None:
    return _SYMBOL_META.get(normalize_market_symbol(symbol))


def default_currency_for_symbol(symbol: str) -> str:
    normalized = normalize_market_symbol(symbol)
    meta = market_asset_meta(normalized)
    if meta:
        return meta.currency
    for suffix, currency in _SUFFIX_CURRENCY:
        if normalized.endswith(suffix):
            return currency
    return ""


def _provider_currency_looks_wrong(symbol: str, provider_currency: Any, inferred_currency: str) -> bool:
    if not inferred_currency:
        return False
    currency = str(provider_currency or "").strip().upper()
    inferred = inferred_currency.upper()
    if not currency:
        return True
    if currency == inferred:
        return False
    normalized = normalize_market_symbol(symbol)
    # Most wrong cases here come from global fallbacks defaulting non-US symbols
    # to USD. Do not override real US tickers that have no exchange suffix.
    return currency == "USD" and "." in normalized


def enrich_market_quote(symbol: str, quote: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a quote copy with deterministic symbol name/currency metadata."""
    data = dict(quote or {})
    normalized = normalize_market_symbol(symbol or data.get("symbol") or "")
    if normalized and not data.get("symbol"):
        data["symbol"] = normalized

    meta = market_asset_meta(normalized)
    inferred_currency = default_currency_for_symbol(normalized)
    if _provider_currency_looks_wrong(normalized, data.get("currency"), inferred_currency):
        data["currency"] = inferred_currency
        data["currency_source"] = "symbol_metadata"
    elif not data.get("currency") and inferred_currency:
        data["currency"] = inferred_currency
        data["currency_source"] = "symbol_metadata"

    if meta:
        current_name = str(data.get("name") or "").strip()
        if not current_name or current_name.upper() == normalized:
            data["name"] = meta.display_name
        data.setdefault("display_name", meta.display_name)
        data.setdefault("short_label", meta.short_label)
        data.setdefault("exchange", meta.exchange)
    return data


def market_display_label(symbol: str, quote: Mapping[str, Any] | None = None) -> str:
    data = dict(quote or {})
    normalized = normalize_market_symbol(symbol or data.get("symbol") or "")
    meta = market_asset_meta(normalized)
    if meta:
        return f"{meta.short_label}({normalized})"
    name = str(data.get("name") or data.get("display_name") or "").strip()
    if name and name.upper() != normalized:
        return f"{name}({normalized})"
    return normalized or name or "market_data"
