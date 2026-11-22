import httpx
import pytest

from navi.gateway import Gateway
from navi.web import create_app
from test_gateway import Clock, Model, ROOT


async def test_acknowledging_one_reminder_hides_it_but_keeps_history(tmp_path):
    clock = Clock()
    gateway = Gateway(ROOT, tmp_path, Model(), clock)
    session_id = gateway.main_session()["id"]
    job = gateway.chat("保留普通消息", session_id)
    gateway.propose(job["id"], "notification", "第一条提醒", "inbox", "核对确认收起")
    gateway.propose(job["id"], "notification", "第二条提醒", "inbox", "保持可见")
    gateway.deliver()
    notifications = {row["title"]: row for row in gateway.snapshot()["notifications"]}
    first, second = notifications["第一条提醒"], notifications["第二条提醒"]
    path = f"/api/sessions/{session_id}/messages"
    endpoint = f"/api/notifications/{first['outbox_id']}/acknowledge"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(gateway)), base_url="http://testserver") as client:
        await client.post(f"/api/sessions/{session_id}/read", json={})
        messages = (await client.get(path)).json()
        assert len(messages) == 3
        assert gateway.search_sessions("核对确认收起", scope="all")
        assert (await client.post(endpoint, json={})).json() == {"acknowledged": True}
        acknowledged_at = clock()
        clock.now += 1
        assert (await client.post(endpoint, json={})).status_code == 200
        assert (await client.post("/api/notifications/missing/acknowledge", json={})).status_code == 404
        messages = (await client.get(path)).json()
        assert len(messages) == 2
        assert [row["outbox_id"] for row in messages if row["outbox_id"]] == [second["outbox_id"]]
        assert not gateway.search_sessions("核对确认收起", scope="all")

    restored = Gateway(ROOT, tmp_path, Model(), clock)
    assert len(restored.messages(session_id)) == 3
    assert len(restored.messages(session_id, visible_only=True)) == 2
    state = restored.snapshot()
    assert len(state["notifications"]) == 2
    status = {row["title"]: row["acknowledged_at"] for row in state["notifications"]}
    assert status == {"第一条提醒": acknowledged_at, "第二条提醒": None}
    assert len(restored.evidence(job["id"])["deliveries"]) == 2


@pytest.mark.parametrize("legacy_without_notification", [False, True])
async def test_dismiss_old_delivery_card_after_main_reset(tmp_path, legacy_without_notification):
    clock = Clock()
    gateway = Gateway(ROOT, tmp_path, Model(), clock)
    main_id = gateway.main_session()["id"]
    job = gateway.chat("测试旧卡片", main_id)
    old = gateway.propose(job["id"], "notification", "旧提醒", "inbox", "已经投递")
    gateway.deliver()
    gateway.db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (job["id"],))
    gateway.reset_main_session(main_id)
    if legacy_without_notification:
        gateway.db.execute("DELETE FROM notifications")
    pending = gateway.propose(job["id"], "notification", "未投递提醒", "inbox", "仍待投递")
    export = gateway.propose(job["id"], "export", "文件", "sample.md", "正文")
    assert not gateway.messages(main_id)
    assert next(row for row in gateway.snapshot()["actions"] if row["id"] == old["id"])["acknowledged_at"] is None

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(gateway)), base_url="http://testserver") as client:
        for action in (pending, export):
            assert (await client.post(f"/api/actions/{action['id']}/dismiss-reminder", json={})).status_code == 400
        assert (await client.post(f"/api/actions/{old['id']}/dismiss-reminder", json={})).status_code == 200

    restored = Gateway(ROOT, tmp_path, Model(), clock)
    actions = {row["id"]: row for row in restored.snapshot()["actions"]}
    assert actions[old["id"]]["acknowledged_at"] == clock()
    assert actions[pending["id"]]["acknowledged_at"] is None
    assert actions[old["id"]]["status"] == "delivered"
    assert len(restored.evidence(job["id"])["deliveries"]) == 1
