import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from navi.gateway import Gateway
from navi.model import Reply
from navi.reminder_time import reminder_timing
from navi.web import create_app
from test_gateway import ROOT
from test_item_edits import edit_model, review


def stamp(zone, year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(zone)).timestamp()


class ReminderModel:
    def __init__(self, arguments):
        self.arguments = arguments
        self.calls = 0

    async def chat(self, **request):
        self.calls += 1
        return Reply([{"type": "tool_use", "name": "schedule_task", "id": str(self.calls), "input": self.arguments}])


async def test_chat_updates_existing_reminder_through_web(tmp_path):
    zone = 'Asia/Taipei'
    now = stamp(zone, 2026, 11, 23, 20)
    gateway = Gateway(ROOT, tmp_path, ReminderModel({}), clock=lambda: now)
    reminder = gateway.schedule({'title': '吃水果提醒', 'instruction': '吃水果',
                                 'kind': 'recurring', 'next_due': now + 60,
                                 'interval_seconds': 86400, 'calendar_timezone': zone})
    gateway.model = edit_model('reminder', '吃水果', [reminder['id']], when='9:00')
    job = gateway.chat('修改8:01吃水果的提醒 改成9:00', timezone=zone)
    await gateway.run_one()
    assert gateway.require('schedules', reminder['id'])['next_due'] == reminder['next_due']
    action = review(gateway, job)
    assert json.loads(action['body'])['changes']['next_due'] == stamp(zone, 2026, 11, 23, 21)
    gateway.edit_schedule(reminder['id'], {'next_due': reminder['next_due']})
    other = gateway.schedule({'title': '吃水果提醒', 'instruction': '吃水果', 'kind': 'once',
                              'next_due': stamp(zone, 2026, 11, 24, 8, 1)})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(gateway)), base_url='http://testserver') as web:
        for text, changes, receipt in [('修改8:01吃水果的提醒 改成9:00', {'when': '9:00'}, '找到多条'),
                                       ('修改晚上8:01吃水果的提醒 改成9:00', {'when': '9:00'}, '已准备审批'),
                                       ('把晚上9:00吃水果的提醒的内容改成「吃苹果」', {'instruction': '吃苹果'}, '已准备审批')]:
            gateway.model = edit_model('reminder', '吃水果', [reminder['id']], **changes)
            response = await web.post('/api/chat', json={'text': text, 'timezone': zone})
            assert response.status_code == 200
            await gateway.run_one()
            job = response.json()
            assert receipt in gateway.require('jobs', job['id'])['result']
            if receipt == '已准备审批':
                review(gateway, job)
            else:
                assert not gateway.db.one('SELECT id FROM actions WHERE job_id=?', (job['id'],))
        rows = {row['id']: row for row in (await web.get('/api/state')).json()['schedules']}
    updated = rows[reminder['id']]
    assert updated['task_id'] == reminder['task_id'] and updated['interval_seconds'] == 86400
    assert updated['title'] == '吃水果提醒' and updated['instruction'] == '吃苹果'
    assert updated['calendar_timezone'] == zone and updated['enabled']
    assert updated['next_due'] == stamp(zone, 2026, 11, 23, 21)
    assert set(rows) == {reminder['id'], other['id']}
    assert gateway.require('schedules', other['id']) == other
    now = stamp(zone, 2026, 11, 23, 20, 1)
    gateway.tick()
    assert not gateway.db.one('SELECT id FROM jobs WHERE task_id=?', (reminder['task_id'],))
    now = updated['next_due']
    gateway.tick()
    assert gateway.db.one('SELECT input FROM jobs WHERE task_id=?', (reminder['task_id'],))['input'] == '吃苹果'


async def test_chat_updates_paused_weekly_reminder_without_resuming(tmp_path):
    zone = 'Asia/Taipei'
    now = stamp(zone, 2026, 11, 23, 12)
    gateway = Gateway(ROOT, tmp_path, ReminderModel({}), clock=lambda: now)
    row = gateway.schedule({'title': '周报提醒', 'instruction': '写周报', 'kind': 'recurring',
                             'next_due': stamp(zone, 2026, 11, 27, 18),
                             'interval_seconds': 604800, 'calendar_timezone': zone})
    gateway.edit_schedule(row['id'], {'operation': 'pause'})
    gateway.model = edit_model('reminder', '周报', [row['id']], instruction='整理周报', when='晚上7点')
    request = gateway.chat('把周报提醒的内容改成整理周报，同时时间改成晚上7点', timezone=zone)
    await gateway.run_one()
    result = gateway.require('jobs', request['id'])['result']
    assert '已准备审批' in result
    action = review(gateway, request)
    assert json.loads(action['body'])['changes'] == {'instruction': '整理周报', 'next_due': stamp(zone, 2026, 11, 27, 19)}
    reminder = gateway.snapshot()['schedules'][0]
    assert reminder['id'] == row['id'] and not reminder['enabled'] and reminder['state'] == 'paused'
    assert reminder['interval_seconds'] == 604800 and reminder['instruction'] == '整理周报'


async def test_web_timezone_matches_form_and_chat_ignores_model_timestamp(tmp_path):
    zone = "America/Los_Angeles"
    now = stamp(zone, 2026, 11, 21, 23)
    expected = stamp(zone, 2026, 11, 22, 8)
    model = ReminderModel({"title": "交报告", "instruction": "交报告", "next_due": 300})
    gateway = Gateway(ROOT, tmp_path, model, clock=lambda: now)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(gateway)), base_url="http://testserver") as web:
        bad = await web.post("/api/chat", json={"text": "你好", "timezone": "invalid/timezone"})
        assert bad.status_code == 400
        response = await web.post("/api/chat", json={"text": "明天早上8点提醒我交报告", "timezone": zone})
        assert response.status_code == 200
        await gateway.run_one()
        result = gateway.require("jobs", response.json()["id"])
        assert result["status"] == "succeeded" and "2026-11-22 08:00:00" in result["result"]
        assert model.calls == 1
        assert gateway.snapshot()["schedules"][0]["next_due"] == expected

        response = await web.post("/api/schedules", json={"title": "表单", "instruction": "交报告", "kind": "once", "local_due": "2026-11-22T08:00", "timezone": zone})
        assert response.json()["next_due"] == expected
        edited = await web.patch("/api/schedules/" + response.json()["id"], json={"local_due": "2026-11-22T08:30", "timezone": zone})
        assert edited.json()["next_due"] == expected + 1800
    assert gateway.settings.current()["timezone"] != zone


@pytest.mark.parametrize(("text", "expected"), [
    ("半小时后提醒我看邮件", (2026, 11, 21, 12, 30)),
    ("2026年11月22日上午8点半提醒我交报告", (2026, 11, 22, 8, 30)),
    ("每周五下午三点提醒我交周报", (2026, 11, 27, 15, 0)),
])
def test_local_time_formats(text, expected):
    zone = "Asia/Shanghai"
    assert reminder_timing(text, stamp(zone, 2026, 11, 21, 12), ZoneInfo(zone))["next_due"] == stamp(zone, *expected)


async def test_reminder_clarifies_missing_time_then_saves_half_hour_reply(tmp_path):
    now = stamp("Asia/Shanghai", 2026, 11, 21, 12)
    model = ReminderModel({"title": "交报告", "instruction": "交报告"})
    gateway = Gateway(ROOT, tmp_path, model, clock=lambda: now)
    first = gateway.chat("提醒我交报告")
    await gateway.run_one()
    assert "什么时候" in gateway.require("jobs", first["id"])["result"]
    assert model.calls == 0
    gateway.chat("半小时后", first["session_id"])
    await gateway.run_one()
    assert model.calls == 1
    assert gateway.snapshot()["schedules"][0]["next_due"] == now + 1800


async def test_invalid_schedule_tool_stops_after_one_retry(tmp_path):
    model = ReminderModel({"title": "缺少指令"})
    gateway = Gateway(ROOT, tmp_path, model, clock=lambda: stamp("Asia/Shanghai", 2026, 11, 21, 12))
    job = gateway.chat("五分钟后提醒我交报告")
    await gateway.run_one()
    result = gateway.require("jobs", job["id"])
    assert model.calls == 2
    assert result["status"] == "failed" and result["error_code"] == "schedule_tool_error"
    assert not gateway.snapshot()["schedules"]


async def test_daily_reminder_keeps_wall_clock_across_dst(tmp_path):
    zone = "America/Los_Angeles"
    now = stamp(zone, 2027, 3, 13, 7)
    due = stamp(zone, 2027, 3, 13, 8)
    model = ReminderModel({"title": "阅读", "instruction": "阅读"})
    gateway = Gateway(ROOT, tmp_path, model, clock=lambda: now)
    gateway.chat("每天早上八点提醒我阅读", timezone=zone)
    await gateway.run_one()
    assert gateway.snapshot()["schedules"][0]["next_due"] == due
    now = due
    gateway.tick()
    assert gateway.snapshot()["schedules"][0]["next_due"] == stamp(zone, 2027, 3, 14, 8)
