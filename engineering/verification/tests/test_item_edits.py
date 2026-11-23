import json

import httpx
import pytest

from navi.agent import Agent
from navi.gateway import Gateway
from navi.model import Reply
from navi.storage import packed
from navi.web import create_app
from test_gateway import Clock, Model, ROOT


class EditModel:
    def __init__(self, *calls):
        self.steps = iter(calls)
        self.calls = []

    async def chat(self, **request):
        name, values = next(self.steps)
        assert name in {tool['name'] for tool in request['tools']}
        self.calls.append(request)
        return Reply([{'type': 'tool_use', 'id': str(len(self.calls)), 'name': name, 'input': values}])


def edit_model(kind, query, ids, **changes):
    return EditModel(('find_edit_targets', {'kind': kind, 'query': query}),
                     ('propose_item_edit', {'kind': kind, 'candidate_ids': ids, **changes}))


def review(gateway, job, decision='approve'):
    action = gateway.db.one("SELECT * FROM actions WHERE kind='item_edit' AND job_id=?", (job['id'],))
    assert action and action['status'] == 'pending'
    gateway.decide(action['id'], decision, action['content_hash'])
    gateway.deliver()
    assert gateway.require('actions', action['id'])['status'] == ('delivered' if decision == 'approve' else 'rejected')
    return action


@pytest.fixture
def gateway(tmp_path):
    return Gateway(ROOT, tmp_path, Model(), Clock())


def reminder(gateway, title, offset=7200):
    return gateway.schedule({'title': title, 'instruction': '喝水', 'kind': 'recurring',
                             'next_due': gateway.clock() + offset, 'interval_seconds': 86400})


async def test_chat_ambiguity_uses_persisted_candidates_and_gateway_executes_after_approval(gateway):
    first, second = reminder(gateway, '晨间喝水提醒'), reminder(gateway, '晚间喝水提醒', 14400)
    # A model that guesses one candidate cannot hide the other literal match.
    gateway.model = edit_model('reminder', '喝水', [first['id']], when='晚上九点')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(gateway)), base_url='http://testserver') as web:
        first_turn = (await web.post('/api/chat', json={'text': '把喝水的提醒改到晚上九点', 'timezone': 'Asia/Taipei'})).json()
        await gateway.run_one()
        assert '找到多条' in gateway.require('jobs', first_turn['id'])['result']
        assert not gateway.db.all('SELECT id FROM actions')
        gateway.model = EditModel(('choose_edit_target', {'id': second['id']}))
        selection = (await web.post('/api/chat', json={'text': '晚上那条', 'timezone': 'Asia/Taipei'})).json()
        await gateway.run_one()
        action = next(row for row in (await web.get('/api/state')).json()['actions'] if row['job_id'] == selection['id'])
        assert action['target'] == second['id'] and action['status'] == 'pending'
        assert action['source_session_id'] == selection['session_id']
        body = json.loads(action['body'])
        assert body['before'] == {'next_due': second['next_due']}
        assert set(body['changes']) == {'next_due'}
        api = f"/api/actions/{action['id']}/decision"
        approval = {'decision': 'approve', 'content_hash': action['content_hash']}
        assert (await web.post(api, json={**approval, 'content_hash': 'different'})).status_code == 400
        assert (await web.post(api, json={**approval, 'target': first['id']})).status_code == 400
        assert (await web.post(api, json=approval)).status_code == 200
        assert gateway.require('schedules', second['id']) == second

    restored = Gateway(ROOT, gateway.directory, gateway.model, gateway.clock)
    restored.deliver()
    restored.decide(action['id'], 'approve', action['content_hash'])
    restored.deliver()
    assert restored.require('schedules', first['id']) == first
    assert restored.require('schedules', second['id'])['next_due'] == body['changes']['next_due']
    assert restored.require('schedules', second['id'])['interval_seconds'] == 86400
    history = restored.action_history(action['id'])
    assert history['versions'][0]['status'] == 'delivered'
    assert len(history['approvals']) == len(history['deliveries']) == 1


@pytest.mark.parametrize('intervening_change', ['reject', 'pause', 'tamper', 'new_time_passed'])
async def test_item_edit_never_applies_rejection_stale_state_or_unapproved_payload(gateway, intervening_change):
    row = reminder(gateway, '喝水打卡')
    job = gateway.chat('把喝水的改成半小时后')
    action = gateway.propose_item_edit(job, 'reminder', row['id'], {'when': '半小时后'})
    with pytest.raises(ValueError, match='逐条审批'):
        gateway.decide(action['id'], 'approve', action['content_hash'], actor='policy')
    with pytest.raises(ValueError):
        gateway.edit_action(action['id'], {'body': 'different'})
    if intervening_change == 'reject':
        gateway.decide(action['id'], 'reject', action['content_hash'])
        with pytest.raises(ValueError):
            gateway.decide(action['id'], 'approve', action['content_hash'])
    else:
        gateway.decide(action['id'], 'approve', action['content_hash'])
        if intervening_change == 'pause':
            gateway.edit_schedule(row['id'], {'operation': 'pause'})
        elif intervening_change == 'tamper':
            stored = gateway.db.one('SELECT payload FROM outbox WHERE action_id=?', (action['id'],))
            payload = json.loads(stored['payload'])
            body = json.loads(payload['body'])
            body['changes']['instruction'] = '未经批准'
            gateway.db.execute('UPDATE outbox SET payload=? WHERE action_id=?', (packed({**payload, 'body': packed(body)}), action['id']))
        else:
            gateway.clock.now += 2000
    gateway.deliver()
    history = gateway.action_history(action['id'])
    assert not history['deliveries']
    assert history['versions'][0]['status'] == ('rejected' if intervening_change == 'reject' else 'invalidated')
    assert gateway.require('schedules', row['id'])['next_due'] == row['next_due']
    assert gateway.require('tasks', row['task_id'])['instruction'] == '喝水'


async def test_current_turn_overrules_candidates_and_values_replayed_from_history(gateway):
    stale = reminder(gateway, '午间拉伸打卡')
    target = reminder(gateway, '论文交稿提醒')
    job = gateway.chat('交稿那个提醒，内容改成「提交终稿」。')
    agent, config = Agent(gateway), gateway.job_config()
    for query in ('拉伸', '交稿'):
        await agent.call_tool(job, config, 'find_edit_targets', {'kind': 'reminder', 'query': query})
    with pytest.raises(ValueError, match='候选与当前这句'):
        await agent.call_tool(job, config, 'propose_item_edit',
                              {'kind': 'reminder', 'candidate_ids': [stale['id']], 'title': '活动一下'})
    with pytest.raises(ValueError, match='引号内的新值'):
        await agent.call_tool(job, config, 'propose_item_edit',
                              {'kind': 'reminder', 'candidate_ids': [target['id']], 'instruction': '上传草稿'})
    assert not gateway.db.all('SELECT id FROM actions')
    result = await agent.call_tool(job, config, 'propose_item_edit',
                                   {'kind': 'reminder', 'candidate_ids': [target['id']], 'instruction': '提交终稿'})
    assert result['target_id'] == target['id']


async def test_lookup_and_edit_tool_schema_disallow_guessing_cross_kind_and_unauthorized_fields(gateway):
    row = reminder(gateway, '水分补给')
    job = gateway.chat('把我的补水打卡改成晚上九点')
    agent, config = Agent(gateway), gateway.job_config()
    valid = {'kind': 'reminder', 'candidate_ids': [row['id']], 'when': '晚上九点'}
    with pytest.raises(ValueError, match='先用'):
        await agent.call_tool(job, config, 'propose_item_edit', valid)
    with pytest.raises(ValueError):
        await agent.call_tool(job, config, 'find_edit_targets', {'kind': 'reminder', 'query': '', 'offset': True})
    result = await agent.call_tool(job, config, 'find_edit_targets', {'kind': 'reminder', 'query': '补水打卡'})
    assert result['scope'] == 'catalog' and result['candidates'][0]['id'] == row['id']
    for arguments in ({**valid, 'target': row['id']}, {**valid, 'candidate_ids': ['0' * 32]},
                      {**valid, 'candidate_ids': [row['id'], row['id']]}, {**valid, 'body': '目标字段'},
                      {**valid, 'when': 123}, {'kind': 'reminder', 'when': '晚上九点'}):
        with pytest.raises(ValueError):
            await agent.call_tool(job, config, 'propose_item_edit', arguments)
    result = await agent.call_tool(job, config, 'propose_item_edit', {'kind': 'reminder', 'candidate_ids': []})
    assert result['status'] == 'no_match'
    assert not gateway.db.all('SELECT id FROM actions')


async def test_chat_retries_when_model_claims_to_have_created_approval_without_tools(gateway):
    row = reminder(gateway, '论文交稿提醒')

    class ClaimsWithoutTool:
        def __init__(self):
            self.steps = iter(('claim', 'lookup', 'propose'))

        async def chat(self, **request):
            step = next(self.steps)
            if step == 'claim':
                return Reply([{'type': 'text', 'text': '已准备审批，请点击批准执行。'}])
            if step == 'lookup':
                assert '尚无成功工具回传' in request['messages'][-1]['content']
                return Reply([{'type': 'tool_use', 'id': step, 'name': 'find_edit_targets',
                               'input': {'kind': 'reminder', 'query': '交稿'}}])
            return Reply([{'type': 'tool_use', 'id': step, 'name': 'propose_item_edit',
                           'input': {'kind': 'reminder', 'candidate_ids': [row['id']], 'instruction': '提交终稿'}}])

    gateway.model = ClaimsWithoutTool()
    job = gateway.chat('交稿那个提醒，帮我把文案换成「提交终稿」。')
    await gateway.run_one()
    assert gateway.db.one("SELECT target FROM actions WHERE job_id=? AND status='pending'", (job['id'],)) == {'target': row['id']}
    assert gateway.require('tasks', row['task_id'])['instruction'] == '喝水'
