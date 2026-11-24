import json

import httpx
import pytest

from navi.gateway import Gateway
from navi.model import AgentError, DeepSeek, Reply
from navi.web import create_app
from test_gateway import Clock, ROOT


SOURCE = {"number": 1, "title": "Python News", "url": "https://www.python.org/news/", "snippet": "Release news"}


@pytest.mark.parametrize("protocol", ["messages", "chat_completions"])
async def test_parse_native_search_receipt(monkeypatch, protocol):
    item = {**SOURCE, "type": "web_search_result"}
    if protocol == "messages":
        events = [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "server_tool_use", "name": "deepseek_web_search"}},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "web_search_tool_result", "content": [item]}},
            {"type": "content_block_start", "index": 2, "content_block": {"type": "text", "text": "回答"}},
            {"type": "message_stop"},
        ]
    else:
        call = {"type": "deepseek_web_search", "status": "completed", "sources": [item]}
        events = [
            {"choices": [{"delta": {}, "server_tool_event": {"type": "call_completed", "call": call}}]},
            {"choices": [{"delta": {"content": "回答", "server_tool_calls": [{"calls": [call]}]}}]},
        ]
    sse = "\n".join("data: " + json.dumps(event) for event in events) + "\ndata: [DONE]\n"

    def handle(request):
        payload = json.loads(request.content)
        assert payload["thinking"] == {"type": "disabled"}
        expected = {"type": "deepseek_web_search"}
        if protocol == "messages":
            expected["name"] = "deepseek_web_search"
        assert payload["tools"] == [expected]
        return httpx.Response(200, text=sse)

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))

    async def delta(_):
        pass

    reply = await DeepSeek().chat(system="", messages=[{"role": "user", "content": "查资料"}], tools=[],
                                 config={"protocol": protocol, "model": "deepseek-flash", "max_output_tokens": 100,
                                         "request_timeout": 2, "native_search_request": True},
                                 api_key="fake-model-key", on_delta=delta)
    assert reply.native_search_completed
    assert reply.native_sources == [SOURCE]
    assert reply.text == "回答"


class NativeModel:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []

    async def chat(self, **request):
        self.calls.append(bool(request["config"].get("native_search_request")))
        if self.calls[-1]:
            assert request["tools"] == []
            if self.behavior == "unsupported":
                raise AgentError("不支持", code="native_search_unsupported")
            return Reply([{"type": "text", "text": "回答"}],
                         native_search_completed=self.behavior in {"success", "empty"},
                         native_search_failed=self.behavior == "failed",
                         native_sources=[SOURCE] if self.behavior == "success" else [])
        if any(tool["name"] == "web_search" for tool in request["tools"]):
            return Reply([{"type": "tool_use", "id": "search", "name": "web_search", "input": {"query": "Python release"}}])
        return Reply([{"type": "text", "text": "普通回复"}])


class ThirdParty:
    calls = 0

    async def search(self, provider, query, key):
        self.calls += 1
        assert provider == "bocha" and key == "fake-search-key"
        return {"provider": "博查 Bocha", "read_scope": "search_snippets_only", "results": [SOURCE]}


@pytest.mark.parametrize(("behavior", "expected"), [
    ("success", "supported"), ("unsupported", "unsupported"), ("silent", "unverified"),
    ("empty", "unverified"), ("failed", "failed"),
])
async def test_settings_probe_and_connection_identity(tmp_path, behavior, expected):
    model = NativeModel(behavior)
    gateway = Gateway(ROOT, tmp_path, model, Clock())
    gateway.settings.update({"api_key": "fake-model-key"})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(gateway)), base_url="http://testserver") as client:
        response = await client.post("/api/settings/check-native-search", json={})
        assert response.status_code == 200
        await gateway.run_one()
        assert gateway.require("jobs", response.json()["id"])["result"] == expected
        assert (await client.get("/api/state")).json()["settings"]["native_search_check"] == expected
        assert model.calls == [True]
    gateway.settings.update({"protocol": "chat_completions"})
    assert gateway.settings.public()["native_search_check"] == "untested"
    gateway.settings.update({"protocol": "messages", "api_key": "fake-new-model-key"})
    assert gateway.settings.public()["native_search_check"] == "untested"


@pytest.mark.parametrize(("behavior", "expected", "calls", "fallback"), [
    ("success", "DeepSeek 原生搜索", [True], 0),
    ("empty", "未返回可用来源", [True], 0),
    ("silent", "未执行搜索", [True], 0),
    ("failed", "已改用第三方搜索", [True, False, False], 1),
    ("unsupported", "已改用第三方搜索", [True, False, False], 1),
])
async def test_native_routing_and_error_only_fallback(tmp_path, behavior, expected, calls, fallback):
    model = NativeModel(behavior)
    gateway = Gateway(ROOT, tmp_path, model, Clock())
    gateway.settings.update({"api_key": "fake-model-key", "web_search_provider": "bocha", "web_search_api_key": "fake-search-key"})
    config = gateway.job_config()
    gateway.settings.save_native_search_check(gateway.settings.native_search_identity(config), "supported")
    gateway.web_search = ThirdParty()
    job = gateway.chat("联网搜索 Python 发布消息", web_search_enabled=True)
    await gateway.run_one()
    assert expected in gateway.require("jobs", job["id"])["result"]
    assert model.calls == calls and gateway.web_search.calls == fallback
    assert not gateway.session()["web_search_enabled"]
    reopened = Gateway(ROOT, tmp_path, model, gateway.clock)
    assert reopened.require("sessions", job["session_id"])["web_search_enabled"]


async def test_mixed_chat_searches_separately_and_preserves_the_local_tool(tmp_path):
    class MixedModel:
        calls = []

        async def chat(self, **request):
            native = request["config"].get("native_search_request", False)
            self.calls.append(native)
            if native:
                assert request["tools"] == []
                assert request["messages"] == [{"role": "user", "content": "请联网搜索：Python release"}]
                return Reply([{"type": "text", "text": "搜索结果"}], native_search_completed=True, native_sources=[SOURCE])
            if len(self.calls) == 1:
                return Reply([{"type": "tool_use", "id": "search", "name": "web_search", "input": {"query": "Python release"}}])
            if len(self.calls) == 3:
                assert "https://www.python.org/news/" in str(request["messages"][-1]["content"])
                assert any(tool["name"] == "remember" for tool in request["tools"])
                return Reply([{"type": "tool_use", "id": "memory", "name": "remember", "input": {"title": "Python", "body": "我在学习 Python"}}])
            return Reply([{"type": "text", "text": "已保存记忆。"}])

    model = MixedModel()
    gateway = Gateway(ROOT, tmp_path, model, Clock())
    gateway.settings.update({"api_key": "fake-model-key"})
    config = gateway.job_config()
    gateway.settings.save_native_search_check(gateway.settings.native_search_identity(config), "supported")
    job = gateway.chat("联网搜索 Python 发布消息，同时记住我在学习 Python", web_search_enabled=True)
    await gateway.run_one()
    result = gateway.require("jobs", job["id"])["result"]
    assert model.calls == [False, True, False, False]
    assert "DeepSeek 原生搜索" in result
    assert len(gateway.db.all("SELECT id FROM records WHERE kind='memory'")) == 1
