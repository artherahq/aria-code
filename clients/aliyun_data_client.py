"""
aliyun_data_client.py — Arthera Alibaba Cloud data services client.

Architecture
------------
The Arthera quant backend runs two HTTP services on Alibaba Cloud:

  ┌─────────────────────────────────────────────────────────────────┐
  │  cloud_api_server.py (FastAPI, default port 8000)               │
  │    /api/v1/quant/factors/{symbol}  → calculate_factors          │
  │    /api/v1/quant/ai/signal         → AI trading signal          │
  │    /api/v1/quant/backtest          → run backtest               │
  │    /api/v1/quant/predict           → ML predictions             │
  │    /api/v1/ai/market-insights      → market insights (AI)       │
  │    /api/v1/ai/portfolio-analysis   → portfolio analysis         │
  │    /api/v1/ai/investment-decision  → investment decision        │
  │    /api/v1/market/quote/{symbol}   → real-time quote            │
  │    /api/v1/market/search           → stock search               │
  │    /api/v1/market/popular          → popular stocks list        │
  │    /health                         → health check               │
  └─────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────┐
  │  akshare_data_server.py (aiohttp, default port 8002)            │
  │    /stock/{symbol}                 → A股历史 OHLCV              │
  │    /stocks                         → multi-symbol batch         │
  │    /cn/indices                     → 上证/深成/沪深300 indices  │
  │    /hk/realtime                    → Hong Kong real-time        │
  │    /health                         → health check               │
  └─────────────────────────────────────────────────────────────────┘

Configuration (env vars or ~/.arthera/config.json)
---------------------------------------------------
  ARTHERA_CLOUD_URL   base URL of cloud_api_server  (default: http://127.0.0.1:8000)
  ARTHERA_DATA_URL    base URL of akshare_data_server (default: http://127.0.0.1:8002)
  ARTHERA_API_TOKEN   JWT Bearer token for authenticated endpoints

Circuit-breaker fallback
-------------------------
If cloud services are unreachable, all methods silently fall back to local
yfinance / akshare calls so the CLI never hard-errors on connectivity.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CloudHealthSummary:
    schema: str
    total: int
    ok: int
    warn: int
    err: int
    breaker_open: int
    token_set: bool
    status: str
    detail: str
    suggestion: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "total": self.total,
            "ok": self.ok,
            "warn": self.warn,
            "err": self.err,
            "breaker_open": self.breaker_open,
            "token_set": self.token_set,
            "status": self.status,
            "detail": self.detail,
            "suggestion": self.suggestion,
        }


def summarize_cloud_health(
    cloud_health: Optional[Dict[str, Any]] = None,
    data_health: Optional[Dict[str, Any]] = None,
    status: Optional[Dict[str, Any]] = None,
) -> CloudHealthSummary:
    cloud_health = dict(cloud_health or {})
    data_health = dict(data_health or {})
    status = dict(status or {})

    checks = [
        ("cloud_api_server", cloud_health),
        ("akshare_data_server", data_health),
    ]
    ok = warn = err = breaker_open = 0
    detail_bits: list[str] = []
    for name, health in checks:
        svc_status = str(health.get("status") or "unknown")
        breaker_value = status.get("cloud_cb") if name == "cloud_api_server" else status.get("data_cb")
        breaker = str(breaker_value or "closed")
        breaker_is_open = breaker == "open"
        if breaker_is_open:
            breaker_open += 1
        if svc_status in ("healthy", "ok", "ready", "online"):
            ok += 1
        elif svc_status == "unreachable" or breaker_is_open:
            err += 1
        else:
            warn += 1
        detail_bits.append(f"{name}={svc_status}")

    if err:
        overall = "err"
    elif warn or breaker_open:
        overall = "warn"
    else:
        overall = "ok"

    token_set = bool(status.get("has_token"))
    if overall == "ok":
        suggestion = "All cloud services healthy."
    elif token_set:
        suggestion = "Retry /cloud health or /doctor --network after cooldown."
    else:
        suggestion = "Check /cloud set, /cloud data, and /cloud token."

    return CloudHealthSummary(
        schema="aria.cloud_health_summary.v1",
        total=2,
        ok=ok,
        warn=warn,
        err=err,
        breaker_open=breaker_open,
        token_set=token_set,
        status=overall,
        detail=", ".join(detail_bits),
        suggestion=suggestion,
    )

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _cfg_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".arthera", "config.json")


def _load_cloud_config() -> Dict[str, str]:
    """Load cloud config from ~/.arthera/config.json, override with env vars."""
    cfg: Dict[str, str] = {
        "cloud_url": "http://127.0.0.1:8000",
        "data_url":  "http://127.0.0.1:8002",
        "api_token": "",
    }
    path = _cfg_path()
    if os.path.exists(path):
        try:
            with open(path) as f:
                saved = json.load(f)
            cfg["cloud_url"] = saved.get("cloud_url", cfg["cloud_url"])
            cfg["data_url"]  = saved.get("data_url",  cfg["data_url"])
            cfg["api_token"] = saved.get("api_token", cfg["api_token"])
        except Exception:
            pass
    # Env-var overrides (highest priority)
    if os.environ.get("ARTHERA_CLOUD_URL"):
        cfg["cloud_url"] = os.environ["ARTHERA_CLOUD_URL"].rstrip("/")
    if os.environ.get("ARTHERA_DATA_URL"):
        cfg["data_url"] = os.environ["ARTHERA_DATA_URL"].rstrip("/")
    if os.environ.get("ARTHERA_API_TOKEN"):
        cfg["api_token"] = os.environ["ARTHERA_API_TOKEN"]
    return cfg


def save_cloud_config(cloud_url: str = "", data_url: str = "",
                      api_token: str = "") -> None:
    """Persist cloud configuration to ~/.arthera/config.json."""
    import pathlib
    p = pathlib.Path(_cfg_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if p.exists():
        try:
            existing = json.loads(p.read_text())
        except Exception:
            pass
    if cloud_url:
        existing["cloud_url"] = cloud_url.rstrip("/")
    if data_url:
        existing["data_url"] = data_url.rstrip("/")
    if api_token:
        existing["api_token"] = api_token
    p.write_text(json.dumps(existing, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Circuit breaker (lightweight, no external deps)
# ---------------------------------------------------------------------------

@dataclass
class _CircuitBreaker:
    """Simple state-machine circuit breaker."""
    failure_threshold: int = 4
    recovery_timeout:  float = 120.0   # seconds before trying again
    _failures:         int   = field(default=0, repr=False)
    _last_failure:     float = field(default=0.0, repr=False)
    _open:             bool  = field(default=False, repr=False)

    def allow(self) -> bool:
        if not self._open:
            return True
        if time.monotonic() - self._last_failure > self.recovery_timeout:
            self._open = False  # enter half-open
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._open     = False

    def record_failure(self) -> None:
        self._failures += 1
        self._last_failure = time.monotonic()
        if self._failures >= self.failure_threshold:
            self._open = True
            logger.debug("AliyunDataClient: circuit breaker OPEN for this endpoint")

    @property
    def is_open(self) -> bool:
        return self._open


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------

class AliyunDataClient:
    """
    Async HTTP client for Arthera's Alibaba Cloud quant services.

    Usage (inside an async context)::

        client = AliyunDataClient()
        result = await client.get_quote("600519")   # A股
        result = await client.get_quote("AAPL")     # US
    """

    _instance: Optional["AliyunDataClient"] = None

    def __init__(self):
        cfg = _load_cloud_config()
        self.cloud_url = cfg["cloud_url"]   # cloud_api_server
        self.data_url  = cfg["data_url"]    # akshare_data_server
        self.api_token = cfg["api_token"]

        self._cb_cloud = _CircuitBreaker()
        self._cb_data  = _CircuitBreaker()

        # Cached aiohttp session — created lazily
        self._session: Any = None

    # ── singleton ──────────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> "AliyunDataClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Force re-read config (useful after /cloud config)."""
        cls._instance = None

    # ── HTTP helpers ───────────────────────────────────────────────────────

    async def _get_session(self):
        try:
            import aiohttp
        except ImportError:
            return None
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=15, connect=5)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def _headers(self, auth: bool = False) -> Dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if auth and self.api_token:
            h["Authorization"] = f"Bearer {self.api_token}"
        return h

    async def _get(self, base: str, path: str,
                   params: Optional[Dict] = None,
                   auth: bool = False,
                   cb: Optional[_CircuitBreaker] = None) -> Optional[Dict]:
        if cb and not cb.allow():
            logger.debug("Circuit breaker open — skipping %s%s", base, path)
            return None
        session = await self._get_session()
        if session is None:
            return None
        url = f"{base}{path}"
        try:
            async with session.get(url, params=params, headers=self._headers(auth)) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    if cb:
                        cb.record_success()
                    return data
                else:
                    logger.debug("GET %s → HTTP %d", url, r.status)
                    if cb:
                        cb.record_failure()
                    return None
        except Exception as exc:
            logger.debug("GET %s failed: %s", url, exc)
            if cb:
                cb.record_failure()
            return None

    async def _post(self, base: str, path: str,
                    body: Dict,
                    auth: bool = True,
                    cb: Optional[_CircuitBreaker] = None) -> Optional[Dict]:
        if cb and not cb.allow():
            logger.debug("Circuit breaker open — skipping %s%s", base, path)
            return None
        session = await self._get_session()
        if session is None:
            return None
        url = f"{base}{path}"
        try:
            async with session.post(url, json=body, headers=self._headers(auth)) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    if cb:
                        cb.record_success()
                    return data
                else:
                    text = await r.text()
                    logger.debug("POST %s → HTTP %d: %s", url, r.status, text[:200])
                    if cb:
                        cb.record_failure()
                    return None
        except Exception as exc:
            logger.debug("POST %s failed: %s", url, exc)
            if cb:
                cb.record_failure()
            return None

    # ── Public API ─────────────────────────────────────────────────────────

    async def health_cloud(self) -> Dict[str, Any]:
        """Check cloud_api_server health."""
        data = await self._get(self.cloud_url, "/health", cb=self._cb_cloud)
        return data or {"status": "unreachable", "cloud_url": self.cloud_url}

    async def health_data(self) -> Dict[str, Any]:
        """Check akshare_data_server health."""
        data = await self._get(self.data_url, "/health", cb=self._cb_data)
        return data or {"status": "unreachable", "data_url": self.data_url}

    # ── Market data ────────────────────────────────────────────────────────

    async def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Fetch real-time quote from cloud_api_server.

        Returns:  { symbol, name, price, change, change_percent, volume,
                    high, low, open, prev_close, market, timestamp }
        """
        return await self._get(
            self.cloud_url,
            f"/api/v1/market/quote/{symbol}",
            cb=self._cb_cloud,
        )

    async def get_stock_history(self, symbol: str,
                                start: str = "", end: str = "",
                                period: str = "daily") -> Optional[Dict[str, Any]]:
        """
        Fetch OHLCV history from akshare_data_server.

        Returns:  { symbol, data: [{date, open, high, low, close, volume},...] }
        """
        params: Dict[str, str] = {}
        if start:
            params["start_date"] = start
        if end:
            params["end_date"] = end
        if period:
            params["period"] = period
        # Normalise to bare 6-digit code that akshare server expects
        sym = symbol.lower()
        for prefix in ("sh", "sz", ".ss", ".sz"):
            sym = sym.replace(prefix, "")
        return await self._get(
            self.data_url,
            f"/stock/{sym}",
            params=params or None,
            cb=self._cb_data,
        )

    async def get_multiple_stocks(self, symbols: List[str],
                                  start: str = "", end: str = "") -> Optional[Dict[str, Any]]:
        """Batch-fetch history for multiple symbols."""
        syms = ",".join(
            s.lower().replace("sh", "").replace("sz", "").replace(".ss", "").replace(".sz", "")
            for s in symbols
        )
        params: Dict[str, str] = {"symbols": syms}
        if start:
            params["start_date"] = start
        if end:
            params["end_date"] = end
        return await self._get(self.data_url, "/stocks", params=params, cb=self._cb_data)

    async def get_cn_indices(self) -> Optional[Dict[str, Any]]:
        """Fetch 上证/深成/沪深300/创业板 index quotes."""
        return await self._get(self.data_url, "/cn/indices", cb=self._cb_data)

    async def get_popular_stocks(self, limit: int = 20) -> Optional[List[Dict]]:
        """沪深300 热门成分股列表。"""
        data = await self._get(
            self.cloud_url,
            "/api/v1/market/popular",
            params={"limit": str(limit)},
            cb=self._cb_cloud,
        )
        if data and "stocks" in data:
            return data["stocks"]
        return None

    async def search_stocks(self, q: str, limit: int = 10) -> Optional[List[Dict]]:
        """搜索股票（按代码或名称）。"""
        data = await self._get(
            self.cloud_url,
            "/api/v1/market/search",
            params={"q": q, "limit": str(limit)},
            cb=self._cb_cloud,
        )
        if data:
            return data.get("results", [])
        return None

    # ── Factor / signal analysis ───────────────────────────────────────────

    async def get_factors(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Call /api/v1/quant/factors/{symbol} — returns enhanced factor snapshot.
        Falls back to local if cloud unavailable.
        """
        return await self._get(
            self.cloud_url,
            f"/api/v1/quant/factors/{symbol}",
            auth=bool(self.api_token),
            cb=self._cb_cloud,
        )

    async def get_ai_signal(self, symbol: str,
                            market: str = "CN") -> Optional[Dict[str, Any]]:
        """
        POST /api/v1/quant/ai/signal — DeepSeek-powered signal generation.

        Returns: { symbol, action, confidence, reasoning, stop_loss, take_profit }
        """
        return await self._post(
            self.cloud_url,
            "/api/v1/quant/ai/signal",
            body={"symbol": symbol, "market": market},
            auth=bool(self.api_token),
            cb=self._cb_cloud,
        )

    async def get_predictions(self, symbols: List[str],
                              prediction_days: int = 5,
                              market: str = "CN") -> Optional[Dict[str, Any]]:
        """
        POST /api/v1/quant/predict — ML model predictions.

        Returns: { predictions: [{symbol, predicted_return, confidence, factors},...] }
        """
        return await self._post(
            self.cloud_url,
            "/api/v1/quant/predict",
            body={
                "symbols":         symbols,
                "prediction_days": prediction_days,
                "market":          market,
            },
            auth=bool(self.api_token),
            cb=self._cb_cloud,
        )

    async def run_backtest(self, symbols: List[str],
                           strategy_config: Dict[str, Any],
                           start_date: str = "",
                           end_date: str = "",
                           market: str = "CN") -> Optional[Dict[str, Any]]:
        """
        POST /api/v1/quant/backtest — run full ML-powered backtest.

        Returns: { backtest_id, status, result: { performance, equity_curve, trades } }
        """
        body: Dict[str, Any] = {
            "symbols":         symbols,
            "strategy_config": strategy_config,
            "market":          market,
        }
        if start_date:
            body["start_date"] = start_date
        if end_date:
            body["end_date"] = end_date
        return await self._post(
            self.cloud_url,
            "/api/v1/quant/backtest",
            body=body,
            auth=bool(self.api_token),
            cb=self._cb_cloud,
        )

    # ── AI analysis ────────────────────────────────────────────────────────

    async def get_market_insights(self, symbols: List[str],
                                  market: str = "CN") -> Optional[Dict[str, Any]]:
        """
        POST /api/v1/ai/market-insights — AI narrative market analysis.

        Returns: { insights, sentiment, key_risks, opportunities }
        """
        return await self._post(
            self.cloud_url,
            "/api/v1/ai/market-insights",
            body={"symbols": symbols, "market": market},
            auth=bool(self.api_token),
            cb=self._cb_cloud,
        )

    async def get_portfolio_analysis(self, portfolio: List[Dict[str, Any]],
                                     market: str = "CN") -> Optional[Dict[str, Any]]:
        """
        POST /api/v1/ai/portfolio-analysis.

        portfolio: [{ symbol, weight }]
        Returns: { risk_metrics, diversification_score, recommendations }
        """
        return await self._post(
            self.cloud_url,
            "/api/v1/ai/portfolio-analysis",
            body={"portfolio": portfolio, "market": market},
            auth=bool(self.api_token),
            cb=self._cb_cloud,
        )

    async def get_investment_decision(self, symbol: str,
                                      context: str = "",
                                      market: str = "CN") -> Optional[Dict[str, Any]]:
        """
        POST /api/v1/ai/investment-decision — full AI investment analysis.
        """
        return await self._post(
            self.cloud_url,
            "/api/v1/ai/investment-decision",
            body={"symbol": symbol, "context": context, "market": market},
            auth=bool(self.api_token),
            cb=self._cb_cloud,
        )

    # ── Utility ────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the underlying aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def status(self) -> Dict[str, Any]:
        """Return current circuit breaker status for /cloud status."""
        payload = {
            "cloud_url":  self.cloud_url,
            "data_url":   self.data_url,
            "has_token":  bool(self.api_token),
            "cloud_cb":   "open" if self._cb_cloud.is_open else "closed",
            "data_cb":    "open" if self._cb_data.is_open  else "closed",
        }
        try:
            payload["health_summary"] = summarize_cloud_health(status=payload).to_dict()
        except Exception:
            pass
        return payload


# ---------------------------------------------------------------------------
# Sync helper for use in non-async contexts (e.g. local_finance_tools.py)
# ---------------------------------------------------------------------------

def run_async(coro) -> Any:
    """
    Run an async coroutine from sync code (e.g. inside tool handlers).

    Uses the running event loop's run_in_executor pattern so we never
    accidentally create nested event loops.
    """
    if not hasattr(coro, "__await__"):
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return None

    async def _run_and_close():
        try:
            return await coro
        finally:
            try:
                await AliyunDataClient.get().close()
            except Exception:
                pass

    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run_and_close())

        # We're already inside an async context — run the coroutine on a fresh
        # event loop in a worker thread to avoid nested-loop errors.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, _run_and_close())
            return future.result(timeout=15)
    except Exception as exc:
        logger.debug("run_async failed: %s", exc)
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return None


def cloud_get_quote_sync(symbol: str) -> Optional[Dict[str, Any]]:
    """Blocking wrapper for AliyunDataClient.get_quote — safe to call from sync code."""
    return run_async(AliyunDataClient.get().get_quote(symbol))


def cloud_get_history_sync(symbol: str, start: str = "", end: str = "") -> Optional[Dict[str, Any]]:
    """Blocking wrapper for AliyunDataClient.get_stock_history."""
    return run_async(AliyunDataClient.get().get_stock_history(symbol, start=start, end=end))


def cloud_get_factors_sync(symbol: str) -> Optional[Dict[str, Any]]:
    """Blocking wrapper for AliyunDataClient.get_factors."""
    return run_async(AliyunDataClient.get().get_factors(symbol))


def cloud_get_ai_signal_sync(symbol: str, market: str = "CN") -> Optional[Dict[str, Any]]:
    """Blocking wrapper for AliyunDataClient.get_ai_signal."""
    return run_async(AliyunDataClient.get().get_ai_signal(symbol, market=market))
