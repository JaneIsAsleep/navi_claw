"""Bounded public HTTPS reads; DNS is checked at the actual TCP boundary."""
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend
from httpcore._backends.base import AsyncNetworkBackend


class FeedReadError(Exception):
    pass


def public_address(value):
    address = ipaddress.ip_address(value)
    mapped = getattr(address, 'ipv4_mapped', None)
    return address.is_global and not address.is_multicast and not address.is_reserved and (mapped is None or public_address(str(mapped)))


def canonical_url(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError('请输入不超过 2048 字符的 HTTPS 订阅地址')
    value = value.strip()
    try:
        parts = urlsplit(value)
        if parts.scheme != 'https' or not parts.hostname or parts.port not in (None, 443) or parts.username is not None or parts.password is not None or any(char.isspace() or ord(char) < 32 for char in value):
            raise ValueError()
        host = parts.hostname.encode('idna').decode('ascii').lower()
        if '%' in host or '\\' in host:
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address and not public_address(host):
            raise ValueError('来源必须使用公开互联网地址')
        authority = '[' + host + ']' if ':' in host else host
        return urlunsplit(('https', authority, parts.path, parts.query, ''))
    except (ValueError, UnicodeError) as error:
        raise ValueError('仅支持公开 HTTPS 443 地址，不接受认证信息、本机或内网地址') from error


class PublicBackend(AsyncNetworkBackend):
    def __init__(self, delegate=None, resolver=None):
        self.delegate = delegate or AutoBackend()
        self.resolver = resolver

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if port != 443:
            raise ValueError('仅支持 HTTPS 443')
        resolver = self.resolver or asyncio.get_running_loop().getaddrinfo
        try:
            rows = await resolver(host, port, type=socket.SOCK_STREAM)
        except OSError as error:
            raise FeedReadError('无法解析来源主机') from error
        addresses = sorted({row[4][0] for row in rows}, key=lambda value: ipaddress.ip_address(value).version)
        if not addresses or not all(public_address(value) for value in addresses):
            raise ValueError('来源 DNS 包含本机、内网或非公开地址')
        for index, address in enumerate(addresses):
            try:
                return await self.delegate.connect_tcp(address, port, timeout=timeout, local_address=local_address, socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout):
                if index == len(addresses) - 1:
                    raise

    async def sleep(self, seconds):
        await self.delegate.sleep(seconds)


class PublicTransport(httpx.AsyncHTTPTransport):
    def __init__(self, backend=None):
        super().__init__(trust_env=False, retries=0)
        # HTTPX exposes transport customization; httpcore owns the TCP/TLS split.
        # Keep the original request host for Host and TLS, connect only to vetted IPs.
        self._pool._network_backend = backend or PublicBackend()


async def read_public(url, max_bytes=2_000_000, mime_types=None, transport=None):
    url = canonical_url(url)
    try:
        async with asyncio.timeout(12):
            async with httpx.AsyncClient(transport=transport or PublicTransport(), trust_env=False, follow_redirects=False, timeout=12) as client:
                async with client.stream('GET', url, headers={'User-Agent': 'Navi/0.1 public reader'}) as response:
                    if 300 <= response.status_code < 400:
                        raise FeedReadError('地址发生重定向，请填写最终 HTTPS 地址')
                    if response.status_code != 200:
                        raise FeedReadError(f'来源返回 HTTP {response.status_code}')
                    mime = response.headers.get('content-type', '').split(';')[0].strip().lower()
                    if mime_types and mime not in mime_types:
                        raise FeedReadError('来源返回了不支持的内容类型')
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > max_bytes:
                            raise FeedReadError(f'来源超过 {max_bytes // 1_000_000} MB 读取限制')
                        data.extend(chunk)
                    return bytes(data), mime
    except (TimeoutError, httpx.TimeoutException) as error:
        raise FeedReadError('读取超时，请稍后重试') from error
    except httpx.HTTPError as error:
        raise FeedReadError('无法连接来源或通过 TLS 验证') from error
