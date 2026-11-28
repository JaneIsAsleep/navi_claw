import json
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from navi import cli
from navi.gateway import Gateway
from navi.web import create_app
from test_gateway import Clock, Model, ROOT


@pytest.fixture
def shell(tmp_path, monkeypatch, capsys):
    gateway = Gateway(ROOT, tmp_path, Model(), Clock())
    web = TestClient(create_app(gateway))
    origin = 'http://127.0.0.1:8766'
    monkeypatch.setattr(httpx, 'request', lambda method, url, json, **options:
                        web.request(method, url.removeprefix(origin), json=json))
    monkeypatch.setattr(httpx, 'get', lambda url, **options: web.get(url.removeprefix(origin)))

    def run(*args, code=0, raw=False):
        monkeypatch.setattr(sys, 'argv', ['navi', *args])
        if code:
            with pytest.raises(SystemExit) as error:
                cli.main()
            assert error.value.code == code
            return capsys.readouterr().err
        cli.main()
        output = capsys.readouterr().out
        return output if raw else json.loads(output)

    return run, web, gateway


def test_cli_settings_and_schedule_lifecycle(shell, monkeypatch):
    run, web, gateway = shell
    monkeypatch.setattr(cli.getpass, 'getpass', lambda prompt: 'fake-model-key')
    settings = run('settings', 'set', '--set-key', '--notification-policy', 'strict',
                   '--workflow', 'deadline_briefing', '--daily-requests', '12',
                   '--messages-url', 'https://example.com/v1/messages',
                   '--completions-url', 'https://example.com/v1/chat/completions')
    assert settings['api_key_configured'] and 'fake-model-key' not in json.dumps(settings)
    assert settings['notification_policy'] == 'strict'
    endpoint = settings['endpoint_id']
    assert endpoint and run('settings', 'show')['endpoint_id'] == endpoint
    assert gateway.settings.api_key(gateway.settings.current()) == 'fake-model-key'
    assert run('settings', 'check')['kind'] == 'connection'
    assert run('settings', 'set', '--endpoint', 'official')['endpoint_id'] == ''
    assert not run('settings', 'delete-endpoint', endpoint, '--yes')['endpoint_profiles']
    assert run('settings', 'usage')['requests'] == 0

    goal = run('goal', 'add', '日常目标', '--next-step', '复习')
    reminder = run('schedule', '周会', '准备简报', '--goal', goal['id'], '--at', '2027-01-04T09:00',
                   '--timezone', 'Asia/Shanghai', '--calendar-timezone', 'Asia/Shanghai', '--interval', '604800')
    assert reminder['calendar_timezone'] == 'Asia/Shanghai'
    assert run('goal', 'show', goal['id'])['schedules'][0]['id'] == reminder['id']
    edited = run('edit-schedule', reminder['id'], '--at', '2027-01-05T10:00', '--timezone', 'Asia/Shanghai')
    assert edited['next_due'] > reminder['next_due']
    assert run('schedule-history', reminder['id']) == []
    assert run('complete-schedule', reminder['id'])['enabled'] == 0
    assert run('remove-schedule', reminder['id'])['removed_from_reminders'] == 1
    assert web.get('/api/state').json()['settings']['notification_policy'] == 'strict'


def test_cli_record_subcommands_and_conversation_search(shell):
    run, _, gateway = shell
    memory = run('memory', 'add', '简洁偏好', '--body', '简短回答')
    assert run('memory', 'disable', memory['id'])['payload']['enabled'] is False
    assert run('memory', 'edit', memory['id'], '--title', '新偏好')['payload']['body'] == '简短回答'
    assert run('memory', 'delete', memory['id'], '--yes')['deleted']
    assert run('memory', 'list') == []

    goal = run('goal', 'add', '准备答辩', '--body', '练习介绍', '--next-step', '整理报告')
    assert run('goal', 'pause', goal['id'])['payload']['state'] == 'paused'
    assert run('goal', 'resume', goal['id'])['payload']['state'] == 'active'
    assert run('goal', 'complete', goal['id'])['goal']['payload']['state'] == 'completed'
    assert run('goal', 'reactivate', goal['id'])['goal']['payload']['state'] == 'active'

    feed = gateway.record('feed', {'title': '官方发布摘要', 'body': '来自订阅的正文', 'origin': 'generated'})
    assert run('feed', 'list')['items'][0]['id'] == feed['id']
    job = run('chat', '正文检索词 123', '--new-session', '--reference', feed['id'], '--reference', goal['id'])
    session = job['session_id']
    messages = run('messages', session)
    assert [row['body'] for row in messages[0]['references']] == ['来自订阅的正文', '练习介绍']
    run('archive-session', session)
    assert run('search-sessions', '正文检索词 123') == []
    hits = run('search-sessions', '正文检索词 123', '--all', '--limit', '1')
    assert hits[0]['id'] == session and hits[0]['state'] == 'archived'
    assert run('search-sessions', '正文检索词 123', '--all', '--offset', '1') == []
    main_job = run('chat', '主对话测试正文')
    assert '--yes' in run('reset-main', code=2)
    assert '仍有工作' in run('reset-main', '--yes', code=1)
    run('cancel-job', main_job['id'], code=1)
    assert run('reset-main', '--yes')['id'] == gateway.main_session()['id']
    assert run('messages', gateway.main_session()['id']) == []


def test_cli_approval_versions_artifact_download_and_reminder_ack(shell, tmp_path):
    run, _, gateway = shell
    run('settings', 'set', '--notification-policy', 'strict')
    job = run('chat', '验收审批')
    export = gateway.propose(job['id'], 'export', '简报', 'brief.md', '原始正文')
    edited = run('edit-action', export['id'], '--body', '更新后的正文')
    assert edited['version'] == 2 and edited['content_hash'] != export['content_hash']
    assert run('approve', export['id'], '--hash', export['content_hash'], code=1)
    assert not (tmp_path / 'exports').exists()
    assert run('action-history', export['id'])['versions']
    run('approve', edited['id'], '--hash', edited['content_hash'])
    reminder = gateway.propose(job['id'], 'notification', '站内提醒', 'inbox', '提醒正文')
    run('approve', reminder['id'], '--hash', reminder['content_hash'])
    gateway.deliver()
    notice, = run('notifications')
    run('acknowledge', notice['outbox_id'])
    assert all(row.get('outbox_id') != notice['outbox_id'] for row in run('messages', job['session_id']))

    artifact, = run('artifact', 'list')
    file = tmp_path / 'download.md'
    assert run('artifact', 'download', artifact['id'], '-o', str(file))['path'] == str(file)
    assert file.read_text() == '更新后的正文'
    assert '已存在' in run('artifact', 'download', artifact['id'], '-o', str(file), code=1)
    assert run('artifact', 'download', artifact['id'], '--output=-', raw=True) == '更新后的正文'


def test_cli_feed_source_and_check_item_management(shell, monkeypatch):
    run, _, gateway = shell
    async def preview(url):
        assert url.startswith('https://')
        return {'url': url, 'title': '测试订阅', 'items': []}

    monkeypatch.setattr(gateway.feeds, 'preview', preview)
    assert run('feed', 'preview', 'https://example.com/feed')['title'] == '测试订阅'
    added = run('feed', 'add-source', 'https://example.com/feed')
    assert run('feed', 'toggle-source', added['id'])['enabled'] is False
    assert any(row['subscription'] and row['subscription']['id'] == added['id'] for row in run('feed', 'sources'))
    assert run('feed', 'brief', '跟进版本')['feed_brief'] == '跟进版本'
    assert run('feed', 'generate')['kind'] == 'feed'
    item, = run('check-items', 'add', '准备材料', '--priority', '2', '--due-at', '2000000000')
    edited, = run('check-items', 'edit', item['id'], '--done', '--clear-due')
    assert edited['done'] and edited['due_at'] is None
    assert run('check-items', 'delete', item['id'], '--yes') == []


def test_cli_goal_and_idea_start_reuse_request_ids(shell):
    run, _, gateway = shell
    started = run('goal', 'start', 'career', '--request-id', 'goal-cli-test')
    assert run('goal', 'start', 'career', '--request-id', 'goal-cli-test') == started
    session = started['session_id']
    setup = json.loads(gateway.require('sessions', session)['goal_setup_json'])
    with gateway.db.transaction() as conn:
        card = gateway.append_card(conn, session, setup, '测试引导选项', [
            {'label': '结束引导', 'value': '结束引导', 'operation': 'close'},
        ])
    history = run('messages', session)
    assert history[-1]['interaction']['choices'] == card['choices']
    assert run('goal', 'choose', session, card['card_id'], card['choices'][0]['id'],
               '--version', str(card['setup_version']), '--wait')['status'] == 'closed'

    idea = gateway.record('idea', {'title': '开始准备报告', 'body': '写提纲', 'action_kind': 'document_draft',
                                   'includes': [], 'how_it_works': '', 'context_refs': []})
    assert run('idea', 'show', idea['id'])['payload']['body'] == '写提纲'
    args = ('idea', 'start', idea['id'], '--request-id', 'idea-cli-test')
    receipt = run(*args)
    assert run(*args) == receipt
    assert any('开始准备报告' in row['content'] for row in run('messages', receipt['session_id']))


def test_cli_stream_keeps_final_result_and_filters_control_sequences(monkeypatch, capsys):
    events = 'event: delta\ndata: {"text":"增量\\u001b"}\n\nevent: done\ndata: {"status":"succeeded"}\n\n'
    stream_client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=events)))
    def stream(method, url, *, timeout, trust_env):
        assert trust_env is False
        return stream_client.stream(method, url, timeout=timeout)
    monkeypatch.setattr(httpx, 'stream', stream)
    api = cli.CLIAdapter()
    final = {'status': 'succeeded', 'result': '完整回复'}
    monkeypatch.setattr(api, 'wait', lambda job_id: final)
    assert api.follow('test') == final
    assert capsys.readouterr().err == '增量\n'
    stream_client.close()
