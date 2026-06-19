"""Market symbol resolution across A-shares, HK, global tickers, FX and futures.

The resolver is intentionally cache-first. Static aliases cover common global
assets and critical names; optional akshare loaders populate full A-share/HK
name tables into a local cache when available.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import time
from typing import Callable, Iterable


@dataclass(frozen=True)
class MarketSymbol:
    name: str
    symbol: str
    market: str
    source: str = "static"


STATIC_MARKET_ALIASES: dict[str, MarketSymbol] = {
    # A-share fixes and high-frequency names not guaranteed in older static maps
    "斯迪克": MarketSymbol("斯迪克", "300806", "CN", "static"),
    # China / HK indices
    "上证指数": MarketSymbol("上证指数", "000001.SS", "INDEX", "static"),
    "上证": MarketSymbol("上证指数", "000001.SS", "INDEX", "static"),
    "深证成指": MarketSymbol("深证成指", "399001.SZ", "INDEX", "static"),
    "创业板指": MarketSymbol("创业板指", "399006.SZ", "INDEX", "static"),
    "沪深300": MarketSymbol("沪深300", "000300.SS", "INDEX", "static"),
    "中证500": MarketSymbol("中证500", "000905.SS", "INDEX", "static"),
    "恒生指数": MarketSymbol("恒生指数", "^HSI", "INDEX", "static"),
    "恒指": MarketSymbol("恒生指数", "^HSI", "INDEX", "static"),
    # US/global indices
    "标普500": MarketSymbol("标普500", "^GSPC", "INDEX", "static"),
    "标普": MarketSymbol("标普500", "^GSPC", "INDEX", "static"),
    "纳斯达克": MarketSymbol("纳斯达克综合", "^IXIC", "INDEX", "static"),
    "纳指": MarketSymbol("纳斯达克综合", "^IXIC", "INDEX", "static"),
    "道琼斯": MarketSymbol("道琼斯工业指数", "^DJI", "INDEX", "static"),
    "道指": MarketSymbol("道琼斯工业指数", "^DJI", "INDEX", "static"),
    "罗素2000": MarketSymbol("罗素2000", "^RUT", "INDEX", "static"),
    "恐慌指数": MarketSymbol("VIX", "^VIX", "INDEX", "static"),
    "vix": MarketSymbol("VIX", "^VIX", "INDEX", "static"),
    "富时100": MarketSymbol("FTSE 100", "^FTSE", "INDEX", "static"),
    "德国dax": MarketSymbol("DAX", "^GDAXI", "INDEX", "static"),
    "dax": MarketSymbol("DAX", "^GDAXI", "INDEX", "static"),
    "法国cac": MarketSymbol("CAC 40", "^FCHI", "INDEX", "static"),
    "日经225": MarketSymbol("Nikkei 225", "^N225", "INDEX", "static"),
    # Europe equities / brands frequently asked by name rather than ticker.
    "lvmh": MarketSymbol("LVMH Moet Hennessy Louis Vuitton SE", "MC.PA", "EU", "static"),
    "路易威登": MarketSymbol("LVMH Moet Hennessy Louis Vuitton SE", "MC.PA", "EU", "static"),
    "路易斯威登": MarketSymbol("LVMH Moet Hennessy Louis Vuitton SE", "MC.PA", "EU", "static"),
    "louis vuitton": MarketSymbol("LVMH Moet Hennessy Louis Vuitton SE", "MC.PA", "EU", "static"),
    "爱马仕": MarketSymbol("Hermes International SCA", "RMS.PA", "EU", "static"),
    "开云集团": MarketSymbol("Kering SA", "KER.PA", "EU", "static"),
    "古驰": MarketSymbol("Kering SA", "KER.PA", "EU", "static"),
    # Crypto
    "比特币": MarketSymbol("比特币", "BTC-USD", "CRYPTO", "static"),
    "btc": MarketSymbol("Bitcoin", "BTC-USD", "CRYPTO", "static"),
    "以太坊": MarketSymbol("以太坊", "ETH-USD", "CRYPTO", "static"),
    "eth": MarketSymbol("Ethereum", "ETH-USD", "CRYPTO", "static"),
    "狗狗币": MarketSymbol("Dogecoin", "DOGE-USD", "CRYPTO", "static"),
    "sol": MarketSymbol("Solana", "SOL-USD", "CRYPTO", "static"),
    "索拉纳": MarketSymbol("Solana", "SOL-USD", "CRYPTO", "static"),
    # FX
    "美元人民币": MarketSymbol("USD/CNY", "CNY=X", "FX", "static"),
    "人民币汇率": MarketSymbol("USD/CNY", "CNY=X", "FX", "static"),
    "美元兑人民币": MarketSymbol("USD/CNY", "CNY=X", "FX", "static"),
    "美元指数": MarketSymbol("美元指数", "DX-Y.NYB", "FX", "static"),
    "欧元美元": MarketSymbol("EUR/USD", "EURUSD=X", "FX", "static"),
    "欧元兑美元": MarketSymbol("EUR/USD", "EURUSD=X", "FX", "static"),
    "美元日元": MarketSymbol("USD/JPY", "JPY=X", "FX", "static"),
    "英镑美元": MarketSymbol("GBP/USD", "GBPUSD=X", "FX", "static"),
    # Futures / commodities via Yahoo continuous futures
    "黄金": MarketSymbol("黄金期货", "GC=F", "FUTURES", "static"),
    "白银": MarketSymbol("白银期货", "SI=F", "FUTURES", "static"),
    "原油": MarketSymbol("WTI原油期货", "CL=F", "FUTURES", "static"),
    "wti": MarketSymbol("WTI原油期货", "CL=F", "FUTURES", "static"),
    "布伦特": MarketSymbol("布伦特原油期货", "BZ=F", "FUTURES", "static"),
    "铜": MarketSymbol("铜期货", "HG=F", "FUTURES", "static"),
    "天然气": MarketSymbol("天然气期货", "NG=F", "FUTURES", "static"),
    "玉米": MarketSymbol("玉米期货", "ZC=F", "FUTURES", "static"),
    "大豆": MarketSymbol("大豆期货", "ZS=F", "FUTURES", "static"),
}


def _cache_path() -> Path:
    root = Path(os.getenv("ARIA_CACHE_DIR") or (Path.home() / ".aria" / "cache"))
    return root / "market_universe.json"


def _load_cache(path: Path | None = None, *, max_age_seconds: int = 7 * 86400) -> list[MarketSymbol]:
    path = path or _cache_path()
    try:
        if not path.exists() or time.time() - path.stat().st_mtime > max_age_seconds:
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [
            MarketSymbol(
                name=str(item.get("name") or ""),
                symbol=str(item.get("symbol") or ""),
                market=str(item.get("market") or ""),
                source=str(item.get("source") or "cache"),
            )
            for item in payload.get("symbols", [])
            if item.get("name") and item.get("symbol")
        ]
    except Exception:
        return []


def _write_cache(symbols: Iterable[MarketSymbol], path: Path | None = None) -> None:
    path = path or _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "updated_at": int(time.time()),
            "symbols": [s.__dict__ for s in symbols if s.name and s.symbol],
        }
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _iter_static_symbols() -> list[MarketSymbol]:
    seen: set[tuple[str, str]] = set()
    out: list[MarketSymbol] = []
    for item in STATIC_MARKET_ALIASES.values():
        key = (item.name, item.symbol)
        if key not in seen:
            out.append(item)
            seen.add(key)
    return out


def _symbols_from_frame(frame, *, name_cols: tuple[str, ...], code_cols: tuple[str, ...], market: str, source: str) -> list[MarketSymbol]:
    out: list[MarketSymbol] = []
    try:
        columns = {str(c).lower(): c for c in frame.columns}
        name_col = next((columns[c.lower()] for c in name_cols if c.lower() in columns), None)
        code_col = next((columns[c.lower()] for c in code_cols if c.lower() in columns), None)
        if name_col is None or code_col is None:
            return []
        for _, row in frame.iterrows():
            name = str(row.get(name_col, "")).strip()
            code = str(row.get(code_col, "")).strip().upper()
            if not name or not code or code.lower() == "nan":
                continue
            if market == "HK":
                digits = re.sub(r"\D", "", code)
                if digits:
                    code = f"{digits.zfill(4)}.HK"
            out.append(MarketSymbol(name, code, market, source))
    except Exception:
        return []
    return out


def fetch_market_universe() -> list[MarketSymbol]:
    """Fetch A-share and HK symbol tables when akshare is available."""
    symbols = _iter_static_symbols()
    try:
        import akshare as ak
        try:
            a_df = ak.stock_info_a_code_name()
            symbols.extend(_symbols_from_frame(
                a_df,
                name_cols=("name", "证券简称", "股票简称", "名称"),
                code_cols=("code", "证券代码", "股票代码", "代码"),
                market="CN",
                source="akshare:a_code_name",
            ))
        except Exception:
            pass
        try:
            hk_df = ak.stock_hk_spot_em()
            symbols.extend(_symbols_from_frame(
                hk_df,
                name_cols=("名称", "股票简称", "name"),
                code_cols=("代码", "code", "股票代码"),
                market="HK",
                source="akshare:hk_spot",
            ))
        except Exception:
            pass
    except Exception:
        pass

    dedup: dict[tuple[str, str], MarketSymbol] = {}
    for item in symbols:
        dedup[(item.name.lower(), item.symbol.upper())] = item
    return list(dedup.values())


def ensure_market_universe(*, force: bool = False) -> list[MarketSymbol]:
    cached = [] if force else _load_cache()
    if cached:
        return _iter_static_symbols() + cached
    fetched = fetch_market_universe()
    _write_cache(fetched)
    return fetched


def resolve_market_mentions(
    text: str,
    *,
    limit: int = 6,
    load_universe: Callable[[], list[MarketSymbol]] | None = None,
) -> list[tuple[int, MarketSymbol]]:
    """Resolve named market assets mentioned in text, preserving positions."""
    if not text:
        return []
    low = text.lower()
    hits: list[tuple[int, MarketSymbol]] = []
    for alias, item in sorted(STATIC_MARKET_ALIASES.items(), key=lambda kv: -len(kv[0])):
        idx = low.find(alias.lower())
        if idx >= 0:
            hits.append((idx, item))

    def scan(items: Iterable[MarketSymbol]) -> None:
        for item in sorted(items, key=lambda s: -len(s.name)):
            if not item.name:
                continue
            idx = low.find(item.name.lower())
            if idx >= 0:
                hits.append((idx, item))

    if load_universe is not None:
        scan(load_universe())
    else:
        scan(_load_cache())
        market_words = "走势|预测|股价|股票|行情|趋势|技术面|基本面|涨跌|价格|市值|k线|图表|财报"
        if not hits and re.search(r"[\u4e00-\u9fff]", text) and re.search(market_words, text, re.I):
            scan(ensure_market_universe(force=True))

    ordered: list[tuple[int, MarketSymbol]] = []
    seen_symbols: set[str] = set()
    for idx, item in sorted(hits, key=lambda pair: (pair[0], -len(pair[1].name))):
        sym = item.symbol.upper()
        if sym in seen_symbols:
            continue
        ordered.append((idx, item))
        seen_symbols.add(sym)
        if len(ordered) >= limit:
            break
    return ordered


def resolve_market_symbol(text: str) -> str:
    hits = resolve_market_mentions(text, limit=1)
    return hits[0][1].symbol if hits else ""


def looks_like_unresolved_market_name(text: str) -> bool:
    """Heuristic guard: a Chinese name before market words should not inherit history."""
    if resolve_market_symbol(text):
        return False
    if not re.search(r"[\u4e00-\u9fff]{2,12}", text or ""):
        return False
    market_words = "走势|预测|股价|股票|行情|趋势|技术面|基本面|涨跌|价格|市值"
    return bool(re.search(rf"[\u4e00-\u9fffA-Za-z0-9]{{2,16}}(?:的)?(?:{market_words})", text or ""))
