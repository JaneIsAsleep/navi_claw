from datetime import datetime
from email.utils import parsedate_to_datetime
import asyncio
import hashlib
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

import httpx

from .feed_http import FeedReadError, canonical_url, read_public
from .storage import packed


SOURCES = {
    'openai': {'title': 'OpenAI 官方动态', 'url': 'https://openai.com/news/rss.xml'},
    'python': {'title': 'Python 官方博客', 'url': 'https://blog.python.org/feeds/posts/default?alt=rss'},
    'nasa': {'title': 'NASA 官方新闻', 'url': 'https://www.nasa.gov/news-release/feed/'},
}
ATOM = '{http://www.w3.org/2005/Atom}'
CONTENT = '{http://purl.org/rss/1.0/modules/content/}'
MEDIA = '{http://search.yahoo.com/mrss/}'


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden, self.images = [], 0, []

    def handle_starttag(self, tag, attrs):
        tag = tag.split(':')[-1]
        if tag in {'script', 'style'}:
            self.hidden += 1
        if tag == 'img' and not self.hidden:
            self.images.append(dict(attrs))

    def handle_endtag(self, tag):
        tag = tag.split(':')[-1]
        if tag in {'script', 'style'}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def clean_text(value, limit):
    parser = TextParser()
    parser.feed(value)
    return re.sub(r'\s+', ' ', re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', ' '.join(parser.parts))).strip()[:limit]


def article_link(value, base):
    url = urljoin(base, value.strip())
    parts = urlsplit(url)
    return url if parts.scheme in {'http', 'https'} and parts.hostname and parts.username is None and parts.password is None and len(url) <= 2048 else ''


def image_metadata(attrs, base, title):
    try:
        url = canonical_url(urljoin(base, attrs.get('url') or attrs.get('src') or ''))
    except ValueError:
        return None
    if not attrs.get('url') and not attrs.get('src'):
        return None
    result = {'asset_id': hashlib.sha256(url.encode()).hexdigest(), 'source_url': url, 'alt': clean_text(attrs.get('alt') or title, 200)}
    for dimension in ('width', 'height'):
        value = str(attrs.get(dimension, ''))
        if value.isdecimal() and 0 < int(value) <= 20000:
            result[dimension] = int(value)
    return result


class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.article, self.main, self.hidden = 0, 0, 0
        self.article_parts, self.main_parts, self.metadata = [], [], {}

    def handle_starttag(self, tag, attrs):
        if tag == 'article':
            self.article += 1
        if tag == 'main':
            self.main += 1
        if tag in {'script', 'style', 'nav', 'aside', 'footer'}:
            self.hidden += 1
        if tag == 'meta':
            attrs = dict(attrs)
            key = attrs.get('property', '').lower()
            if key in {'og:image', 'og:image:width', 'og:image:height', 'og:image:alt'}:
                self.metadata[key] = attrs.get('content', '')

    def handle_endtag(self, tag):
        if tag == 'article':
            self.article = max(0, self.article - 1)
        if tag == 'main':
            self.main = max(0, self.main - 1)
        if tag in {'script', 'style', 'nav', 'aside', 'footer'}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            if self.article:
                self.article_parts.append(data)
            if self.main:
                self.main_parts.append(data)

    def result(self, url, title):
        body = clean_text(' '.join(self.article_parts or self.main_parts), 2000)
        image = image_metadata({'url': self.metadata.get('og:image', ''), **{key: self.metadata.get('og:image:' + key, '') for key in ('width', 'height', 'alt')}}, url, title)
        return body, image


def parse_feed(data, source_id, source=None):
    source = source or SOURCES[source_id]
    root = ET.fromstring(data)
    atom = root.tag == ATOM + 'feed'
    if root.tag != 'rss' and not atom:
        raise ValueError('需要 RSS 2.0 或 Atom 订阅地址，普通网页不能添加')
    entries = root.findall(ATOM + 'entry') if atom else root.findall('./channel/item')
    result = []
    for entry in entries:
        prefix = ATOM if atom else ''
        title = clean_text(entry.findtext(prefix + 'title', ''), 200)
        link = next((node.get('href', '') for node in entry.findall(ATOM + 'link') if node.get('rel', 'alternate') == 'alternate'), '') if atom else entry.findtext('link', '')
        try:
            url = article_link(link, source['url']) if link.strip() else ''
            date = entry.findtext(ATOM + 'published', '') or entry.findtext(ATOM + 'updated', '') if atom else entry.findtext('pubDate', '')
            published_date = datetime.fromisoformat(date.replace('Z', '+00:00')) if atom else parsedate_to_datetime(date)
            if not published_date.tzinfo or not title or not url:
                continue
            published = published_date.timestamp()
        except (TypeError, ValueError, OverflowError):
            continue
        node = entry.find(ATOM + 'content') if atom else entry.find(CONTENT + 'encoded')
        content = (node.text or '') + ''.join(ET.tostring(child, encoding='unicode') for child in node) if node is not None else ''
        if not content.strip():
            content = entry.findtext(ATOM + 'summary', '') if atom else entry.findtext('description', '')
        candidates = [dict(node.attrib) for node in entry.findall(ATOM + 'link') if node.get('rel') == 'enclosure' and node.get('type', '').startswith('image/')] if atom else [dict(node.attrib) for node in entry.findall('enclosure') if node.get('type', '').startswith('image/')]
        for candidate in candidates:
            candidate['url'] = candidate.get('url') or candidate.get('href')
        candidates += [dict(node.attrib) for node in entry.findall('.//' + MEDIA + 'thumbnail')]
        candidates += [dict(node.attrib) for node in entry.findall('.//' + MEDIA + 'content') if node.get('medium') == 'image' or node.get('type', '').startswith('image/')]
        parser = TextParser(); parser.feed(content)
        candidates += parser.images
        image = next((image for attrs in candidates if (image := image_metadata(attrs, url, title))), None)
        identity = 'rss-' + hashlib.sha256((source_id + url + title + date).encode()).hexdigest()[:24]
        result.append({'id': identity, 'title': title, 'body': clean_text(content, 2000), 'images': [image] if image else [], 'read_scope': 'rss', 'priority': 2, 'done': False,
                       'due_at': None, 'source': source['title'], 'source_url': url, 'updated_at': published,
                       'published_at': published_date.isoformat(), 'source_id': source_id})
    if not result:
        raise ValueError('信息源没有提供有效链接与发布时间的可验证条目')
    return sorted(result, key=lambda row: row['updated_at'], reverse=True)[:3]


async def read_builtin(url):
    async with asyncio.timeout(12):
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
            async with client.stream('GET', url, headers={'User-Agent': 'Navi/0.1 RSS reader'}) as response:
                if response.status_code != 200:
                    raise FeedReadError(f'来源返回 HTTP {response.status_code}')
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > 2_000_000:
                        raise FeedReadError('信息源超过 2 MB 读取限制')
                    data.extend(chunk)
                return bytes(data)


class PublicFeeds:
    def __init__(self, gateway):
        self.g = gateway
        self.media_slots = asyncio.Semaphore(3)

    async def enrich(self, materials):
        rows, per_source = [], {}
        for material in sorted(materials, key=lambda row: row.get('updated_at', 0), reverse=True):
            source = material.get('source_id', '')
            if per_source.get(source, 0) >= 3:
                continue
            per_source[source] = per_source.get(source, 0) + 1
            rows.append({**material, 'body': material.get('body', '')[:2000]})
            if len(rows) == 12:
                break
        semaphore = asyncio.Semaphore(3)
        async def supplement(row):
            try:
                async with semaphore:
                    data, _ = await read_public(row['source_url'], mime_types={'text/html', 'application/xhtml+xml'})
                    parser = ArticleParser(); parser.feed(data.decode('utf-8', errors='replace'))
                    body, image = parser.result(row['source_url'], row['title'])
                    if body:
                        row.update(body=body if len(body) > len(row['body']) else row['body'], read_scope='article', read_at=self.g.clock())
                    if not row.get('images') and image:
                        row['images'] = [image]
            except (FeedReadError, ValueError):
                return
        candidates = [row for row in rows if row.get('source_url') and (len(row['body']) < 400 or not row.get('images'))][:5]
        tasks = [asyncio.create_task(supplement(row)) for row in candidates]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=30)
            for task in pending:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    raise result
        for row in rows:
            existing = self.g.db.one('SELECT payload FROM records WHERE id=?', ('feed-' + row['id'],))
            if existing:
                payload = {**json.loads(existing['payload']), **{key: row.get(key) for key in ('body', 'images', 'read_scope', 'read_at')}}
                self.g.db.execute('UPDATE records SET payload=?,updated=? WHERE id=?', (packed(payload), self.g.clock(), 'feed-' + row['id']))
        return rows

    async def media(self, identity):
        if not re.fullmatch('[0-9a-f]{64}', identity):
            raise ValueError('图片引用不存在')
        row = self.g.db.one("SELECT image.value FROM records r,json_each(r.payload,'$.images') image WHERE r.kind='feed' AND json_extract(r.payload,'$.origin')='generated' AND COALESCE(json_extract(r.payload,'$.display_state'),'')<>'hidden_legacy' AND json_extract(image.value,'$.asset_id')=? LIMIT 1", (identity,))
        if not row:
            raise ValueError('图片引用不存在')
        url = canonical_url(json.loads(row['value'])['source_url'])
        if hashlib.sha256(url.encode()).hexdigest() != identity:
            raise ValueError('图片引用无效')
        async with self.media_slots:
            data, mime = await read_public(url, max_bytes=5_000_000, mime_types={'image/jpeg', 'image/png', 'image/webp', 'image/gif'})
        signatures = {'image/jpeg': data.startswith(b'\xff\xd8\xff'), 'image/png': data.startswith(b'\x89PNG\r\n\x1a\n'), 'image/gif': data.startswith((b'GIF87a', b'GIF89a')), 'image/webp': data.startswith(b'RIFF') and data[8:12] == b'WEBP'}
        if not signatures.get(mime):
            raise FeedReadError('图片内容格式无效')
        return data, mime

    def catalog(self):
        builtin = [{'id': identity, 'source_type': 'builtin', **values} for identity, values in SOURCES.items()]
        custom = [json.loads(row['payload']) for row in self.g.db.all("SELECT payload FROM records WHERE kind='source'")]
        return builtin + [{'id': row['source_id'], 'source_type': 'custom', 'title': row['title'], 'url': row['url']} for row in custom if row.get('source_type') == 'custom']

    def follow(self, identity):
        if identity not in SOURCES:
            raise ValueError('请选择支持的英文官方信息源')
        record_id = 'source-' + identity
        existing = self.g.db.one('SELECT * FROM records WHERE id=?', (record_id,))
        payload = {**(json.loads(existing['payload']) if existing else SOURCES[identity]), 'source_id': identity, 'source_type': 'builtin', 'enabled': True}
        self.save_source(record_id, payload)
        self.g.db.event('source.followed', {'source_id': identity}, now=self.g.clock())
        return {'id': record_id, 'payload': payload}

    def save_source(self, identity, payload):
        now = self.g.clock()
        self.g.db.execute('INSERT INTO records VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated=excluded.updated', (identity, 'source', packed(payload), now, now))

    async def preview(self, url):
        url = canonical_url(url)
        data, mime = await read_public(url)
        if mime in {'text/html', 'application/xhtml+xml'}:
            raise ValueError('这是普通网页，请填写公开 RSS/Atom 订阅地址')
        try:
            root = ET.fromstring(data)
            title = clean_text(root.findtext(ATOM + 'title', '') if root.tag == ATOM + 'feed' else root.findtext('./channel/title', ''), 80)
            source = {'title': title or urlsplit(url).hostname, 'url': url}
            items = parse_feed(data, 'preview', source)
        except ET.ParseError as error:
            raise ValueError('订阅内容不是可解析的 RSS/Atom XML') from error
        return {'url': url, 'title': source['title'], 'domain': urlsplit(url).hostname, 'items': [{'title': row['title'], 'source_url': row['source_url'], 'published_at': row['published_at']} for row in items]}

    async def add_custom(self, url, title=''):
        if not isinstance(title, str) or len(title) > 80:
            raise ValueError('来源名称最多 80 字符')
        preview = await self.preview(url)
        for row in self.g.db.all("SELECT * FROM records WHERE kind='source'"):
            payload = json.loads(row['payload'])
            if canonical_url(payload['url']) == preview['url']:
                return {'id': row['id'], 'payload': payload}
        builtin = next((identity for identity, source in SOURCES.items() if canonical_url(source['url']) == preview['url']), None)
        if builtin:
            return self.follow(builtin)
        identity = 'custom-' + hashlib.sha256(preview['url'].encode()).hexdigest()[:24]
        payload = {'source_id': identity, 'source_type': 'custom', 'url': preview['url'], 'title': title.strip() or preview['title'], 'enabled': True, 'last_checked': self.g.clock(), 'last_success': self.g.clock(), 'last_error': ''}
        self.save_source('source-' + identity, payload)
        self.g.db.event('source.followed', {'source_id': identity, 'source_type': 'custom'}, now=self.g.clock())
        return {'id': 'source-' + identity, 'payload': payload}

    async def refresh(self, job_id=None):
        rows = [row for row in self.g.db.all("SELECT * FROM records WHERE kind='source'") if json.loads(row['payload']).get('enabled', True)]
        semaphore = asyncio.Semaphore(3)
        items, errors = [], []

        def failure(row, reason):
            payload = json.loads(row['payload'])
            payload.update(last_checked=self.g.clock(), last_error=reason)
            self.save_source(row['id'], payload)
            errors.append({'source': payload['title'], 'error': reason})
            self.g.db.event('source.failed', {'source_id': payload['source_id'], 'reason': reason}, job_id, self.g.clock())

        async def read(row):
            payload = json.loads(row['payload'])
            identity = payload['source_id']
            try:
                async with semaphore:
                    if payload.get('source_type', 'builtin') == 'custom':
                        data, _ = await read_public(payload['url'])
                        source = payload
                    else:
                        source = SOURCES[identity]
                        data = await read_builtin(source['url'])
                    parsed = parse_feed(data, identity, source)
                now = self.g.clock()
                for item in parsed:
                    item['read_at'] = now
                items.extend(parsed)
                for item in parsed:
                    record_id = 'feed-' + item['id']
                    existing = self.g.db.one('SELECT created,payload FROM records WHERE id=?', (record_id,))
                    feed = {'title': item['title'], 'body': item['body'], 'images': item['images'], 'read_scope': 'rss', 'read_at': now, 'source': item['source'], 'source_url': item['source_url'], 'checked_at': now, 'source_time': item['updated_at'], 'item_id': item['id'], 'source_id': identity, 'job_id': json.loads(existing['payload']).get('job_id') if existing else job_id, 'collected_at': existing['created'] if existing else now, 'origin': 'rss_material'}
                    self.g.db.execute('INSERT INTO records VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated=excluded.updated', (record_id, 'feed', packed(feed), now, now))
                payload.update(last_checked=now, last_success=now, last_error='')
                self.save_source(row['id'], payload)
                self.g.db.event('source.checked', {'source_id': identity, 'item_ids': [item['id'] for item in parsed]}, job_id, now)
            except (httpx.RequestError, ET.ParseError, ValueError, FeedReadError, TimeoutError, KeyError) as error:
                reason = str(error) if isinstance(error, (ValueError, FeedReadError)) else '读取失败：' + type(error).__name__
                failure(row, reason)

        tasks = {asyncio.create_task(read(row)): row for row in rows}
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=60)
            for task in pending:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    raise result
            for task in pending:
                failure(tasks[task], '超过 60 秒刷新窗口，尚未完成')
        return {'items': items, 'errors': errors, 'checked_sources': len(rows)}
