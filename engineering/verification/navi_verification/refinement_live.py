"""Real DeepSeek product-flow acceptance on isolated, non-personal data."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

from navi.gateway import Gateway
from .live import LiveGateway, ROOT, STATE_ROOT, wait, output_directory, summarize


async def scheduled_job(g, schedule):
    for _ in range(50):
        row = g.db.one("SELECT * FROM jobs WHERE task_id=? ORDER BY created DESC LIMIT 1", (schedule["task_id"],))
        if row:
            return row
        await asyncio.sleep(0.2)
    raise RuntimeError("后台未自动触发约定提醒")


async def main():
    output_dir = output_directory()
    owner = Gateway(ROOT, ROOT / "data")
    key = owner.settings.api_key()
    if not key:
        print("细化计划真实验收未执行：本项目尚未配置 DeepSeek 密钥。")
        return 2
    directory = Path(tempfile.mkdtemp(prefix="refinement-live-", dir=STATE_ROOT))
    g = LiveGateway(ROOT, directory)
    g.owner = owner
    g.settings.api_key = lambda: key
    g.settings.update(owner.settings.current())
    g.settings.update({"notification_policy": "strict"})
    g.skills.save_items([{"id": "outline", "title": "整理验收报告提纲", "body": "独立测试事项", "priority": 3, "done": False, "due_at": None}])
    report = {"evidence_type": "live-deepseek-product-refinement", "timestamp": datetime.now(timezone.utc).isoformat(), "model": g.settings.current()["model"], "checks": [], "status": "running", "week_trial": "pending-real-use"}
    jobs = []
    await g.start()
    try:
        topic = g.session("Navi 独立验收读书报告")
        created = g.chat("请调用 create_goal 保存目标：完成独立验收读书报告，下一步为整理三篇参考资料。并调用 remember 记住验收阅读偏好：回复先给一个具体下一步。这是独立验收资料。不要安排提醒。", topic["id"])
        jobs.append(created["id"])
        await wait(g, created["id"])
        tool_names = {row["name"] for row in g.evidence(created["id"])["tools"]}
        assert {"create_goal", "remember"} <= tool_names, "真实模型没有完成目标与记忆保存"
        goal = next(row for row in g.snapshot()["records"] if row["kind"] == "goal")
        memory = next(row for row in g.snapshot()["records"] if row["kind"] == "memory")
        assert goal["source_session"]["id"] == topic["id"]
        assert not g.snapshot()["schedules"]
        report["checks"].append("真实对话明确保存目标和记忆，来源可追溯，未自动创建提醒")

        updated = g.chat(f"请调用 update_goal 修改目标 {goal['id']}，下一步准确保存为：列出三个论点。保持进行中，不设置提醒。", topic["id"])
        jobs.append(updated["id"])
        await wait(g, updated["id"])
        assert json.loads(g.require("records", goal["id"])["payload"])["next_step"] == "列出三个论点"
        report["checks"].append("真实模型工具修改目标下一步，持久内容与回执核对")

        request = g.chat(f"请调用 schedule_task，为已保存目标 {goal['id']} 设置单次提醒，标题为“报告测试提醒”，提醒我列出三个论点。kind 为 once，next_due 为 {g.clock() + 3600:.3f}，归属本对话。只设一个提醒。", topic["id"])
        jobs.append(request["id"])
        await wait(g, request["id"])
        schedules = g.snapshot()["schedules"]
        assert len(schedules) == 1, "真实模型未持久保存唯一的单次提醒"
        assert schedules[0]["session_id"] == topic["id"], "真实提醒的归属对话不符"
        assert schedules[0]["goal_id"] == goal["id"], "真实模型未保留明确的目标关联"
        schedule = schedules[0]
        g.edit_schedule(schedule["id"], {"next_due": g.clock()})
        due = await scheduled_job(g, schedule)
        jobs.append(due["id"])
        await wait(g, due["id"])
        action = g.db.one("SELECT * FROM actions WHERE job_id=? AND kind='notification'", (due["id"],))
        assert action and action["status"] == "pending"
        assert not g.db.all("SELECT * FROM deliveries")
        assert not g.db.all("SELECT * FROM messages WHERE outbox_id IS NOT NULL")
        g.edit_session(topic["id"], {"operation": "archive"})
        g.decide(action["id"], "approve", action["content_hash"])
        g.deliver(); g.deliver()
        main_messages = [r for r in g.messages(g.main_session()["id"]) if r["outbox_id"]]
        assert len(main_messages) == 1 and main_messages[0]["origin_session_id"] == topic["id"]
        assert "已封存话题" in main_messages[0]["content"]
        report["checks"].append("真实单次提醒先生成持久提案，严格审批后按实际封存状态转主对话，重复投递无新增")

        unarchive = g.chat(f"请解封副对话“{topic['title']}”，只处理这个独立验收话题。", g.main_session()["id"])
        jobs.append(unarchive["id"])
        await wait(g, unarchive["id"])
        assert g.require("sessions", topic["id"])["state"] == "active"
        assert any(row["name"] == "unarchive_conversation" for row in g.evidence(unarchive["id"])["tools"])
        report["checks"].append("直接请求 Navi 解封通过真实运行时工具完成，核对持久结果")

        g.settings.update({"notification_policy": "daily"})
        normal = g.schedule({"title": "解封后的周期提醒", "instruction": "简短提醒我列出报告的三个论点。", "kind": "recurring", "interval_seconds": 3600, "next_due": g.clock(), "session_id": topic["id"], "goal_id": goal["id"]})
        normal_job = await scheduled_job(g, normal)
        g.edit_schedule(normal["id"], {"operation": "pause"})
        jobs.append(normal_job["id"])
        await wait(g, normal_job["id"])
        g.deliver()
        assert len([r for r in g.messages(topic["id"]) if r["outbox_id"]]) == 1
        assert len([r for r in g.messages(g.main_session()["id"]) if r["outbox_id"]]) == 1
        report["checks"].append("解封后真实周期提醒按日常授权回创建副对话，旧投递历史不搬动")

        exported = g.chat(f"请为目标 {goal['id']} 调用 propose_export 准备读书报告提纲，filename 为 refinement.md。内容只用本次独立验收信息。等待审批后再写文件。", topic["id"], "draft")
        jobs.append(exported["id"])
        await wait(g, exported["id"])
        proposal = g.db.one("SELECT * FROM actions WHERE job_id=? AND kind='export'", (exported["id"],))
        assert proposal and not (directory / "exports").exists()
        revised = g.edit_action(proposal["id"], {"body": proposal["body"] + "\n\n验收修改：先列出三个论点。"})
        try:
            g.decide(proposal["id"], "approve", proposal["content_hash"])
            raise AssertionError("旧版本仍可批准")
        except ValueError:
            pass
        g.decide(revised["id"], "approve", revised["content_hash"])
        g.deliver()
        artifact = g.record_detail("artifact-" + revised["id"])
        file = directory / "exports" / artifact["payload"]["filename"]
        assert file.read_text() == revised["body"]
        assert goal["id"] in artifact["payload"]["goal_ids"]
        report["checks"].append("真实模型准备成果，编辑使旧版本失效，当前版本批准后实际导出，文件字节和目标关联核对")

        pending = g.propose(exported["id"], "export", "删除取消验收", "cancelled.md", "此待审内容不得写入")
        await g.delete_session(topic["id"])
        assert g.require("actions", pending["id"])["status"] == "invalidated"
        assert g.require("tasks", normal["task_id"])["state"] == "cancelled"
        assert not g.search_sessions(topic["title"])
        assert g.require("records", goal["id"]) and g.require("records", memory["id"])
        assert file.read_text() == revised["body"]
        await g.stop()
        restored = Gateway(ROOT, directory)
        restored.recover(); restored.tick(); restored.deliver()
        assert not restored.search_sessions(topic["title"])
        assert restored.require("schedules", normal["id"])["enabled"] == 0
        assert len(restored.db.all("SELECT * FROM deliveries")) == 3
        report["checks"].append("删除副对话取消关联提醒及未决动作，保留独立目标、记忆和已导出成果，重建后无复活或重复")
        from .muse_revision_live import run as revision_acceptance
        if not await revision_acceptance(owner, key, stage=3, output_dir=output_dir):
            raise RuntimeError('Muse 第一轮真实验收失败：' + str(output_dir / 'muse-v1.json'))
        report["checks"].append("Muse 修改的自动内容、主题摘要、构想启动及两个类别的目标闭环通过真实验收")
        from .muse_revision_live import run_v2, V2_PHASE
        if not await run_v2(owner, key, phase=V2_PHASE, output_dir=output_dir):
            raise RuntimeError('Muse v2 真实验收失败：' + str(output_dir / 'muse-v2.json'))
        report['checks'].append('Muse v2 当前交付阶段通过独立真实验收')
        report["status"] = "passed"
    except (RuntimeError, AssertionError, ValueError) as error:
        report["status"], report["error"] = "failed", str(error) or "未满足验收断言"
        report["failed_step"] = len(report["checks"]) + 1
    finally:
        await g.stop()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["jobs"] = [g.evidence(identity) for identity in jobs]
        path = output_dir / 'refinement.json'
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        owner.db.event("acceptance.refinement", {"status": report["status"], "report": str(path.relative_to(ROOT))})
        from .muse_revision_live import V2_PHASE
        summarize(output_dir, V2_PHASE)
    print(f"细化计划真实验收：{report['status']}。证据：{path}")
    if report.get("error"):
        print(report["error"])
    return 0 if report["status"] == "passed" else 1
