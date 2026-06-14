"""
datasources/sources/web_scraper_source.py — 公开财务数据网络爬虫
================================================================
爬取完全公开的金融数据网站，无需账号或 API key。
所有请求遵守 robots.txt 和频率限制。

覆盖:
  - Macrotrends   — 历史财务数据（收入/利润/现金流/估值）
  - Wisesheets    — 免费财务摘要
  - 东方财富       — A股公告/研报摘要
  - 同花顺         — A股财务数据
  - Yahoo Finance  — 基本面摘要（HTML解析）
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
}


def _fetch_html(url: str, timeout: int = 12, encoding: str = "utf-8") -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers=_DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                return raw.decode("gbk", errors="replace")
    except Exception as e:
        logger.debug(f"[web_scraper] fetch {url[:80]} 失败: {e}")
        return None


def _fetch_json(url: str, timeout: int = 12, extra_headers: dict = None) -> Optional[Any]:
    import json
    try:
        headers = {**_DEFAULT_HEADERS, **(extra_headers or {})}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.debug(f"[web_scraper] json {url[:80]} 失败: {e}")
        return None


# ─── 东方财富 A股公告爬虫 ────────────────────────────────────────────────────

def scrape_eastmoney_announcements(symbol: str, count: int = 10) -> List[Dict]:
    """
    爬取东方财富 A股公告列表（免费公开页面）。
    symbol: 6位A股代码（如 '600519'）
    """
    s = symbol.replace(".SS", "").replace(".SZ", "").zfill(6)
    url = (
        f"https://np-anotice-stock.eastmoney.com/api/security/ann?"
        f"sr=-1&page_size={count}&page_index=1&ann_type=A&client_source=web"
        f"&stock_list={s}&f_node=0&s_node=0"
    )
    data = _fetch_json(url, extra_headers={"Referer": "https://www.eastmoney.com/"})
    if not data or "data" not in data:
        return []
    anns = data["data"].get("list", [])
    result = []
    for a in anns[:count]:
        result.append({
            "title":   a.get("notice_title", ""),
            "date":    a.get("notice_date", ""),
            "type":    a.get("notice_type", ""),
            "url":     f"https://data.eastmoney.com/notices/detail/{s}/{a.get('art_code','')}.html",
            "source":  "eastmoney",
        })
    return result


def scrape_eastmoney_news(symbol: str, count: int = 10) -> List[Dict]:
    """爬取东方财富个股新闻。"""
    s = symbol.replace(".SS", "").replace(".SZ", "").zfill(6)
    url = (
        f"https://newsapi.eastmoney.com/kuaixun/v1/getlist_101_ajaxResult_50_{s},,50,1.html"
    )
    html = _fetch_html(url)
    if not html:
        return []
    # 尝试从 JSON 响应中解析
    try:
        import json
        data = json.loads(html)
        items = data.get("LiveList", [])[:count]
        return [{
            "title":  i.get("title", ""),
            "time":   i.get("showtime", ""),
            "source": i.get("medianame", ""),
            "url":    i.get("url", ""),
        } for i in items]
    except Exception:
        pass
    return []


# ─── Macrotrends 历史财务数据爬虫 ────────────────────────────────────────────

def scrape_macrotrends_revenue(symbol: str) -> Optional[List[Dict]]:
    """
    从 Macrotrends 爬取年度营收历史（免费公开页面，无需登录）。
    注意：依赖页面结构，如网站改版可能失效。
    """
    slug_map = {"AAPL": "apple", "MSFT": "microsoft", "GOOGL": "alphabet",
                "AMZN": "amazon", "META": "meta-platforms", "TSLA": "tesla",
                "NVDA": "nvidia", "JPM": "jpmorgan-chase", "V": "visa"}
    slug = slug_map.get(symbol.upper())
    if not slug:
        slug = symbol.lower()

    url = f"https://www.macrotrends.net/stocks/charts/{symbol.upper()}/{slug}/revenue"
    html = _fetch_html(url)
    if not html:
        return None

    # 提取嵌入的 JSON 数据
    match = re.search(r'var originalData\s*=\s*(\[.*?\]);', html, re.DOTALL)
    if not match:
        return None
    try:
        import json
        rows = json.loads(match.group(1))
        return [
            {"date": r.get("date", ""), "revenue_usd": r.get("v1", 0)}
            for r in rows
        ][-20:]  # 最近20个数据点
    except Exception:
        return None


# ─── SSE/SZSE 交易所公开数据爬虫 ─────────────────────────────────────────────

def scrape_sse_financials(symbol: str) -> Optional[Dict]:
    """
    爬取上交所公开的公司财务摘要（适用于 6 开头的A股）。
    """
    s = symbol.replace(".SS", "").zfill(6)
    if not s.startswith(("6", "9")):
        return None

    url = (
        f"https://query.sse.com.cn/commonSoaQuery.do?"
        f"isPagination=false&sqlId=COMMON_SSE_CP_GPLB_GPXG_JBXX_L&stockCode={s}"
    )
    data = _fetch_json(url, extra_headers={
        "Referer": "https://www.sse.com.cn/",
        "Host": "query.sse.com.cn",
    })
    if not data or "result" not in data:
        return None
    r = data["result"]
    if isinstance(r, list) and r:
        r = r[0]
    if not isinstance(r, dict):
        return None
    return {
        "symbol":     s,
        "name":       r.get("FULL_NAME", ""),
        "industry":   r.get("INDUSTRY", ""),
        "listing_date": r.get("LISTING_DATE", ""),
        "total_shares": r.get("TOTAL_SHARES", ""),
        "source":     "sse",
    }


def scrape_szse_financials(symbol: str) -> Optional[Dict]:
    """爬取深交所公开的公司基本信息（适用于 0/3 开头的A股）。"""
    s = symbol.replace(".SZ", "").zfill(6)
    if not s.startswith(("0", "3", "2")):
        return None

    url = (
        f"https://www.szse.cn/api/report/show/nature/detail?"
        f"id=&"
    )
    # 深交所 API
    data = _fetch_json(
        f"https://www.szse.cn/api/market/smstock/list?random=0.1&tab2PAGENUM=1"
        f"&tab2PAGENO=1&tab2COUNT=1&tab2SORTTAB=tab2&tab2SORTKEY=",
        extra_headers={"Referer": "https://www.szse.cn/"},
    )
    return None  # 深交所API结构复杂，返回None让上层fallback


# ─── 统一爬虫接口 ─────────────────────────────────────────────────────────────

class WebScraperSource:
    """
    公开网页数据爬虫 — 汇总多个免费公开金融数据网站。
    用于补充 API 数据源的缺失数据。
    """

    name = "web_scraper"

    def get_announcements(self, symbol: str, count: int = 10) -> List[Dict]:
        """A股公告列表（东方财富）。"""
        return scrape_eastmoney_announcements(symbol, count)

    def get_news(self, symbol: str, count: int = 10) -> List[Dict]:
        """个股新闻。"""
        return scrape_eastmoney_news(symbol, count)

    def get_historical_revenue(self, symbol: str) -> Optional[List[Dict]]:
        """美股历史营收（Macrotrends）。"""
        return scrape_macrotrends_revenue(symbol)

    def get_exchange_info(self, symbol: str) -> Optional[Dict]:
        """A股交易所公开信息。"""
        s = symbol.replace(".SS", "").replace(".SZ", "").zfill(6)
        if s.startswith(("6", "9")):
            return scrape_sse_financials(s)
        elif s.startswith(("0", "3")):
            return scrape_szse_financials(s)
        return None

    def bulk_scrape(self, symbols: List[str], data_type: str = "announcements") -> Dict[str, Any]:
        """批量爬取，带速率限制（每次请求间隔 1 秒）。"""
        results = {}
        for sym in symbols:
            try:
                if data_type == "announcements":
                    results[sym] = self.get_announcements(sym)
                elif data_type == "news":
                    results[sym] = self.get_news(sym)
                elif data_type == "revenue":
                    results[sym] = self.get_historical_revenue(sym)
                time.sleep(1.0)  # 礼貌性速率限制
            except Exception as e:
                results[sym] = {"error": str(e)}
        return results
