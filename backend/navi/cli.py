import argparse
import getpass
import json
import sys
import time
import warnings
from urllib.parse import quote

import httpx

from .web_search import SEARCH_PROVIDERS


class CLIAdapter:
    def __init__(self, url="http://127.0.0.1:8766"):
        self.url = url

    def call(self, method, path, values=None):
        try:
            response = httpx.request(method, self.url + "/api" + path, json=values if values is not None else {}, timeout=15, trust_env=False)
            if response.is_error:
                raise ValueError(response.json().get("detail", "请求失败"))
            return response.json()
        except httpx.HTTPError as error:
            raise ValueError("无法连接 Navi，请先双击 start.command") from error

    def wait(self, identity):
        while True:
            job = self.call("GET", f"/jobs/{identity}")
            if job["status"] in {"succeeded", "failed", "timed_out", "cancelled"}:
                return job
            time.sleep(0.3)


def read_search_key():
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass("搜索服务 API 密钥：").strip()
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        raise ValueError("请输入密钥，请使用支持隐藏输入的终端") from None
    if not key:
        raise ValueError("搜索服务 API 密钥不能为空")
    return key


def main():
    parser = argparse.ArgumentParser(description="Navi CLI，与 Web 共享本机 Gateway")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tui", help="打开全屏终端界面")
    sub.add_parser("status")
    sub.add_parser("sessions")
    p = sub.add_parser("campus", help="查看 Blackboard 校园看板")
    p.add_argument("--refresh", action="store_true", help="跳过缓存，重新读取并整理")
    p = sub.add_parser("search-sessions")
    p.add_argument("title")
    for name in ("rename-session", "archive-session", "unarchive-session", "delete-session", "deletion-impact"):
        p = sub.add_parser(name)
        p.add_argument("id")
        if name == "rename-session":
            p.add_argument("title")
    for name in ("chat", "ideas", "draft", "compact"):
        p = sub.add_parser(name)
        p.add_argument("text", nargs="?", default="压缩当前会话" if name == "compact" else "根据我的目标和关注事项给出建议" if name == "ideas" else "")
        target = p.add_mutually_exclusive_group()
        target.add_argument("--session", help="指定会话 ID，默认使用主对话")
        if name == "chat":
            target.add_argument("--new-session", action="store_true", help="新建副对话并发送消息")
            p.add_argument("--web-search", action=argparse.BooleanOptionalAction, help="开启或关闭此会话的联网搜索，并保存开关")
        p.add_argument("--wait", action="store_true")
    p = sub.add_parser("web-search", help="查看或设置单个对话的联网搜索开关")
    p.add_argument("operation", choices=["status", "on", "off"], nargs="?", default="status")
    p.add_argument("--session", help="指定未封存的会话 ID，默认使用主对话")
    p = sub.add_parser("search-settings", help="查看或修改与 Web 共用的第三方搜索服务")
    p.add_argument("--provider", choices=SEARCH_PROVIDERS, help="选择第三方搜索服务")
    p.add_argument("--set-key", action="store_true", help="以隐藏输入保存所选服务的密钥")
    p = sub.add_parser("check-native-search", help="测试当前模型连接的 DeepSeek 原生搜索")
    p.add_argument("--wait", action="store_true")
    p = sub.add_parser("messages")
    p.add_argument("session")
    sub.add_parser("schedules")
    p = sub.add_parser("schedule")
    p.add_argument("title")
    p.add_argument("instruction")
    p.add_argument("--kind", choices=["once", "recurring", "heartbeat"], default="recurring")
    p.add_argument("--interval", type=float, default=3600)
    p.add_argument("--in", dest="delay", type=float, default=3600)
    p.add_argument("--session")
    for name in ("pause", "resume", "cancel-schedule"):
        p = sub.add_parser(name)
        p.add_argument("id")
    p = sub.add_parser("edit-schedule")
    p.add_argument("id")
    p.add_argument("--title")
    p.add_argument("--instruction")
    p.add_argument("--interval", type=float)
    p = sub.add_parser("job")
    p.add_argument("id")
    p.add_argument("--wait", action="store_true")
    p = sub.add_parser("cancel-job")
    p.add_argument("id")
    sub.add_parser("actions")
    for name in ("approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("id")
        p.add_argument("--hash", required=True, help="审批列表中具体内容的 content_hash")
    p = sub.add_parser("evidence")
    p.add_argument("id")
    sub.add_parser("audit")
    if len(sys.argv) == 1 and sys.stdin.isatty() and sys.stdout.isatty():
        args = parser.parse_args(["tui"])
    else:
        args = parser.parse_args()
    if args.command == "tui":
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            parser.error("全屏界面需要交互式终端")
        from .tui import main as run_terminal
        run_terminal()
        return
    adapter = CLIAdapter()
    try:
        name = args.command
        if name in {"status", "sessions", "schedules", "actions"}:
            result = adapter.call("GET", "/state")
            result = result if name == "status" else result[name]
        elif name == "search-sessions":
            result = adapter.call("GET", "/sessions/search?q=" + quote(args.title))
        elif name == "deletion-impact":
            result = adapter.call("GET", f"/sessions/{args.id}/deletion-impact")
        elif name == "delete-session":
            result = adapter.call("DELETE", f"/sessions/{args.id}")
        elif name in {"rename-session", "archive-session", "unarchive-session"}:
            values = {"title": args.title} if name == "rename-session" else {"operation": "archive" if name == "archive-session" else "unarchive"}
            result = adapter.call("PATCH", f"/sessions/{args.id}", values)
        elif name in {"chat", "ideas", "draft", "compact"}:
            values = {"text": args.text, "session_id": args.session, "kind": name}
            if name == "chat":
                if args.new_session:
                    values["new_session"] = True
                if args.web_search is not None:
                    values["web_search_enabled"] = args.web_search
            result = adapter.call("POST", "/chat", values)
            if args.wait:
                result = adapter.wait(result["id"])
        elif name == "web-search":
            identity = args.session
            if args.operation == "status" or identity is None:
                state = adapter.call("GET", "/state")
                identity = identity or state["main_session_id"]
            if args.operation == "status":
                session = next((row for row in state["sessions"] if row["id"] == identity), None)
                if session is None:
                    raise ValueError("对话不可用，请确认 ID 且对话未封存")
            else:
                session = adapter.call("PUT", f"/sessions/{quote(identity, safe='')}/web-search", {"enabled": args.operation == "on"})
            result = {"session_id": session["id"], "web_search_enabled": bool(session["web_search_enabled"])}
        elif name == "search-settings":
            values = {}
            if args.provider:
                values["web_search_provider"] = args.provider
            if args.set_key:
                values["web_search_api_key"] = read_search_key()
            settings = adapter.call("PUT", "/settings", values) if values else adapter.call("GET", "/state")["settings"]
            result = {key: settings[key] for key in ("web_search_provider", "web_search_keys_configured", "native_search_check")}
        elif name == "check-native-search":
            result = adapter.call("POST", "/settings/check-native-search")
            if args.wait:
                result = adapter.wait(result["id"])
        elif name == "messages":
            result = adapter.call("GET", f"/sessions/{args.session}/messages")
        elif name == "campus":
            result = adapter.call("GET", f"/campus/dashboard?refresh={'true' if args.refresh else 'false'}")
        elif name == "schedule":
            result = adapter.call("POST", "/schedules", {"title": args.title, "instruction": args.instruction, "kind": args.kind, "interval_seconds": args.interval, "next_due": time.time() + args.delay, "session_id": args.session})
        elif name in {"pause", "resume", "cancel-schedule"}:
            result = adapter.call("PATCH", f"/schedules/{args.id}", {"operation": "cancel" if name == "cancel-schedule" else name})
        elif name == "edit-schedule":
            result = adapter.call("PATCH", f"/schedules/{args.id}", {key: value for key, value in {"title": args.title, "instruction": args.instruction, "interval_seconds": args.interval}.items() if value is not None})
        elif name == "job":
            result = adapter.wait(args.id) if args.wait else adapter.call("GET", f"/jobs/{args.id}")
        elif name == "cancel-job":
            result = adapter.call("POST", f"/jobs/{args.id}/cancel")
        elif name in {"approve", "reject"}:
            result = adapter.call("POST", f"/actions/{args.id}/decision", {"decision": "approve" if name == "approve" else "reject", "content_hash": args.hash})
        elif name == "evidence":
            result = adapter.call("GET", f"/jobs/{args.id}/evidence")
        else:
            result = adapter.call("GET", "/audit")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if isinstance(result, dict) and result.get("status") in {"failed", "cancelled", "timed_out"}:
            sys.exit(1)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
