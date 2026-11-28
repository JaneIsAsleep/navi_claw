import argparse
import getpass
import json
import sys
import time
import warnings
from urllib.parse import urlencode

import httpx

from .cli_features import (
    add_check_items, add_feed, add_records, add_settings, check_items_command, confirm,
    feed_command, fields, records_command, reference, segment, settings_command, with_wait,
)
from .web_search import SEARCH_PROVIDERS


TERMINAL = {"succeeded", "failed", "timed_out", "cancelled"}
COLLECTIONS = {"sessions", "schedules", "actions", "jobs", "notifications"}
SCHEDULE_OPERATIONS = {"pause": "pause", "resume": "resume", "cancel-schedule": "cancel",
                       "complete-schedule": "complete", "remove-schedule": "remove_from_list"}
CHAT_COMMANDS = {"chat": "", "ideas": "根据我的目标和关注事项给出建议", "draft": "", "compact": "压缩当前会话"}
RECORD_COMMANDS = {"goal", "memory", "idea", "artifact"}
SESSION_EDITS = {"rename-session": "rename", "archive-session": "archive", "unarchive-session": "unarchive"}


class CLIAdapter:
    def __init__(self, url="http://127.0.0.1:8766"):
        self.url = url

    @staticmethod
    def check_response(response):
        if response.is_error:
            try:
                detail = response.json().get("detail")
            except (ValueError, AttributeError):
                detail = None
            raise ValueError(detail or f"请求失败（HTTP {response.status_code}）")

    def call(self, method, path, values=None, *, timeout=15):
        try:
            response = httpx.request(method, self.url + "/api" + path, json=values if values is not None else {},
                                     timeout=timeout, trust_env=False)
            self.check_response(response)
            return response.json()
        except httpx.HTTPError as error:
            raise ValueError("Navi 请求未完成，请检查 ./status.command 和作业或记录状态") from error

    def download(self, path):
        try:
            response = httpx.get(self.url + "/api" + path, timeout=15, trust_env=False)
            self.check_response(response)
            return response.content
        except httpx.HTTPError as error:
            raise ValueError("无法从本机 Navi 下载成果") from error

    def wait(self, identity):
        while True:
            job = self.call("GET", f"/jobs/{segment(identity)}")
            if job["status"] in TERMINAL:
                return job
            time.sleep(0.3)

    def follow(self, identity):
        event = ""
        saw_delta = False
        try:
            with httpx.stream("GET", self.url + f"/api/jobs/{segment(identity)}/events",
                              timeout=30, trust_env=False) as response:
                if response.is_error:
                    response.read()
                    self.check_response(response)
                for line in response.iter_lines():
                    if line.startswith("event: "):
                        event = line[7:]
                    elif line.startswith("data: ") and event:
                        payload = json.loads(line[6:])
                        if event == "done":
                            break
                        if event == "delta":
                            safe = "".join(char for char in payload.get("text", "")
                                           if char in "\n\t" or char.isprintable())
                            print(safe, end="", file=sys.stderr, flush=True)
                            saw_delta = True
                        event = ""
        except (httpx.HTTPError, ValueError):
            print("\n流式连接已中断，继续查询作业结果。", file=sys.stderr)
        finally:
            if saw_delta:
                print(file=sys.stderr)
        return self.wait(identity)


def read_secret(label):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass(f"{label}：").strip()
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        raise ValueError("请输入密钥，请使用支持隐藏输入的终端") from None
    if not key:
        raise ValueError(f"{label}不能为空")
    return key


def read_search_key():
    return read_secret("搜索服务 API 密钥")


def add_schedule_time(parser):
    due = parser.add_mutually_exclusive_group()
    due.add_argument("--in", dest="delay", type=float, help="距离触发的秒数")
    due.add_argument("--at", dest="local_due", help="当地时间，例如 2026-12-01T09:00")
    parser.add_argument("--timezone", help="--at 使用的 IANA 时区，默认沿用 Navi 设置")


def schedule_time(api, args, default=None):
    if args.timezone and args.local_due is None:
        raise ValueError("--timezone 需与 --at 一起使用")
    if args.local_due is not None:
        timezone = args.timezone or api.call("GET", "/state?feed_limit=1")["settings"]["timezone"]
        return {"local_due": args.local_due, "timezone": timezone}
    delay = args.delay if args.delay is not None else default
    return {"next_due": time.time() + delay} if delay is not None else {}


def add_wait_and_stream(parser):
    options = parser.add_mutually_exclusive_group()
    options.add_argument("--wait", action="store_true")
    options.add_argument("--stream", action="store_true", help="实时增量写到 stderr，最终作业 JSON 写到 stdout")


def build_parser():
    parser = argparse.ArgumentParser(prog="navi", description="Navi CLI，与 Web 共享本机 Gateway")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tui", help="打开全屏终端界面")
    sub.add_parser("status", help="查看完整状态")
    sub.add_parser("sessions", help="查看未封存会话")
    search = sub.add_parser("search-sessions", help="搜索对话，包含封存话题")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--all", action="store_true", help="同时匹配消息正文")
    search.add_argument("--recent", action="store_true", help="无关键词时列出最近对话")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--offset", type=int, default=0)
    sub.add_parser("session", help="查看会话详情").add_argument("id")
    confirm(sub.add_parser("reset-main", help="清空主对话消息"), "确认清空主对话")
    sub.add_parser("read-session", help="将对话标为已读").add_argument("id")
    for name in (*SESSION_EDITS, "delete-session", "deletion-impact"):
        command = sub.add_parser(name)
        command.add_argument("id")
        if name == "rename-session":
            command.add_argument("title")
    for name, default_text in CHAT_COMMANDS.items():
        command = sub.add_parser(name)
        command.add_argument("text", nargs="?", default=default_text)
        target = command.add_mutually_exclusive_group()
        target.add_argument("--session", help="指定会话 ID，默认使用主对话")
        if name == "chat":
            target.add_argument("--new-session", action="store_true", help="新建副对话并发送消息")
            command.add_argument("--web-search", action=argparse.BooleanOptionalAction, help="保存该对话的联网搜索开关")
        add_wait_and_stream(command)
        command.add_argument("--reference", action="append", metavar="ID", help="引用已有记录，可以重复指定")
        command.add_argument("--timezone", help="本轮对话的 IANA 时区")
    command = sub.add_parser("web-search", help="查看或设置单个对话的联网搜索开关")
    command.add_argument("operation", choices=["status", "on", "off"], nargs="?", default="status")
    command.add_argument("--session", help="指定未封存的会话 ID，默认使用主对话")
    command = sub.add_parser("search-settings", help="查看或修改与 Web 共用的第三方搜索服务")
    command.add_argument("--provider", choices=SEARCH_PROVIDERS, help="选择第三方搜索服务")
    command.add_argument("--set-key", action="store_true", help="以隐藏输入保存所选服务的密钥")
    with_wait(sub.add_parser("check-native-search", help="测试当前模型连接的 DeepSeek 原生搜索"))
    sub.add_parser("messages").add_argument("session")
    sub.add_parser("schedules", help="查看提醒排程")
    command = sub.add_parser("schedule", help="创建单次、周期或 Heartbeat 提醒")
    command.add_argument("title")
    command.add_argument("instruction")
    command.add_argument("--kind", choices=["once", "recurring", "heartbeat"], default="recurring")
    command.add_argument("--interval", dest="interval_seconds", type=float, default=3600)
    add_schedule_time(command)
    command.add_argument("--session", dest="session_id")
    command.add_argument("--goal", dest="goal_id", help="关联目标 ID")
    command.add_argument("--calendar-timezone", help="日历周期的 IANA 时区，间隔须为 86400 或 604800 秒")
    for name in (*SCHEDULE_OPERATIONS, "schedule-history"):
        sub.add_parser(name).add_argument("id")
    command = sub.add_parser("edit-schedule")
    command.add_argument("id")
    command.add_argument("--title")
    command.add_argument("--instruction")
    command.add_argument("--interval", dest="interval_seconds", type=float)
    add_schedule_time(command)
    command = sub.add_parser("job")
    command.add_argument("id")
    add_wait_and_stream(command)
    sub.add_parser("jobs", help="查看近期和进行中的作业")
    sub.add_parser("cancel-job").add_argument("id")
    sub.add_parser("actions", help="查看审批提案和投递状态")
    sub.add_parser("action-history", help="查看审批版本和执行记录").add_argument("id")
    command = sub.add_parser("edit-action", help="修改待审批提案，修改后须重新审批")
    command.add_argument("id")
    command.add_argument("--title")
    command.add_argument("--target")
    command.add_argument("--body")
    for name in ("approve", "reject"):
        command = sub.add_parser(name)
        command.add_argument("id")
        command.add_argument("--hash", required=True, help="审批列表中具体内容的 content_hash")
    sub.add_parser("notifications", help="查看站内通知")
    sub.add_parser("read-notifications", help="将站内通知标为已读")
    sub.add_parser("acknowledge", help="确认收到并收起某条提醒").add_argument("id", metavar="OUTBOX_ID")
    sub.add_parser("dismiss-reminder", help="按审批动作 ID 收起已投递提醒").add_argument("id")
    sub.add_parser("evidence").add_argument("id")
    sub.add_parser("audit").add_argument("--job", dest="job_id", help="仅查看指定作业")
    add_settings(sub)
    add_records(sub)
    add_feed(sub)
    add_check_items(sub)
    return parser


def chat_command(api, args):
    values = {"text": args.text, "session_id": args.session, "kind": args.command, **fields(args, "timezone")}
    if args.command == "chat":
        if args.new_session:
            values["new_session"] = True
        if args.web_search is not None:
            values["web_search_enabled"] = args.web_search
    if args.reference:
        if len(args.reference) > 5:
            raise ValueError("最多引用 5 项内容")
        values["references"] = [reference(api, identity) for identity in args.reference]
    job = api.call("POST", "/chat", values)
    if args.stream:
        return api.follow(job["id"])
    return api.wait(job["id"]) if args.wait else job


def session_search_command(api, args):
    identity = args.session
    if args.operation == "status" or identity is None:
        state = api.call("GET", "/state")
        identity = identity or state["main_session_id"]
    if args.operation == "status":
        session = next((row for row in state["sessions"] if row["id"] == identity), None)
        if session is None:
            raise ValueError("对话不可用，请确认 ID 且对话未封存")
    else:
        session = api.call("PUT", f"/sessions/{segment(identity)}/web-search", {"enabled": args.operation == "on"})
    return {"session_id": session["id"], "web_search_enabled": bool(session["web_search_enabled"])}


def search_settings_command(api, args):
    values = {"web_search_provider": args.provider} if args.provider else {}
    if args.set_key:
        values["web_search_api_key"] = read_search_key()
    settings = api.call("PUT", "/settings", values) if values else api.call("GET", "/state")["settings"]
    return {key: settings[key] for key in ("web_search_provider", "web_search_keys_configured", "native_search_check")}


def execute(api, args):
    name = args.command
    identity = segment(args.id) if hasattr(args, "id") else None
    call = api.call
    if name in COLLECTIONS or name == "status":
        state = call("GET", "/state")
        return state if name == "status" else state[name]
    if name == "search-sessions":
        parameters = {"q": args.query, "scope": "all" if args.all else "title", "recent": str(args.recent).lower(),
                      "limit": args.limit, "offset": args.offset}
        return call("GET", "/sessions/search?" + urlencode(parameters))
    if name == "session":
        return call("GET", f"/sessions/{identity}")
    if name == "reset-main":
        identity = segment(call("GET", "/state?feed_limit=1")["main_session_id"])
        return call("POST", f"/sessions/{identity}/reset", {"confirmed": True})
    if name == "read-session":
        return call("POST", f"/sessions/{identity}/read")
    if name == "deletion-impact":
        return call("GET", f"/sessions/{identity}/deletion-impact")
    if name == "delete-session":
        return call("DELETE", f"/sessions/{identity}")
    if name in SESSION_EDITS:
        values = {"title": args.title} if name == "rename-session" else {"operation": SESSION_EDITS[name]}
        return call("PATCH", f"/sessions/{identity}", values)
    if name in CHAT_COMMANDS:
        return chat_command(api, args)
    if name == "web-search":
        return session_search_command(api, args)
    if name == "search-settings":
        return search_settings_command(api, args)
    if name == "check-native-search":
        job = call("POST", "/settings/check-native-search")
        return api.wait(job["id"]) if args.wait else job
    if name == "messages":
        return call("GET", f"/sessions/{segment(args.session)}/messages")
    if name == "schedule":
        values = fields(args, "title", "instruction", "kind", "interval_seconds", "session_id", "goal_id", "calendar_timezone")
        return call("POST", "/schedules", {**values, **schedule_time(api, args, default=3600)})
    if name in SCHEDULE_OPERATIONS:
        return call("PATCH", f"/schedules/{identity}", {"operation": SCHEDULE_OPERATIONS[name]})
    if name == "schedule-history":
        return call("GET", f"/schedules/{identity}/history")
    if name == "edit-schedule":
        values = {**fields(args, "title", "instruction", "interval_seconds"), **schedule_time(api, args)}
        return call("PATCH", f"/schedules/{identity}", values)
    if name == "job":
        if args.stream:
            return api.follow(args.id)
        return api.wait(args.id) if args.wait else call("GET", f"/jobs/{identity}")
    if name == "cancel-job":
        return call("POST", f"/jobs/{identity}/cancel")
    if name in {"approve", "reject"}:
        return call("POST", f"/actions/{identity}/decision", {"decision": name, "content_hash": args.hash})
    if name == "action-history":
        return call("GET", f"/actions/{identity}/history")
    if name == "edit-action":
        changes = fields(args, "title", "target", "body")
        if not changes:
            raise ValueError("请指定要修改的提案字段")
        return call("PUT", f"/actions/{identity}", changes)
    if name == "read-notifications":
        return call("POST", "/notifications/read")
    if name == "acknowledge":
        return call("POST", f"/notifications/{identity}/acknowledge")
    if name == "dismiss-reminder":
        return call("POST", f"/actions/{identity}/dismiss-reminder")
    if name == "evidence":
        return call("GET", f"/jobs/{identity}/evidence")
    if name == "audit":
        query = "?" + urlencode({"job_id": args.job_id}) if args.job_id else ""
        return call("GET", "/audit" + query)
    if name == "settings":
        return settings_command(api, args, read_secret)
    if name in RECORD_COMMANDS:
        return records_command(api, args)
    if name == "feed":
        return feed_command(api, args)
    if name == "check-items":
        return check_items_command(api, args)
    raise ValueError("未知命令")


def main():
    parser = build_parser()
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    args = parser.parse_args(["tui"] if len(sys.argv) == 1 and interactive else None)
    if args.command == "tui":
        if not interactive:
            parser.error("全屏界面需要交互式终端")
        from .tui import main as run_terminal
        run_terminal()
        return
    try:
        result = execute(CLIAdapter(), args)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        if isinstance(result, dict) and result.get("status") in TERMINAL - {"succeeded"}:
            sys.exit(1)
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
