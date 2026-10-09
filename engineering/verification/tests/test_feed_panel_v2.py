import asyncio
import base64
import hashlib
import json

import httpx
import pytest

from navi import feeds
from navi.agent import Agent
from navi.feed_http import FeedReadError
from navi.feeds import parse_feed
from navi.gateway import Gateway
from navi.model import Reply
from navi.storage import packed
from navi.web import create_app
from test_gateway import ROOT, Model


def published(g, identity, created, **extra):
    payload = {'title': identity, 'body': '真实材料正文', 'origin': 'generated', 'source_ids': ['material-a'], **extra}
    g.db.execute('INSERT INTO records VALUES(?,?,?,?,?)', (identity, 'feed', packed(payload), created, created))


def test_rss_full_content_images_priority_and_limits():
    data = ('<rss xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:media="http://search.yahoo.com/mrss/"><channel><item><title>Actual article</title><link>https://example.org/article</link><pubDate>Thu, 01 Oct 2026 12:00:00 GMT</pubDate><description>Short summary</description><content:encoded><![CDATA[<p>' + '事实' * 1500 + '</p><script>ignore()</script><img src="/body.png">]]></content:encoded><enclosure url="https://EXAMPLE.org:443/Photo.PNG#view" type="image/png" width="1200" height="800"/><media:thumbnail url="https://example.org/thumbnail.png"/></item></channel></rss>').encode()
    item = parse_feed(data, 'custom-a', {'title':'Actual feed', 'url':'https://example.org/feed'})[0]
    assert len(item['body']) == 2000 and 'Short summary' not in item['body'] and 'ignore' not in item['body']
    image = item['images'][0]
    assert image['source_url'] == 'https://example.org/Photo.PNG'
    assert image['asset_id'] == hashlib.sha256(image['source_url'].encode()).hexdigest()
    assert (image['width'], image['height'], image['alt']) == (1200, 800, 'Actual article')
    assert item['read_scope'] == 'rss'


def test_atom_xhtml_content_and_relative_image():
    data = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom</title><link href="/article"/><updated>2026-10-01T12:00:00Z</updated><summary>short</summary><content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>Full actual content</p><img src="/image.png" alt="Actual image"/></div></content></entry></feed>'
    item = parse_feed(data, 'custom-b', {'title':'Atom', 'url':'https://example.org/feed'})[0]
    assert item['body'] == 'Full actual content'
    assert item['images'][0]['source_url'] == 'https://example.org/image.png'


async def test_article_enrichment_is_bounded_and_failed_reads_remain_rss(tmp_path, monkeypatch):
    g = Gateway(ROOT, tmp_path, Model())
    rows = [{'id':f'm{i}', 'title':f'Article {i}', 'body':'RSS fact', 'source_url':f'https://example.org/{i}', 'source_id':f's{i // 3}', 'updated_at':100-i, 'read_scope':'rss', 'read_at':20, 'images':[]} for i in range(18)]
    seen, active, maximum = [], 0, 0
    async def reader(url, **kwargs):
        nonlocal active, maximum
        active += 1; maximum = max(maximum, active); seen.append(url)
        await asyncio.sleep(.01); active -= 1
        if url.endswith('/1'):
            raise FeedReadError('HTTP 404')
        if url.endswith('/2'):
            return b'<html><meta property="og:image" content="/only-image.png"><body>No article element</body></html>', 'text/html'
        return ('<html><meta property="og:image" content="/photo.png"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630"><article><p>' + '公开事实。' * 140 + '</p><script>ignore()</script></article></html>').encode(), 'text/html'
    monkeypatch.setattr(feeds, 'read_public', reader)
    result = await g.feeds.enrich(rows)
    assert len(result) == 12 and sum(len(row['body']) for row in result) <= 24000
    assert len(seen) == 5 and maximum == 3
    assert result[0]['read_scope'] == 'article' and len(result[0]['body']) >= 400
    assert result[0]['images'][0]['width'] == 1200
    assert result[1]['body'] == 'RSS fact' and result[1]['read_scope'] == 'rss'
    assert result[2]['read_scope'] == 'rss' and result[2]['images']
    assert all(row['read_scope'] == 'rss' for row in result[5:])
    assert not g.model.calls and not g.db.all('SELECT * FROM usage')


async def test_enrichment_total_30_second_window_and_per_source_three(tmp_path, monkeypatch):
    g = Gateway(ROOT, tmp_path, Model())
    rows = [{'id':f'm{i}', 'title':'Article', 'body':'fact', 'source_url':f'https://example.org/{i}', 'source_id':'one' if i < 6 else 'two', 'updated_at':100-i, 'images':[], 'read_scope':'rss'} for i in range(12)]
    original = asyncio.wait
    async def bounded(tasks, timeout):
        assert timeout == 30
        return await original(tasks, timeout=.01)
    async def slow(*args, **kwargs):
        await asyncio.sleep(10)
    monkeypatch.setattr(feeds.asyncio, 'wait', bounded)
    monkeypatch.setattr(feeds, 'read_public', slow)
    result = await g.feeds.enrich(rows)
    assert len(result) == 6 and all(row['read_scope'] == 'rss' for row in result)


async def test_pages_stable_with_equal_timestamps_new_batch_and_state_limit(tmp_path):
    g = Gateway(ROOT, tmp_path, Model())
    for index in range(65):
        published(g, f'item-{index:03}', 100 + index // 5)
    published(g, 'raw', 500, origin='rss_material')
    published(g, 'hidden', 500, display_state='hidden_legacy')
    g.record('goal', {'title':'Kept goal', 'state':'active'})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(g)), base_url='http://testserver') as client:
        first = (await client.get('/api/feed/items?limit=20')).json()
        assert len(first['items']) == 20 and first['next_cursor']
        published(g, 'new-batch', 1000)
        seen = [row['id'] for row in first['items']]
        cursor = first['next_cursor']
        while cursor:
            page = (await client.get('/api/feed/items', params={'limit':20, 'before':cursor})).json()
            seen.extend(row['id'] for row in page['items']); cursor = page['next_cursor']
        assert len(seen) == 65 and len(set(seen)) == 65 and 'new-batch' not in seen
        expected = [row['id'] for row in g.db.all("SELECT id FROM records WHERE id LIKE 'item-%' ORDER BY created DESC,id DESC")]
        assert seen == expected
        limited = (await client.get('/api/state?feed_limit=20')).json()
        full = (await client.get('/api/state')).json()
        assert len([r for r in limited['records'] if r['kind']=='feed']) == 20
        assert len(full['records']) > len(limited['records'])
        assert [r['id'] for r in full['records'] if r['kind']!='feed'] == [r['id'] for r in limited['records'] if r['kind']!='feed']
        assert not g.model.calls and not g.db.all('SELECT * FROM usage')


@pytest.mark.parametrize('params', [{'limit':'0'}, {'limit':'51'}, {'limit':'abc'}, {'before':'!bad'}, {'before':'a'*257}, {'before':base64.urlsafe_b64encode(b'["x","id"]').decode()}, {'before':base64.urlsafe_b64encode(b'[NaN,"id"]').decode()}, {'before':base64.urlsafe_b64encode(b'[1,""]').decode()}])
async def test_invalid_page_parameters_are_400(tmp_path, params):
    g = Gateway(ROOT, tmp_path, Model())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(g)), base_url='http://testserver') as client:
        assert (await client.get('/api/feed/items', params=params)).status_code == 400


async def test_media_only_published_assets_mime_signature_cache_and_zero_model(tmp_path, monkeypatch):
    g = Gateway(ROOT, tmp_path, Model())
    url = 'https://example.org/photo.png'; identity = hashlib.sha256(url.encode()).hexdigest()
    calls = []
    async def reader(value, **kwargs):
        calls.append((value, kwargs)); return b'\x89PNG\r\n\x1a\nactual-image', 'image/png'
    monkeypatch.setattr(feeds, 'read_public', reader)
    published(g, 'raw-material', 10, origin='rss_material', images=[{'asset_id':identity, 'source_url':url, 'alt':'Actual'}])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(g)), base_url='http://testserver') as client:
        assert (await client.get('/api/feed-media/' + identity)).status_code == 404
        assert not calls
        published(g, 'generated', 11, images=[{'asset_id':identity, 'source_url':url, 'alt':'Actual'}])
        response = await client.get('/api/feed-media/' + identity)
        assert response.status_code == 200 and response.content.startswith(b'\x89PNG')
        assert response.headers['cache-control'] == 'private, max-age=86400'
        assert response.headers['content-type'] == 'image/png' and response.headers['x-content-type-options'] == 'nosniff'
        assert calls[0][1]['max_bytes'] == 5_000_000
        async def html(value, **kwargs):
            return b'<html>pretending to be a picture</html>', 'image/png'
        monkeypatch.setattr(feeds, 'read_public', html)
        assert (await client.get('/api/feed-media/' + identity)).status_code == 502
        assert (await client.get('/api/feed-media/' + 'x'*64)).status_code == 404
    assert not g.model.calls and not g.db.all('SELECT * FROM usage')


async def test_media_concurrency_at_most_three(tmp_path, monkeypatch):
    g = Gateway(ROOT, tmp_path, Model())
    active, maximum = 0, 0
    async def reader(value, **kwargs):
        nonlocal active, maximum
        active += 1; maximum = max(maximum, active)
        await asyncio.sleep(.01); active -= 1
        return b'GIF89aactual', 'image/gif'
    monkeypatch.setattr(feeds, 'read_public', reader)
    identities = []
    for index in range(8):
        url = f'https://example.org/{index}.gif'; identity = hashlib.sha256(url.encode()).hexdigest(); identities.append(identity)
        published(g, f'generated-{index}', index, images=[{'asset_id':identity, 'source_url':url, 'alt':'Actual'}])
    await asyncio.gather(*(g.feeds.media(identity) for identity in identities))
    assert maximum == 3


def test_feed_reference_revalidated_and_uses_persisted_body_source_ids(tmp_path):
    g = Gateway(ROOT, tmp_path, Model())
    published(g, 'generated', 20, source_ids=['actual-material'])
    job = g.chat('讨论引用事实', references=[{'id':'generated', 'kind':'feed', 'title':'forged', 'body':'forged', 'source_ids':['forged']}])
    reference = g.messages(job['session_id'])[0]['references'][0]
    assert reference['body'] == '真实材料正文' and reference['source_ids'] == ['actual-material']
    g.db.execute("UPDATE records SET payload=json_set(payload,'$.display_state','hidden_legacy') WHERE id='generated'")
    before = {table:len(g.db.all('SELECT * FROM '+table)) for table in ('jobs','messages','sessions')}
    with pytest.raises(ValueError, match='引用'):
        g.chat('讨论已失效引用', references=[{'id':'generated', 'kind':'feed', 'title':'title', 'body':'body'}], new_session=True)
    assert before == {table:len(g.db.all('SELECT * FROM '+table)) for table in before}


async def test_model_can_choose_icon_but_images_bound_to_verified_source_ids(tmp_path, monkeypatch):
    g = Gateway(ROOT, tmp_path, Model())
    agent = Agent(g)
    image = {'asset_id':'a'*64, 'source_url':'https://example.org/actual.png', 'alt':'Actual'}
    checked = {'checked_at':20, 'items':[{'id':'actual', 'title':'Actual', 'body':'事实'*800, 'source':'Actual feed', 'source_url':'https://example.org/article', 'read_scope':'article', 'read_at':19, 'images':[image]}, {'id':'unrelated', 'title':'Other', 'body':'Other', 'images':[{'asset_id':'b'*64,'source_url':'https://example.org/other.png'}]}]}
    async def tool(*args):
        return checked
    async def structured(job, config, instruction, validate):
        assert '140' in instruction and 'read_scope' in instruction
        item = {'title':'Actual summary', 'body':'公开事实。'*40+'\n\n'+'学习意义。'*20, 'source_ids':['actual'], 'icon_key':'code', 'images':[{'source_url':'https://evil.example/invented.png'}]}
        validate({'items':[item]})
        return {'items':[item]}
    monkeypatch.setattr(agent, 'call_tool', tool)
    monkeypatch.setattr(agent, 'structured', structured)
    result = await agent.daily_content({'kind':'feed'}, {'feed_brief':'公开变化'})
    assert result['items'][0]['images'] == [image]
    assert result['items'][0]['icon_key'] == 'code'
    assert result['items'][0]['sources'][0]['read_scope'] == 'article'


async def test_invalid_source_group_id_repair_gets_actual_allowed_material_ids(tmp_path, monkeypatch):
    class RepairModel(Model):
        async def chat(self, **request):
            self.calls.append(request)
            if len(self.calls) == 1:
                identity = 'source-group'
            else:
                assert 'rss-actual-material' in request['messages'][-1]['content']
                assert '1 至 5' in request['messages'][-1]['content']
                identity = 'rss-actual-material'
            return Reply([{'type':'text', 'text':packed({'items':[{'title':'来源变化', 'body':'真实事实。', 'source_ids':[identity]}]})}], 100, 40)
    g = Gateway(ROOT, tmp_path, RepairModel())
    g.settings.update({'feed_brief':'公开来源变化'})
    job = g.generate_feed()
    agent = Agent(g)
    async def tool(*args):
        return {'checked_at':20, 'items':[{'id':'rss-actual-material','source_id':'source-group','title':'Actual article','body':'Actual fact'}]}
    monkeypatch.setattr(agent, 'call_tool', tool)
    result = await agent.daily_content(job, g.job_config())
    assert result['items'][0]['source_ids'] == ['rss-actual-material']
    assert len(g.model.calls) == 2
