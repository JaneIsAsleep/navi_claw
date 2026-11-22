import asyncio
import ipaddress
import json
import re
from html import unescape
from urllib.parse import urlsplit

import httpx


SEARCH_PROVIDERS = {
    "bocha": ("博查 Bocha", "https://api.bocha.cn/v1/web-search", "BOCHA_API_KEY"),
    "brave": ("Brave Search", "https://api.search.brave.com/res/v1/web/search", "BRAVE_SEARCH_API_KEY"),
    "anysearch": ("AnySearch", "https://api.anysearch.com/v1/search", "ANYSEARCH_API_KEY"),
}
DEFAULT_PROVIDER = "bocha"
MAX_RESULTS = 5


def plain_text(value, limit):
    if not isinstance(value, str):
        return ""
    return " ".join(re.sub(r"<[^>]*>", "", unescape(value)).split())[:limit]


def source_url(value):
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\s\x00-\x1f\x7f<>\\]", value):
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
        if parts.scheme != "https" or not host or "." not in host or parts.username or "@" in parts.netloc:
            return None
        if host.endswith((".local", ".localhost", ".internal")):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        return value
    except ValueError:
        return None


def response_items(provider, payload):
    if provider == "brave":
        return payload.get("web", {}).get("results", [])
    code = payload.get("code")
    if code != (200 if provider == "bocha" else 0):
        if code in (401, 402, 403, 429):
            raise ValueError(search_error(code))
        raise ValueError("搜索服务报告请求失败，请在服务商后台检查配置")
    data = payload["data"]
    if provider == "bocha":
        return (data.get("webPages") or {}).get("value", [])
    return data["results"]


def search_error(code):
    if code in (401, 403):
        return "搜索服务拒绝了请求，请检查所选服务的密钥与权限"
    if code == 402:
        return "搜索服务余额或额度不足"
    if code == 429:
        return "搜索服务达到调用限制，请稍后再试"
    return f"搜索服务返回 HTTP {code}"


class WebSearch:
    async def search(self, provider, query, api_key):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 250 or any(ord(char) < 32 for char in query):
            raise ValueError("搜索关键词须为 1 至 250 字的单行文本")
        if provider not in SEARCH_PROVIDERS or not api_key:
            raise ValueError("请配置所选搜索服务的密钥")
        label, url, _ = SEARCH_PROVIDERS[provider]
        query = query.strip()
        headers = {"Accept": "application/json"}
        if provider == "brave":
            method = "GET"
            headers["X-Subscription-Token"] = api_key
            options = {"params": {"q": query, "count": MAX_RESULTS, "search_lang": "en", "result_filter": "web"}}
        else:
            method = "POST"
            headers["Authorization"] = f"Bearer {api_key}"
            options = {"json": {"query": query, **(
                {"count": MAX_RESULTS, "summary": True} if provider == "bocha"
                else {"max_results": MAX_RESULTS, "format": "json"}
            )}}
        try:
            async with asyncio.timeout(12), httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
                async with client.stream(method, url, headers=headers, **options) as response:
                    if response.status_code != 200:
                        raise ValueError(search_error(response.status_code))
                    body = bytearray()
                    async for chunk in response.aiter_bytes(65536):
                        body.extend(chunk)
                        if len(body) > 524288:
                            raise ValueError("搜索响应过大")
                    payload = json.loads(body)
            results = response_items(provider, payload)
            if not isinstance(results, list):
                raise TypeError()
        except (httpx.HTTPError, TimeoutError, UnicodeError):
            raise ValueError("搜索服务连接失败或超时") from None
        except (json.JSONDecodeError, AttributeError, KeyError, TypeError):
            raise ValueError("搜索服务返回了无法读取的数据") from None
        selected = []
        seen = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            url = source_url(item.get("url"))
            title = plain_text(item.get("name" if provider == "bocha" else "title"), 180)
            raw_snippet = (item.get("summary") or item.get("snippet")) if provider == "bocha" else item.get("description" if provider == "brave" else "snippet")
            snippet = plain_text(raw_snippet, 500)
            if not url or url in seen or not snippet:
                continue
            seen.add(url)
            selected.append({"number": len(selected) + 1, "title": title or urlsplit(url).hostname, "url": url, "snippet": snippet})
            if len(selected) == MAX_RESULTS:
                break
        return {"provider": label, "read_scope": "search_snippets_only", "results": selected}


def search_sources(result):
    return "\n".join(
        f"{item['number']}. [{urlsplit(item['url']).hostname}](<{item['url']}>)"
        for item in result["results"]
    )
