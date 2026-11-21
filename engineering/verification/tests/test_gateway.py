import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from navi.agent import Agent
from navi.cli import CLIAdapter
from navi.gateway import Gateway
from navi.model import AgentError, Reply
from navi.storage import packed, uid
from navi.web import create_app


ROOT = Path(__file__).resolve().parents[3]


class Clock:
    now = 1780000000.0
    def __call__(self):
        return self.now


class Model:
    """Offline test double. It is never installed in the user runtime."""
    def __init__(self):
        self.calls = []
    async def chat(self, **request):
        self.calls.append(request)
        text = str(request["messages"][-1]["content"])
        await request["on_delta"]("离线测试结果")
        return Reply([{"type": "text", "text": "离线测试结果：" + text[-5000:]}], 100, 40)


@pytest.fixture
def g(tmp_path):
    return Gateway(ROOT, tmp_path, Model(), Clock())


def create_due(g, kind="heartbeat"):
    return g.schedule({"title": "固定检查", "instruction": "检查关注事项并给出简报", "kind": kind, "interval_seconds": 10, "next_due": g.clock()})


def sample(g):
    g.skills.save_items([{"id": "priority", "title": "重要事项", "priority": 3, "due_at": None}, {"id": "deadline", "title": "临近截止", "priority": 1, "due_at": g.clock() + 3600}])


async def test_session_survives_restart_and_adapters_share_state(g, monkeypatch):
    with TestClient(create_app(g)) as web:
        monkeypatch.setattr(httpx, "request", lambda method, url, **kwargs: web.request(method, url.replace("http://127.0.0.1:8766", ""), json=kwargs.get("json")))
        cli = CLIAdapter()
        session = web.post("/api/sessions", json={}).json()
        job = cli.call("POST", "/chat", {"text": "通道共享的消息", "session_id": session["id"]})
        assert web.get(f"/api/sessions/{session['id']}/messages").json()[0]["job_id"] == job["id"]
        assert cli.call("GET", "/state")["user"]["id"] == "local-user"
    other = Gateway(ROOT, g.directory, Model(), g.clock)
    assert other.messages(session["id"])[0]["content"] == "通道共享的消息"
    assert any(row["id"] == session["id"] for row in other.snapshot()["sessions"])


async def test_scheduler_runs_without_messages_and_coalesces_missed_intervals(g):
    schedule = create_due(g, "recurring")
    g.clock.now += 95
    g.tick(); g.tick()
    assert len([job for job in g.snapshot()["jobs"] if job["task_id"]]) == 1
    event = next(row for row in g.audit() if row["kind"] == "schedule.triggered")
    assert event["data"]["missed_intervals"] == 9
    assert g.require("schedules", schedule["id"])["next_due"] == g.clock.now + 5
    await g.run_one()
    assert g.snapshot()["jobs"][0]["status"] == "succeeded"
    assert len(g.snapshot()["sessions"]) == 1
    assert g.snapshot()["sessions"][0]["kind"] == "main"


async def test_empty_heartbeat_is_quiet_and_does_not_spend_budget(g):
    create_due(g); g.tick(); await g.run_one(); g.deliver()
    assert not g.snapshot()["actions"]
    assert not g.snapshot()["notifications"]
    assert not g.model.calls
    assert g.snapshot()["jobs"][0]["status"] == "succeeded"
    assert g.db.one("SELECT COUNT(*) n FROM tool_calls")["n"] == 1


async def test_strict_heartbeat_approval_denial_and_unchanged_silence(g):
    g.settings.update({"notification_policy": "strict"}); sample(g)
    create_due(g); g.tick(); await g.run_one()
    action = g.snapshot()["actions"][0]
    g.deliver()
    assert not g.snapshot()["notifications"]
    g.decide(action["id"], "reject", action["content_hash"]); g.deliver()
    assert not g.db.all("SELECT * FROM outbox")
    g.clock.now += 10; g.tick(); await g.run_one()
    assert len(g.model.calls) == 1
    changed = g.skills.items(); changed[0]["body"] = "真实变化"; g.skills.save_items(changed)
    g.clock.now += 10; g.tick(); await g.run_one()
    second = g.snapshot()["actions"][0]
    g.decide(second["id"], "approve", second["content_hash"])
    assert g.db.one("SELECT state FROM outbox")["state"] == "pending"
    g.deliver(); g.deliver()
    assert len(g.snapshot()["notifications"]) == 1
    evidence = g.evidence(second["job_id"])
    assert evidence["evidence_type"] == "offline-test-double"
    assert evidence["deliveries"][0]["action_id"] == second["id"]
    assert evidence["tools"][0]["result"]["extension_version"]


async def test_daily_policy_auto_sends_only_internal_notifications(g):
    sample(g); create_due(g); g.tick(); await g.run_one(); g.deliver()
    assert len(g.snapshot()["notifications"]) == 1
    job = g.snapshot()["jobs"][0]
    draft = g.propose(job["id"], "export", "文档", "briefing.md", "具体文档")
    assert draft["status"] == "pending"
    assert not (g.directory / "exports").exists()
    approval = g.db.one("SELECT * FROM approvals")
    assert approval["actor"] == "policy" and "用户已授权" in approval["basis"]


async def test_edited_action_requires_current_hash_and_export_is_idempotent(g):
    job = g.chat("创建文档"); await g.run_one()
    original = g.propose(job["id"], "export", "文档", "文件.md", "旧内容")
    edited = g.edit_action(original["id"], {"body": "新内容"})
    with pytest.raises(ValueError): g.decide(original["id"], "approve", original["content_hash"])
    assert not g.db.all("SELECT * FROM outbox")
    g.decide(edited["id"], "approve", edited["content_hash"])
    # Simulate a crash after writing immutable content but before SQLite delivery commit.
    exports = g.directory / "exports"; exports.mkdir()
    path = exports / f"{edited['id']}_文件.md"; path.write_text("新内容")
    recovered = Gateway(ROOT, g.directory, Model(), g.clock)
    recovered.deliver(); recovered.deliver()
    assert path.read_text() == "新内容"
    assert len(recovered.db.all("SELECT * FROM deliveries")) == 1
    assert len(recovered.snapshot()["records"]) == 1


async def test_delivery_conflict_is_uncertain_and_not_blindly_retried(g):
    job = g.chat("文档"); await g.run_one()
    action = g.propose(job["id"], "export", "文档", "result.txt", "批准的内容")
    g.decide(action["id"], "approve", action["content_hash"])
    folder = g.directory / "exports"; folder.mkdir()
    path = folder / f"{action['id']}_result.txt"; path.write_text("外部已修改")
    g.deliver(); g.deliver()
    assert path.read_text() == "外部已修改"
    assert g.db.one("SELECT * FROM outbox")["state"] == "uncertain"
    assert not g.db.all("SELECT * FROM deliveries")


async def test_generated_and_exported_artifacts_keep_goal_links(g):
    goal = g.record("goal", {"title": "验收目标", "state": "active", "next_step": "生成成果"})
    job = g.chat(f"生成目标 {goal['id']} 的简报", kind="draft"); await g.run_one()
    draft = next(row for row in g.snapshot()["records"] if row["kind"] == "artifact")
    assert draft["payload"]["goal_ids"] == [goal["id"]]
    action = g.propose(job["id"], "export", "关联成果", "goal.md", "固定内容")
    g.decide(action["id"], "approve", action["content_hash"]); g.deliver()
    exported = g.require("records", "artifact-" + action["id"])
    assert json.loads(exported["payload"])["goal_ids"] == [goal["id"]]


async def test_schedule_lifecycle_changes_future_jobs_only_and_persists(g):
    row = create_due(g, "recurring"); g.tick()
    old = g.snapshot()["jobs"][0]
    g.edit_schedule(row["id"], {"instruction": "新指令", "interval_seconds": 30, "operation": "pause"})
    g.clock.now += 100; g.tick()
    assert len([job for job in g.snapshot()["jobs"] if job["task_id"]]) == 1
    g.edit_schedule(row["id"], {"operation": "resume"})
    g.clock.now += 30; g.tick()
    latest = g.snapshot()["jobs"][0]
    assert old["input"] == "检查关注事项并给出简报"
    assert latest["input"] == "新指令"
    g.edit_schedule(row["id"], {"operation": "cancel"})
    with pytest.raises(ValueError): g.edit_schedule(row["id"], {"operation": "resume"})
    other = Gateway(ROOT, g.directory, Model(), g.clock)
    assert other.require("tasks", row["task_id"])["state"] == "cancelled"


async def test_swappable_skill_changes_model_input_and_inflight_snapshot(g):
    sample(g)
    first = g.chat("简报", kind="draft")
    g.settings.update({"workflow": "deadline_briefing"})
    await g.run_one()
    assert "重要事项" in g.require("jobs", first["id"])["result"]
    assert "临近截止" not in g.require("jobs", first["id"])["result"]
    second = g.chat("简报", kind="draft"); await g.run_one()
    assert "临近截止" in g.require("jobs", second["id"])["result"]
    assert "重要事项" not in g.require("jobs", second["id"])["result"]
    assert len({entry["result"]["extension_version"] for job in (first, second) for entry in g.evidence(job["id"])["tools"]}) == 2


async def test_real_tool_protocol_round_trip_affects_result(g):
    sample(g)
    class ToolModel:
        async def chat(self, **request):
            content = request["messages"][-1]["content"]
            if isinstance(content, str):
                return Reply([{"type": "tool_use", "id": "call-1", "name": "check_updates", "input": {}}], 50, 20)
            result = json.loads(content[0]["content"])
            return Reply([{"type": "text", "text": result["items"][0]["title"]}], 50, 20)
    g.model = ToolModel()
    job = g.chat("请调用 Skill"); await g.run_one()
    assert g.require("jobs", job["id"])["result"] == "重要事项"
    assert len(g.evidence(job["id"])["tools"]) == 1


async def test_transient_failure_retries_same_job_and_charges_failed_calls(g):
    class Failing:
        async def chat(self, **request): raise AgentError("受控网络失败", True)
    g.model = Failing(); job = g.chat("失败检查")
    for _ in range(3):
        await g.run_one(); g.clock.now += 3
    assert g.require("jobs", job["id"])["status"] == "failed"
    assert len(g.evidence(job["id"])["attempts"]) == 3
    assert len(g.evidence(job["id"])["usage"]) == 3
    assert not g.snapshot()["actions"]


async def test_cancellation_is_visible_and_survives_restart(g):
    started = asyncio.Event()
    class Blocking:
        async def chat(self, **request):
            started.set(); await asyncio.Event().wait()
    g.model = Blocking(); job = g.chat("取消检查")
    runner = asyncio.create_task(g.run_one()); await asyncio.wait_for(started.wait(), 2)
    await g.cancel(job["id"]); await runner
    other = Gateway(ROOT, g.directory, Model(), g.clock); other.recover()
    assert other.require("jobs", job["id"])["status"] == "cancelled"
    assert not other.db.all("SELECT * FROM messages WHERE role='assistant'")
    assert not other.snapshot()["actions"]


async def test_timeout_and_restart_deadline_are_bounded(g):
    class Blocking:
        async def chat(self, **request): await asyncio.sleep(5)
    g.model = Blocking(); g.settings.update({"job_timeout": 1})
    job = g.chat("超时检查"); await g.run_one()
    assert g.require("jobs", job["id"])["status"] == "timed_out"
    new = g.chat("恢复检查")
    g.db.execute("UPDATE jobs SET status='running',attempt=1,deadline=? WHERE id=?", (g.clock.now + 20, new["id"]))
    recovered = Gateway(ROOT, g.directory, Model(), g.clock); recovered.recover()
    assert recovered.require("jobs", new["id"])["deadline"] == g.clock.now + 20
    await recovered.run_one()
    assert recovered.require("jobs", new["id"])["attempt"] == 2


async def test_compaction_preserves_history_session_goals_and_pending_actions(g):
    goal = g.record("goal", {"title": "完成 Navi", "state": "active", "next_step": "审批文档"})
    session = g.session()
    previous = g.chat("准备文档", session["id"]); await g.run_one()
    action = g.propose(previous["id"], "export", "文档", "goal.md", "成果")
    job = g.chat("压缩当前会话", session["id"], "compact"); await g.run_one()
    saved = g.require("sessions", session["id"])
    protected = json.loads(saved["summary"])["protected"]
    assert protected["goals"][0]["id"] == goal["id"]
    assert protected["pending_actions"][0]["id"] == action["id"]
    assert len(g.messages(session["id"])) == 4
    resumed = g.chat("继续", session["id"]); await g.run_one()
    assert resumed["session_id"] == session["id"]
    assert action["id"] in g.model.calls[-1]["system"]


async def test_memory_management_invalidates_summaries_and_retrieval(g):
    record = g.record("memory", {"title": "偏好", "body": "喜欢简洁", "enabled": True})
    session = g.session()
    g.db.execute("UPDATE sessions SET summary='cached',summary_through='old' WHERE id=?", (session["id"],))
    g.record("memory", {**record["payload"], "enabled": False}, record["id"])
    assert not Agent(g).context()["memories"]
    assert g.require("sessions", session["id"])["summary"] == ""
    g.delete_record(record["id"])
    assert not g.snapshot()["records"]


async def test_long_history_automatically_compacts_before_continuing(g):
    session = g.session()
    with g.db.transaction() as conn:
        for index in range(42):
            conn.execute("INSERT INTO messages(id,session_id,role,content,job_id,created) VALUES(?,?,?,?,?,?)", (uid(), session["id"], "user" if index % 2 == 0 else "assistant", f"固定历史 {index}", None, g.clock.now - 100 + index))
    job = g.chat("继续原会话", session["id"]); await g.run_one()
    assert g.require("jobs", job["id"])["status"] == "succeeded"
    assert len(g.model.calls) == 2
    assert len(g.messages(session["id"])) == 44
    assert g.require("sessions", session["id"])["summary_through"]
    assert len(g.model.calls[-1]["messages"]) == 8


async def test_budget_stops_new_call_and_does_not_fabricate_success(g):
    g.settings.update({"daily_requests": 1})
    first = g.chat("第一次"); await g.run_one()
    second = g.chat("第二次"); await g.run_one()
    assert g.require("jobs", first["id"])["status"] == "succeeded"
    assert g.require("jobs", second["id"])["status"] == "failed"
    assert "预算" in g.require("jobs", second["id"])["error"]
    assert len(g.model.calls) == 1


def test_keys_are_private_and_absent_from_public_state_and_evidence(g):
    g.settings.update({"api_key": "test-only-secret"})
    assert g.settings.key_path.stat().st_mode & 0o777 == 0o600
    assert "test-only-secret" not in packed(g.snapshot())
    assert "test-only-secret" not in packed(g.audit())
    assert "test-only-secret" not in g.db.path.read_bytes().decode(errors="ignore")


def test_external_origin_and_unsafe_export_are_rejected(g):
    with TestClient(create_app(g)) as web:
        assert web.post("/api/sessions", json={}, headers={"Origin": "https://example.com"}).status_code == 403
        assert web.post("/api/sessions", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
        assert web.get("/api/state", headers={"Host": "evil.example"}).status_code == 403
    job = g.chat("测试")
    with pytest.raises(ValueError): g.propose(job["id"], "export", "文档", "../escape.md", "内容")
    with pytest.raises(ValueError): g.propose(job["id"], "notification", "消息", "email", "内容")


def test_invalid_config_is_rejected(g):
    for value in ({"daily_tokens": float("inf")}, {"timezone": "invalid/timezone"}, {"notification_policy": "unchecked"}):
        with pytest.raises(ValueError): g.settings.update(value)


async def test_endpoint_history_and_credentials_through_settings_api(g, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-official-key")
    first = {"messages": "https://first.example/anthropic/generate/", "chat_completions": "https://first.example/chat"}
    second = {**first, "chat_completions": "https://second.example/openai/chat"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(g)), base_url="http://127.0.0.1:8766") as web:
        initial = (await web.get("/api/state")).json()["settings"]
        assert initial["endpoint_id"] == "" and initial["endpoint_profiles"] == []
        assert initial["official_endpoints"]["messages"] == "https://api.deepseek.com/anthropic/v1/messages"
        saved = await web.put("/api/settings", json={"endpoint_urls": first})
        assert saved.status_code == 200
        first_id = saved.json()["endpoint_id"]
        assert saved.json()["endpoint_profiles"] == [{"id": first_id, "urls": first}]
        assert not saved.json()["api_key_configured"]
        assert g.settings.api_key() == ""
        assert (await web.put("/api/settings", json={"api_key": "test-first-key"})).json()["api_key_configured"]
        first_job_config = g.job_config()
        saved = (await web.put("/api/settings", json={"endpoint_urls": second, "api_key": "test-second-key"})).json()
        second_id = saved["endpoint_id"]
        assert [profile["urls"] for profile in saved["endpoint_profiles"]] == [second, first]
        assert g.settings.api_key(first_job_config) == "test-first-key"
        assert g.settings.api_key() == "test-second-key"
        assert (await web.put("/api/settings", json={"endpoint_id": first_id, "protocol": "chat_completions"})).json()["endpoint_id"] == first_id
        assert g.job_config()["endpoint_urls"] == first
        assert g.settings.api_key() == "test-first-key"
        assert (await web.put("/api/settings", json={"endpoint_id": ""})).json()["endpoint_id"] == ""
        assert g.settings.api_key() == "test-official-key"
        for bad_url in ("https://user:secret@example.com/v1/", "https://example.com/v1/?key=secret", "http://example.com/v1/", "file:///tmp/v1/", ""):
            assert (await web.put("/api/settings", json={"endpoint_urls": {**first, "messages": bad_url}})).status_code == 400
        assert (await web.put("/api/settings", json={"endpoint_urls": {"messages": first["messages"]}})).status_code == 400
        assert (await web.put("/api/settings", json={"endpoint_urls": first})).json()["endpoint_id"] == first_id
        public = (await web.get("/api/state")).text
        assert "test-first-key" not in public and "test-second-key" not in public
        assert "endpoint_profiles" not in first_job_config
    reloaded = Gateway(ROOT, g.directory, Model(), g.clock)
    assert [profile["id"] for profile in reloaded.settings.public()["endpoint_profiles"]] == [second_id, first_id]
    assert reloaded.settings.api_key(first_job_config) == "test-first-key"


async def test_delete_endpoint_clears_key_and_falls_back_only_for_active_endpoint(g, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    first = {"messages": "https://first.example/v1/messages", "chat_completions": "https://first.example/v1/chat/completions"}
    second = {"messages": "https://second.example/v1/messages", "chat_completions": "https://second.example/v1/chat/completions"}
    g.settings.update({"api_key": "test-official"})
    first_id = g.settings.update({"endpoint_urls": first, "api_key": "test-first"})["endpoint_id"]
    old_first_config = g.job_config()
    second_id = g.settings.update({"endpoint_urls": second, "api_key": "test-second"})["endpoint_id"]
    old_second_config = g.job_config()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(g)), base_url="http://127.0.0.1:8766") as web:
        rejected = await web.request("DELETE", "/api/settings/endpoints", json={"endpoint_id": ""})
        assert rejected.status_code == 400
        deleted = await web.request("DELETE", "/api/settings/endpoints", json={"endpoint_id": first_id})
        assert deleted.status_code == 200
        assert deleted.json()["endpoint_id"] == second_id and deleted.json()["endpoint_profiles"] == [{"id": second_id, "urls": second}]
        assert g.settings.api_key(old_first_config) == "" and g.settings.api_key() == "test-second"
        deleted = await web.request("DELETE", "/api/settings/endpoints", json={"endpoint_id": second_id})
        assert deleted.status_code == 200
        assert deleted.json()["endpoint_id"] == "" and deleted.json()["endpoint_profiles"] == []
        assert g.settings.api_key(old_second_config) == "" and g.settings.api_key() == "test-official"
        assert not (await web.put("/api/settings", json={"endpoint_urls": first})).json()["api_key_configured"]
    reloaded = Gateway(ROOT, g.directory, Model(), g.clock)
    assert [profile["urls"] for profile in reloaded.settings.public()["endpoint_profiles"]] == [first]
    assert reloaded.settings.api_key() == ""


def test_legacy_endpoint_migration_preserves_keys_and_existing_routes(g):
    active = "https://first.example/anthropic/v1/"
    second = "https://second.example/chat/completions"
    with g.db.transaction() as conn:
        g.settings.store(conn, "endpoint_url", active)
        g.settings.store(conn, "endpoint_history", [second, active])
    g.settings.write_credentials({"deepseek_api_key": "test-official", "endpoint_keys": {
        active: "test-active", second: "test-second",
    }})
    reloaded = Gateway(ROOT, g.directory, Model(), g.clock)
    public = reloaded.settings.public()
    assert len(public["endpoint_profiles"]) == 2
    assert public["endpoint_profiles"][0]["id"] == public["endpoint_id"]
    assert public["endpoint_profiles"][0]["urls"] == {"messages": active + "messages", "chat_completions": active + "chat/completions"}
    assert public["endpoint_profiles"][1]["urls"]["chat_completions"] == second
    assert reloaded.settings.api_key() == "test-active"
    assert reloaded.settings.api_key({"endpoint_url": second}) == "test-second"
    assert reloaded.settings.api_key({"endpoint_url": ""}) == "test-official"
    assert "endpoint_keys" not in reloaded.settings.credentials()


def test_single_heartbeat_and_invalid_record_payloads_are_rejected(g):
    first = create_due(g)
    with pytest.raises(ValueError): create_due(g)
    g.edit_schedule(first["id"], {"operation": "cancel"})
    assert create_due(g)["id"] != first["id"]
    with pytest.raises(ValueError): g.record("memory", {"title": "无效", "body": []})
    with pytest.raises(ValueError): g.skills.save_items([{"id": "x", "title": "x", "priority": 2, "source_url": "javascript:alert(1)"}])
    with TestClient(create_app(g)) as web:
        assert web.post("/api/chat", json=[]).status_code == 400
        assert web.post("/api/chat", content="{", headers={"Content-Type": "application/json"}).status_code == 400


async def test_reminder_retries_same_occurrence_after_sleep_crosses_deadline(g):
    class SleepyModel(Model):
        async def chat(self, **request):
            if not self.calls:
                self.calls.append(request)
                g.clock.now += 5400
                raise AgentError('DeepSeek 请求超时', True, 'model_timeout')
            return Reply([{'type': 'text', 'text': '今天想聊什么？'}])

    g.model = SleepyModel()
    task = g.schedule({'title': '早间聊天', 'instruction': '找我聊天', 'kind': 'recurring',
                       'interval_seconds': 86400, 'next_due': g.clock()})
    g.tick()
    await g.run_one()
    job = g.db.one('SELECT * FROM jobs WHERE task_id=?', (task['task_id'],))
    assert job['status'] == 'pending' and job['deadline'] is None
    g.clock.now = job['next_run']
    g.tick()
    await g.run_one()
    g.deliver()
    assert g.require('jobs', job['id'])['attempt'] == 2
    assert [r['body'] for r in g.snapshot()['notifications']] == ['今天想聊什么？']


@pytest.mark.parametrize('policy', ['daily', 'strict'])
async def test_reminder_still_notifies_when_generation_fails(g, policy):
    class Unavailable:
        async def chat(self, **request):
            raise AgentError('DeepSeek 请求超时', True, 'model_timeout')

    g.model = Unavailable()
    g.settings.update({'notification_policy': policy})
    create_due(g, 'once')
    g.tick()
    for _ in range(3):
        await g.run_one()
        g.clock.now += 2
    action, = g.snapshot()['actions']
    assert g.require('jobs', action['job_id'])['status'] == 'failed'
    assert '未能生成' in action['body']
    g.deliver()
    if policy == 'strict':
        assert not g.snapshot()['notifications'] and action['status'] == 'pending'
        g.decide(action['id'], 'approve', action['content_hash'])
    g.deliver()
    assert len(g.snapshot()['notifications']) == 1


@pytest.mark.parametrize('existing', ['undelivered', 'already_delivered', 'paused', 'exhausted'])
async def test_restart_recovers_only_outstanding_interrupted_reminder(g, existing):
    task = g.schedule({'title': '晨间聊天', 'instruction': '聊几句', 'kind': 'recurring',
                       'interval_seconds': 86400, 'next_due': g.clock()})
    g.tick()
    job = g.db.one('SELECT id FROM jobs WHERE task_id=?', (task['task_id'],))
    g.clock.now += 5400
    g.db.execute("UPDATE jobs SET status='failed', attempt=?, deadline=?, updated=?, error_code='model_timeout' WHERE id=?",
                 (3 if existing == 'exhausted' else 1, g.clock.now-5000, g.clock.now, job['id']))
    if existing == 'already_delivered':
        g.propose(job['id'], 'notification', '晨间聊天', 'inbox', '已经送达')
        g.deliver()
    if existing == 'paused':
        g.edit_schedule(task['id'], {'operation': 'pause'})
    restored = Gateway(ROOT, g.directory, Model(), g.clock)
    restored.recover()
    restored.recover()
    row = restored.require('jobs', job['id'])
    if existing != 'undelivered':
        assert row['status'] == 'failed'
        return
    assert row['status'] == 'pending'
    g.clock.now = row['next_run']
    await restored.run_one()
    restored.deliver()
    assert restored.require('jobs', job['id'])['attempt'] == 2
    assert len(restored.snapshot()['notifications']) == 1
