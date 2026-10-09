import asyncio
from datetime import datetime
import json
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from navi.gateway import Gateway
from navi.model import Reply
from navi.storage import packed
from navi.web import create_app
from test_gateway import ROOT, Clock, Model


class StructuredModel(Model):
    def __init__(self, value=None):
        super().__init__()
        self.value = value or {'title': '阅读报告', 'display_abstract': '整理阅读报告提纲，已明确下一步。'}

    async def chat(self, **request):
        if 'display_abstract' not in request['system']:
            return await super().chat(**request)
        self.calls.append(request)
        await request['on_delta']('不可流入聊天的 JSON')
        return Reply([{'type': 'text', 'text': packed(self.value)}], 100, 40)


@pytest.fixture
def g(tmp_path):
    gateway = Gateway(ROOT, tmp_path, StructuredModel(), Clock())
    warm(gateway)
    return gateway


def warm(gateway):
    # These tests exercise publishing; the v2 suite measures the actual awake gate.
    gateway._awake_since = gateway.clock() - 60
    gateway._last_tick = gateway.clock()


async def test_metadata_is_background_preserves_manual_title_and_hides_private_summary(g):
    topic = g.session()
    g.chat('这是临时测试。请整理阅读报告提纲。', topic['id'])
    assert g.require('sessions', topic['id'])['title'] == '新对话'
    await g.run_one()
    meta = g.db.one("SELECT * FROM jobs WHERE kind='session_meta'")
    assert meta and meta['status'] == 'pending'
    assert len(g.messages(topic['id'])) == 2
    queue = asyncio.Queue()
    g.listeners[meta['id']] = {queue}
    await g.run_one()
    row = g.require('sessions', topic['id'])
    assert row['title'] == '阅读报告' and row['display_abstract']
    assert all(event['event'] != 'delta' for event in list(queue._queue))
    assert len(g.messages(topic['id'])) == 2
    g.db.execute("UPDATE sessions SET summary='private protected state' WHERE id=?", (topic['id'],))
    result = g.search_sessions('阅读')[0]
    assert 'summary' not in result and 'summary_through' not in result
    assert 'private' not in packed(result)
    assert g.main_session()['title'] == '主对话'


async def test_metadata_does_not_overwrite_rename_during_generation(g):
    topic = g.session()
    g.chat('阅读报告', topic['id']); await g.run_one()
    g.edit_session(topic['id'], {'title': '我的标题'})
    await g.run_one()
    assert g.require('sessions', topic['id'])['title'] == '我的标题'
    assert g.require('sessions', topic['id'])['display_abstract']


async def test_legacy_metadata_only_renames_exact_prefix_and_failure_keeps_chat(g):
    for title, expected in [('旧的第一条消息', '阅读报告'), ('来源不明旧标题', '来源不明旧标题')]:
        topic = g.session(title)
        g.chat('旧的第一条消息', topic['id']); await g.run_one(); await g.run_one()
        assert g.require('sessions', topic['id'])['title'] == expected
    topic = g.session()
    g.chat('下一件事', topic['id']); await g.run_one()
    g.model.value = {'title': 'x' * 17, 'display_abstract': ''}
    await g.run_one()
    assert g.require('sessions', topic['id'])['title'] == '新对话'
    assert len(g.messages(topic['id'])) == 2
    assert g.db.one("SELECT status FROM jobs WHERE trigger_key=?", ('meta:' + topic['id'],))['status'] == 'failed'
    restored = Gateway(ROOT, g.directory, StructuredModel(), g.clock)
    restored.open_session(topic['id'])
    assert len(restored.db.all("SELECT id FROM jobs WHERE trigger_key=?", ('meta:' + topic['id'],))) == 1


def test_search_paginates_1000_titles_literal_and_never_reads_body(g):
    for index in range(1000):
        g.session(f'话题 {index:04}')
    special = g.session("100%_ 'quote'")
    g.edit_session(special['id'], {'operation': 'archive'})
    assert len(g.search_sessions('话题')) == 1000
    assert len(g.search_sessions('', recent=True, limit=20)) == 20
    assert len(g.search_sessions('话题', limit=20, offset=980)) == 20
    assert g.search_sessions("%_ 'quote'")[0]['state'] == 'archived'
    with TestClient(create_app(g)) as client:
        response = client.get('/api/sessions/search?recent=true&limit=20&offset=1000')
        assert response.status_code == 200 and len(response.json()) == 2
        assert client.get('/api/sessions/search?limit=0').status_code == 400
    assert g.require('sessions', special['id'])['state'] == 'archived'


def ideas(refs=None):
    return {'items': [{'title': title, 'abstract': '整理一份可以直接使用的结果。', 'body': '我帮你整理当前信息并给出可用草稿。', 'includes': ['一份草稿'], 'how_it_works': '根据你提供的资料开始。', 'action_kind': kind, 'context_refs': refs or []} for title, kind in [('拆解阅读目标','goal_breakdown'),('整理来源摘要','source_summary'),('起草读书报告','document_draft'),('确定本周下一步','next_step'),('复盘现有任务','task_review')]]}


class ContentModel(StructuredModel):
    async def chat(self, **request):
        if '恰好五个独立构想' in request['system']:
            self.calls.append(request)
            return Reply([{'type':'text','text':packed(self.value or ideas())}], 100, 40)
        if 'source_ids' in request['system']:
            self.calls.append(request)
            return Reply([{'type':'text','text':packed({'items':[{'title':'整理阅读材料','body':'本机阅读事项需要整理。','source_ids':['reading']} ]})}],100,40)
        return await super().chat(**request)


def local_clock(value):
    clock = Clock()
    clock.now = datetime.fromisoformat(value).replace(tzinfo=ZoneInfo('Asia/Taipei')).timestamp()
    return clock


async def test_calendar_trigger_deduplicates_coalesces_and_snapshots_timezone(tmp_path):
    clock = local_clock('2026-10-07T05:59:59')
    g = Gateway(ROOT,tmp_path,ContentModel(ideas()),clock)
    g.tick();g.tick()
    assert {r['trigger_key'] for r in g.snapshot()['jobs']} == {'daily:feed:2026-10-06','daily:ideas:2026-10-06'}
    clock.now += 1;g.tick();g.tick()
    assert len(g.db.all("SELECT id FROM jobs")) == 4
    clock.now += 5 * 86400
    restored = Gateway(ROOT,tmp_path,ContentModel(ideas()),clock)
    restored.recover();restored.tick();restored.tick()
    assert len(restored.db.all("SELECT id FROM jobs")) == 6
    latest=restored.db.one("SELECT * FROM jobs WHERE trigger_key='daily:ideas:2026-10-12'")
    assert json.loads(latest['config'])['timezone']=='Asia/Taipei'
    assert json.loads(latest['input'])['content_date']=='2026-10-12'
    restored.settings.update({'timezone':'America/New_York'})
    due=restored.content_due()
    assert due.hour == 6 and str(due.tzinfo)=='America/New_York'


async def test_atomic_five_ideas_failure_keeps_old_and_start_is_idempotent(g):
    g.model=ContentModel(ideas())
    g.tick()
    await g.run_one();await g.run_one()
    batch=[r for r in g.snapshot()['records'] if r['kind']=='idea']
    assert len(batch)==5
    assert not g.snapshot()['notifications'] and len(g.snapshot()['sessions'])==1
    old={r['id']:g.require('records',r['id'])['payload'] for r in batch}
    g.clock.now += 86400;g.tick();warm(g)
    g.model.value={'items':ideas()['items'][:4]}
    await g.run_one();await g.run_one()
    assert {r['id']:g.require('records',r['id'])['payload'] for r in batch}==old
    assert g.db.one("SELECT status FROM jobs WHERE kind='ideas' ORDER BY rowid DESC LIMIT 1")['status']=='failed'
    chosen=batch[0]
    start=g.start_idea(chosen['id'],'start-request-1')
    assert g.start_idea(chosen['id'],'start-request-1')==start
    assert g.messages(start['session_id'])[0]['job_id']==start['job_id']
    assert len(g.snapshot()['sessions'])==2
    g.clock.now+=86400;g.tick();warm(g);g.model.value=ideas()
    while g.db.one("SELECT id FROM jobs WHERE status='pending'"):
        await g.run_one()
    assert len([r for r in g.snapshot()['records'] if r['kind']=='idea'])==5
    assert g.record_detail(chosen['id'])['payload']['state']=='superseded'
    with pytest.raises(ValueError):g.start_idea(chosen['id'],'start-request-2')
    assert g.start_idea(chosen['id'],'start-request-1')==start


@pytest.mark.parametrize('change',['duplicate','unknown_ref','unsupported','six'])
async def test_invalid_batch_never_publishes(g,change):
    value=ideas()
    if change=='duplicate':value['items'][1]['title']=value['items'][0]['title']
    if change=='unknown_ref':value['items'][0]['context_refs']=['imaginary']
    if change=='unsupported':value['items'][0]['action_kind']='email_monitor'
    if change=='six':value['items'].append(value['items'][0])
    g.model=ContentModel(value);g.tick()
    await g.run_one();await g.run_one()
    assert not [r for r in g.snapshot()['records'] if r['kind']=='idea']


async def test_feed_sources_are_gateway_owned_and_pause_is_idempotent(g):
    g.model=ContentModel(ideas())
    g.skills.save_items([{'id':'reading','title':'阅读材料','priority':3}])
    g.settings.update({'feed_brief':'关注阅读'})
    job=g.generate_feed();await g.run_one()
    row=next(r for r in g.snapshot()['records'] if r['kind']=='feed')
    assert row['payload']['origin']=='generated'
    assert row['payload']['source_ids']==['reading']
    assert row['payload']['generated_at']==g.clock()
    assert not g.db.all("SELECT id FROM records WHERE kind='goal'")
    source=g.feeds.follow('python');g.feeds.follow('python')
    assert len(g.db.all("SELECT id FROM records WHERE kind='source'"))==1
    g.db.execute("UPDATE records SET payload=json_set(payload,'$.enabled',json('false')) WHERE id=?",(source['id'],))
    assert (await g.feeds.refresh())['checked_sources']==0


class GoalModel(ContentModel):
    async def chat(self, **request):
        if '引导用户确定一个目标' in request['system']:
            self.calls.append(request)
            data=json.loads(request['messages'][0]['content'])
            rounds=data['setup']['user_rounds']
            value=({'stage':'question','question':'选择哪种阅读方向？' if rounds==0 else '每次投入多久？','choices':[{'label':'短时阅读' if rounds==0 else '10 分钟','value':'每天短时阅读' if rounds==0 else '每天阅读10分钟'},{'label':'自己说明','value':'我自己说明'}]} if rounds<2 else {'stage':'confirm','draft':{'title':'每天阅读','abstract':'每天阅读10分钟。','body':'保持每天阅读的节奏。','next_step':'选一本书开始。'}})
            return Reply([{'type':'text','text':packed(value)}],100,40)
        return await super().chat(**request)


def active_card(g,session_id):
    setup=json.loads(g.require('sessions',session_id)['goal_setup_json'])
    return next(m['interaction'] for m in g.messages(session_id) if m['id']==setup['active_card_id'])


def choose(g,session_id,card,index=0):
    return g.goal_choice(session_id,{'card_id':card['card_id'],'choice_id':card['choices'][index]['id'],'setup_version':card['setup_version']})


async def test_goal_choices_persist_two_rounds_save_once_and_old_choices_conflict(g):
    g.model=GoalModel(ideas())
    start=g.start_goal('interests','goal-start-request')
    assert g.start_goal('interests','goal-start-request')==start
    with pytest.raises(ValueError):g.start_goal('finance','goal-start-request')
    await g.run_one();first=active_card(g,start['session_id'])
    queued=choose(g,start['session_id'],first)
    assert choose(g,start['session_id'],first)==queued
    with pytest.raises(ValueError):choose(g,start['session_id'],first,1)
    await g.run_one();second=active_card(g,start['session_id'])
    choose(g,start['session_id'],second);await g.run_one()
    confirm=active_card(g,start['session_id'])
    assert confirm['choices'][0]['operation']=='save'
    assert not [r for r in g.snapshot()['records'] if r['kind']=='goal']
    restored=Gateway(ROOT,g.directory,GoalModel(ideas()),g.clock)
    assert active_card(restored,start['session_id'])==confirm
    saved=choose(restored,start['session_id'],confirm)
    assert choose(restored,start['session_id'],confirm)==saved
    assert len([r for r in restored.snapshot()['records'] if r['kind']=='goal'])==1
    after=active_card(restored,start['session_id'])
    reminder=choose(restored,start['session_id'],after,1)
    assert json.loads(restored.require('jobs',reminder['job_id'])['config'])['goal_id']==saved['goal']['id']
    assert not restored.snapshot()['schedules']


async def test_goal_free_text_invalidates_old_confirm_and_deferral_does_not_save(g):
    g.model=GoalModel(ideas());start=g.start_goal('finance','financial-test-start')
    await g.run_one();first=active_card(g,start['session_id'])
    g.chat('具体目标是每天阅读10分钟，不需要基线。',start['session_id']);await g.run_one()
    with pytest.raises(ValueError):choose(g,start['session_id'],first)
    g.chat('先不要保存，只是讨论。',start['session_id']);await g.run_one()
    assert json.loads(g.require('sessions',start['session_id'])['goal_setup_json'])['status']=='closed'
    assert not [r for r in g.snapshot()['records'] if r['kind']=='goal']


async def test_goal_completion_invalidates_actions_outbox_jobs_and_restores_only_goal(g):
    topic=g.session('生命周期')
    goal=g.record('goal',{'title':'阅读测试','body':'每天阅读10分钟','state':'active'})
    other=g.record('goal',{'title':'独立目标','state':'active'})
    schedules=[]
    for target in (goal,other,None):
        schedules.append(g.schedule({'title':'关联提醒','instruction':'测试提醒','kind':'recurring','interval_seconds':3600,'next_due':g.clock(),'session_id':topic['id'],'goal_id':target['id'] if target else None}))
    g.tick()
    linked=g.db.one('SELECT * FROM jobs WHERE task_id=?',(schedules[0]['task_id'],))
    pending=g.propose(linked['id'],'export','未决文件','pending.md','不得写入')
    approved=g.propose(linked['id'],'notification','获准未投递','inbox','不得投递')
    g.settings.update({'notification_policy':'strict'})
    if approved['status']=='pending':g.decide(approved['id'],'approve',approved['content_hash'])
    result=g.goal_state(goal['id'],'complete')
    assert result['affected_tasks']==[schedules[0]['task_id']]
    assert g.require('jobs',linked['id'])['status']=='cancelled'
    assert g.require('actions',pending['id'])['status']=='invalidated'
    assert g.require('actions',approved['id'])['status']=='invalidated'
    g.deliver();assert not g.snapshot()['notifications'] and not (g.directory/'exports').exists()
    assert g.require('tasks',schedules[1]['task_id'])['state']=='active'
    assert g.require('tasks',schedules[2]['task_id'])['state']=='active'
    g.goal_state(goal['id'],'reactivate')
    assert g.require('schedules',schedules[0]['id'])['enabled']==0
    with pytest.raises(ValueError):g.edit_schedule(schedules[0]['id'],{'operation':'resume'})
    restored=Gateway(ROOT,g.directory,Model(),g.clock);restored.recover();restored.tick();restored.deliver()
    assert restored.require('tasks',schedules[0]['task_id'])['state']=='completed'
    assert not restored.snapshot()['notifications']


async def test_natural_authorization_corrects_actual_goal_id_and_cancel_needs_unique_match(g):
    from navi.agent import Agent
    job=g.chat('确认目标：每天阅读10分钟。然后为这个目标设置提醒，next_due 为 1800000000。')
    agent=Agent(g)
    goal=await agent.call_tool(job,g.job_config(),'create_goal',{'title':'阅读目标','body':'每天阅读10分钟','next_step':'选一本书'})
    schedule=await agent.call_tool(job,g.job_config(),'schedule_task',{'title':'阅读提醒','instruction':'阅读','kind':'once','next_due':1800000000,'goal_id':'made-up-id'})
    assert g.require('tasks',schedule['task_id'])['goal_id']==goal['id']
    for text in ('先不要保存目标','只是讨论如何创建目标','确认','引用：“取消阅读目标”','不要取消阅读目标'):
        request=g.chat(text)
        with pytest.raises(ValueError):
            await Agent(g).call_tool(request,g.job_config(),'cancel_goal' if '取消' in text else 'create_goal',{'title':'阅读目标'} if '取消' in text else {'title':'目标','body':'讨论','next_step':'讨论'})
    duplicate=g.record('goal',{'title':'阅读目标','state':'active'})
    request=g.chat('取消目标：阅读目标')
    ambiguous=await Agent(g).call_tool(request,g.job_config(),'cancel_goal',{'title':'阅读目标'})
    assert ambiguous['status']=='needs_clarification'
    assert len(ambiguous['matches'])==2
    request=g.chat('取消目标 '+goal['id'])
    result=await Agent(g).call_tool(request,g.job_config(),'cancel_goal',{'id':goal['id']})
    assert result['status']=='cancelled'
    assert g.record_detail(duplicate['id'])['payload']['state']=='active'


async def test_complete_tracking_cancels_running_model_and_late_result(g):
    started=asyncio.Event()
    class Slow(Model):
        async def chat(self,**request):
            started.set();await asyncio.sleep(20)
            return Reply([{'type':'text','text':'迟到结果'}],100,40)
    g.model=Slow()
    goal=g.record('goal',{'title':'结束运行测试','state':'active'})
    schedule=g.schedule({'title':'运行提醒','instruction':'提醒我阅读','kind':'recurring','interval_seconds':3600,'next_due':g.clock(),'goal_id':goal['id']})
    g.tick();execution=asyncio.create_task(g.run_one());await started.wait()
    g.goal_state(goal['id'],'complete');await execution
    assert not g.snapshot()['actions']
    assert not g.snapshot()['notifications']
    assert g.require('tasks',schedule['task_id'])['state']=='completed'
    assert g.db.one('SELECT status FROM jobs WHERE task_id=?',(schedule['task_id'],))['status']=='cancelled'


def test_100_historical_batches_do_not_enter_current_snapshot(g):
    for index in range(100):
        for item in ideas()['items']:
            g.record('idea',{**item,'batch_id':f'old-{index}','state':'superseded'})
    for item in ideas()['items']:g.record('idea',{**item,'batch_id':'current','state':'active'})
    current=[r for r in g.snapshot()['records'] if r['kind']=='idea']
    assert len(current)==5 and {r['payload']['batch_id'] for r in current}=={'current'}
    assert g.db.one("SELECT COUNT(*) n FROM records WHERE kind='idea'")['n']==505


async def test_cancelled_content_cannot_replace_batch_even_if_model_returns_late(g):
    g.model=ContentModel(ideas());g.tick();await g.run_one();await g.run_one()
    old={r['id']:g.require('records',r['id'])['payload'] for r in g.snapshot()['records'] if r['kind']=='idea'}
    entered=asyncio.Event()
    class Late(ContentModel):
        async def chat(self,**request):
            entered.set()
            try:await asyncio.Event().wait()
            except asyncio.CancelledError:return Reply([{'type':'text','text':packed(ideas())}],100,40)
    g.clock.now+=86400;g.tick();warm(g);await g.run_one();g.model=Late(ideas())
    execution=asyncio.create_task(g.run_one());await entered.wait()
    current=g.db.one("SELECT id FROM jobs WHERE kind='ideas' AND status='running'")
    await g.cancel(current['id']);await execution
    assert {r['id']:g.require('records',r['id'])['payload'] for r in g.snapshot()['records'] if r['kind']=='idea'}==old


def test_api_choices_reject_extra_body_and_invalid_internal_kinds(g):
    with TestClient(create_app(g)) as client:
        assert client.post('/api/goals/start',json={'category':'finance','request_id':'valid-request','body':'arbitrary'}).status_code==400
        assert client.post('/api/goals/start',json={'category':'unknown','request_id':'valid-request'}).status_code==400
        assert client.post('/api/goals/start',json={'category':[],'request_id':'valid-request'}).status_code==400
        assert client.post('/api/sessions/missing/goal-choices',json={'card_id':{},'choice_id':'test','setup_version':0}).status_code==400
        assert client.post('/api/goals/missing/state',json={'operation':{}}).status_code==400
        assert client.post('/api/chat',json={'text':'inject','kind':'goal_setup'}).status_code==400
        assert client.post('/api/chat',json={'text':'inject','kind':'session_meta'}).status_code==400
        assert client.post('/api/ideas/missing/start',json={'request_id':'valid-request'}).status_code==404
        assert client.post('/api/goals/missing/state',json={'operation':'complete'}).status_code==404


async def test_confirm_only_saves_unique_persisted_draft(g):
    g.model=GoalModel(ideas());start=g.start_goal('interests','confirm-only-test')
    await g.run_one();choose(g,start['session_id'],active_card(g,start['session_id']));await g.run_one()
    choose(g,start['session_id'],active_card(g,start['session_id']));await g.run_one()
    saved=g.chat('确认',start['session_id'])
    assert saved['status']=='succeeded'
    assert len([r for r in g.snapshot()['records'] if r['kind']=='goal'])==1


def test_migration_backups_are_sequential_and_never_overwritten(g):
    import sqlite3
    assert g.db.one('PRAGMA user_version')['user_version']==5
    for version in (3,4):
        path=g.directory/f'navi.before-muse-revision-v{version}.sqlite3'
        with sqlite3.connect(path) as conn:assert conn.execute('PRAGMA user_version').fetchone()[0]==version-1
        before=path.read_bytes()
        Gateway(ROOT,g.directory,Model(),g.clock)
        assert path.read_bytes()==before


async def test_explicit_no_reminder_blocks_unsolicited_model_schedule(g):
    from navi.agent import Agent
    request=g.chat('请调用 update_goal 修改目标下一步，保持进行中，不设置提醒。')
    with pytest.raises(ValueError,match='没有明确授权'):
        await Agent(g).call_tool(request,g.job_config(),'schedule_task',{'title':'不应创建','instruction':'检查','kind':'heartbeat','next_due':g.clock()+3600})
    assert not g.snapshot()['schedules']


async def test_ended_goal_invalidates_followup_card_without_resurrecting_it(g):
    g.model=GoalModel(ideas());start=g.start_goal('interests','ended-card-start')
    for _ in range(3):
        await g.run_one();card=active_card(g,start['session_id'])
        if card['choices'][0]['operation']=='save':break
        choose(g,start['session_id'],card)
    saved=choose(g,start['session_id'],card)
    followup=active_card(g,start['session_id'])
    g.goal_state(saved['goal']['id'],'complete')
    with pytest.raises(ValueError,match='步骤已更新'):
        choose(g,start['session_id'],followup,1)
    g.goal_state(saved['goal']['id'],'reactivate')
    assert json.loads(g.require('sessions',start['session_id'])['goal_setup_json'])['active_card_id'] is None
    assert not g.snapshot()['schedules']
