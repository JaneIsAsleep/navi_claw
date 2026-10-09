from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
URL = 'http://127.0.0.1:8766/api'


def get(path):
    with urllib.request.urlopen(URL + path, timeout=5) as response:
        return json.load(response)


def command(name, *args):
    return subprocess.run([str(ROOT / name), *args], cwd=ROOT, text=True, capture_output=True, check=True).stdout


before, health = get('/state'), get('/health')
before_messages = {row['id']: get('/sessions/' + row['id'] + '/messages') for row in before['sessions']}
before_evidence = {row['id']: get('/jobs/' + row['id'] + '/evidence') for row in before['jobs']}
report = {'timestamp': datetime.now(timezone.utc).isoformat(), 'evidence_type': 'actual-local-runtime', 'checks': []}
command('start.command', '--no-browser')
assert get('/health')['pid'] == health['pid']
report['checks'].append('重复启动复用同一个 PID')
process_state = subprocess.check_output(['ps', '-o', 'ppid=,tty=', '-p', str(health['pid'])], text=True).strip().split()
assert process_state[0] == '1' and process_state[1] in {'??', '?'}
report['checks'].append('启动父进程退出后 Gateway 由 PID 1 托管且没有终端')
command('stop.command')
try:
    get('/health')
    raise AssertionError('停止后仍可连接')
except urllib.error.URLError:
    pass
report['checks'].append('停止后 HTTP 入口断开')
command('start.command', '--no-browser')
after, new_health = get('/state'), get('/health')
assert new_health['instance'] != health['instance']
for collection in ('sessions', 'schedules', 'jobs', 'actions', 'notifications', 'records'):
    assert {r['id'] for r in before[collection]} <= {r['id'] for r in after[collection]}
for row in before['records']:
    assert row['payload'] == next(current['payload'] for current in after['records'] if current['id'] == row['id'])
for identity, messages in before_messages.items():
    assert messages == get('/sessions/' + identity + '/messages')
for identity, evidence in before_evidence.items():
    restored = get('/jobs/' + identity + '/evidence')
    for collection in ('attempts', 'tools', 'actions', 'approvals', 'outbox', 'deliveries'):
        assert evidence[collection] == restored[collection]
    assert evidence['job']['result'] == restored['job']['result']
report['checks'].append('新进程启动后会话、调度、作业、审批、投递和个人条目 ID 保留')
report['checks'].append('重启后原始消息、个人条目内容、作业结果、工具调用、审批及投递记录逐项一致')
assert before['settings'] == after['settings']
report['checks'].append('配置和密钥已配置状态保留；未输出或复制密钥')
cli_sessions = json.loads(command('navi', 'sessions'))
assert {r['id'] for r in cli_sessions} == {r['id'] for r in after['sessions']}
report['checks'].append('实际 CLI 与 Web 查看相同会话 ID')
schedule = next(r for r in after['schedules'] if r['title'] == '界面验收周期任务')
command('navi', 'resume', schedule['id'])
assert next(r for r in get('/state')['schedules'] if r['id'] == schedule['id'])['enabled'] == 1
command('navi', 'pause', schedule['id'])
command('navi', 'edit-schedule', schedule['id'], '--interval', '5400')
saved = next(r for r in get('/state')['schedules'] if r['id'] == schedule['id'])
assert saved['enabled'] == 0 and saved['interval_seconds'] == 5400
report['checks'].append('CLI 恢复、暂停、编辑调度后 Web 状态一致')
report['counts_after_restart'] = {k: len(after[k]) for k in ('sessions', 'schedules', 'jobs', 'actions', 'notifications', 'records')}
report['status'] = 'passed'
previous_path = ROOT / 'engineering/evidence/01-initial/runtime-evidence.json'
previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
report['native_terminal_window_close'] = previous.get('native_terminal_window_close', 'not yet checked')
if report['native_terminal_window_close'] == 'passed, directly confirmed by user':
    report['checks'].append('用户实际关闭 start.command 的原生终端窗口后，确认网页仍正常')
(ROOT / 'engineering/evidence/01-initial/runtime-evidence.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False, indent=2))
