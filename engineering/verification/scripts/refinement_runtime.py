"""Validate launchers against the isolated preview, then restore daily Navi."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[3]
PREVIEW = ROOT / "engineering/verification/.cache/refinement-preview"
REPORT = ROOT / "engineering/evidence/03-refinement/runtime.json"
URL = "http://127.0.0.1:8766/api"
PREVIEW_ID = hashlib.sha256((str(ROOT) + ":" + str(PREVIEW)).encode()).hexdigest()[:24]
DAILY_ID = hashlib.sha256(str(ROOT).encode()).hexdigest()[:24]


def get(path):
    with urllib.request.urlopen(URL + path, timeout=10) as response:
        return json.load(response)


def command(name, *args, preview=True):
    environment = dict(os.environ)
    environment.pop("NAVI_ACCEPTANCE_DATA", None)
    if preview:
        environment["NAVI_ACCEPTANCE_DATA"] = str(PREVIEW)
    return subprocess.run([str(ROOT / name), *args], cwd=ROOT, env=environment,
                          capture_output=True, text=True, check=True).stdout


def save(report):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def check():
    health, before = get("/health"), get("/state")
    assert health["project_id"] == PREVIEW_ID, "只允许检查隔离预览服务"
    report = {"timestamp": datetime.now(timezone.utc).isoformat(),
              "evidence_type": "actual-local-runtime-isolated-data", "checks": []}
    command("start.command", "--no-browser")
    assert get("/health")["pid"] == health["pid"]
    assert "正在运行" in command("status.command")
    process = subprocess.check_output(["ps", "-o", "ppid=,tty=", "-p", str(health["pid"])], text=True).split()
    assert process[0] == "1" and process[1] in {"??", "?"}
    report["checks"].append("重复启动复用同一进程，status 正确；启动器退出后 PID 1 托管且无终端")
    messages = {row["id"]: get("/sessions/" + row["id"] + "/messages") for row in before["sessions"]}
    command("stop.command")
    try:
        get("/health")
        raise AssertionError("停止后服务仍可连接")
    except urllib.error.URLError:
        pass
    command("start.command", "--no-browser")
    after, restarted = get("/state"), get("/health")
    assert restarted["instance"] != health["instance"]
    for key in ("sessions", "records", "actions", "schedules", "notifications"):
        assert {row["id"] for row in before[key]} == {row["id"] for row in after[key]}
    for identity, history in messages.items():
        assert get("/sessions/" + identity + "/messages") == history
    assert before["settings"] == after["settings"]
    report["checks"].append("实际停止后入口断开；新进程恢复相同消息、条目、调度、动作和配置")
    assert {row["id"] for row in json.loads(command("navi", "sessions"))} == {row["id"] for row in after["sessions"]}
    report["checks"].append("CLI 与 Web 使用同一 Gateway 和会话集合")
    request = urllib.request.Request(URL + "/sessions", data=b'{"title":"CLI lifecycle acceptance"}', headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request) as response:
        topic = json.load(response)
    identity = topic["id"]
    command("navi", "rename-session", identity, "CLI title acceptance")
    command("navi", "archive-session", identity)
    found = json.loads(command("navi", "search-sessions", "CLI title acceptance"))
    assert len(found) == 1 and found[0]["state"] == "archived"
    command("navi", "unarchive-session", identity)
    reminder = json.loads(command("navi", "schedule", "CLI delete acceptance", "本机测试提醒", "--kind", "once", "--in", "86400", "--session", identity))
    impact = json.loads(command("navi", "deletion-impact", identity))
    assert len(impact["reminders"]) == 1 and impact["reminders"][0]["id"] == reminder["id"]
    command("navi", "delete-session", identity)
    assert not json.loads(command("navi", "search-sessions", "CLI title acceptance"))
    saved = next(row for row in get("/state")["schedules"] if row["id"] == reminder["id"])
    assert saved["state"] == "cancelled" and not saved["enabled"]
    report["checks"].append("CLI 重命名、封存、标题找回、解封及永久删除实际生效，删除只取消关联测试提醒")
    before_idle = get("/health")["instance"]
    command("start.command", "--no-browser")
    assert get("/health")["instance"] == before_idle
    report.update(status="passed", browser_close="pending", native_terminal_window_close="not directly exercised; detached process verified", daily_service_restored=False)
    save(report)
    print("隔离启动、重启恢复和 CLI 生命周期验收通过。")


def restore():
    try:
        health = get("/health")
    except urllib.error.URLError:
        health = None
    if health and health["project_id"] == PREVIEW_ID:
        command("stop.command")
    elif health:
        assert health["project_id"] == DAILY_ID, "未知服务，未停止"
    command("start.command", "--no-browser", preview=False)
    assert get("/health")["project_id"] == DAILY_ID
    assert "正在运行" in command("status.command", preview=False)
    report = json.loads(REPORT.read_text())
    report["daily_service_restored"] = True
    report["restored_at"] = datetime.now(timezone.utc).isoformat()
    save(report)
    print("日常 Navi 已恢复，使用原来的 data 目录。")


def browser_closed():
    assert get("/health")["project_id"] == PREVIEW_ID
    state = get("/state")
    assert not state["check_items"]
    assert not any(row["payload"].get("enabled", True) for row in state["records"] if row["kind"] == "source")
    reminder = json.loads(command("navi", "schedule", "关闭浏览器后台空检查", "检查空的本机事项", "--kind", "heartbeat", "--interval", "3600", "--in", "0"))
    for _ in range(25):
        job = next((row for row in get("/state")["jobs"] if row["task_id"] == reminder["task_id"]), None)
        if job and job["status"] == "succeeded":
            break
        time.sleep(0.2)
    else:
        raise AssertionError("关闭浏览器后的后台检查未完成")
    evidence = get("/jobs/" + job["id"] + "/evidence")
    assert not evidence["usage"] and not evidence["actions"]
    assert any(row["kind"] == "heartbeat.quiet" for row in evidence["audit"])
    command("navi", "pause", reminder["id"])
    report = json.loads(REPORT.read_text())
    report["browser_close"] = "passed"
    report["checks"].append("实际关闭验收浏览器后服务可访问，后台空检查自动完成且静默，零模型调用及投递")
    save(report)


def finish():
    try:
        browser_closed()
    except Exception as error:
        report = json.loads(REPORT.read_text())
        report.update(browser_close="failed", browser_close_error=str(error))
        save(report)
        raise
    finally:
        restore()


if __name__ == "__main__":
    operation = sys.argv[1] if len(sys.argv) == 2 else ""
    if operation == "check":
        check()
    elif operation == "restore":
        restore()
    elif operation == "finish":
        finish()
    else:
        raise SystemExit("用法：engineering/verification/scripts/refinement_runtime.py check|restore|finish")
