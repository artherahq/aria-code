"""Provider 端点表与 API key 连通性测试 —— 从 aria_cli.py 抽出。

这两块（_PROVIDER_BASE_URLS 的 28 个端点 + _test_api_key 的 178 行分支）跟
aria_cli 的会话状态完全无关：一个是纯数据，一个是纯函数（给定 provider 与
key，发一次 HTTP 请求判断是否有效）。放在 7278 行的主文件里只是历史原因。

⚠️ 调用约束（跟 apps/cli/football_reports.py 同一套）：
apps/cli/commands/model_cmds.py 是 mixin，它用**裸名**调用 _test_api_key，
靠 aria_cli 的 _rebind_mixin_globals() 把方法 __globals__ 指向 aria_cli 命名
空间才能解析。所以本模块不能只是被 import，必须由 aria_cli 用
_rebind_module_function_globals() 绑回它自己的 globals。
"""

from __future__ import annotations

from typing import Dict, Tuple

__all__ = ["PROVIDER_BASE_URLS", "_PROVIDER_BASE_URLS", "_test_api_key"]


_PROVIDER_BASE_URLS: Dict[str, str] = {
    # ── 国际主流 ──────────────────────────────────────────────────────
    "deepseek":    "https://api.deepseek.com",
    "openai":      "https://api.openai.com",
    "anthropic":   "https://api.anthropic.com",
    "claude":      "https://api.anthropic.com",
    "groq":        "https://api.groq.com/openai",
    "together":    "https://api.together.xyz",
    "google":      "https://generativelanguage.googleapis.com/v1beta/openai",
    "gemini":      "https://generativelanguage.googleapis.com/v1beta/openai",
    "xai":         "https://api.x.ai/v1",
    "grok":        "https://api.x.ai/v1",
    "mistral":     "https://api.mistral.ai/v1",
    "cohere":      "https://api.cohere.ai/compatibility/v1",
    "perplexity":  "https://api.perplexity.ai",
    # ── 国内主流 ──────────────────────────────────────────────────────
    "dashscope":   "https://dashscope.aliyuncs.com/compatible-mode",
    "aliyun":      "https://dashscope.aliyuncs.com/compatible-mode",
    "siliconflow": "https://api.siliconflow.cn",
    "moonshot":    "https://api.moonshot.cn/v1",
    "zhipu":       "https://open.bigmodel.cn/api/paas/v4",
    "glm":         "https://open.bigmodel.cn/api/paas/v4",
    "baidu":       "https://qianfan.baidubce.com/v2",
    "ernie":       "https://qianfan.baidubce.com/v2",
    "qianfan":     "https://qianfan.baidubce.com/v2",
    "bytedance":   "https://ark.cn-beijing.volces.com/api/v3",
    "doubao":      "https://ark.cn-beijing.volces.com/api/v3",
    "ark":         "https://ark.cn-beijing.volces.com/api/v3",
    "minimax":     "https://api.minimax.chat/v1",
    "stepfun":     "https://api.stepfun.com/v1",
    "01ai":        "https://api.lingyiwanwu.com/v1",
    "yi":          "https://api.lingyiwanwu.com/v1",
}


def _test_api_key(provider: str, key: str) -> tuple:
    """Test if an API key is valid. Returns (ok: bool, message: str)."""
    import urllib.request as _ur
    import urllib.error as _ue
    import json as _json

    provider = provider.lower()

    try:
        # ── Anthropic (different auth scheme) ────────────────────────────────
        if provider in ("anthropic", "claude"):
            req = _ur.Request(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                },
            )
            with _ur.urlopen(req, timeout=8) as r:
                return True, f"✅ Anthropic  HTTP {r.status}  key 有效"

        # ── ZhiPu (JWT-based, just try /v1/models) ───────────────────────────
        if provider == "zhipu":
            base = _PROVIDER_BASE_URLS.get("zhipu", "https://open.bigmodel.cn/api/paas/v4")
            req = _ur.Request(
                base.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            try:
                with _ur.urlopen(req, timeout=8) as r:
                    return True, f"✅ 智谱 GLM  HTTP {r.status}  key 有效"
            except _ue.HTTPError as e:
                if e.code in (401, 403):
                    return False, f"❌ 智谱 GLM  HTTP {e.code}  key 无效"
                return True, f"✅ 智谱 GLM  HTTP {e.code}  可连接"

        # ── Standard OpenAI-compat LLM providers ─────────────────────────────
        if provider in _PROVIDER_BASE_URLS:
            base = _PROVIDER_BASE_URLS[provider].rstrip("/")
            # Avoid double /v1 when base already ends with /v1 or /v2 etc.
            if base.endswith(("/v1", "/v2", "/v3", "/v4", "/openai")):
                url = base + "/models"
            else:
                url = base + "/v1/models"
            req = _ur.Request(url, headers={"Authorization": f"Bearer {key}"})
            try:
                with _ur.urlopen(req, timeout=8) as r:
                    return True, f"✅ {provider.capitalize()}  HTTP {r.status}  key 有效"
            except _ue.HTTPError as e:
                if e.code in (401, 403):
                    return False, f"❌ {provider.capitalize()}  HTTP {e.code}  key 无效或已过期"
                return True, f"✅ {provider.capitalize()}  HTTP {e.code}  可连接"

        # ── Data services ─────────────────────────────────────────────────────
        if provider == "finnhub":
            url = f"https://finnhub.io/api/v1/quote?symbol=AAPL&token={key}"
            req = _ur.Request(url, headers={"User-Agent": "aria-code/1.0"})
            with _ur.urlopen(req, timeout=8) as r:
                body = _json.loads(r.read())
                if body.get("error"):
                    return False, f"❌ Finnhub  error: {body['error']}"
                price = body.get("c", "?")
                return True, f"✅ Finnhub  AAPL现价 ${price}  key 有效"

        if provider == "alphavantage":
            url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=AAPL&apikey={key}"
            req = _ur.Request(url, headers={"User-Agent": "aria-code/1.0"})
            with _ur.urlopen(req, timeout=10) as r:
                body = _json.loads(r.read())
                if "Information" in body:
                    return False, f"❌ Alpha Vantage  超出频率限制或 key 无效"
                if "Global Quote" in body and body["Global Quote"]:
                    price = body["Global Quote"].get("05. price", "?")
                    return True, f"✅ Alpha Vantage  AAPL=${price}  key 有效"
                return False, f"❌ Alpha Vantage  返回异常: {str(body)[:80]}"

        if provider == "polygon":
            url = f"https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/2024-01-09/2024-01-09?adjusted=true&sort=asc&limit=1&apiKey={key}"
            req = _ur.Request(url, headers={"User-Agent": "aria-code/1.0"})
            try:
                with _ur.urlopen(req, timeout=8) as r:
                    body = _json.loads(r.read())
                    if body.get("status") == "OK":
                        return True, f"✅ Polygon  {body.get('resultsCount', 0)} 条数据  key 有效"
                    return False, f"❌ Polygon  {body.get('status', 'unknown')}: {body.get('error', '')}"
            except _ue.HTTPError as e:
                if e.code == 403:
                    return False, f"❌ Polygon  HTTP 403  key 无效"
                return True, f"✅ Polygon  HTTP {e.code}  可连接"

        if provider == "fmp":
            url = f"https://financialmodelingprep.com/api/v3/quote/AAPL?apikey={key}"
            req = _ur.Request(url, headers={"User-Agent": "aria-code/1.0"})
            with _ur.urlopen(req, timeout=8) as r:
                body = _json.loads(r.read())
                if isinstance(body, list) and body:
                    price = body[0].get("price", "?")
                    return True, f"✅ FMP  AAPL=${price}  key 有效"
                if isinstance(body, dict) and "Error Message" in body:
                    return False, f"❌ FMP  {body['Error Message']}"
                return False, f"❌ FMP  返回异常: {str(body)[:80]}"

        if provider == "twelvedata":
            url = f"https://api.twelvedata.com/api_usage?apikey={key}"
            req = _ur.Request(url, headers={"User-Agent": "aria-code/1.0"})
            with _ur.urlopen(req, timeout=8) as r:
                body = _json.loads(r.read())
                if body.get("status") == "error":
                    return False, f"❌ TwelveData  {body.get('message', 'key 无效')}"
                used = body.get("current_usage", {}).get("daily", {}).get("used", "?")
                limit = body.get("current_usage", {}).get("daily", {}).get("limit", "?")
                return True, f"✅ TwelveData  今日已用 {used}/{limit}  key 有效"

        if provider == "newsapi":
            url = f"https://newsapi.org/v2/top-headlines?country=us&pageSize=1&apiKey={key}"
            req = _ur.Request(url, headers={"User-Agent": "aria-code/1.0"})
            try:
                with _ur.urlopen(req, timeout=8) as r:
                    body = _json.loads(r.read())
                    if body.get("status") == "ok":
                        return True, f"✅ NewsAPI  {body.get('totalResults', 0)} 条新闻  key 有效"
                    return False, f"❌ NewsAPI  {body.get('message', 'key 无效')}"
            except _ue.HTTPError as e:
                err_body = _json.loads(e.read().decode()) if e.read else {}
                return False, f"❌ NewsAPI  HTTP {e.code}  {err_body.get('message', '')}"

        if provider == "coingecko":
            url = "https://pro-api.coingecko.com/api/v3/ping"
            req = _ur.Request(url, headers={"x-cg-pro-api-key": key, "User-Agent": "aria-code/1.0"})
            try:
                with _ur.urlopen(req, timeout=8) as r:
                    return True, f"✅ CoinGecko Pro  key 有效"
            except _ue.HTTPError as e:
                if e.code == 401:
                    url2 = f"https://api.coingecko.com/api/v3/ping?x_cg_demo_api_key={key}"
                    req2 = _ur.Request(url2, headers={"User-Agent": "aria-code/1.0"})
                    try:
                        with _ur.urlopen(req2, timeout=8) as r2:
                            return True, f"✅ CoinGecko Demo  key 有效"
                    except Exception:
                        pass
                return False, f"❌ CoinGecko  HTTP {e.code}  key 无效"

        if provider == "tavily":
            import urllib.parse as _up
            data = _json.dumps({"api_key": key, "query": "test", "max_results": 1}).encode()
            req = _ur.Request(
                "https://api.tavily.com/search",
                data=data,
                headers={"Content-Type": "application/json", "User-Agent": "aria-code/1.0"},
            )
            try:
                with _ur.urlopen(req, timeout=10) as r:
                    return True, f"✅ Tavily  HTTP {r.status}  key 有效"
            except _ue.HTTPError as e:
                if e.code == 401:
                    return False, f"❌ Tavily  HTTP 401  key 无效"
                return True, f"✅ Tavily  HTTP {e.code}  可连接"

        if provider == "brave":
            req = _ur.Request(
                "https://api.search.brave.com/res/v1/web/search?q=AAPL&count=1",
                headers={"X-Subscription-Token": key, "User-Agent": "aria-code/1.0"},
            )
            try:
                with _ur.urlopen(req, timeout=8) as r:
                    return True, f"✅ Brave Search  HTTP {r.status}  key 有效"
            except _ue.HTTPError as e:
                if e.code == 401:
                    return False, f"❌ Brave Search  HTTP 401  key 无效"
                return True, f"✅ Brave Search  HTTP {e.code}  可连接"

        return False, f"⚠ 未知 provider '{provider}'，无法测试"

    except _ue.URLError as e:
        return False, f"❌ 网络错误: {e.reason}"
    except Exception as e:
        return False, f"❌ 测试失败: {e}"


# 对外的规范名；_PROVIDER_BASE_URLS 保留给既有裸名调用方。
PROVIDER_BASE_URLS = _PROVIDER_BASE_URLS
