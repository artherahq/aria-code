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
    "四环生物": MarketSymbol("四环生物", "000518", "CN", "static"),
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


def is_offline() -> bool:
    """True 时跳过一切联网抓取，只用静态符号表。

    2026-08-19：fetch_market_universe() 会真的打 akshare（stock_info_a_code_name
    与 stock_hk_spot_em），既没有超时也没有开关。CI 上这两个请求会挂住，
    tests/test_aria_cli_core.py::test_send_message_visual_route_helper 因此撞
    pytest-timeout 的 60 秒上限而失败——测的明明是"自然语言路由到 /chart"这种
    纯本地逻辑，答案（Apple→AAPL）来自 market_detect.py 的静态映射，根本不需要
    联网，网络只是扫描全量宇宙时的副作用。

    默认行为不变（有网就抓）。设 ARIA_OFFLINE=1 即可让它立刻退回静态表，
    供 CI、离线环境和确定性测试使用。
    """
    return os.getenv("ARIA_OFFLINE", "").strip().lower() in {"1", "true", "yes", "on"}


def _fetch_timeout_seconds() -> float:
    """Wall-clock budget for one remote universe fetch.

    ``is_offline()`` documents that these akshare calls had neither a timeout
    nor a switch, so a slow or hanging endpoint stalled the caller
    indefinitely — on the routing hot path that reads as the CLI freezing.
    ARIA_OFFLINE=1 still skips the network entirely; this bounds the wait when
    the network *is* used.
    """
    try:
        value = float(os.getenv("ARIA_UNIVERSE_FETCH_TIMEOUT", "8") or 8)
    except (TypeError, ValueError):
        return 8.0
    return max(1.0, value)


def _call_with_timeout(fn, timeout: float):
    """Run *fn* with a wall-clock budget; return None if it overruns.

    The worker thread is a daemon and is abandoned on timeout — akshare offers
    no cancellation — but the caller is released instead of blocking forever.
    """
    import concurrent.futures

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(fn)
        try:
            return future.result(timeout=timeout)
        except Exception:
            return None
    finally:
        executor.shutdown(wait=False)


def fetch_market_universe() -> list[MarketSymbol]:
    """Fetch A-share and HK symbol tables when akshare is available."""
    symbols = _iter_static_symbols()
    if is_offline():
        return symbols
    budget = _fetch_timeout_seconds()
    try:
        import akshare as ak
        try:
            a_df = _call_with_timeout(ak.stock_info_a_code_name, budget)
            if a_df is None:
                raise TimeoutError("stock_info_a_code_name timed out")
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
            hk_df = _call_with_timeout(ak.stock_hk_spot_em, budget)
            if hk_df is None:
                raise TimeoutError("stock_hk_spot_em timed out")
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


# Guard against repeating an expensive remote refresh within one process.  The
# fetch pulls the full A-share + HK symbol universe over the network; when it
# fails or returns nothing, retrying it on the next message just stalls the REPL
# again for the same result.
_UNIVERSE_REFRESH_ATTEMPTED = False


def _universe_refresh_attempted() -> bool:
    return _UNIVERSE_REFRESH_ATTEMPTED


def _mark_universe_refresh_attempted() -> None:
    global _UNIVERSE_REFRESH_ATTEMPTED
    _UNIVERSE_REFRESH_ATTEMPTED = True


def reset_universe_refresh_guard() -> None:
    """Clear the once-per-process refresh guard (tests, explicit /refresh)."""
    global _UNIVERSE_REFRESH_ATTEMPTED
    _UNIVERSE_REFRESH_ATTEMPTED = False


def ensure_market_universe(*, force: bool = False) -> list[MarketSymbol]:
    cached = [] if force else _load_cache()
    if cached:
        return _iter_static_symbols() + cached
    _mark_universe_refresh_attempted()
    fetched = fetch_market_universe()
    _write_cache(fetched)
    return fetched


def _find_mention(low_text: str, needle: str) -> int:
    """Position of *needle* in lowercased text, or -1.

    ASCII needles must sit on word boundaries — plain substring matching
    turned "whether" into an ETH hit and "gateway" into GE (observed in the
    channels e2e drill). CJK names keep substring semantics: Chinese has no
    word boundaries, which is why find() was used originally.
    """
    n = str(needle or "").lower()
    if not n:
        return -1
    if re.fullmatch(r"[a-z0-9.\-]+", n):
        m = re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", low_text)
        return m.start() if m else -1
    return low_text.find(n)


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
        idx = _find_mention(low, alias)
        if idx >= 0:
            hits.append((idx, item))

    def scan(items: Iterable[MarketSymbol]) -> None:
        for item in sorted(items, key=lambda s: -len(s.name)):
            if not item.name:
                continue
            idx = _find_mention(low, item.name)
            if idx >= 0:
                hits.append((idx, item))

    if load_universe is not None:
        scan(load_universe())
    else:
        scan(_load_cache())
        market_words = "走势|预测|股价|股票|行情|趋势|技术面|基本面|涨跌|价格|市值|k线|图表|财报"
        if (
            not hits
            and re.search(r"[\u4e00-\u9fff]", text)
            and re.search(market_words, text, re.I)
            and not _universe_refresh_attempted()
        ):
            # Refresh only when the local cache cannot answer, and only once per
            # process.  This used to pass force=True, which skipped the 7-day
            # cache and re-downloaded the entire A-share + HK universe over the
            # network on *every* message containing a word like "行情" — a
            # multi-second synchronous stall on the routing hot path, repeated
            # even for messages such as "现在的行情呢" that name no company
            # at all and so can never be resolved by any refresh.
            _mark_universe_refresh_attempted()
            scan(ensure_market_universe())

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


def ambiguous_market_candidates(
    text: str,
    *,
    limit: int = 20,
    load_universe: Callable[[], list[MarketSymbol]] | None = None,
) -> list[tuple[int, str, list[MarketSymbol]]]:
    """Return same-name mentions that map to more than one market symbol.

    Different assets mentioned in one sentence are not considered ambiguous;
    candidates must start at the same position and have the same display name.
    """
    # Ambiguity checks run on every natural-language message, so they must stay
    # cache-only.  A missing cache must not turn an ordinary methodology
    # question into a slow network refresh.
    loader = load_universe or _load_cache
    hits = resolve_market_mentions(text, limit=limit, load_universe=loader)
    return _ambiguous_groups_from_hits(hits)


def _ambiguous_groups_from_hits(
    hits: Iterable[tuple[int, MarketSymbol]],
) -> list[tuple[int, str, list[MarketSymbol]]]:
    grouped: dict[tuple[int, str], list[MarketSymbol]] = {}
    display_names: dict[tuple[int, str], str] = {}
    for position, item in hits:
        normalized_name = re.sub(r"\s+", "", item.name).casefold()
        key = (position, normalized_name)
        grouped.setdefault(key, []).append(item)
        display_names[key] = item.name

    ambiguous: list[tuple[int, str, list[MarketSymbol]]] = []
    for key, candidates in grouped.items():
        unique: list[MarketSymbol] = []
        seen: set[str] = set()
        for candidate in candidates:
            symbol = candidate.symbol.upper()
            if symbol not in seen:
                unique.append(candidate)
                seen.add(symbol)
        if len(unique) > 1:
            ambiguous.append((key[0], display_names[key], unique))
    ambiguous.sort(key=lambda item: item[0])
    return ambiguous


def select_market_candidate(reply: str, candidates: Iterable[MarketSymbol]) -> MarketSymbol | None:
    """Resolve a short clarification reply to one of the offered candidates."""
    options = list(candidates)
    text = str(reply or "").strip()
    if not text:
        return None
    normalized = re.sub(r"\s+", "", text).casefold()
    for option in options:
        market_aliases = {
            "CN": {"a股", "沪深", "中国a股"},
            "HK": {"港股", "香港"},
            "US": {"美股", "美国"},
            "EU": {"欧股", "欧洲"},
        }.get(option.market.upper(), set())
        labels = {
            option.symbol,
            option.name,
            option.market,
            f"{option.name}{option.symbol}",
            f"{option.name}{option.market}",
            *market_aliases,
        }
        if normalized in {re.sub(r"\s+", "", value).casefold() for value in labels if value}:
            return option
    if text.isdigit():
        index = int(text) - 1
        return options[index] if 0 <= index < len(options) else None
    return None


def resolve_market_symbol(text: str) -> str:
    hits = resolve_market_mentions(text, limit=20)
    if _ambiguous_groups_from_hits(hits):
        return ""
    return hits[0][1].symbol if hits else ""


def looks_like_unresolved_market_name(text: str) -> bool:
    """Heuristic guard: a Chinese name before market words should not inherit history."""
    if resolve_market_symbol(text):
        return False
    if not re.search(r"[\u4e00-\u9fff]{2,12}", text or ""):
        return False
    market_words = "走势|预测|股价|股票|行情|趋势|技术面|基本面|涨跌|价格|市值"
    return bool(re.search(rf"[\u4e00-\u9fffA-Za-z0-9]{{2,16}}(?:的)?(?:{market_words})", text or ""))
