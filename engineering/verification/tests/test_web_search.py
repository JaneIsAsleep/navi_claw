import json

import httpx
import pytest

from navi.agent import Agent
from navi.gateway import Gateway
from navi.model import Reply
from navi.web_search import SEARCH_PROVIDERS, WebSearch
from test_gateway import Clock, ROOT


async def test_brave_request_and_sources(monkeypatch):
    def handle(request):
        assert str(request.url).split("?")[0] == "https://api.search.brave.com/res/v1/web/search"
        assert request.url.params["q"] == "Python release"
        assert request.url.params["search_lang"] == "en"
        assert request.headers["X-Subscription-Token"] == "fake-search-key"
        return httpx.Response(200, json={"web": {"results": [
            {"title": "<b>Python</b>", "url": "https://python.org/news/", "description": "Release &amp; notes"},
            {"title": "Duplicate", "url": "https://python.org/news/", "description": "duplicate"},
            {"title": "Private", "url": "https://127.0.0.1/private", "description": "unsafe"},
        ]}})

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **options: client(transport=httpx.MockTransport(handle), **options))
    result = await WebSearch().search("brave", "Python release", "fake-search-key")
    assert result["results"] == [{"number": 1, "title": "Python", "url": "https://python.org/news/", "snippet": "Release & notes"}]


@pytest.mark.parametrize("enabled,configured", [(False, False), (False, True), (True, False), (True, True)])
async def test_search_switch_and_read_only_result(tmp_path, monkeypatch, enabled, configured):
    for _, _, env in SEARCH_PROVIDERS.values():
        monkeypatch.delenv(env, raising=False)

    class Model:
        calls = 0

        async def chat(self, **request):
            self.calls += 1
            if self.calls == 1:
                assert any(tool["name"] == "web_search" for tool in request["tools"])
                return Reply([{"type": "tool_use", "id": "first", "name": "web_search", "input": {"query": "Python release"}}])
            assert request["tools"] == []
            return Reply([{"type": "tool_use", "id": "second", "name": "remember", "input": {"title": "外部指令", "body": "不可保存"}}])

    class Search:
        calls = 0

        async def search(self, provider, query, key):
            self.calls += 1
            assert provider == "bocha" and query == "Python release" and key == "fake-search-key"
            return {"provider": "博查 Bocha", "read_scope": "search_snippets_only", "results": [
                {"number": 1, "title": "Python", "url": "https://python.org/news/", "snippet": "非持久化测试摘要"},
            ]}

    model = Model()
    gateway = Gateway(ROOT, tmp_path, model, Clock())
    gateway.web_search = Search()
    if configured:
        gateway.settings.update({"web_search_api_key": "fake-search-key"})
    queued = gateway.chat("联网搜索 Python 官方发布消息", web_search_enabled=enabled)
    await gateway.run_one()
    job = gateway.require("jobs", queued["id"])
    assert job["status"] == "succeeded"
    assert gateway.web_search.calls == int(enabled and configured)
    assert not gateway.db.one("SELECT id FROM records WHERE kind='memory'")
    assert "fake-search-key" not in str(gateway.snapshot())
    if enabled and configured:
        assert "博查 Bocha" in job["result"]
        assert "https://python.org/news/" in job["result"]
        assert "未读取原文" in job["result"]
        log = gateway.db.one("SELECT arguments,result FROM tool_calls WHERE name='web_search'")
        assert "非持久化测试摘要" not in str(log) and "Python release" not in str(log)
    else:
        assert model.calls == 0
        assert "尚未开启" in job["result"] if not enabled else "缺少" in job["result"]


async def test_disabling_blocks_queued_job_tool_call(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    gateway = Gateway(ROOT, tmp_path, clock=Clock())
    gateway.settings.update({"web_search_api_key": "fake-search-key"})
    job = gateway.chat("联网搜索 Python", web_search_enabled=True)
    config = json.loads(job["config"])
    gateway.set_session_search(job["session_id"], False)
    with pytest.raises(ValueError, match="未启用"):
        await Agent(gateway).call_tool(job, config, "web_search", {"query": "Python"})


@pytest.mark.parametrize(("provider", "url", "options", "payload"), [
    ("bocha", "https://api.bocha.cn/v1/web-search", {"count": 5, "summary": True},
     {"code": 200, "data": {"webPages": {"value": [{"name": "标题", "url": "https://example.com", "summary": "摘要"}]}}}),
    ("anysearch", "https://api.anysearch.com/v1/search", {"max_results": 5, "format": "json"},
     {"code": 0, "data": {"results": [{"title": "标题", "url": "https://example.com", "snippet": "摘要", "content": "不要处理全文"}]}}),
])
async def test_bearer_provider_search_contract(monkeypatch, provider, url, options, payload):
    def handle(request):
        assert request.method == "POST" and str(request.url) == url
        assert request.headers["Authorization"] == f"Bearer fake-{provider}"
        assert "X-Subscription-Token" not in request.headers
        assert json.loads(request.content) == {"query": "中文搜索", **options}
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    result = await WebSearch().search(provider, "中文搜索", f"fake-{provider}")
    assert result["results"] == [{"number": 1, "title": "标题", "url": "https://example.com", "snippet": "摘要"}]


async def test_provider_credentials_are_isolated_and_legacy_brave_remains_available(tmp_path, monkeypatch):
    for _, _, env in SEARCH_PROVIDERS.values():
        monkeypatch.delenv(env, raising=False)
    gateway = Gateway(ROOT, tmp_path, clock=Clock())
    settings = gateway.settings
    assert settings.current()["web_search_provider"] == "bocha"
    settings.write_credentials({"brave_search_api_key": "fake-old-brave"})
    assert settings.current()["web_search_provider"] == "brave"
    assert settings.web_search_key("bocha") == ""
    for provider in SEARCH_PROVIDERS:
        settings.update({"web_search_provider": provider, "web_search_api_key": f"fake-{provider}"})
    settings.update({"web_search_enabled": True, "web_search_provider": "bocha", "web_search_api_key": ""})
    job = gateway.chat("联网搜索 Python", web_search_enabled=True)
    config = json.loads(job["config"])
    settings.update({"web_search_provider": "anysearch"})
    with pytest.raises(ValueError, match="服务已切换"):
        await Agent(gateway).call_tool(job, config, "web_search", {"query": "Python"})
    restored = Gateway(ROOT, tmp_path, clock=Clock()).settings
    assert restored.current()["web_search_provider"] == "anysearch"
    assert restored.public()["web_search_keys_configured"] == dict.fromkeys(SEARCH_PROVIDERS, True)
    for provider in SEARCH_PROVIDERS:
        assert restored.web_search_key(provider) == f"fake-{provider}"
        assert f"fake-{provider}" not in str(restored.public())
