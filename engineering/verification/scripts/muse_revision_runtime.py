"""Check command entry points on the isolated preview after each stage."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[3]
DATA=ROOT/'engineering/verification/.cache/muse-revision-preview'
ENV={**os.environ,'NAVI_ACCEPTANCE_DATA':str(DATA)}
URL='http://127.0.0.1:8766/api'


def command(name,*args):
    return subprocess.check_output([str(ROOT/name),*args],cwd=ROOT,env=ENV,text=True)


def get(path):
    with urllib.request.urlopen(URL+path,timeout=10) as response:return json.load(response)


def check(stage):
    before=get('/state');health=get('/health')
    assert '正在运行' in command('status.command')
    command('start.command','--no-browser')
    assert get('/health')['pid']==health['pid']
    process=subprocess.check_output(['ps','-o','ppid=,tty=','-p',str(health['pid'])],text=True).split()
    assert process[0]=='1' and process[1] in {'??','?'}
    histories={r['id']:get('/sessions/'+r['id']+'/messages') for r in before['sessions']}
    command('stop.command')
    try:get('/health');raise AssertionError('停止后仍运行')
    except urllib.error.URLError:pass
    command('start.command','--no-browser')
    after=get('/state')
    assert get('/health')['instance']!=health['instance']
    for kind in ('sessions','records','actions','schedules'):
        assert {r['id'] for r in before[kind]}=={r['id'] for r in after[kind]}
    for identity,rows in histories.items():
        current=get('/sessions/'+identity+'/messages')
        fields=('id','role','content','job_id','created','references','outbox_id','origin_session_id','unread')
        assert [{k:r.get(k) for k in fields} for r in rows]==[{k:r.get(k) for k in fields} for r in current]
    cli=json.loads(command('navi','sessions'))
    assert {r['id'] for r in cli}=={r['id'] for r in after['sessions']}
    report={'stage':stage,'timestamp':time.time(),'status':'passed','checks':['启动器退出后 PID 1 托管，无终端','重复启动复用进程','实际停止、重新启动和持久状态恢复','CLI 和 Web 会话一致'],'browser_closed':'verified separately by UI and background endpoint'}
    path=ROOT/'engineering/evidence/04-muse-revision-v1/implementation'/f'runtime-stage{stage}.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('stage',stage,'runtime passed')

if __name__=='__main__':check(int(sys.argv[1]))
