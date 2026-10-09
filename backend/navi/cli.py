import argparse
import json
import sys
import time
from urllib.parse import quote

import httpx


class CLIAdapter:
    def __init__(self, url="http://127.0.0.1:8766"):
        self.url = url

    def call(self, method, path, values=None):
        try:
            response = httpx.request(method, self.url + "/api" + path, json=values if values is not None else {}, timeout=15)
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


def main():
    parser = argparse.ArgumentParser(description="Navi CLI，与 Web 共享本机 Gateway")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("sessions")
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
        p.add_argument("--session")
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
    args, adapter = parser.parse_args(), CLIAdapter()
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
            result = adapter.call("POST", "/chat", {"text": args.text, "session_id": args.session, "kind": name})
            if args.wait:
                result = adapter.wait(result["id"])
        elif name == "messages":
            result = adapter.call("GET", f"/sessions/{args.session}/messages")
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
