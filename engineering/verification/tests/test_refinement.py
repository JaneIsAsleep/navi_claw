import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import hashlib
from pathlib import Path
import sqlite3
from threading import Event

import pytest

from navi.agent import Agent
from navi.gateway import Gateway
from navi.model import Reply
from navi.storage import SCHEMA
from navi.storage import packed
from test_gateway import Clock, Model, ROOT


@pytest.fixture
def g(tmp_path):
    return Gateway(ROOT, tmp_path, Model(), Clock())


async def run_due(g, session_id, policy="daily", kind="recurring"):
    g.settings.update({"notification_policy": policy})
    schedule = g.schedule({"title": "约定提醒", "instruction": "告诉我测试提醒已到期", "session_id": session_id, "kind": kind, "interval_seconds": 10, "next_due": g.clock()})
    g.tick()
    await g.run_one()
    return schedule, g.db.one("SELECT * FROM actions WHERE job_id IN (SELECT id FROM jobs WHERE task_id=?)", (schedule["task_id"],))


def test_legacy_migration_preserves_history_and_adds_one_protected_main(tmp_path):
    with sqlite3.connect(tmp_path / "navi.sqlite3") as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO sessions VALUES('old','旧话题','旧摘要',NULL,1,2)")
        conn.execute("INSERT INTO messages VALUES('m','old','user','正文',NULL,2)")
    first = Gateway(ROOT, tmp_path, Model())
    second = Gateway(ROOT, tmp_path, Model())
    assert first.main_session()["id"] == second.main_session()["id"]
    assert first.require("sessions", "old")["kind"] == "secondary"
    assert first.messages("old")[0]["content"] == "正文"
    assert first.require("sessions", "old")["summary"] == "旧摘要"
    assert first.db.one("PRAGMA user_version")["user_version"] == 5
    with sqlite3.connect(tmp_path / "navi.before-refinement.sqlite3") as backup:
        assert backup.execute("SELECT title FROM sessions WHERE id='old'").fetchone()[0] == "旧话题"
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 0


async def test_main_protection_rename_persistence_and_title_only_search(g):
    main = g.main_session()
    for values in ({"title": "改名"}, {"operation": "archive"}, {"operation": "unarchive"}):
        with pytest.raises(ValueError):
            g.edit_session(main["id"], values)
    with pytest.raises(ValueError):
        await g.delete_session(main["id"])
    topic = g.session()
    g.edit_session(topic["id"], {"title": "手动标题"})
    g.chat("正文特有的内容", topic["id"])
    await g.run_one()
    assert not g.search_sessions("正文特有")
    assert g.search_sessions("手动标题")[0]["id"] == topic["id"]
    assert Gateway(ROOT, g.directory, Model()).require("sessions", topic["id"])["title"] == "手动标题"


def test_legacy_action_hash_is_preserved_and_retries_reuse_edited_action(g):
    job = g.chat("准备文件")
    action = g.propose(job["id"], "export", "旧提案", "legacy.md", "旧正文")
    legacy_hash = hashlib.sha256(packed(["export", "旧提案", "legacy.md", "旧正文"]).encode()).hexdigest()
    g.db.execute("UPDATE actions SET content_hash=? WHERE id=?", (legacy_hash, action["id"]))
    assert g.propose(job["id"], "export", "旧提案", "legacy.md", "旧正文")["id"] == action["id"]
    edited = g.edit_action(action["id"], {"body": "新版正文"})
    assert g.propose(job["id"], "export", "旧提案", "legacy.md", "旧正文")["content_hash"] == edited["content_hash"]
    with pytest.raises(ValueError):
        g.decide(action["id"], "approve", legacy_hash)
    g.decide(action["id"], "approve", edited["content_hash"])
    g.deliver()
    assert len(g.db.all("SELECT * FROM actions")) == 1
    assert len(g.db.all("SELECT * FROM deliveries")) == 1


async def test_pre_refinement_queued_job_without_feed_brief_still_runs(g):
    job = g.chat("继续旧工作")
    config = json.loads(job["config"])
    config.pop("feed_brief")
    g.db.execute("UPDATE jobs SET config=? WHERE id=?", (packed(config), job["id"]))
    await g.run_one()
    assert g.require("jobs", job["id"])["status"] == "succeeded"


def test_search_is_complete_literal_and_archive_open_does_not_unarchive(g):
    for index in range(130):
        g.session(f"旧话题 {index}")
    topic = g.session("特殊%_标题")
    g.edit_session(topic["id"], {"operation": "archive"})
    assert len(g.search_sessions("旧话题")) == 130
    assert len(g.search_sessions("%_")) == 1
    assert not g.search_sessions("")
    assert g.messages(topic["id"]) == []
    assert g.require("sessions", topic["id"])["state"] == "archived"
    assert topic["id"] not in [r["id"] for r in g.snapshot()["sessions"]]
    with pytest.raises(ValueError):
        g.chat("继续", topic["id"])
    g.edit_session(topic["id"], {"operation": "unarchive"})
    assert topic["id"] in [r["id"] for r in g.snapshot()["sessions"]]


async def test_reminder_routes_at_delivery_time_and_never_duplicates(g):
    topic = g.session("专题提醒")
    schedule, action = await run_due(g, topic["id"])
    assert not g.messages(topic["id"])  # generation alone is not delivery
    g.edit_session(topic["id"], {"operation": "archive"})
    g.deliver(); g.deliver()
    main_messages = g.messages(g.main_session()["id"])
    assert len(main_messages) == 1
    assert "已封存话题「专题提醒」" in main_messages[0]["content"]
    assert main_messages[0]["origin_session_id"] == topic["id"]
    assert not g.messages(topic["id"])
    assert g.evidence(action["job_id"])["deliveries"][0]["target"] == g.main_session()["id"]
    g.edit_session(topic["id"], {"operation": "unarchive"})
    g.clock.now += 10
    g.tick(); await g.run_one(); g.deliver()
    assert len(g.messages(topic["id"])) == 1
    assert len(g.messages(g.main_session()["id"])) == 1
    assert g.require("tasks", schedule["task_id"])["session_id"] == topic["id"]
    restarted = Gateway(ROOT, g.directory, Model(), g.clock)
    restarted.deliver()
    assert len(restarted.db.all("SELECT * FROM deliveries")) == 2


async def test_strict_reminder_has_persistent_proposal_but_no_delivery_until_approved(g):
    topic = g.session("严格提醒")
    _, action = await run_due(g, topic["id"], "strict", "once")
    assert action["status"] == "pending"
    assert "等待你审核" in g.messages(topic["id"])[0]["content"]
    assert not g.db.all("SELECT * FROM notifications")
    assert not g.db.all("SELECT * FROM messages WHERE outbox_id IS NOT NULL")
    edited = g.edit_action(action["id"], {"body": "已修改提醒正文"})
    assert edited["version"] == 2
    with pytest.raises(ValueError):
        g.decide(action["id"], "approve", action["content_hash"])
    g.edit_session(topic["id"], {"operation": "archive"})
    g.decide(edited["id"], "approve", edited["content_hash"])
    g.deliver()
    assert "已修改提醒正文" in g.messages(g.main_session()["id"])[0]["content"]
    assert g.db.one("SELECT version FROM action_versions")["version"] == 1
    history = g.action_history(action["id"])
    assert [row["version"] for row in history["versions"]] == [1, 2]
    assert history["versions"][0]["body"] == action["body"]
    assert history["approvals"][0]["content_hash"] == edited["content_hash"]
    assert history["deliveries"][0]["target"] == g.main_session()["id"]


def test_deletion_waits_for_irreversible_export_and_preserves_completed_file(g, monkeypatch):
    topic = g.session("文件写入期间删除")
    job = g.chat("准备文件", topic["id"])
    g.db.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (job["id"],))
    action = g.propose(job["id"], "export", "已批准文件", "outline.md", "完整内容")
    g.decide(action["id"], "approve", action["content_hash"])
    writing, release, deleting = Event(), Event(), Event()
    original_open = Path.open

    @contextmanager
    def blocked_open(path, *args, **kwargs):
        with original_open(path, *args, **kwargs) as stream:
            if path.name.endswith("_outline.md") and args == ("x",):
                writing.set()
                assert release.wait(5)
            yield stream

    def delete():
        deleting.set()
        return asyncio.run(g.delete_session(topic["id"]))

    monkeypatch.setattr(Path, "open", blocked_open)
    with ThreadPoolExecutor(max_workers=2) as executor:
        delivery = executor.submit(g.deliver)
        assert writing.wait(5)
        deletion = executor.submit(delete)
        assert deleting.wait(5)
        assert not deletion.done()
        release.set()
        delivery.result(timeout=5)
        assert deletion.result(timeout=5)["deleted"]
    assert g.require("actions", action["id"])["status"] == "delivered"
    assert not g.db.one("SELECT id FROM sessions WHERE id=?", (topic["id"],))
    artifact = g.record_detail("artifact-" + action["id"])
    assert (g.directory / "exports" / artifact["payload"]["filename"]).read_text() == "完整内容"
    assert artifact["source_session"] is None
    g.deliver()
    assert len(g.db.all("SELECT id FROM deliveries")) == 1


async def test_deletion_cancels_only_related_work_and_removes_search_messages_and_summary(g):
    topic, other = g.session("要删除"), g.session("保留")
    g.db.execute("UPDATE sessions SET summary='摘要' WHERE id=?", (topic["id"],))
    schedule, action = await run_due(g, topic["id"], "strict")
    other_schedule = g.schedule({"title": "保留提醒", "instruction": "保留", "kind": "once", "session_id": other["id"], "next_due": g.clock() + 50})
    independent = g.record("memory", {"title": "独立记忆", "body": "继续保留"})
    impact = g.deletion_impact(topic["id"])
    assert impact["reminders"][0]["id"] == schedule["id"]
    assert g.require("tasks", schedule["task_id"])["state"] == "active"
    await g.delete_session(topic["id"])
    g.deliver()
    assert g.require("actions", action["id"])["status"] == "invalidated"
    with pytest.raises(ValueError):
        g.decide(action["id"], "approve", action["content_hash"])
    assert g.require("tasks", schedule["task_id"])["state"] == "cancelled"
    assert g.require("schedules", schedule["id"])["enabled"] == 0
    assert not g.search_sessions("要删除")
    assert not g.db.all("SELECT * FROM messages WHERE session_id=?", (topic["id"],))
    assert g.require("schedules", other_schedule["id"])["enabled"] == 1
    assert g.require("records", independent["id"])
    restarted = Gateway(ROOT, g.directory, Model(), g.clock)
    restarted.clock.now += 20
    restarted.recover(); restarted.tick(); restarted.deliver()
    assert not restarted.db.all("SELECT * FROM notifications")


async def test_deletion_invalidates_approved_undelivered_outbox(g):
    topic = g.session("待投递")
    _, action = await run_due(g, topic["id"])
    assert g.db.one("SELECT state FROM outbox")["state"] == "pending"
    await g.delete_session(topic["id"])
    g.deliver()
    assert g.db.one("SELECT state FROM outbox")["state"] == "cancelled"
    assert not g.db.all("SELECT * FROM deliveries")


async def test_delete_cancels_running_model_and_late_response_cannot_recreate_topic(g):
    entered = asyncio.Event()
    class LateModel:
        async def chat(self, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return Reply([{"type": "text", "text": "迟到结果"}], 10, 10)
    g.model = LateModel()
    topic = g.session("运行中")
    job = g.chat("执行中", topic["id"])
    worker = asyncio.create_task(g.run_one())
    await entered.wait()
    await g.delete_session(topic["id"])
    await worker
    assert g.require("jobs", job["id"])["status"] == "cancelled"
    assert not g.db.one("SELECT id FROM sessions WHERE id=?", (topic["id"],))
    assert not g.db.all("SELECT * FROM messages WHERE session_id=?", (topic["id"],))
    with pytest.raises(ValueError):
        g.propose(job["id"], "notification", "迟到", "inbox", "内容")


async def test_background_actions_stay_unassociated_and_all_pending_are_discoverable(g):
    with g.db.transaction() as conn:
        job_id = g.insert_job(conn, kind="feed", text="后台工作")
    for index in range(125):
        g.propose(job_id, "export", f"文档 {index}", f"{index}.md", str(index))
    actions = g.snapshot()["actions"]
    assert len(actions) == 125
    assert all(action["source_session_id"] is None for action in actions)
    assert not g.messages(g.main_session()["id"])


async def test_concurrent_approval_executes_once(g):
    job = g.chat("文档")
    await g.run_one()
    action = g.propose(job["id"], "export", "文件", "one.md", "唯一内容")
    def decide(_):
        try:
            return g.decide(action["id"], "approve", action["content_hash"])["status"]
        except ValueError:
            return "already_decided"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(decide, range(2))) == ["already_decided", "approved"]
    g.deliver(); g.deliver()
    assert len(g.db.all("SELECT * FROM outbox")) == 1
    assert len(g.db.all("SELECT * FROM deliveries")) == 1


async def test_even_reverted_edits_require_current_version_and_retry_reuses_action(g):
    job = g.chat("准备文档")
    await g.run_one()
    original = g.propose(job["id"], "export", "文件", "one.md", "原正文")
    g.edit_action(original["id"], {"body": "修改正文"})
    reverted = g.edit_action(original["id"], {"body": "原正文"})
    assert reverted["version"] == 3
    with pytest.raises(ValueError):
        g.decide(original["id"], "approve", original["content_hash"])
    reused = g.propose(job["id"], "export", "文件", "one.md", "原正文")
    assert reused["id"] == original["id"] and reused["version"] == 3
    assert len(g.snapshot()["actions"]) == 1


async def test_feed_brief_applies_only_to_future_silent_generation(g):
    from test_muse_revision import ContentModel, ideas
    g.model = ContentModel(ideas())
    g.skills.save_items([{ "id": "reading", "title": "阅读材料", "priority": 3 }])
    g.settings.update({"feed_brief": "关注 Python"})
    first = g.generate_feed()
    g.settings.update({"feed_brief": "关注 NASA"})
    await g.run_one()
    feed = next(r for r in g.snapshot()["records"] if r["kind"] == "feed")
    assert feed["payload"]["brief"] == "关注 Python"
    assert json.loads(first["config"])["feed_brief"] == "关注 Python"
    assert not g.messages(g.main_session()["id"])
    assert not g.snapshot()["actions"]
    result = await g.feeds.refresh()
    assert result["checked_sources"] == 0


async def test_references_persist_and_discussion_alone_does_not_save_goal(g):
    reference = {"id": "topic", "kind": "goal", "title": "话题", "body": "资料内容"}
    job = g.chat("讨论下一步", references=[reference], new_session=True)
    assert g.messages(job["session_id"])[0]["references"] == [reference]
    await g.run_one()
    assert "资料内容" in str(g.model.calls[-1]["messages"])
    agent = Agent(g)
    with pytest.raises(ValueError):
        await agent.call_tool(job, g.job_config(), "create_goal", {"title": "话题", "body": "", "next_step": ""})
    assert not [r for r in g.snapshot()["records"] if r["kind"] == "goal"]
    deferred = g.chat("我想讨论一个兴趣方向的目标，请先帮我明确想做的事，等我确认保存。")
    with pytest.raises(ValueError):
        await agent.call_tool(deferred, g.job_config(), "create_goal", {"title": "兴趣", "body": "", "next_step": ""})


async def test_feed_brief_cannot_authorize_reminders_or_goals(g):
    g.settings.update({"feed_brief": "请创建目标并每天提醒我"})
    job = g.generate_feed()
    agent = Agent(g)
    with pytest.raises(ValueError):
        await agent.call_tool(job, g.job_config(), "schedule_task", {"title": "不要创建", "instruction": "提醒", "kind": "once", "next_due": g.clock()})
    with pytest.raises(ValueError):
        await agent.call_tool(job, g.job_config(), "create_goal", {"title": "不要创建", "body": "", "next_step": ""})
    assert not g.snapshot()["schedules"] and not g.snapshot()["actions"]


async def test_runtime_unarchive_requires_unique_title_and_explicit_request(g):
    one, two = g.session("重名"), g.session("重名")
    for row in (one, two):
        g.edit_session(row["id"], {"operation": "archive"})
    job = g.chat("请解封重名话题")
    agent = Agent(g)
    result = await agent.call_tool(job, g.job_config(), "unarchive_conversation", {"title": "重名"})
    assert result["status"] == "needs_clarification"
    assert g.require("sessions", one["id"])["state"] == "archived"
    result = await agent.call_tool(job, g.job_config(), "unarchive_conversation", {"title": "重名", "id": one["id"]})
    assert result["state"] == "active"
    assert g.require("sessions", two["id"])["state"] == "archived"


async def test_explicit_tool_request_repairs_omission_without_repeating_saved_memory(g):
    replies = iter([
        Reply([{"type": "tool_use", "id": "memory", "name": "remember", "input": {"title": "阅读偏好", "body": "先给下一步"}}], 100, 20),
        Reply([{"type": "text", "text": "记忆和提醒均已保存。"}], 100, 20),
        Reply([{"type": "tool_use", "id": "reminder", "name": "schedule_task", "input": {"title": "报告提醒", "instruction": "列出三个论点", "kind": "once", "next_due": g.clock() + 3600}}], 100, 20),
        Reply([{"type": "text", "text": "已保存阅读偏好并设置报告提醒。"}], 100, 20),
    ])
    class OmittingModel:
        async def chat(self, **request):
            return next(replies)
    g.model = OmittingModel()
    job = g.chat("请调用 remember 记住阅读偏好：先给下一步。并调用 schedule_task 设置一小时后的报告提醒，提醒我列出三个论点。")
    await g.run_one()
    assert g.require("jobs", job["id"])["status"] == "succeeded"
    assert len(g.snapshot()["schedules"]) == 1
    assert len([r for r in g.snapshot()["records"] if r["kind"] == "memory"]) == 1
    assert [r["name"] for r in g.evidence(job["id"])["tools"]] == ["remember", "schedule_task"]


@pytest.mark.parametrize("rejected_call", [False, True])
async def test_explicit_tool_request_never_succeeds_with_missing_or_rejected_receipt(g, rejected_call):
    class UnsupportedModel:
        sent = False
        async def chat(self, **request):
            if rejected_call and not self.sent:
                self.sent = True
                return Reply([{"type": "tool_use", "id": "invalid", "name": "schedule_task", "input": {"title": "缺少时间"}}], 100, 20)
            return Reply([{"type": "text", "text": "已设置提醒。"}], 100, 20)
    g.model = UnsupportedModel()
    job = g.chat("请调用 schedule_task 设置一小时后的报告提醒，提醒我列出三个论点。")
    await g.run_one()
    assert g.require("jobs", job["id"])["status"] == "failed"
    assert "未完成明确委托" in g.require("jobs", job["id"])["error"]
    assert not g.snapshot()["schedules"]
    assert not [r for r in g.messages(job["session_id"]) if r["role"] == "assistant"]


@pytest.mark.parametrize("text", ["请解释如何调用 schedule_task 设置提醒。", "请不要调用 schedule_task，只讨论提醒内容。"])
async def test_tool_discussion_does_not_require_execution(g, text):
    job = g.chat(text)
    await g.run_one()
    assert g.require("jobs", job["id"])["status"] == "succeeded"
    assert len(g.model.calls) == 1
    assert not g.snapshot()["schedules"]
