"""Isolated offline UI acceptance only, never the personal runtime."""
import json
import hashlib
import sys
from pathlib import Path

import uvicorn
from fastapi import Request
from fastapi.responses import JSONResponse, Response

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'engineering/verification/tests'))

from navi.gateway import Gateway
from navi.web import create_app
from test_muse_revision import GoalModel, ideas


def main():
    directory = Path(sys.argv[1])
    g = Gateway(ROOT, directory, GoalModel(ideas()))
    # Isolate user interactions from daily publishing; scheduling is tested separately.
    g.tick = lambda: None
    goal = g.record('goal', {'title': '临时阅读验收目标', 'body': '只用于界面验收', 'abstract': '整理一页中性阅读笔记', 'state': 'active'})
    other = g.record('goal', {'title': '相邻独立目标', 'body': '保留相邻目标', 'state': 'active'})
    completed = g.record('goal', {'title': '临时已完成目标', 'body': '只用于删除验收', 'state': 'completed'})
    schedule = g.schedule({'title': '临时目标提醒', 'instruction': '中性验收', 'kind': 'once', 'next_due': g.clock() + 86400, 'goal_id': goal['id']})
    idea = g.record('idea', {**ideas()['items'][2], 'state': 'active'})
    search_topics = []
    feed_topic = None
    media_errors = {}
    if '--feed' in sys.argv:
        feed_topic = g.session('V2讨论验收')
        for index in range(40):
            g.db.execute('INSERT INTO messages(id,session_id,role,content,created) VALUES(?,?,?,?,?)', (f'feed-chat-{index:02}', feed_topic['id'], 'assistant', f'这是隔离界面验收段落 {index:02}。只用于检查聊天阅读位置和草稿。' * 5, index))
        for index in range(65):
            images = []
            if index < 3:
                asset = hashlib.sha256(f'fixture-media-{index}'.encode()).hexdigest()
                media_errors[asset] = ('404', 'timeout', 'mime')[index]
                images = [{'asset_id':asset, 'source_url':f'https://example.org/fixture-{index}.png', 'alt':'隔离验收配图', 'width':1200, 'height':675}]
            row = g.record('feed', {'title':f'临时阅读材料 {index:02}', 'body':'这是隔离界面验收使用的中性阅读材料。它只用于检查分页、完整段落、讨论入口和滚动位置，所有文字均明确属于测试样本。可以继续向下浏览，并在失败时保留已经加载的条目。\n\n第二段用于观察两段正文的间距与行高。打开左侧聊天后，当前阅读条目应该保持位置；关闭聊天、展开完整聊天或回到动态时，草稿和待发送引用也应保留。', 'origin':'generated', 'source_ids':[f'fixture-material-{index}'], 'icon_key':('ai','code','space','science','learning','general')[index % 6], 'images':images, 'source':'Navi 隔离界面验收'})
            g.db.execute('UPDATE records SET created=? WHERE id=?', (g.clock() - index * 3600, row['id']))
    if '--search' in sys.argv:
        for index in range(19):
            topic = g.session(f'中性检索话题 {index:02}')
            search_topics.append(topic['id'])
            for number in range(40):
                content = f'公开阅读验收段落 {number}。' * 10 + (' 定位关键词 %_ HELLO' if number == 15 else '')
                g.db.execute('INSERT INTO messages(id,session_id,role,content,created) VALUES(?,?,?,?,?)', (f'search-{index:02}-{number:02}', topic['id'], 'assistant', content, number))
            if index % 3 == 0:
                g.edit_session(topic['id'], {'operation': 'archive'})
    controls = {'complete_failures': 0, 'idea_errors': [], 'idea_requests': [], 'search_delay': 0, 'search_error': False, 'recent_count': 20, 'feed_failures':0, 'feed_requests':[], 'media_requests':[], 'geometry_case':'', 'geometry':[]}
    app = create_app(g)

    @app.post('/api/fixture/control')
    async def control(request: Request):
        values = await request.json()
        controls.update({key: value for key, value in values.items() if key in controls})
        if 'recent_count' in values:
            ids = [g.main_session()['id'], *search_topics]
            for index, identity in enumerate(ids):
                g.db.execute('UPDATE sessions SET state=? WHERE id=?', ('active' if index < values['recent_count'] else 'deleting', identity))
        if values.get('new_feed'):
            g.record('feed', {'title':'隔离验收的新批次条目', 'body':'新批次只用于验证轮询更新后保留当前阅读位置。\n\n原有分页条目和当前聊天草稿应继续保留。', 'origin':'generated', 'source_ids':['fixture-new-material'], 'icon_key':'general'})
        return controls

    @app.get('/api/fixture/state')
    def fixture_state():
        return {'controls': controls, 'goal_id': goal['id'], 'other_goal_id': other['id'], 'completed_goal_id': completed['id'], 'schedule_id': schedule['id'], 'idea_id': idea['id'], 'feed_topic':feed_topic, 'counts': {table: g.db.one('SELECT COUNT(*) n FROM ' + table)['n'] for table in ('sessions', 'messages', 'jobs', 'records', 'usage')}, 'task_state': g.require('tasks', schedule['task_id'])['state']}

    @app.post('/api/fixture/geometry')
    async def geometry(request: Request):
        sample = await request.json()
        allowed = {'phase', 'ms', 'width', 'height', 'transform', 'opacity', 'rows', 'has_query', 'error', 'viewport_width', 'viewport_height', 'horizontal_overflow'}
        controls['geometry'].append({'case': controls['geometry_case'], **{key: value for key, value in sample.items() if key in allowed}})
        controls['geometry'] = controls['geometry'][-200:]
        return {'saved': True}

    @app.middleware('http')
    async def failures(request, call_next):
        if request.url.path == '/api/feed/items':
            controls['feed_requests'].append(dict(request.query_params))
            if request.query_params.get('before') and controls['feed_failures']:
                controls['feed_failures'] -= 1
                return JSONResponse({'detail':'隔离验收模拟：下一页读取失败'}, 503)
        if request.url.path.startswith('/api/feed-media/'):
            asset = request.url.path.rsplit('/', 1)[-1]
            if asset in media_errors:
                reason = media_errors[asset]
                controls['media_requests'].append({'asset_id':asset, 'reason':reason})
                if reason == 'mime':
                    return Response('<html>隔离验收模拟：错误 MIME</html>', media_type='text/html')
                return JSONResponse({'detail':'隔离验收模拟：' + reason}, 404 if reason == '404' else 502)
        if request.method == 'POST' and request.url.path.startswith('/api/goals/') and request.url.path.endswith('/state') and controls['complete_failures']:
            controls['complete_failures'] -= 1
            return JSONResponse({'detail': '隔离验收模拟：完成请求失败'}, 503)
        if request.method == 'POST' and request.url.path.startswith('/api/ideas/') and request.url.path.endswith('/start'):
            controls['idea_requests'].append((await request.json()).get('request_id'))
            if controls['idea_errors']:
                return JSONResponse({'detail': '隔离验收模拟：开始失败'}, controls['idea_errors'].pop(0))
        if request.url.path == '/api/sessions/search':
            if controls['search_delay']:
                import asyncio
                await asyncio.sleep(controls['search_delay'])
            if controls['search_error']:
                return JSONResponse({'detail': '隔离验收模拟：读取失败'}, 503)
        return await call_next(request)

    (directory / 'fixture.json').write_text(json.dumps({'evidence_type': 'isolated-offline-ui-fixture', 'goal_id': goal['id'], 'idea_id': idea['id']}, indent=2))
    uvicorn.run(app, host='127.0.0.1', port=8766, access_log=False, log_level='warning')


if __name__ == '__main__':
    main()
