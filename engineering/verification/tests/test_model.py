import json

import httpx
import pytest

from navi.config import DEFAULTS
from navi.model import AgentError, DeepSeek


async def test_messages_stream_parses_tool_fragments_and_usage(monkeypatch):
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 20, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 10}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "检查"}},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "t1", "name": "check_updates", "input": {}}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{}"}},
        {"type": "message_delta", "usage": {"output_tokens": 7}}, {"type": "message_stop"},
    ]
    original = httpx.AsyncClient
    captured = []
    def handle(request):
        assert request.url.path == "/anthropic/v1/messages"
        assert request.headers["x-api-key"] == "test-key"
        return httpx.Response(200, text="\n\n".join("data: " + json.dumps(event) for event in events))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    async def delta(value): captured.append(value)
    reply = await DeepSeek().chat(system="测试", messages=[{"role": "user", "content": "检查"}], tools=[], config=DEFAULTS, api_key="test-key", on_delta=delta)
    assert reply.blocks[1]["input"] == {}
    assert reply.input_tokens == 60 and reply.output_tokens == 7
    assert reply.cache_read_tokens == 30 and reply.cache_write_tokens == 10
    assert captured == ["检查"]


async def test_missing_key_and_http_failure_never_become_success(monkeypatch):
    async def delta(value): pass
    request = dict(system="测试", messages=[], tools=[], config=DEFAULTS, on_delta=delta)
    with pytest.raises(AgentError): await DeepSeek().chat(**request, api_key="")
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(lambda request: httpx.Response(401, text="private provider body")), **kw))
    with pytest.raises(AgentError) as error: await DeepSeek().chat(**request, api_key="test-key")
    assert "401" in str(error.value)
    assert "test-key" not in str(error.value) and "private" not in str(error.value)


async def test_openai_stream_and_tool_result_translation(monkeypatch):
    original = httpx.AsyncClient
    def handle(request):
        assert request.url.path == "/chat/completions"
        payload = json.loads(request.content)
        assert payload["messages"][-1]["role"] == "tool"
        events = [{"choices": [{"delta": {"content": "真实协议解析"}}]}, {"choices": [], "usage": {"prompt_tokens": 30, "completion_tokens": 5}}]
        return httpx.Response(200, text="\n\n".join("data: " + json.dumps(event) for event in events) + "\n\ndata: [DONE]\n\n")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    async def delta(value): pass
    messages = [{"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "check_updates", "input": {}}]}, {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "实际事项"}]}]
    reply = await DeepSeek().chat(system="测试", messages=messages, tools=[], config={**DEFAULTS, "protocol": "chat_completions"}, api_key="test-key", on_delta=delta)
    assert reply.text == "真实协议解析" and reply.input_tokens == 30
