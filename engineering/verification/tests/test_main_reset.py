import httpx

from navi.gateway import Gateway
from navi.web import create_app
from test_gateway import Clock, Model, ROOT


async def test_main_reset_clears_context_and_preserves_reminders(tmp_path):
    clock = Clock()
    gateway = Gateway(ROOT, tmp_path, Model(), clock)
    main_id = gateway.main_session()["id"]
    side_id = gateway.session("副对话")["id"]
    side_job = gateway.chat("保留的副对话消息", side_id)
    old_job = gateway.chat("待清空的主对话消息", main_id)
    old_message = gateway.messages(main_id)[0]["id"]
    gateway.db.execute("UPDATE sessions SET summary='旧摘要',summary_through=?,display_abstract='旧简介' WHERE id=?", (old_message, main_id))
    goal = gateway.record("goal", {"title": "保留目标", "session_id": main_id})
    memory = gateway.record("memory", {"title": "保留记忆", "body": "测试", "session_id": main_id})
    reminder = gateway.schedule({"title": "保留提醒", "instruction": "提醒正文", "kind": "once",
                                 "next_due": clock() + 60, "goal_id": goal["id"]})

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(gateway)), base_url="http://testserver") as client:
        path = f"/api/sessions/{main_id}/reset"
        assert (await client.post(path, json={})).status_code == 400
        assert (await client.post(f"/api/sessions/{side_id}/reset", json={"confirmed": True})).status_code == 400
        assert (await client.post(path, json={"confirmed": True})).status_code == 409
        assert gateway.messages(main_id)[0]["id"] == old_message
        gateway.db.execute("UPDATE jobs SET status='succeeded' WHERE id IN (?,?)", (old_job["id"], side_job["id"]))
        reset = await client.post(path, json={"confirmed": True})
        assert reset.status_code == 200
        assert reset.json()["id"] == main_id and reset.json()["kind"] == "main"
        assert (await client.request("DELETE", f"/api/sessions/{main_id}", json={})).status_code == 400
        assert (await client.get(f"/api/sessions/{main_id}/messages")).json() == []
        assert not gateway.search_sessions("待清空的主对话消息", scope="all")
        assert gateway.messages(side_id)[0]["content"] == "保留的副对话消息"

    restored = Gateway(ROOT, tmp_path, Model(), clock)
    main = restored.main_session()
    assert main["id"] == main_id and not main["summary"] and not main["summary_through"] and not main["display_abstract"]
    assert restored.require("records", memory["id"])
    assert restored.require("records", goal["id"])
    assert restored.require("jobs", old_job["id"])
    assert restored.require("tasks", reminder["task_id"])["session_id"] == main_id
    assert restored.require("schedules", reminder["id"])["enabled"]
    clock.now += 60
    restored.tick()
    await restored.run_one()
    restored.deliver()
    assert any(message["outbox_id"] and "保留提醒" in message["content"] for message in restored.messages(main_id))
