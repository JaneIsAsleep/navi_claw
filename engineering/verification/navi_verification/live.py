"""Explicit, paid DeepSeek acceptance against isolated fixed data."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from zoneinfo import ZoneInfo

from navi.gateway import Gateway, TERMINAL
from navi.storage import packed
from navi.storage import uid


ROOT = Path(__file__).resolve().parents[3]
STATE_ROOT = ROOT / 'engineering/verification/state'


def output_directory():
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y%m%d-%H%M%S')
    path = ROOT / 'engineering/evidence/05-muse-revision-v2/implementation' / ('run-' + stamp)
    path.mkdir(parents=True, exist_ok=False)
    return path


def summarize(output_dir, phase):
    groups = {}
    for name in ('basic', 'refinement', 'muse-v1', 'muse-v2'):
        path = output_dir / (name + '.json')
        if path.exists():
            report = json.loads(path.read_text())
            usage = [row for job in report.get('jobs', []) for row in job.get('usage', [])]
            groups[name] = {'status': report['status'], 'report': str(path.relative_to(ROOT)), 'requests': len(usage), 'input_tokens': sum(row['input_tokens'] for row in usage), 'output_tokens': sum(row['output_tokens'] for row in usage), 'estimated_requests': sum(row['estimated'] for row in usage)}
        else:
            groups[name] = {'status': 'not-executed'}
    (output_dir / 'summary.json').write_text(json.dumps({'phase': phase, 'groups': groups, 'real_0600': 'pending-real-observation', 'real_sleep_recovery': 'pending-real-observation', 'long_trial': 'pending-real-use'}, ensure_ascii=False, indent=2))


class LiveGateway(Gateway):
    """Keep live acceptance data isolated while charging the real daily budget."""
    def reserve_call(self, job_id, input_size, config):
        identity = self.owner.reserve_call(job_id, input_size, config)
        row = self.owner.db.one("SELECT * FROM usage WHERE id=?", (identity,))
        self.db.execute("INSERT INTO usage VALUES(?,?,?,?,?,?)", tuple(row[key] for key in ("id", "job_id", "input_tokens", "output_tokens", "estimated", "created")))
        return identity

    def finish_usage(self, identity, reply):
        super().finish_usage(identity, reply)
        self.owner.finish_usage(identity, reply)


async def wait(gateway, job_id):
    for _ in range(1200):
        job = gateway.require("jobs", job_id)
        if job["status"] in TERMINAL:
            if job["status"] != "succeeded":
                raise RuntimeError(job["error"] or job["status"])
            return job
        await asyncio.sleep(0.2)
    await gateway.cancel(job_id)
    raise RuntimeError("真实验收等待超时，作业已取消")


async def main():
    output_dir = output_directory()
    existing = Gateway(ROOT, ROOT / "data")
    api_key = existing.settings.api_key()
    if not api_key:
        print("真实 API 验收未执行：请先在 http://127.0.0.1:8766/#settings 配置 DeepSeek 密钥。", file=sys.stderr)
        return 2
    directory = Path(tempfile.mkdtemp(prefix="live-", dir=STATE_ROOT))
    gateway = LiveGateway(ROOT, directory)
    gateway.owner = existing
    gateway.settings.api_key = lambda: api_key
    gateway.settings.update(existing.settings.current())
    gateway.settings.update({"notification_policy": "strict"})
    items = json.loads((ROOT / "engineering/verification/fixtures/check_items.json").read_text())
    for item in items:
        if "due_offset_seconds" in item:
            item["due_at"] = gateway.clock() + item.pop("due_offset_seconds")
    gateway.skills.save_items(items)
    report = {"evidence_type": "live-deepseek", "timestamp": datetime.now(timezone.utc).isoformat(), "model": gateway.settings.current()["model"], "protocol": gateway.settings.current()["protocol"], "data_source": "isolated fixed fixtures", "checks": [], "jobs": [], "status": "running"}
    job_ids = []
    await gateway.start()
    try:
        connection = gateway.chat("请调用 check_updates 工具，列出工具实际返回的事项标题并说明来源。")
        job_ids.append(connection["id"])
        await wait(gateway, connection["id"])
        assert gateway.evidence(connection["id"])["tools"], "模型未调用真实 Skill，不能宣布工具回传通过"
        report["checks"].append("真实 API 与工具结果回传")

        schedule = gateway.schedule({"title": "真实 Heartbeat", "instruction": "根据实际 Skill 检查结果生成简报，写出每条事项的原始标题和来源。", "kind": "heartbeat", "interval_seconds": 600, "next_due": gateway.clock()})
        for _ in range(30):
            heartbeat = gateway.db.one("SELECT * FROM jobs WHERE task_id=?", (schedule["task_id"],))
            if heartbeat: break
            await asyncio.sleep(0.2)
        assert heartbeat, "后台未自动触发"
        job_ids.append(heartbeat["id"])
        await wait(gateway, heartbeat["id"])
        gateway.edit_schedule(schedule["id"], {"operation": "pause"})
        action = gateway.db.one("SELECT * FROM actions WHERE job_id=?", (heartbeat["id"],))
        assert action and action["status"] == "pending"
        assert not gateway.db.all("SELECT * FROM deliveries")
        gateway.decide(action["id"], "reject", action["content_hash"])
        gateway.deliver()
        assert not gateway.db.all("SELECT * FROM outbox")
        report["checks"].append("无消息触发 Heartbeat；严格策略拒绝后零投递")

        items[0]["body"] += "；真实验收出现一项变化"
        gateway.skills.save_items(items)
        gateway.edit_schedule(schedule["id"], {"operation": "resume", "next_due": gateway.clock()})
        for _ in range(30):
            second = gateway.db.one("SELECT * FROM jobs WHERE task_id=? AND id<>?", (schedule["task_id"], heartbeat["id"]))
            if second: break
            await asyncio.sleep(0.2)
        assert second
        job_ids.append(second["id"])
        await wait(gateway, second["id"])
        gateway.edit_schedule(schedule["id"], {"operation": "pause"})
        action = gateway.db.one("SELECT * FROM actions WHERE job_id=?", (second["id"],))
        gateway.decide(action["id"], "approve", action["content_hash"])
        assert gateway.db.one("SELECT state FROM outbox WHERE action_id=?", (action["id"],))["state"] == "pending"
        gateway.deliver(); gateway.deliver()
        assert len(gateway.db.all("SELECT * FROM deliveries")) == 1
        report["checks"].append("批准后先进入 Outbox，实际投递且重复执行无新增投递")

        gateway.settings.update({"workflow": "deadline_briefing", "notification_policy": "daily"})
        replacement = gateway.chat("生成简报，写出 Skill 实际返回的原始事项标题", kind="draft")
        job_ids.append(replacement["id"])
        await wait(gateway, replacement["id"])
        original_items = gateway.evidence(heartbeat["id"])["tools"][0]["result"]["items"]
        alternate_items = gateway.evidence(replacement["id"])["tools"][0]["result"]["items"]
        assert [r["id"] for r in original_items] != [r["id"] for r in alternate_items]
        assert alternate_items[0]["title"] in gateway.require("jobs", replacement["id"])["result"]
        report["checks"].append("配置切换 Skill 后实际筛选与 Agent 输出变化")

        goal = gateway.record("goal", {"title": "完成固定验收", "state": "active", "next_step": "审批并保存简报"})
        export_job = gateway.chat("请调用 propose_export，把当前实际关注事项整理成一份简报，文件名 acceptance.md。仅准备提案，等待人工审批。", kind="draft")
        job_ids.append(export_job["id"])
        await wait(gateway, export_job["id"])
        export = gateway.db.one("SELECT * FROM actions WHERE job_id=? AND kind='export'", (export_job["id"],))
        assert export, "模型未产生文档提案"
        assert export["status"] == "pending"
        # Fixed long history makes the compaction evidence reproducible without buying 42 chats.
        with gateway.db.transaction() as conn:
            for index in range(42):
                conn.execute("INSERT INTO messages(id,session_id,role,content,job_id,created) VALUES(?,?,?,?,?,?)", (uid(), connection["session_id"], "user" if index % 2 == 0 else "assistant", f"固定长会话第 {index + 1} 轮：目标为完成固定验收，下一步为审批并保存简报。", connection["id"], gateway.clock() - 100 + index / 1000))
        compaction = gateway.chat("压缩当前会话，保留目标和未完成工作", connection["session_id"], "compact")
        job_ids.append(compaction["id"])
        await wait(gateway, compaction["id"])
        summary = json.loads(gateway.require("sessions", connection["session_id"])["summary"])
        assert goal["id"] in packed(summary) and export["id"] in packed(summary)
        continuation = gateway.chat("继续当前会话，明确说出我的目标和下一步。", connection["session_id"])
        job_ids.append(continuation["id"])
        resumed = await wait(gateway, continuation["id"])
        assert resumed["session_id"] == connection["session_id"] and "完成固定验收" in resumed["result"]
        gateway.decide(export["id"], "approve", export["content_hash"])
        gateway.deliver()
        report["checks"].append("固定长历史经真实 API 压缩后同会话继续；保留目标与待审批 ID；Agent 文档提案审批后实际导出")
        daily = gateway.schedule({"title": "日常自动提醒", "instruction": "根据实际检查结果生成简短提醒", "kind": "once", "interval_seconds": 60, "next_due": gateway.clock()})
        for _ in range(30):
            daily_job = gateway.db.one("SELECT * FROM jobs WHERE task_id=?", (daily["task_id"],))
            if daily_job: break
            await asyncio.sleep(0.2)
        assert daily_job
        job_ids.append(daily_job["id"])
        await wait(gateway, daily_job["id"])
        gateway.deliver()
        daily_action = gateway.db.one("SELECT * FROM actions WHERE job_id=? AND kind='notification'", (daily_job["id"],))
        assert daily_action["status"] == "delivered"
        report["checks"].append("日常策略真实普通提醒自动投递，外部导出仍人工审批")
        gateway.settings.update({"workflow": "priority_briefing"})
        gateway.feeds.follow("python")
        public_job = gateway.chat("根据实际检查结果，列出 Python 官方 RSS 返回的每条原始标题、发布时间和完整来源 URL。只描述已获取的事实，区分发布时间与检查时间。", kind="draft")
        job_ids.append(public_job["id"])
        public_result = await wait(gateway, public_job["id"])
        checked = gateway.evidence(public_job["id"])["tools"][0]["result"]
        public_items = [item for item in checked["items"] if item["id"].startswith("rss-")]
        assert public_items and not checked["source_errors"], "真实公开信息源读取未通过"
        assert public_items[0]["title"] in public_result["result"] and public_items[0]["source_url"] in public_result["result"]
        report["public_sources"] = {"items": public_items, "checked_at": checked["checked_at"]}
        report["checks"].append("Python 官方 RSS 实际读取，标题、来源及发布时间进入真实 DeepSeek 简报")
        await gateway.stop()
        restored = Gateway(ROOT, directory)
        restored.recover()
        assert restored.messages(connection["session_id"])
        assert restored.require("actions", export["id"])["status"] == "delivered"
        assert restored.require("schedules", schedule["id"])["enabled"] == 0
        report["checks"].append("重建 Gateway 后会话、审批投递与暂停调度保留")
        report["status"] = "passed"
    except (RuntimeError, AssertionError, ValueError) as error:
        report["status"] = "failed"
        report["error"] = str(error)
    finally:
        await gateway.stop()
        report["jobs"] = [gateway.evidence(identity) for identity in job_ids]
        evidence_path = output_dir / 'basic.json'
        evidence_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        existing.db.event("acceptance.completed", {"status": report["status"], "report": str(evidence_path.relative_to(ROOT)), "job_ids": job_ids})
        from .muse_revision_live import V2_PHASE
        summarize(output_dir, V2_PHASE)
    print(f"真实验收：{report['status']}。证据：{evidence_path}")
    if report.get("error"): print(report["error"], file=sys.stderr)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    if "--refinement" in sys.argv[1:]:
        from .refinement_live import main as refinement_main
        raise SystemExit(asyncio.run(refinement_main()))
    raise SystemExit(asyncio.run(main()))
