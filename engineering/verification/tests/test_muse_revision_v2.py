import asyncio
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from navi.agent import Agent
from navi.gateway import APIError, Gateway
from navi.storage import packed
from test_gateway import ROOT, Clock, Model
from test_muse_revision import ContentModel, ideas, local_clock
from navi.model import AgentError, Reply
from navi.web import create_app
import httpx


@pytest.fixture
def g(tmp_path):
    return Gateway(ROOT, tmp_path, Model(), Clock())


def reading(g):
    return g.record('goal', {'title': '阅读目标', 'body': '每日阅读', 'next_step': '选书', 'state': 'active'})


@pytest.mark.parametrize('text', ['请用两句话概括引用中可核实的变化。仅讨论，不调用工具，不保存目标、记忆或提醒。', '不保存目标，只讨论阅读内容。', '不创建目标，先说明资料。'])
async def test_feed_discussion_negative_goal_save_does_not_require_or_authorize_tool(g, text):
    request = g.chat(text)
    await g.run_one()
    assert g.require('jobs', request['id'])['status'] == 'succeeded'
    assert len(g.model.calls) == 1
    assert not g.db.all("SELECT * FROM records WHERE kind='goal'")
    assert not g.db.all('SELECT * FROM tool_calls')
    with pytest.raises(ValueError):
        await Agent(g).call_tool(request, g.job_config(), 'create_goal', {'title':'夹带目标','body':'未获授权','next_step':'选资料'})
    assert not g.db.all("SELECT * FROM records WHERE kind='goal'")


async def test_negative_reminder_does_not_block_explicit_goal_save(g):
    request = g.chat('不设置提醒，请保存目标：整理公开阅读资料。')
    result = await Agent(g).call_tool(request, g.job_config(), 'create_goal', {'title':'整理公开阅读资料','body':'阅读中性资料','next_step':'选一篇文章'})
    assert g.record_detail(result['id'])['payload']['title'] == '整理公开阅读资料'
    assert not g.db.all('SELECT * FROM schedules')


def search_message(g, topic, identity, content, created=1):
    g.db.execute('INSERT INTO messages(id,session_id,role,content,created) VALUES(?,?,?,?,?)', (identity, topic['id'], 'assistant', content, created))


@pytest.mark.parametrize('query', ['稀有', '稀', 'Hello', "%_ 'quote'", '<b>'])
async def test_full_search_literal_body_latest_excerpt_and_zero_model(g, query):
    topic = g.session('中性资料')
    g.db.execute('UPDATE sessions SET goal_setup_json=? WHERE id=?', (packed({'category': 'interests'}), topic['id']))
    search_message(g, topic, 'older', query, 1)
    search_message(g, topic, 'latest', '中性内容 ' * 50 + query.swapcase() + ' 可阅读材料 ' * 50, 2)
    g.edit_session(topic['id'], {'operation': 'archive'})
    before = {table: g.db.all('SELECT * FROM ' + table) for table in ('jobs', 'usage')}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(g)), base_url='http://testserver') as client:
        assert (await client.get('/api/sessions/search', params={'q': query})).json() == []
        result = (await client.get('/api/sessions/search', params={'q': query, 'scope': 'all'})).json()
        assert len(result) == 1 and result[0]['id'] == topic['id']
        assert result[0]['match_kind'] == 'message' and result[0]['match_message_id'] == 'latest'
        assert len(result[0]['match_excerpt']) <= 160 and query.lower() in result[0]['match_excerpt'].lower()
        assert result[0]['state'] == 'archived' and json.loads(result[0]['goal_setup_json'])['category'] == 'interests'
    assert before == {table: g.db.all('SELECT * FROM ' + table) for table in before}
    assert not g.model.calls


def test_full_search_title_priority_paging_empty_and_deleted(g):
    title = g.session('稀有标题')
    body = g.session('正文话题')
    search_message(g, body, 'match', '稀有正文')
    g.db.execute('UPDATE sessions SET updated=updated+100 WHERE id=?', (body['id'],))
    rows = g.search_sessions(' 稀有 ', scope='all', limit=1)
    assert rows[0]['id'] == title['id'] and rows[0]['match_kind'] == 'title'
    assert g.search_sessions('稀有', scope='all', limit=1, offset=1)[0]['id'] == body['id']
    g.db.execute("UPDATE sessions SET state='deleting' WHERE id=?", (body['id'],))
    assert len(g.search_sessions('稀有', scope='all')) == 1
    assert g.search_sessions('', scope='all') == []
    assert all(not row.get('match_message_id') for row in g.search_sessions('', recent=True, scope='all'))
    with pytest.raises(ValueError):
        g.search_sessions('x', scope='audit')
    with pytest.raises(ValueError):
        g.search_sessions('x' * 101, scope='all')


@pytest.mark.parametrize('text,changes', [
    ('不要修改阅读目标', {'state': 'completed'}),
    ('如何完成阅读目标', {'state': 'completed'}),
    ('引用：“完成阅读目标”', {'state': 'completed'}),
    ('等我确认再完成阅读目标', {'state': 'completed'}),
    ('请更新阅读目标的描述为每天阅读十分钟', {'body': '每天阅读十分钟', 'state': 'completed'}),
    ('请修改阅读目标的下一步为选书', {'next_step': '选书', 'body': '夹带描述'}),
    ('请更新阅读目标的描述为完成一本书', {'body': '完成一本书', 'state': 'completed'}),
])
async def test_l01_rejects_unauthorized_goal_fields_without_lifecycle_changes(g, text, changes):
    goal = reading(g)
    schedule = g.schedule({'title': '阅读提醒', 'instruction': '阅读', 'kind': 'once', 'next_due': g.clock(), 'goal_id': goal['id']})
    g.tick()
    due = g.db.one('SELECT * FROM jobs WHERE task_id=?', (schedule['task_id'],))
    g.propose(due['id'], 'notification', '阅读', 'inbox', '阅读提醒')
    request = g.chat(text)
    before = {table: g.db.all('SELECT * FROM ' + table) for table in ('records', 'tasks', 'schedules', 'actions', 'outbox')}
    with pytest.raises(ValueError):
        await Agent(g).call_tool(request, g.job_config(), 'update_goal', {'id': goal['id'], **changes})
    assert before == {table: g.db.all('SELECT * FROM ' + table) for table in before}


async def test_l01_matches_current_target_and_positive_update_complete_restore(g):
    goal = reading(g)
    other = g.record('goal', {'title': '运动目标', 'state': 'active'})
    request = g.chat('请更新阅读目标的描述为每天阅读十分钟')
    with pytest.raises(ValueError):
        await Agent(g).call_tool(request, g.job_config(), 'update_goal', {'id': other['id'], 'body': '错误目标'})
    result = await Agent(g).call_tool(request, g.job_config(), 'update_goal', {'id': goal['id'], 'body': '每天阅读十分钟'})
    assert result['payload']['body'] == '每天阅读十分钟'
    for text, state in [('请完成阅读目标', 'completed'), ('请恢复阅读目标', 'active'), ('请暂停阅读目标', 'paused')]:
        request = g.chat(text)
        result = await Agent(g).call_tool(request, g.job_config(), 'update_goal', {'id': goal['id'], 'state': state})
        assert result['payload']['state'] == state
    assert g.record_detail(other['id'])['payload']['state'] == 'active'


async def test_l01_due_task_cannot_update_goal(g):
    goal = reading(g)
    schedule = g.schedule({'title': '更新阅读目标', 'instruction': '请完成阅读目标', 'kind': 'once', 'next_due': g.clock()})
    g.tick()
    job = g.db.one('SELECT * FROM jobs WHERE task_id=?', (schedule['task_id'],))
    with pytest.raises(ValueError):
        await Agent(g).call_tool(job, g.job_config(), 'update_goal', {'id': goal['id'], 'state': 'completed'})
    assert g.record_detail(goal['id'])['payload']['state'] == 'active'


async def test_l02_unrelated_reminder_does_not_inherit_goal_and_completion_is_precise(g):
    goal = reading(g)
    topic = g.session('阅读引导')
    g.db.execute('UPDATE sessions SET goal_setup_json=? WHERE id=?', (packed({'status': 'saved', 'version': 4, 'saved_goal_id': goal['id'], 'followup': 'reminder'}), topic['id']))
    unrelated = g.chat('明天早上八点提醒我交作业', topic['id'])
    config = json.loads(unrelated['config'])
    assert not config.get('goal_id')
    agent = Agent(g)
    first = await agent.call_tool(unrelated, config, 'schedule_task', {'title': '交作业', 'instruction': '交作业', 'kind': 'once', 'next_due': g.clock() + 86400, 'goal_id': goal['id']})
    assert g.require('tasks', first['task_id'])['goal_id'] is None
    linked = g.chat('明天早上八点提醒我推进阅读目标', topic['id'])
    second = await agent.call_tool(linked, json.loads(linked['config']), 'schedule_task', {'title': '阅读', 'instruction': '阅读', 'kind': 'once', 'next_due': g.clock() + 86400})
    assert g.require('tasks', second['task_id'])['goal_id'] == goal['id']
    g.goal_state(goal['id'], 'complete')
    assert g.require('tasks', first['task_id'])['state'] == 'active'
    assert g.require('tasks', second['task_id'])['state'] == 'completed'
    assert 'followup' not in json.loads(g.require('sessions', topic['id'])['goal_setup_json'])


async def test_l02_time_answer_only_inherits_current_reminder_question(g):
    goal = reading(g)
    topic = g.session('阅读引导')
    setup = {'status': 'saved', 'version': 4, 'saved_goal_id': goal['id'], 'followup': 'reminder', 'followup_version': 4}
    g.db.execute('UPDATE sessions SET goal_setup_json=? WHERE id=?', (packed(setup), topic['id']))
    question = g.chat('请为已保存的这个目标设置站内提醒。先问我提醒时间。', topic['id'])
    # A persisted answer to the current question, without a model call.
    g.db.execute("UPDATE jobs SET status='succeeded',config=? WHERE id=?", (packed({**json.loads(question['config']), 'goal_id': goal['id']}), question['id']))
    g.db.execute("INSERT INTO messages(id,session_id,role,content,job_id,created) VALUES('question',?,'assistant','什么时候提醒？',?,?)", (topic['id'], question['id'], g.clock()))
    g.db.execute('UPDATE sessions SET goal_setup_json=? WHERE id=?', (packed(setup), topic['id']))
    answer = g.chat('每天晚上八点', topic['id'])
    config = json.loads(answer['config'])
    assert config.get('goal_id') == goal['id']
    result = await Agent(g).call_tool(answer, config, 'schedule_task', {'title': '阅读', 'instruction': '阅读', 'kind': 'recurring', 'interval_seconds': 86400, 'next_due': g.clock() + 3600})
    assert g.require('tasks', result['task_id'])['goal_id'] == goal['id']
    later = g.chat('明天提醒我交作业', topic['id'])
    assert not json.loads(later['config']).get('goal_id')


@pytest.mark.parametrize('operation', ['complete', 'cancel'])
def test_l03_terminated_heartbeat_releases_slot_without_rewriting_history(g, operation):
    first = g.schedule({'title': '第一次', 'instruction': '检查', 'kind': 'heartbeat', 'interval_seconds': 3600})
    g.edit_schedule(first['id'], {'operation': operation})
    second = g.schedule({'title': '第二次', 'instruction': '检查', 'kind': 'heartbeat', 'interval_seconds': 3600})
    assert second['id'] != first['id']
    assert g.require('tasks', first['task_id'])['state'] == ('completed' if operation == 'complete' else 'cancelled')


def test_l03_active_paused_and_concurrent_creation_allow_one_slot(g):
    def create():
        try:
            return g.schedule({'title': '检查', 'instruction': '检查', 'kind': 'heartbeat', 'interval_seconds': 3600})
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert sum(row is not None for row in results) == 1
    first = next(row for row in results if row)
    assert create() is None
    g.edit_schedule(first['id'], {'operation': 'pause'})
    assert create() is None


async def test_l04_deleted_session_returns_410_and_new_request_can_restart(g):
    idea = g.record('idea', {**ideas()['items'][0], 'state': 'active'})
    first = g.start_idea(idea['id'], 'same-request-id')
    assert g.start_idea(idea['id'], 'same-request-id') == first
    await g.delete_session(first['session_id'])
    with pytest.raises(APIError) as failure:
        g.start_idea(idea['id'], 'same-request-id')
    assert failure.value.status == 410
    second = g.start_idea(idea['id'], 'fresh-request-id')
    assert second['session_id'] and second['session_id'] != first['session_id']
    assert g.start_idea(idea['id'], 'fresh-request-id') == second
    g.record('idea', {**idea['payload'], 'state': 'superseded'}, idea['id'])
    with pytest.raises(APIError):
        g.start_idea(idea['id'], 'third-request-id')
    assert g.start_idea(idea['id'], 'fresh-request-id') == second


def continuous(g, seconds=60):
    for _ in range(seconds):
        g.clock.now += 1
        g.tick()


def only_ideas(g):
    g.tick()
    g.db.execute("UPDATE jobs SET status='cancelled' WHERE kind='feed'")
    return g.db.one("SELECT * FROM jobs WHERE kind='ideas' ORDER BY rowid DESC LIMIT 1")


async def test_awake_gate_blocks_short_wakes_then_claims_after_sixty_seconds(g):
    g.model = ContentModel(ideas())
    job = only_ideas(g)
    assert not await g.run_one()
    continuous(g, 2)
    assert not await g.run_one()
    g.clock.now += 2700; g.tick()
    continuous(g, 45)
    assert not await g.run_one()
    assert not g.db.all('SELECT * FROM usage')
    continuous(g, 15)
    assert await g.run_one()
    assert g.require('jobs', job['id'])['status'] == 'succeeded'
    assert len([row for row in g.snapshot()['records'] if row['kind'] == 'idea']) == 5


async def test_network_recovery_is_persistent_bounded_and_shares_call_budget(g):
    g.clock.now = local_clock('2026-10-08T12:00:00').now
    class Failing(Model):
        async def chat(self, **request):
            self.calls.append(request)
            raise AgentError('网络连接失败', True, 'model_connection_error')
    g.model = Failing()
    job = only_ideas(g)
    continuous(g)
    for _ in range(3):
        await g.run_one()
        continuous(g, 2)
    row = g.require('jobs', job['id'])
    assert row['status'] == 'pending' and row['recovery_count'] == 0
    assert row['next_run'] == row['updated'] + 600
    assert len(g.db.all('SELECT * FROM usage')) == 3
    restored = Gateway(ROOT, g.directory, Failing(), g.clock)
    restored.recover()
    assert restored.require('jobs', job['id'])['next_run'] == row['next_run']
    g = restored
    g.clock.now = row['next_run']; g.tick(); continuous(g, 45)
    assert not await g.run_one()
    assert g.require('jobs', job['id'])['recovery_count'] == 0
    continuous(g, 15); await g.run_one()
    row = g.require('jobs', job['id'])
    assert row['recovery_count'] == 1 and row['next_run'] == row['updated'] + 1800
    g.clock.now = row['next_run']; g.tick(); continuous(g)
    await g.run_one()
    row = g.require('jobs', job['id'])
    assert row['status'] == 'failed' and row['recovery_count'] == 2
    assert len(g.db.all('SELECT * FROM usage')) == 5
    assert len(g.db.all('SELECT * FROM attempts WHERE job_id=?', (job['id'],))) == 5
    g.clock.now += 3600; g.tick(); continuous(g)
    assert not await g.run_one()


@pytest.mark.parametrize('code', ['model_auth_error', 'model_balance_error', 'content_validation_error', 'job_timeout'])
async def test_nonrecoverable_errors_do_not_get_delayed_retry(g, code):
    class Failing(Model):
        async def chat(self, **request):
            raise AgentError('需人工处理', code=code)
    g.model = Failing()
    job = only_ideas(g); continuous(g)
    await g.run_one()
    row = g.require('jobs', job['id'])
    assert row['status'] == 'failed' and row['error_code'] == code and row['recovery_count'] == 0
    assert not [row for row in g.audit(job['id']) if row['kind'] == 'content.recovery_queued']


async def test_budget_zero_records_usage_and_real_budget_change_recovers_only_latest(g):
    g.model = ContentModel(ideas())
    job = only_ideas(g); continuous(g)
    g.settings.update({'daily_requests': 1, 'daily_tokens': 0})
    config = json.loads(job['config']); config['daily_requests'] = 1
    g.db.execute('UPDATE jobs SET config=? WHERE id=?', (packed(config), job['id']))
    other = g.chat('普通测试')
    g.reserve_call(other['id'], 100, config)
    await g.run_one()  # ordinary chat reaches its explicit budget
    await g.run_one()
    assert g.require('jobs', job['id'])['error_code'] == 'daily_budget_exhausted'
    before = len(g.audit(job['id']))
    g.settings.update({'daily_requests': 1})
    assert len(g.audit(job['id'])) == before
    g.settings.update({'daily_requests': 0})
    recovered = g.require('jobs', job['id'])
    assert recovered['status'] == 'pending' and recovered['deadline'] is None
    assert json.loads(recovered['config'])['daily_requests'] == 0
    g.settings.update({'daily_requests': 0})
    assert sum(row['kind'] == 'content.recovery_queued' for row in g.audit(job['id'])) == 1
    await g.run_one()
    assert g.require('jobs', job['id'])['status'] == 'succeeded'
    assert len(g.db.all('SELECT * FROM usage')) == 2
    assert any(row['estimated'] == 1 for row in g.db.all('SELECT * FROM usage'))


async def test_sleep_interrupt_cancels_execution_and_rejects_late_batch(g):
    entered = asyncio.Event()
    class Late(ContentModel):
        async def chat(self, **request):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return Reply([{'type': 'text', 'text': packed(ideas())}], 100, 40)
    old = g.record('idea', {**ideas()['items'][0], 'state': 'active'})
    g.model = Late(ideas()); job = only_ideas(g); continuous(g)
    execution = asyncio.create_task(g.run_one()); await entered.wait()
    g.clock.now += 2700; g.tick(); await execution
    row = g.require('jobs', job['id'])
    assert row['status'] == 'pending' and row['error_code'] == 'sleep_interrupted'
    assert row['deadline'] is None and row['next_run'] == g.clock() + 600
    assert {row['id'] for row in g.snapshot()['records'] if row['kind'] == 'idea'} == {old['id']}


async def test_cancel_and_cross_day_prevent_queued_recovery(g):
    job = only_ideas(g)
    g.db.execute("UPDATE jobs SET status='pending',error_code='model_timeout',next_run=?,deadline=NULL WHERE id=?", (g.clock() + 600, job['id']))
    await g.cancel(job['id'])
    g.clock.now += 600; g.tick(); continuous(g)
    assert not await g.run_one()
    assert g.require('jobs', job['id'])['status'] == 'cancelled'
    g.clock.now += 86400; newer = only_ideas(g)
    g.db.execute("UPDATE jobs SET error_code='model_timeout',next_run=? WHERE id=?", (g.clock() + 600, newer['id']))
    g.clock.now += 86400; only_ideas(g)
    assert g.require('jobs', newer['id'])['error_code'] == 'superseded_daily'
    assert len(g.db.all("SELECT * FROM jobs WHERE kind='ideas'")) == 3


def test_v5_migration_preserves_success_history_and_restores_only_latest_transport(g):
    import sqlite3
    goal = reading(g)
    legacy = g.record('feed', {'title': '旧动态', 'body': '保留原文'})
    generated = g.record('feed', {'title': '真实动态', 'body': '真实来源', 'origin': 'generated'})
    g.settings.update({'daily_requests': 300, 'daily_tokens': 300000})
    g.tick()
    feed = g.db.one("SELECT * FROM jobs WHERE kind='feed'")
    idea = g.db.one("SELECT * FROM jobs WHERE kind='ideas'")
    g.db.execute("UPDATE jobs SET status='succeeded',result='{}',attempt=1 WHERE id=?", (feed['id'],))
    g.db.execute("UPDATE jobs SET status='failed',error='DeepSeek 请求超时或网络连接失败',attempt=1 WHERE id=?", (idea['id'],))
    before_feed = g.require('jobs', feed['id'])
    g.db.execute('PRAGMA user_version=4')
    (g.directory / 'backups/navi.before-muse-revision-v5.sqlite3').unlink()
    upgraded = Gateway(ROOT, g.directory, Model(), g.clock)
    assert upgraded.db.one('PRAGMA user_version')['user_version'] == 5
    assert upgraded.settings.current()['daily_requests'] == upgraded.settings.current()['daily_tokens'] == 0
    assert upgraded.require('jobs', feed['id']) == before_feed
    assert upgraded.require('jobs', idea['id'])['status'] == 'pending'
    assert upgraded.require('jobs', idea['id'])['error_code'] == 'model_transport_error'
    assert upgraded.record_detail(goal['id'])['payload'] == goal['payload']
    assert upgraded.record_detail(legacy['id'])['payload']['body'] == '保留原文'
    assert legacy['id'] not in {row['id'] for row in upgraded.snapshot()['records']}
    assert generated['id'] in {row['id'] for row in upgraded.snapshot()['records']}
    path = g.directory / 'backups/navi.before-muse-revision-v5.sqlite3'
    before = path.read_bytes()
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 4
    count = len(upgraded.audit())
    Gateway(ROOT, g.directory, Model(), g.clock)
    assert path.read_bytes() == before and len(upgraded.audit()) == count


@pytest.mark.parametrize('text,changes', [
    ('修改目标描述为每天十分钟', {'body': '每天十分钟'}),
    ('把描述改成每天十分钟', {'body': '每天十分钟'}),
    ('改成每天十分钟', {'body': '每天十分钟'}),
    ('把下一步改成先选书', {'next_step': '先选书'}),
    ('暂停这个目标', {'state': 'paused'}),
])
async def test_natural_goal_edits_use_session_without_id(g, text, changes):
    topic = g.session('目标讨论')
    goal = g.record('goal', {'title': '睡前阅读', 'session_id': topic['id']})
    other = reading(g)
    request = g.chat(text, topic['id'])
    await Agent(g).call_tool(request, g.job_config(), 'update_goal', changes)
    assert g.record_detail(goal['id'])['payload'].items() >= changes.items()
    assert g.record_detail(other['id'])['payload']['body'] == '每日阅读'


async def test_goal_edit_uses_selected_reference_not_replacement_text(g):
    goal = reading(g)
    other = g.record('goal', {'title': '运动目标'})
    request = g.chat('把描述改成完成运动目标', references=[{'kind': 'goal', 'id': goal['id']}])
    await Agent(g).call_tool(request, g.job_config(), 'update_goal', {'body': '完成运动目标'})
    assert g.record_detail(goal['id'])['payload']['body'] == '完成运动目标'
    assert g.record_detail(goal['id'])['payload']['state'] == 'active'
    assert 'body' not in g.record_detail(other['id'])['payload']


async def test_ambiguous_or_missing_target_cannot_be_selected_by_model(g):
    goal = reading(g)
    other = g.record('goal', {'title': '周末运动'})
    for text in ('修改目标描述为十分钟', '把游泳目标的描述改成十分钟'):
        request = g.chat(text)
        result = await Agent(g).call_tool(request, g.job_config(), 'update_goal', {'id': goal['id'], 'body': '十分钟'})
        assert result['status'] == 'needs_clarification'
        assert g.record_detail(goal['id'])['payload']['body'] == '每日阅读'
        assert 'body' not in g.record_detail(other['id'])['payload']


async def test_quoted_goal_title_and_short_name_resolve_in_chat(g):
    goal = reading(g)
    g.record('goal', {'title': '周末运动'})
    for text in ('把“阅读目标”的描述改成十分钟', '把阅读的描述改成十分钟'):
        result = await Agent(g).call_tool(g.chat(text), g.job_config(), 'update_goal', {'body': '十分钟'})
        assert result['id'] == goal['id']


async def test_cancel_goal_and_its_reminder_in_context_without_ids(g):
    topic = g.session('目标讨论')
    goal = g.record('goal', {'title': '睡前阅读', 'session_id': topic['id']})
    other = reading(g)
    def schedule(target):
        return g.schedule({'title': '晚间提醒', 'instruction': '阅读', 'kind': 'once', 'next_due': g.clock()+3600, 'goal_id': target['id']})
    first, second = schedule(goal), schedule(other)
    request = g.chat('取消这个目标的提醒', topic['id'])
    agent = Agent(g)
    await agent.call_tool(request, g.job_config(), 'cancel_tracking', {})
    assert g.require('tasks', first['task_id'])['state'] == 'cancelled'
    assert g.require('tasks', second['task_id'])['state'] == 'active'
    assert g.record_detail(goal['id'])['payload'].get('state', 'active') == 'active'
    with pytest.raises(ValueError):
        await agent.call_tool(request, g.job_config(), 'cancel_goal', {})
    await agent.call_tool(g.chat('取消这个目标', topic['id']), g.job_config(), 'cancel_goal', {})
    assert g.record_detail(goal['id'])['payload']['state'] == 'cancelled'
    assert g.record_detail(other['id'])['payload']['state'] == 'active'


async def test_short_edit_after_previous_goal_discussion_uses_web_chat(g):
    goal = reading(g)
    other = g.record('goal', {'title': '旅行计划', 'body': '选城市'})
    topic = g.session('普通聊天')
    g.chat('讨论一下阅读目标，每天读十分钟怎么样？', topic['id'])
    await g.run_one()
    # First the model asks for the ID despite an actionable request. The retry
    # must use the actual tool before the chat can report a successful edit.
    replies = iter([
        [{'type': 'text', 'text': '请提供目标 ID'}],
        [{'type': 'tool_use', 'id': 'edit', 'name': 'update_goal', 'input': {'body': '每天读十分钟'}}],
        [{'type': 'text', 'text': '阅读目标已修改。'}],
    ])
    class EditModel(Model):
        async def chat(self, **request):
            return Reply(next(replies), 100, 40)
    g.model = EditModel()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(g)), base_url='http://testserver') as client:
        job = (await client.post('/api/chat', json={'text': '改成每天读十分钟', 'session_id': topic['id']})).json()
        await g.run_one()
        assert g.require('jobs', job['id'])['status'] == 'succeeded'
        assert g.record_detail(goal['id'])['payload']['body'] == '每天读十分钟'
        assert g.record_detail(other['id'])['payload']['body'] == '选城市'


@pytest.mark.parametrize('text', ['先别修改目标', '如果修改目标会怎么样？', '目标完成了吗？', '引用：“把目标改成读书”'])
async def test_natural_phrasing_keeps_discussion_read_only(g, text):
    goal = reading(g)
    request = g.chat(text)
    with pytest.raises(ValueError):
        await Agent(g).call_tool(request, g.job_config(), 'update_goal', {'body': '意外修改'})
    assert g.record_detail(goal['id'])['payload']['body'] == '每日阅读'


async def test_named_practice_steps_are_saved_verbatim_from_chat(g):
    title = '自学吉他隔日练习安排'
    goal = g.record('goal', {'title': title, 'body': '隔日练习三十分钟', 'next_step': '列出练习项目顺序'})
    other = g.record('goal', {'title': '自学吉他隔日练习安排备选', 'next_step': '原安排'})
    steps = '前10分钟爬格子热身，后20分钟练习周杰伦《晴天》，并记录难点。'

    class NoModel:
        async def chat(self, **request):
            raise AssertionError('明确点名的练习步骤更新不需要模型')

    g.model = NoModel()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(g)), base_url='http://testserver') as client:
        response = await client.post('/api/chat', json={'text': f'修改{title}，练习步骤改为{steps}'})
        assert response.status_code == 200
        job = response.json()
        await g.run_one()

    result = g.require('jobs', job['id'])
    assert result['status'] == 'succeeded' and steps in result['result']
    assert g.record_detail(goal['id'])['payload'].items() >= {'body': '隔日练习三十分钟', 'next_step': steps}.items()
    assert g.record_detail(other['id'])['payload']['next_step'] == '原安排'
    assert not g.db.all('SELECT id FROM schedules')


async def test_guitar_goal_chat_collects_details_confirms_and_edits_by_short_name(g):
    draft = {'title': '自学吉他弹唱《晴天》', 'body': '学期末前完整弹唱，隔天练半小时', 'next_step': '确认曲谱'}
    steps = '前10分钟爬格子热身，后20分钟练习周杰伦《晴天》，并记录难点'
    opening = '我要再设置一个自学吉他的目标'
    details = '学会弹唱周杰伦的晴天，可以隔天练习半小时，在这学期末前吧'
    edit = f'修改一下自学吉他隔日练习安排，练习步骤改为{steps}'

    class GuitarModel:
        async def chat(self, **request):
            last = request['messages'][-1]['content']
            if last == opening:
                return Reply([{'type': 'text', 'text': '你希望学会弹哪首歌？'}])
            assert last == details
            return Reply([{'type': 'tool_use', 'id': 'draft', 'name': 'create_goal', 'input': draft}])

    g.model = GuitarModel()
    for text in (opening, details):
        job = g.chat(text)
        await g.run_one()
        assert g.require('jobs', job['id'])['status'] == 'succeeded'
    assert '回复「确认」' in g.require('jobs', job['id'])['result']
    assert not g.db.all("SELECT id FROM records WHERE kind='goal'")

    confirmation = g.chat('确认')
    await g.run_one()
    assert g.require('jobs', confirmation['id'])['result'] == '已保存目标「自学吉他弹唱《晴天》」。'
    goal = g.db.one("SELECT id FROM records WHERE kind='goal'")

    failed = g.chat(edit)
    g.db.execute("UPDATE jobs SET status='failed' WHERE id=?", (failed['id'],))
    retry = g.chat('你再试一次写入')
    await g.run_one()
    assert g.require('jobs', retry['id'])['status'] == 'succeeded'
    assert g.record_detail(goal['id'])['payload']['next_step'] == steps
    direct = g.chat(edit)
    await g.run_one()
    assert g.require('jobs', direct['id'])['status'] == 'succeeded'


async def test_answering_clarification_by_title_completes_original_edit(g):
    goal = reading(g)
    other = g.record('goal', {'title': '周末运动', 'body': '跑步'})
    class AskModel(Model):
        async def chat(self, **request):
            if isinstance(request['messages'][-1]['content'], str):
                return Reply([{'type': 'tool_use', 'id': 'edit', 'name': 'update_goal', 'input': {'body': '每天十分钟'}}], 100, 40)
            return Reply([{'type': 'text', 'text': '想改阅读目标还是周末运动？'}], 100, 40)
    g.model = AskModel()
    initial = g.chat('修改目标描述为每天十分钟')
    await g.run_one()
    assert g.require('jobs', initial['id'])['status'] == 'succeeded'
    assert g.record_detail(goal['id'])['payload']['body'] == '每日阅读'
    selection = g.chat('阅读目标', initial['session_id'])
    await g.run_one()
    assert g.require('jobs', selection['id'])['status'] == 'succeeded'
    assert g.record_detail(goal['id'])['payload']['body'] == '每天十分钟'
    assert g.record_detail(other['id'])['payload']['body'] == '跑步'


async def test_repeat_guitar_edit_uses_short_name_and_strips_separator(g):
    goal = g.record('goal', {'title': '自学吉他弹唱《晴天》', 'body': '隔天练习'})
    first = '前10分钟爬格子热身，后20分钟练《晴天》，并记录难点'
    second = '前20分钟爬格子热身，后20分钟练习周杰伦《晴天》，并记录难点和收获'
    for command, expected in (
        (f'把自学吉他弹唱《晴天》目标的练习步骤改成：{first}', first),
        (f'再修改一下自学吉他隔日练习安排，练习步骤改为{second}', second),
    ):
        job = g.chat(command)
        await g.run_one()
        assert g.require('jobs', job['id'])['result'].endswith(expected)
        assert g.record_detail(goal['id'])['payload']['next_step'] == expected


@pytest.mark.parametrize('answer', ['自学吉他弹唱晴天', '是的', '你提交吧'])
async def test_goal_edit_selects_or_confirms_persisted_target(g, answer):
    goal = g.record('goal', {'title': '自学吉他弹唱《晴天》', 'next_step': '练和弦'})
    if answer == '自学吉他弹唱晴天':
        other = g.record('goal', {'title': '绩点目标', 'next_step': '复习'})
        command = '修改目标：前 20 分钟爬格子热身，后 20 分钟练《晴天》，记录难点和收获'
    else:
        other = None
        command = '把吉他安排的练习步骤改成：前 20 分钟爬格子热身，后 20 分钟练《晴天》，记录难点和收获'
    expected = command.split('：', 1)[1]
    initial = g.chat(command)
    await g.run_one()
    assert '自学吉他弹唱《晴天》' in g.require('jobs', initial['id'])['result']
    assert g.record_detail(goal['id'])['payload']['next_step'] == '练和弦'
    selection = g.chat(answer, initial['session_id'])
    await g.run_one()
    assert g.require('jobs', selection['id'])['status'] == 'succeeded'
    assert g.record_detail(goal['id'])['payload']['next_step'] == expected
    if other:
        assert g.record_detail(other['id'])['payload']['next_step'] == '复习'


async def test_goal_content_edit_does_not_authorize_scheduling(g):
    topic = g.session('午餐计划')
    goal = g.record('goal', {'title': '饮食计划', 'body': '午餐五十克', 'session_id': topic['id']})
    job = g.chat('把午餐改成每天一百克', topic['id'])
    agent = Agent(g)
    result = await agent.call_tool(job, g.job_config(), 'update_goal', {'body': '午餐每天一百克'})
    assert result['id'] == goal['id']
    with pytest.raises(ValueError):
        await agent.call_tool(job, g.job_config(), 'schedule_task', {'title': '午餐', 'instruction': '午餐', 'kind': 'once', 'next_due': g.clock()+3600})
    assert not g.db.all('SELECT * FROM schedules')
