from dataclasses import dataclass
import json
from typing import Awaitable, Callable

import httpx


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

    @property
    def text(self):
        return "".join(block.get("text", "") for block in self.blocks if block.get("type") == "text")


class DeepSeek:
    async def chat(self, *, system: str, messages: list[dict], tools: list[dict], config: dict, api_key: str,
                   on_delta: Callable[[str], Awaitable[None]]) -> Reply:
        if not api_key:
            raise AgentError("请先在设置中配置 DeepSeek API 密钥", code='model_auth_error')
        is_messages = config["protocol"] == "messages"
        if is_messages:
            url = "https://api.deepseek.com/anthropic/v1/messages"
            headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
            payload = {"model": config["model"], "system": system, "messages": messages,
                       "max_tokens": int(config["max_output_tokens"]), "stream": True, "thinking": {"type": "disabled"}}
            if tools:
                payload["tools"] = tools
        else:
            url = "https://api.deepseek.com/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}"}
            payload = {"model": config["model"], "messages": [{"role": "system", "content": system}, *self.openai_messages(messages)],
                       "max_tokens": int(config["max_output_tokens"]), "stream": True, "stream_options": {"include_usage": True}}
            if tools:
                payload["tools"] = [{"type": "function", "function": {"name": tool["name"], "description": tool["description"], "parameters": tool["input_schema"]}} for tool in tools]
        blocks = {}
        text = ""
        input_tokens = output_tokens = 0
        uncached_input = cache_read = cache_write = 0
        finished = False
        try:
            async with httpx.AsyncClient(timeout=float(config["request_timeout"]), follow_redirects=False) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    if response.status_code != 200:
                        code = response.status_code
                        guidance = "，请检查密钥或账户余额" if code in {401, 402, 403} else "，请核对模型名称和接口协议" if code in {400, 404} else ""
                        error_code = 'model_rate_limited' if code == 429 else 'model_server_error' if code >= 500 else 'model_auth_error' if code in {401,403} else 'model_balance_error' if code == 402 else 'model_request_error'
                        raise AgentError(f"DeepSeek 返回 HTTP {code}{guidance}", code == 429 or code >= 500, error_code)
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            finished = True
                            break
                        event = json.loads(raw)
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
                                if block.get("type") in {"text", "tool_use"}:
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
                            elif kind == "message_delta":
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
            if not result:
                raise AgentError("DeepSeek 返回空结果", code='model_format_error')
            return Reply(result, input_tokens, output_tokens, cache_read, cache_write)
        except httpx.TimeoutException as error:
            raise AgentError("DeepSeek 请求超时", True, 'model_timeout') from error
        except httpx.RequestError as error:
            raise AgentError("DeepSeek 网络连接失败", True, 'model_connection_error') from error
        except (json.JSONDecodeError, KeyError, TypeError) as error:
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
