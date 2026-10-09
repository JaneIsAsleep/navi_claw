import asyncio
import hashlib
import json
import ssl

import httpx
import pytest

from navi import feeds, feed_http
from navi.feed_http import FeedReadError, PublicBackend, PublicTransport, canonical_url, read_public
from navi.feeds import parse_feed
from navi.gateway import Gateway
from navi.web import create_app
from test_gateway import ROOT, Model
from test_feeds import RSS


@pytest.mark.parametrize('url', ['http://example.org/feed', 'https://user:pass@example.org/feed', 'https://example.org:444/feed', 'https://127.0.0.1/feed', 'https://[::1]/feed', 'https://10.0.0.1/feed', 'https://169.254.169.254/feed', 'https://example.org\\@127.0.0.1/feed'])
def test_custom_rejects_invalid_targets(url):
    with pytest.raises(ValueError):
        canonical_url(url)


def test_custom_canonical_keeps_case_sensitive_path_query():
    assert canonical_url('HTTPS://Example.ORG:443/Feed?Q=One#fragment') == 'https://example.org/Feed?Q=One'
    assert canonical_url('https://example.org/feed?Q=One') != canonical_url('https://example.org/Feed?Q=One')


class Stream:
    def __init__(self):
        self.sent, self.tls, self.reads = [], [], 0
    async def read(self, max_bytes, timeout=None):
        self.reads += 1
        return b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Type: text/xml\r\n\r\nok' if self.reads == 1 else b''
    async def write(self, buffer, timeout=None):
        self.sent.append(buffer)
    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        assert ssl_context.check_hostname and ssl_context.verify_mode == ssl.CERT_REQUIRED
        self.tls.append(server_hostname)
        return self
    async def aclose(self):
        pass
    def get_extra_info(self, key):
        return None


class Delegate:
    def __init__(self):
        self.hosts, self.stream = [], Stream()
    async def connect_tcp(self, host, port, **kwargs):
        self.hosts.append(host)
        return self.stream
    async def sleep(self, seconds):
        pass


async def test_dns_rebinding_pinned_ip_real_httpcore_host_tls_and_no_proxy(monkeypatch):
    calls = []
    async def changing_dns(host, port, **kwargs):
        calls.append(host)
        return [(2, 1, 6, '', ('93.184.216.34' if len(calls) == 1 else '127.0.0.1', port))]
    delegate = Delegate()
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:9')
    data, mime = await read_public('https://example.org/feed', transport=PublicTransport(PublicBackend(delegate, changing_dns)))
    assert data == b'ok' and mime == 'text/xml'
    assert calls == ['example.org'] and delegate.hosts == ['93.184.216.34']
    assert delegate.stream.tls == ['example.org']
    assert b'Host: example.org' in b''.join(delegate.stream.sent)
    assert b'Authorization:' not in b''.join(delegate.stream.sent) and b'Cookie:' not in b''.join(delegate.stream.sent)


@pytest.mark.parametrize('address', ['127.0.0.1', '192.168.1.2', '169.254.1.2', '::1', 'fe80::1', '224.0.0.1', '::ffff:127.0.0.1'])
async def test_every_dns_address_must_be_public(address):
    async def resolver(host, port, **kwargs):
        return [(2, 1, 6, '', ('93.184.216.34', port)), (2, 1, 6, '', (address, port))]
    delegate = Delegate()
    with pytest.raises(ValueError):
        await PublicBackend(delegate, resolver).connect_tcp('example.org', 443)
    assert not delegate.hosts


@pytest.mark.parametrize('status,body,headers,reason', [
    (302, b'', {'location':'https://example.org/new'}, '重定向'),
    (503, b'', {}, '503'),
    (200, b'x' * 2_000_001, {}, '2 MB'),
    (200, b'<svg/>', {'content-type':'image/svg+xml'}, '类型'),
])
async def test_public_reader_status_size_and_mime(status, body, headers, reason):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, content=body, headers=headers))
    with pytest.raises(FeedReadError, match=reason):
        await read_public('https://example.org/feed', mime_types={'image/png'} if '类型' == reason else None, transport=transport)


async def test_entire_public_read_timeout_includes_dns(monkeypatch):
    original = asyncio.timeout
    monkeypatch.setattr(feed_http.asyncio, 'timeout', lambda seconds: original(.02))
    async def slow_resolver(*args, **kwargs):
        await asyncio.sleep(10)
    delegate = Delegate()
    with pytest.raises(FeedReadError, match='超时'):
        await read_public('https://example.org/feed', transport=PublicTransport(PublicBackend(delegate, slow_resolver)))
    assert not delegate.hosts


def test_atom_relative_links_dates_and_invalid_entries():
    atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><title>Official Atom</title><entry><title>Newest</title><link href="/new"/><updated>2026-10-08T00:00:00Z</updated><summary>&lt;p&gt;Fact&lt;/p&gt;&lt;script&gt;hidden&lt;/script&gt;</summary></entry><entry><title>Missing date</title><link href="/old"/></entry></feed>'''
    rows = parse_feed(atom, 'custom-test', {'title':'Atom', 'url':'https://example.org/feed'})
    assert len(rows) == 1 and rows[0]['source_url'] == 'https://example.org/new'
    assert rows[0]['body'] == 'Fact'
    for invalid in (b'<html><body>Page</body></html>', b'<rss>', b'<rss><channel><item><title>Missing date</title><link>https://example.org/x</link></item></channel></rss>'):
        with pytest.raises((ValueError, feeds.ET.ParseError)):
            parse_feed(invalid, 'custom-test', {'title':'Atom', 'url':'https://example.org/feed'})


async def test_preview_confirm_revalidates_current_url_deduplicates_and_zero_model(tmp_path, monkeypatch):
    g = Gateway(ROOT, tmp_path, Model())
    reads = []
    async def reader(url):
        reads.append(url)
        return RSS, 'application/rss+xml'
    monkeypatch.setattr(feeds, 'read_public', reader)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(g)), base_url='http://testserver') as client:
        before = g.db.all('SELECT * FROM records')
        preview = await client.post('/api/feed-sources/preview', json={'url':'https://A.example.org/Feed'})
        assert preview.status_code == 200 and len(preview.json()['items']) == 1
        assert g.db.all('SELECT * FROM records') == before
        added = await client.post('/api/feed-sources', json={'url':'https://B.example.org/Feed','title':'中性官方来源'})
        row = added.json(); assert added.status_code == 200 and row['payload']['url'] == 'https://b.example.org/Feed'
        expected = 'custom-' + hashlib.sha256(b'https://b.example.org/Feed').hexdigest()[:24]
        assert row['id'] == 'source-' + expected
        repeated = await client.post('/api/feed-sources', json={'url':'https://B.example.org:443/Feed#fragment'})
        assert repeated.json()['id'] == row['id'] and len(g.db.all("SELECT * FROM records WHERE kind='source'")) == 1
        assert (await client.post('/api/feed-sources', json={'source_id':'python','url':'https://example.org/feed'})).status_code == 400
        assert (await client.post('/api/feed-sources/'+row['id']+'/toggle', json={})).json()['enabled'] is False
        assert (await client.post('/api/feed-sources/'+row['id']+'/toggle', json={})).json()['enabled'] is True
    assert reads == ['https://a.example.org/Feed', 'https://b.example.org/Feed', 'https://b.example.org/Feed']
    assert not g.db.all('SELECT * FROM usage') and not g.model.calls


async def test_failed_source_keeps_other_material_and_previous_success(tmp_path, monkeypatch):
    g = Gateway(ROOT, tmp_path, Model())
    for identity in ('python','nasa'):
        g.feeds.follow(identity)
    original = httpx.AsyncClient
    def response(request):
        return httpx.Response(503 if request.url.host == 'www.nasa.gov' else 200, content=RSS)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs))
    result = await g.feeds.refresh()
    assert len(result['items']) == 1 and len(result['errors']) == 1
    source = json.loads(g.require('records','source-nasa')['payload'])
    assert not source.get('last_success') and '503' in source['last_error']


async def test_refresh_concurrency_three_and_total_window_marks_unfinished(tmp_path, monkeypatch):
    g = Gateway(ROOT, tmp_path, Model())
    active = maximum = 0
    async def reader(url):
        nonlocal active, maximum
        active += 1; maximum = max(active, maximum)
        try:
            await asyncio.sleep(.025)
            return RSS, 'text/xml'
        finally:
            active -= 1
    monkeypatch.setattr(feeds, 'read_public', reader)
    for index in range(8):
        g.feeds.save_source('source-custom-'+str(index), {'source_id':'custom-'+str(index),'source_type':'custom','url':f'https://example.org/{index}','title':str(index),'enabled':True})
    original = asyncio.wait
    async def window(tasks, timeout):
        assert timeout == 60
        return await original(tasks, timeout=.04)
    monkeypatch.setattr(feeds.asyncio, 'wait', window)
    result = await g.feeds.refresh()
    assert maximum == 3 and result['checked_sources'] == 8
    assert len(result['items']) == 3 and len(result['errors']) == 5
    assert all('60 秒' in row['error'] for row in result['errors'])
