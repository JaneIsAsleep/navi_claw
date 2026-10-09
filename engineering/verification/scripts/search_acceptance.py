"""Full local ASGI API benchmark using synthetic data, no model calls."""
import asyncio
import json
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'backend'))
from navi.gateway import Gateway
from navi.web import create_app


async def main():
    directory = Path(tempfile.mkdtemp(prefix='search-benchmark-', dir=ROOT / 'engineering/verification/.cache'))
    g = Gateway(ROOT, directory)
    text = ('公开资料 Python 常规内容。' * 40)[:600]
    with g.db.transaction() as conn:
        conn.executemany('INSERT INTO sessions(id,title,kind,state,created,updated) VALUES(?,?,?,?,?,?)', ((f's{i:04}', f'中性话题 {i}', 'secondary', 'archived' if i % 5 == 0 else 'active', i, i) for i in range(2000)))
        conn.executemany('INSERT INTO messages(id,session_id,role,content,created) VALUES(?,?,?,?,?)', ((f'm{i:06}', f's{i // 50:04}', 'user' if i % 2 else 'assistant', text[:-10] + (' RareNeedle' if i % 4999 == 0 else ' 常规的文本资料。'), i) for i in range(100000)))
    samples = []
    queries = ['RareNeedle', 'NoSuchTerm', '公', '资料', 'python', "%_ 'quote'", '话题']
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(g)), base_url='http://testserver') as client:
        for _ in range(4):
            for query in queries:
                start = time.perf_counter()
                response = await client.get('/api/sessions/search', params={'q': query, 'scope': 'all', 'limit': 20})
                rows = response.json(); assert response.status_code == 200
                samples.append({'query': query, 'ms': (time.perf_counter() - start) * 1000, 'results': len(rows)})
    ordered = sorted(row['ms'] for row in samples)
    p95 = ordered[int(len(ordered) * .95)]
    report = {'evidence_type': 'synthetic-full-asgi-api', 'machine': platform.platform(), 'python': platform.python_version(), 'sessions': 2000, 'messages': 100000, 'characters_per_message': 600, 'p95_ms': p95, 'median_ms': statistics.median(ordered), 'samples': samples, 'usage_count': g.db.one('SELECT COUNT(*) n FROM usage')['n'], 'status': 'passed' if p95 <= 500 else 'failed'}
    path = ROOT / 'engineering/evidence/05-muse-revision-v2/implementation/search-performance.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != 'samples'}, ensure_ascii=False))
    return 0 if p95 <= 500 else 1

if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
