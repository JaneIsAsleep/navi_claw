from dataclasses import dataclass, field
import json
from typing import Awaitable, Callable

import httpx

from .endpoints import request_url
from .web_search import plain_text, source_url


class AgentError(Exception):
    def __init__(self, message: str, retryable: bool = False, code: str = ''):
        super().__init__(message)
        self.retryable = retryable
        self.code = code


@dataclass
class Reply:
    blocks: list[dict]
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    native_search_completed: bool = False
    native_search_failed: bool = False
    native_sources: list[dict] = field(default_factory=list)

    @property
    def text(self):
        return "".join(block.get("text", "") for block in self.blocks if block.get("type") == "text")


class DeepSeek:
    async def chat(self, *, system: str, messages: list[dict], tools: list[dict], config: dict, api_key: str,
                   on_delta: Callable[[str], Awaitable[None]]) -> Reply:
        if not api_key:
            raise AgentError("请先在设置中为当前 Endpoint 配置 API 密钥", code='model_auth_error')
        is_messages = config["protocol"] == "messages"
        native = config.get("native_search_request", False)
        url = request_url(config)
        if is_messages:
            headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
            payload = {"model": config["model"], "system": system, "messages": messages,
                       "max_tokens": int(config["max_output_tokens"]), "stream": True, "thinking": {"type": "disabled"}}
            if tools:
                payload["tools"] = tools
        else:
            headers = {"Authorization": f"Bearer {api_key}"}
            payload = {"model": config["model"], "messages": [{"role": "system", "content": system}, *self.openai_messages(messages)],
                       "max_tokens": int(config["max_output_tokens"]), "stream": True, "stream_options": {"include_usage": True}}
            if tools:
                payload["tools"] = [{"type": "function", "function": {"name": tool["name"], "description": tool["description"], "parameters": tool["input_schema"]}} for tool in tools]
        if native:
            payload["thinking"] = {"type": "disabled"}
            payload["tools"] = [{"type": "deepseek_web_search", **({"name": "deepseek_web_search"} if is_messages else {})}]
        blocks = {}
        text = ""
        input_tokens = output_tokens = 0
        uncached_input = cache_read = cache_write = 0
        finished = False
        completed_search = failed_search = False
        sources = {}
        citations = {}

        def add_sources(items, target):
            for item in items:
                url = source_url(item.get("url"))
                if url and url not in target and len(target) < 10:
                    target[url] = {
                        "url": url,
                        "title": plain_text(item.get("title"), 180),
                        "snippet": plain_text(item.get("snippet"), 500),
                    }
        try:
            async with httpx.AsyncClient(timeout=float(config["request_timeout"]), follow_redirects=False) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    if response.status_code != 200:
                        code = response.status_code
                        guidance = "，请检查密钥或账户余额" if code in {401, 402, 403} else "，请核对 Endpoint、模型名称和接口协议" if code in {400, 404} else ""
                        error_code = 'model_rate_limited' if code == 429 else 'model_server_error' if code >= 500 else 'model_auth_error' if code in {401,403} else 'model_balance_error' if code == 402 else 'model_request_error'
                        if native and code in {400, 404, 422}:
                            # Read one bounded chunk solely to classify errors, never echo provider text.
                            raw = await anext(response.aiter_bytes(), b"")
                            detail = raw[:8192].lower()
                            if b"content exists risk" in detail or b"content_filter" in detail:
                                error_code = "model_content_filtered"
                            elif b"deepseek_web_search" in detail or (
                                b"tool" in detail and any(marker in detail for marker in (b"unsupported", b"not support", b"unknown"))
                            ):
                                error_code = "native_search_unsupported"
                        raise AgentError(f"DeepSeek 返回 HTTP {code}{guidance}", code == 429 or code >= 500, error_code)
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            finished = True
                            break
                        event = json.loads(raw)
                        if native and event.get("error"):
                            raise AgentError("原生搜索流式请求失败", code="native_search_error")
                        if is_messages:
                            kind = event.get("type")
                            if kind == "error":
                                raise AgentError("DeepSeek 流式请求返回错误", True, 'model_server_error')
                            if kind == "message_start":
                                usage = event.get("message", {}).get("usage", {})
                                uncached_input = usage.get("input_tokens", 0)
                                cache_read = usage.get("cache_read_input_tokens", 0)
                                cache_write = usage.get("cache_creation_input_tokens", 0)
                                input_tokens = uncached_input + cache_read + cache_write
                            elif kind == "content_block_start":
                                block = dict(event["content_block"])
                                if native and block.get("type") == "web_search_tool_result":
                                    items = block["content"]
                                    if not isinstance(items, list):
                                        raise TypeError()
                                    if any(item.get("type") == "web_search_tool_result_error" for item in items):
                                        failed_search = True
                                    else:
                                        completed_search = True
                                        add_sources((item for item in items if item.get("type") == "web_search_result"), sources)
                                if block.get("type") in {"text", "tool_use"}:
                                    if native:
                                        add_sources(block.get("citations", []), citations)
                                    blocks[event["index"]] = block
                            elif kind == "content_block_delta":
                                delta = event.get("delta", {})
                                block = blocks.get(event["index"])
                                if block is None:
                                    continue
                                if delta.get("type") == "text_delta":
                                    chunk = delta.get("text", "")
                                    block["text"] = block.get("text", "") + chunk
                                    await on_delta(chunk)
                                elif delta.get("type") == "input_json_delta":
                                    block["partial"] = block.get("partial", "") + delta.get("partial_json", "")
                                elif native and delta.get("type") == "citations_delta":
                                    add_sources([delta["citation"]], citations)
                            elif kind == "message_delta":
                                if native and event.get("delta", {}).get("stop_reason") == "refusal":
                                    raise AgentError("DeepSeek 内容审核拒绝了本次请求", code="model_content_filtered")
                                usage = event.get("usage", {})
                                output_tokens = usage.get("output_tokens", output_tokens)
                                uncached_input = usage.get("input_tokens", uncached_input)
                                cache_read = usage.get("cache_read_input_tokens", cache_read)
                                cache_write = usage.get("cache_creation_input_tokens", cache_write)
                                input_tokens = uncached_input + cache_read + cache_write
                            elif kind == "message_stop":
                                finished = True
                        else:
                            usage = event.get("usage") or {}
                            input_tokens = usage.get("prompt_tokens", input_tokens)
                            output_tokens = usage.get("completion_tokens", output_tokens)
                            for choice in event.get("choices", []):
                                delta = choice.get("delta", {})
                                if native:
                                    if choice.get("finish_reason") == "content_filter":
                                        raise AgentError("DeepSeek 内容审核拒绝了本次请求", code="model_content_filtered")
                                    progress = choice.get("server_tool_event") or {}
                                    calls = [call for step in delta.get("server_tool_calls", []) for call in step.get("calls", [])]
                                    if progress.get("type") == "call_completed":
                                        calls.append(progress["call"])
                                    for call in calls:
                                        if call.get("type") != "deepseek_web_search":
                                            continue
                                        if call.get("status") == "completed":
                                            completed_search = True
                                            add_sources(call.get("sources", []), sources)
                                        elif call.get("status") == "failed":
                                            failed_search = True
                                    add_sources(delta.get("annotations", []), citations)
                                chunk = delta.get("content") or ""
                                text += chunk
                                if chunk:
                                    await on_delta(chunk)
                                for call in delta.get("tool_calls", []):
                                    index = call["index"]
                                    block = blocks.setdefault(index, {"type": "tool_use", "id": "", "name": "", "partial": ""})
                                    block["id"] += call.get("id", "")
                                    block["name"] += call.get("function", {}).get("name", "")
                                    block["partial"] += call.get("function", {}).get("arguments", "")
            if not finished:
                raise AgentError("DeepSeek 连接在结果完成前中断", True, 'model_connection_error')
            result = [blocks[index] for index in sorted(blocks)]
            for block in result:
                if block.get("type") == "tool_use":
                    partial = block.pop("partial", "")
                    if partial:
                        block["input"] = json.loads(partial)
                    if not isinstance(block.get("input"), dict) or not block.get("id") or not block.get("name"):
                        raise AgentError("DeepSeek 返回了无效的工具调用", code='model_format_error')
            if text:
                result.insert(0, {"type": "text", "text": text})
            if not result and not (native and (completed_search or failed_search)):
                raise AgentError("DeepSeek 返回空结果", code='model_format_error')
            ordered = {**citations, **sources} if completed_search else {}
            native_sources = [{"number": number, **item} for number, item in enumerate(list(ordered.values())[:10], 1)]
            return Reply(result, input_tokens, output_tokens, cache_read, cache_write, completed_search, failed_search, native_sources)
        except httpx.TimeoutException as error:
            raise AgentError("DeepSeek 请求超时", True, 'model_timeout') from error
        except httpx.RequestError as error:
            raise AgentError("DeepSeek 网络连接失败", True, 'model_connection_error') from error
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as error:
            raise AgentError("DeepSeek 响应格式无效", code='model_format_error') from error

    @staticmethod
    def openai_messages(messages):
        result = []
        for message in messages:
            content = message["content"]
            if isinstance(content, str):
                result.append(message)
                continue
            texts = [block["text"] for block in content if block.get("type") == "text"]
            calls = [block for block in content if block.get("type") == "tool_use"]
            if calls:
                result.append({"role": "assistant", "content": "".join(texts) or None,
                               "tool_calls": [{"id": block["id"], "type": "function", "function": {"name": block["name"], "arguments": json.dumps(block["input"], ensure_ascii=False)}} for block in calls]})
            elif texts:
                result.append({"role": message["role"], "content": "".join(texts)})
            for block in content:
                if block.get("type") == "tool_result":
                    result.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": block["content"]})
        return result
