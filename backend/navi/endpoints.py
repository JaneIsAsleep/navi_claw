from urllib.parse import urlsplit

import httpx


OFFICIAL_ORIGIN = "https://api.deepseek.com"
OFFICIAL_URLS = {
    "messages": f"{OFFICIAL_ORIGIN}/anthropic/v1/messages",
    "chat_completions": f"{OFFICIAL_ORIGIN}/chat/completions",
}
ROUTE_SUFFIXES = {"messages": "messages", "chat_completions": "chat/completions"}


def validate_endpoint(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("请填写两种协议的完整 Endpoint URL")
    value = value.strip()
    if len(value) > 2048 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char in "\\?#" for char in value):
        raise ValueError("Endpoint URL 无效，不能包含空格、查询参数或片段")
    try:
        parts = urlsplit(value)
        if not parts.hostname or "@" in parts.netloc or "%" in parts.netloc or parts.port == 0:
            raise ValueError()
        endpoint = httpx.URL(value)
        if endpoint.scheme != "https" and not (endpoint.scheme == "http" and endpoint.host in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError()
    except (ValueError, httpx.InvalidURL) as error:
        raise ValueError("请输入 HTTPS Endpoint URL，本机回环地址可使用 HTTP，不要在 URL 中填写凭据") from error
    return value


def legacy_endpoint_urls(endpoint):
    prefix = endpoint.rstrip("/")
    for suffix in ("/v1/messages", "/chat/completions"):
        if prefix.endswith(suffix):
            if suffix == "/v1/messages":
                prefix = prefix.removesuffix("/messages")
            else:
                prefix = prefix.removesuffix(suffix)
            break
    prefix += "/" if urlsplit(prefix).path.endswith("/v1") else "/v1/"
    urls = {protocol: prefix + suffix for protocol, suffix in ROUTE_SUFFIXES.items()}
    for protocol, suffix in {"messages": "/v1/messages", "chat_completions": "/chat/completions"}.items():
        if endpoint.rstrip("/").endswith(suffix):
            urls[protocol] = endpoint.rstrip("/")
    return urls


def request_url(config):
    protocol = config["protocol"]
    if config.get("endpoint_urls"):
        return config["endpoint_urls"][protocol]
    if config.get("endpoint_url"):
        return legacy_endpoint_urls(config["endpoint_url"])[protocol]
    return OFFICIAL_URLS[protocol]
