import httpx
import pytest

from navi.agent import cancel_tool
from navi.gateway import Gateway
from navi.web import create_app
from test_gateway import Clock, ROOT


class NoModel:
    async def chat(self, **request):
        raise AssertionError("明确的取消指令无需请求模型")


def schedule(gateway, title="喝水提醒", **fields):
    return gateway.schedule({"title": title, "instruction": "喝水", "kind": "recurring",
                             "next_due": gateway.clock() + 60, "interval_seconds": 60, **fields})


@pytest.mark.parametrize("text", ["帮我把喝水提醒取消掉", "取消我的喝水提醒", "请取消提醒：喝水提醒", "取消「喝水提醒」", "把这个提醒关掉"])
async def test_chat_cancels_reminder_and_next_trigger_through_web(tmp_path, text):
    gateway = Gateway(ROOT, tmp_path, NoModel(), Clock())
    reminder = schedule(gateway)
    if text != "把这个提醒关掉":
        schedule(gateway, "另一个提醒")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(gateway)), base_url="http://testserver") as web:
        response = await web.post("/api/chat", json={"text": text})
        assert response.status_code == 200
        await gateway.run_one()
        job = gateway.require("jobs", response.json()["id"])
        assert job["status"] == "succeeded" and "已取消站内提醒「喝水提醒」" in job["result"]
        state = (await web.get("/api/state")).json()
        row = next(row for row in state["schedules"] if row["id"] == reminder["id"])
        assert row["state"] == "cancelled" and not row["enabled"]
        assert all(row["state"] == "active" for row in state["schedules"] if row["id"] != reminder["id"])
    gateway.clock.now += 120
    gateway.tick()
    assert not gateway.db.one("SELECT id FROM jobs WHERE task_id=?", (reminder["task_id"],))


@pytest.mark.parametrize(("text", "message"), [
    ("取消喝水提醒", "匹配到多个提醒"),
    ("取消不存在的提醒", "没有找到对应的提醒"),
])
async def test_ambiguous_or_missing_reminder_does_not_claim_success(tmp_path, text, message):
    gateway = Gateway(ROOT, tmp_path, NoModel(), Clock())
    schedule(gateway, "喝水提醒")
    schedule(gateway, "喝水提醒")
    job = gateway.chat(text)
    await gateway.run_one()
    result = gateway.require("jobs", job["id"])["result"]
    assert message in result and "未取消任何提醒" in result
    assert all(row["enabled"] and row["state"] == "active" for row in gateway.snapshot()["schedules"])


@pytest.mark.parametrize("text", ["不要取消喝水提醒", "请解释怎么取消喝水提醒", "把喝水提醒取消吗？", "如果我取消喝水提醒", "「取消喝水提醒」"])
def test_discussion_does_not_authorize_cancel(text):
    assert cancel_tool(text) != "cancel_tracking"


async def test_remove_cancelled_reminder_from_list_persists_and_keeps_history(tmp_path):
    gateway = Gateway(ROOT, tmp_path, NoModel(), Clock())
    reminder = schedule(gateway)
    path = "/api/schedules/" + reminder["id"]
    operation = {"operation": "remove_from_list"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(gateway)), base_url="http://testserver") as web:
        assert (await web.patch(path, json=operation)).status_code == 400
        gateway.edit_schedule(reminder["id"], {"operation": "pause"})
        assert (await web.patch(path, json=operation)).status_code == 400
        gateway.clock.now += 120
        gateway.edit_schedule(reminder["id"], {"operation": "resume"})
        gateway.clock.now += 120
        gateway.tick()
        await web.patch(path, json={"operation": "cancel"})
        history = (await web.get(path + "/history")).json()
        assert history
        for _ in range(2):
            removed = await web.patch(path, json=operation)
            assert removed.status_code == 200
            assert removed.json()["removed_from_reminders"] == 1
        assert (await web.get(path + "/history")).json() == history
    reopened = Gateway(ROOT, tmp_path, NoModel(), gateway.clock)
    row = next(row for row in reopened.snapshot()["schedules"] if row["id"] == reminder["id"])
    assert row["removed_from_reminders"] == 1
    assert row["state"] == "cancelled" and not row["enabled"]
    assert len([entry for entry in reopened.audit() if entry["kind"] == "schedule.removed_from_list"]) == 1
