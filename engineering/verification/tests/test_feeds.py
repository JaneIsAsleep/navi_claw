import json
from pathlib import Path

import httpx
import pytest

from navi.feeds import parse_feed
from navi.gateway import Gateway
from navi.model import Reply


ROOT = Path(__file__).resolve().parents[3]
RSS = b'''<rss><channel><item><title>Official fixed update</title><link>https://www.python.org/example</link><pubDate>Thu, 01 Oct 2026 12:00:00 GMT</pubDate><description>&lt;p&gt;Verified fixed source text&lt;/p&gt;</description></item></channel></rss>'''


class Model:
    async def chat(self, **request):
        return Reply([{"type": "text", "text": str(request["messages"][-1]["content"])}], 100, 30)


async def test_actual_reader_preserves_source_times_and_heartbeat_silence(tmp_path, monkeypatch):
    g = Gateway(ROOT, tmp_path, Model(), lambda: 1790850000)
    g.feeds.follow("python")
    original = httpx.AsyncClient
    def handle(request):
        assert "x-api-key" not in request.headers and "authorization" not in request.headers
        assert request.url.host == "blog.python.org"
        return httpx.Response(200, content=RSS)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    first = await g.feeds.refresh(); second = await g.feeds.refresh()
    assert first["items"] == second["items"]
    assert first["items"][0]["published_at"] == "2026-10-01T12:00:00+00:00"
    feed = next(row for row in g.snapshot()["records"] if row["kind"] == "feed")
    assert feed["payload"]["source_time"] != feed["payload"]["checked_at"]
    assert len([row for row in g.snapshot()["records"] if row["kind"] == "feed"]) == 1
    schedule = g.schedule({"title": "来源检查", "instruction": "检查官方更新", "kind": "heartbeat", "interval_seconds": 10, "next_due": g.clock()})
    g.tick(); await g.run_one(); g.deliver()
    assert "Official fixed update" in g.snapshot()["notifications"][0]["body"]
    before = len(g.db.all("SELECT * FROM usage"))
    g.clock = lambda: 1790850010
    g.tick(); await g.run_one()
    assert len(g.db.all("SELECT * FROM usage")) == before


async def test_source_failure_is_visible_and_never_treated_as_empty(tmp_path, monkeypatch):
    g = Gateway(ROOT, tmp_path, Model())
    g.feeds.follow("nasa")
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(lambda r: httpx.Response(503)), **kwargs))
    result = await g.feeds.refresh()
    assert result["errors"] and not result["items"]
    row = g.db.one("SELECT * FROM records WHERE kind='source'")
    assert "503" in json.loads(row["payload"])["last_error"]
    g.schedule({"title": "检查", "instruction": "检查来源", "kind": "heartbeat", "interval_seconds": 10, "next_due": g.clock()})
    g.tick(); await g.run_one()
    job = g.snapshot()["jobs"][0]
    assert job["status"] == "pending" and "失败" in job["error"]
    assert not g.snapshot()["notifications"]


def test_reader_rejects_unverifiable_feed_and_unknown_sources(tmp_path):
    with pytest.raises(ValueError): parse_feed(b'<rss><channel/></rss>', 'python')
    g = Gateway(ROOT, tmp_path, Model())
    with pytest.raises(ValueError): g.feeds.follow("unknown")
