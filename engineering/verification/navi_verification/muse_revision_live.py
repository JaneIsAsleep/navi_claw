"""Real model checks use neutral records and charge the existing budget."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

from navi.gateway import Gateway
from navi.storage import packed
from .live import LiveGateway, ROOT, STATE_ROOT, wait, output_directory

V2_PHASE = 5


async def run(owner, key, stage=2, output_dir=None):
    output_dir = output_dir or output_directory()
    directory = Path(tempfile.mkdtemp(prefix='muse-revision-live-', dir=STATE_ROOT))
    g = LiveGateway(ROOT, directory)
    g.owner = owner
    g.settings.api_key = lambda: key
    g.settings.update(owner.settings.current())
    g.settings.update({'feed_brief': '关注 Python 官方更新，以及本机阅读材料整理事项。', 'notification_policy': 'strict'})
    g.skills.save_items([{'id':'reading','title':'整理中性阅读材料','body':'准备一个阅读笔记模板。','priority':3,'done':False}])
    g.feeds.follow('python')
    report = {'evidence_type':'live-deepseek-muse-revision','stage':stage,'timestamp':datetime.now(timezone.utc).isoformat(),'model':g.settings.current()['model'],'checks':[],'status':'running','directory':str(directory.relative_to(ROOT))}
    jobs=[]
    await g.start()
    try:
        for kind in ('feed','ideas'):
            for _ in range(100):
                row=g.db.one("SELECT * FROM jobs WHERE kind=? AND trigger_key LIKE 'daily:%'",(kind,))
                if row:break
                await asyncio.sleep(.1)
            assert row, '自动调度未排入 '+kind
            jobs.append(row['id']);await wait(g,row['id'])
        ideas=[r for r in g.snapshot()['records'] if r['kind']=='idea']
        assert len(ideas)==5 and len({r['payload']['title'] for r in ideas})==5
        assert all(r['payload']['batch_id'] for r in ideas)
        assert len(g.snapshot()['sessions'])==1 and not g.snapshot()['actions']
        feeds=[r for r in g.snapshot()['records'] if r['kind']=='feed' and r['payload'].get('origin')=='generated']
        assert feeds, '真实模型未产生与实际材料有关的关注内容'
        for row in feeds:
            for source in row['payload']['sources']:
                assert source['id'] in {i['id'] for i in g.skills.items()} or source['url'].startswith('https://')
        report['checks'].append('无人点击生成，实际 scheduler 自动排入并发布真实来源关注内容和完整五条构想，无提醒与目标写入')
        picked=next((r for r in ideas if r['payload']['action_kind']=='document_draft'),ideas[0])
        started=g.start_idea(picked['id'],'live-idea-request')
        assert g.start_idea(picked['id'],'live-idea-request')==started
        jobs.append(started['job_id']);await wait(g,started['job_id'])
        assert len(g.messages(started['session_id']))==2
        assert g.require('jobs',started['job_id'])['result'].strip()
        meta=g.db.one("SELECT * FROM jobs WHERE trigger_key=?",('meta:'+started['session_id'],))
        assert meta; jobs.append(meta['id']); await wait(g,meta['id'])
        topic=g.require('sessions',started['session_id'])
        assert topic['display_abstract'] and len(topic['title'])<=16 and topic['title']!='新对话'
        assert not g.search_sessions(topic['title'])[0].get('summary')
        assert not (directory/'exports').exists()
        report['checks'].append('现在就开始一次请求自动发送并得到真实首份输出；重复请求无新增，真实后台标题及摘要落库，导出仍未执行')
        if stage >= 3:
            for category in ('interests','finance'):
                begin=g.start_goal(category,'live-goal-'+category)
                jobs.append(begin['job_id']);await wait(g,begin['job_id'])
                assert g.start_goal(category,'live-goal-'+category)==begin
                for round_number in range(3):
                    setup=json.loads(g.require('sessions',begin['session_id'])['goal_setup_json'])
                    card=next(m['interaction'] for m in g.messages(begin['session_id']) if m['id']==setup['active_card_id'])
                    if setup['status']=='confirming':break
                    assert 2 <= len(card['choices']) <= 3
                    choice=card['choices'][0]
                    response=g.goal_choice(begin['session_id'],{'card_id':card['card_id'],'choice_id':choice['id'],'setup_version':card['setup_version']})
                    jobs.append(response['job_id']);await wait(g,response['job_id'])
                setup=json.loads(g.require('sessions',begin['session_id'])['goal_setup_json'])
                assert setup['status']=='confirming' and setup['user_rounds']<=2
                card=next(m['interaction'] for m in g.messages(begin['session_id']) if m['id']==setup['active_card_id'])
                values={'card_id':card['card_id'],'choice_id':card['choices'][0]['id'],'setup_version':card['setup_version']}
                saved=g.goal_choice(begin['session_id'],values)
                assert g.goal_choice(begin['session_id'],values)==saved
                actual=saved['goal']['id']
                assert actual==json.loads(g.require('sessions',begin['session_id'])['goal_setup_json'])['saved_goal_id']
                request=g.chat('请为这个目标设置单次站内提醒，标题为“中性目标验收提醒”，内容为检查已保存目标。next_due 为 '+str(g.clock()+86400)+'。只设置一个提醒。',begin['session_id'])
                jobs.append(request['id']);await wait(g,request['id'])
                task=g.db.one('SELECT * FROM tasks WHERE goal_id=?',(actual,))
                assert task and task['session_id']==begin['session_id']
                completed=g.goal_state(actual,'complete')
                assert task['id'] in completed['affected_tasks']
                g.goal_state(actual,'reactivate')
                assert g.require('tasks',task['id'])['state']=='completed'
                cancel=g.chat('请取消目标 '+actual+'，仅取消这一个目标。',begin['session_id'])
                jobs.append(cancel['id']);await wait(g,cancel['id'])
                assert g.record_detail(actual)['payload']['state']=='cancelled'
                assert any(t['name']=='cancel_goal' and t['result'].get('status')=='cancelled' for t in g.evidence(cancel['id'])['tools'])
                report['checks'].append(category+' 真实可点击选项，两轮内确认；幂等保存、真实关联提醒、完成与恢复、自然语言取消均持久生效')
            natural=g.chat('确认目标：每天整理10分钟的中性阅读笔记。只保存目标，不安排提醒。')
            jobs.append(natural['id']);await wait(g,natural['id'])
            assert any(t['name']=='create_goal' and t['result'].get('id') for t in g.evidence(natural['id'])['tools'])
            report['checks'].append('自然语言确认目标授权通过真实 create_goal 工具保存')
        report['status']='passed'
    except (AssertionError, RuntimeError, ValueError) as error:
        report.update(status='failed',error=str(error),failed_step=len(report['checks'])+1)
    finally:
        await g.stop()
        report['jobs']=[g.evidence(identity) for identity in jobs]
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        path=output_dir/'muse-v1.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('Muse revision live stage',stage,report['status'],report.get('error',''))
    return report['status']=='passed'


async def run_v2(owner, key, phase, output_dir):
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='muse-v2-live-', dir=STATE_ROOT))
    g = LiveGateway(ROOT, directory)
    g.owner = owner
    g.settings.api_key = lambda: key
    g.settings.update(owner.settings.current())
    g.settings.update({'feed_brief': '关注 Python、OpenAI 和 NASA 官方公开更新，说明具体变化与学习的关系。', 'notification_policy': 'strict'})
    if phase >= 5:
        g.settings.update({'feed_brief':'关注 Python、OpenAI、NASA 和 GitHub 官方公开更新。选择事实材料较充足的独立文章分别整理，不把同站的多篇不同主题文章合并成一条；说明具体变化与学习的关系，至多五条。'})
    for identity in ('openai', 'python', 'nasa'):
        g.feeds.follow(identity)
    report = {'evidence_type': 'live-deepseek-muse-v2', 'phase': phase, 'timestamp': datetime.now(timezone.utc).isoformat(), 'model': g.settings.current()['model'], 'checks': [], 'jobs': [], 'status': 'running', 'directory': str(directory.relative_to(ROOT))}
    jobs = []
    try:
        if phase >= 4:
            preview = await g.feeds.preview('https://github.blog/feed/')
            added = await g.feeds.add_custom(preview['url'], 'GitHub Blog')
            duplicate = await g.feeds.add_custom('https://GITHUB.BLOG:443/feed/#latest')
            assert added['id'] == duplicate['id'] and len(preview['items']) == 3
            assert not g.db.all('SELECT * FROM jobs') and not g.db.all('SELECT * FROM usage')
            report['source_preview'] = preview
            report['custom_source_id'] = added['id']
            report['checks'].append('v2 GitHub 官方 RSS 实际预览、再次读取后确认及规范 URL 去重，零模型调用')
        await g.start()
        for kind in ('feed', 'ideas'):
            for _ in range(100):
                row = g.db.one("SELECT * FROM jobs WHERE kind=? AND trigger_key LIKE 'daily:%'", (kind,))
                if row:
                    break
                await asyncio.sleep(.1)
            assert row, 'v2 自动调度未排入 ' + kind
            jobs.append(row['id']); await wait(g, row['id'])
            assert any(not usage['estimated'] for usage in g.evidence(row['id'])['usage']), '自动内容缺少真实 receipt'
        cards = [row for row in g.snapshot()['records'] if row['kind'] == 'idea']
        assert len(cards) == 5 and all(row['payload'].get('action_kind') for row in cards)
        feeds = [row for row in g.snapshot()['records'] if row['kind'] == 'feed' and row['payload'].get('origin') == 'generated']
        assert feeds and not g.snapshot()['actions']
        report['checks'].append('v2 三个预设源参与真实自动生成，五条可执行构想与动态有各自 receipt')
        if phase >= 5:
            evidence = g.evidence(jobs[0])
            checked = next(row['result'] for row in evidence['tools'] if row['name'] == 'check_updates')
            material = {row['id']: row for row in checked['items']}
            lengths = [{'id':row['id'], 'title':row['payload']['title'], 'body_length':len(row['payload']['body']), 'paragraphs':len([part for part in row['payload']['body'].split('\n\n') if part.strip()]), 'sources':row['payload'].get('sources'), 'images':row['payload'].get('images', [])} for row in feeds]
            report.update(feed_bodies=lengths, feed_materials=[{'id':row['id'], 'title':row['title'], 'body_length':len(row.get('body', '')), 'read_scope':row.get('read_scope'), 'read_at':row.get('read_at'), 'source_url':row.get('source_url')} for row in checked['items']])
            enough = [row for row in feeds if 140 <= len(row['payload']['body']) <= 300 and '\n\n' in row['payload']['body'] and sum(len(material.get(identity, {}).get('body', '')) for identity in row['payload']['source_ids']) >= 400]
            assert len(enough) >= 3, '事实材料充足的正文达标条目不足三条：' + packed(lengths)
            assert len(checked['items']) <= 12 and sum(len(row.get('body', '')) for row in checked['items']) <= 24000
            counts = {table: g.db.one('SELECT COUNT(*) n FROM ' + table)['n'] for table in ('jobs', 'messages', 'sessions', 'usage')}
            assert {row['id'] for row in g.feed_items()['items']} == {row['id'] for row in feeds}
            media = []
            for row in feeds:
                for image in row['payload'].get('images', []):
                    try:
                        data, mime = await g.feeds.media(image['asset_id'])
                        media.append({'asset_id':image['asset_id'], 'source_url':image['source_url'], 'status':'passed', 'mime':mime, 'bytes':len(data)})
                    except ValueError as error:
                        media.append({'asset_id':image['asset_id'], 'status':'failed', 'error':str(error)})
                    except Exception as error:
                        from navi.feed_http import FeedReadError
                        if not isinstance(error, FeedReadError):
                            raise
                        media.append({'asset_id':image['asset_id'], 'status':'failed', 'error':str(error)})
            assert any(row['status'] == 'passed' for row in media), '已发布动态没有实际可读取的来源图'
            assert counts == {table: g.db.one('SELECT COUNT(*) n FROM ' + table)['n'] for table in counts}
            report.update(feed_bodies=lengths, feed_materials=[{'id':row['id'], 'title':row['title'], 'body_length':len(row.get('body', '')), 'read_scope':row.get('read_scope'), 'read_at':row.get('read_at'), 'source_url':row.get('source_url')} for row in checked['items']], media_checks=media)
            chosen = enough[0]
            reference = {'id':chosen['id'], 'kind':'feed', 'title':chosen['payload']['title'], 'body':chosen['payload']['body'], 'source_ids':chosen['payload']['source_ids']}
            discussion = g.chat('请用两句话概括引用中可核实的变化。仅讨论，不调用工具，不保存目标、记忆或提醒。', g.main_session()['id'], references=[reference])
            jobs.append(discussion['id']); await wait(g, discussion['id'])
            assert g.db.one('SELECT COUNT(*) n FROM sessions')['n'] == counts['sessions']
            assert g.db.one('SELECT COUNT(*) n FROM messages')['n'] == counts['messages'] + 2
            assert any(not usage['estimated'] for usage in g.evidence(discussion['id'])['usage'])
            report['checks'].append('v2 至少三条真实完整正文达标，真实来源图可读取，分页与图片零模型，明确发送后才产生一个真实聊天及 receipt')
        topic = g.session('v2中性授权验收')
        goal = g.record('goal', {'title': 'v2阅读目标', 'body': '阅读公开资料', 'next_step': '选资料', 'state': 'active'})
        negative = g.chat('不要修改v2阅读目标，说明现有下一步即可。', topic['id'])
        jobs.append(negative['id']); await wait(g, negative['id'])
        assert g.record_detail(goal['id'])['payload'] == goal['payload']
        positive = g.chat('请调用 update_goal 修改v2阅读目标的下一步为：整理阅读笔记。保持进行中。', topic['id'])
        jobs.append(positive['id']); await wait(g, positive['id'])
        assert g.record_detail(goal['id'])['payload']['next_step'] == '整理阅读笔记'
        assert g.record_detail(goal['id'])['payload']['state'] == 'active'
        report['checks'].append('真实模型否定请求未修改目标，明确下一步更新通过当前目标与字段校验')
        setup = {'status': 'saved', 'version': 4, 'saved_goal_id': goal['id'], 'followup': 'reminder'}
        g.db.execute('UPDATE sessions SET goal_setup_json=? WHERE id=?', (packed(setup), topic['id']))
        unrelated = g.chat('请提醒我交独立验收作业，单次站内提醒，next_due 为 ' + str(g.clock() + 86400) + '。仅此一条提醒。', topic['id'])
        jobs.append(unrelated['id']); await wait(g, unrelated['id'])
        first = g.db.one('SELECT * FROM tasks WHERE session_id=?', (topic['id'],))
        assert first and first['goal_id'] is None
        linked = g.schedule({'title': '明确阅读提醒', 'instruction': '阅读', 'kind': 'once', 'next_due': g.clock() + 86400, 'session_id': topic['id'], 'goal_id': goal['id']})
        g.goal_state(goal['id'], 'complete')
        assert g.require('tasks', first['id'])['state'] == 'active'
        assert g.require('tasks', linked['task_id'])['state'] == 'completed'
        report['checks'].append('真实无关提醒保持空目标关联，完成阅读目标仅结束其明确关联提醒')
        first_heartbeat = g.schedule({'title': 'v2检查', 'instruction': '检查', 'kind': 'heartbeat', 'interval_seconds': 3600, 'next_due': g.clock() + 86400})
        g.edit_schedule(first_heartbeat['id'], {'operation': 'complete'})
        next_heartbeat = g.schedule({'title': 'v2新检查', 'instruction': '检查', 'kind': 'heartbeat', 'interval_seconds': 3600, 'next_due': g.clock() + 86400})
        assert next_heartbeat['id'] != first_heartbeat['id']
        report['checks'].append('真实 Gateway 已完成 Heartbeat 保留历史并释放名额')
        picked = next((row for row in cards if row['payload']['action_kind'] == 'document_draft'), cards[0])
        started = g.start_idea(picked['id'], 'v2-live-idea-first')
        jobs.append(started['job_id']); await wait(g, started['job_id'])
        assert g.start_idea(picked['id'], 'v2-live-idea-first') == started
        await g.delete_session(started['session_id'])
        from navi.gateway import APIError
        try:
            g.start_idea(picked['id'], 'v2-live-idea-first')
            raise AssertionError('删除会话后旧请求仍返回回执')
        except APIError as error:
            assert error.status == 410
        restarted = g.start_idea(picked['id'], 'v2-live-idea-second')
        jobs.append(restarted['job_id']); await wait(g, restarted['job_id'])
        assert restarted['session_id'] and restarted['session_id'] != started['session_id']
        assert g.require('jobs', restarted['job_id'])['result'].strip()
        assert not (directory / 'exports').exists()
        report['checks'].append('真实构想启动、幂等、删除后的 410 及新请求重启，首份输出有 receipt，未执行导出')
        if phase >= 2:
            begin = g.start_goal('interests', 'v2-goal-ui')
            assert g.start_goal('interests', 'v2-goal-ui') == begin
            jobs.append(begin['job_id']); await wait(g, begin['job_id'])
            for _ in range(3):
                setup = json.loads(g.require('sessions', begin['session_id'])['goal_setup_json'])
                card = next(message['interaction'] for message in g.messages(begin['session_id']) if message['id'] == setup['active_card_id'])
                if setup['status'] == 'confirming':
                    break
                response = g.goal_choice(begin['session_id'], {'card_id': card['card_id'], 'choice_id': card['choices'][0]['id'], 'setup_version': card['setup_version']})
                jobs.append(response['job_id']); await wait(g, response['job_id'])
            assert setup['status'] == 'confirming' and setup['user_rounds'] <= 2
            saved = g.goal_choice(begin['session_id'], {'card_id': card['card_id'], 'choice_id': card['choices'][0]['id'], 'setup_version': card['setup_version']})
            actual = saved['goal']['id']
            scheduled = g.schedule({'title': 'v2目标界面提醒', 'instruction': '检查阅读', 'kind': 'once', 'next_due': g.clock() + 86400, 'goal_id': actual, 'session_id': begin['session_id']})
            completed = g.goal_state(actual, 'complete')
            assert scheduled['task_id'] in completed['affected_tasks']
            g.goal_state(actual, 'reactivate')
            assert g.require('tasks', scheduled['task_id'])['state'] == 'completed'
            g.goal_state(actual, 'complete')
            counts = {table: g.db.one('SELECT COUNT(*) n FROM ' + table)['n'] for table in ('sessions', 'messages')}
            g.delete_record(actual)
            assert g.record_detail(actual)['payload']['state'] == 'cancelled'
            assert counts == {table: g.db.one('SELECT COUNT(*) n FROM ' + table)['n'] for table in counts}
            report['checks'].append('v2 真实目标选项两轮确认、幂等保存，完成即时结束提醒，恢复不复活，移除保留会话和消息')
        if phase >= 3:
            counts = {table: g.db.one('SELECT COUNT(*) n FROM ' + table)['n'] for table in ('jobs', 'usage')}
            text = 'v2正文独有检索标记'
            g.db.execute('INSERT INTO messages(id,session_id,role,content,created) VALUES(?,?,?,?,?)', ('v2-search-hit', topic['id'], 'assistant', text, g.clock()))
            g.edit_session(topic['id'], {'operation': 'archive'})
            assert not g.search_sessions(text)
            matches = g.search_sessions(text, scope='all')
            assert matches[0]['match_message_id'] == 'v2-search-hit'
            assert matches[0]['state'] == 'archived'
            assert counts == {table: g.db.one('SELECT COUNT(*) n FROM ' + table)['n'] for table in counts}
            report['checks'].append('v2 正文检索定位到已保存消息，包含归档，零新增作业与模型用量')
        report['status'] = 'passed'
    except (AssertionError, RuntimeError, ValueError) as error:
        report.update(status='failed', error=str(error) or '未满足验收断言', failed_step=len(report['checks']) + 1)
    finally:
        await g.stop()
        report['jobs'] = [g.evidence(identity) for identity in jobs]
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        (output_dir / 'muse-v2.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print('Muse v2 live phase', phase, report['status'], report.get('error', ''))
    return report['status'] == 'passed'


if __name__=='__main__':
    owner=Gateway(ROOT,ROOT/'data')
    key=owner.settings.api_key()
    if not key:raise SystemExit('本项目没有模型密钥')
    raise SystemExit(0 if asyncio.run(run(owner,key,stage=3)) else 1)
