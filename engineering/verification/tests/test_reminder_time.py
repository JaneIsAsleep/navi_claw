from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from navi.gateway import Gateway
from navi.model import Reply
from navi.reminder_time import reminder_timing
from navi.web import create_app
from test_gateway import ROOT


def stamp(zone, year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(zone)).timestamp()


class ReminderModel:
    def __init__(self, arguments):
        self.arguments = arguments
        self.calls = 0

    async def chat(self, **request):
        self.calls += 1
        return Reply([{"type": "tool_use", "name": "schedule_task", "id": str(self.calls), "input": self.arguments}])


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
