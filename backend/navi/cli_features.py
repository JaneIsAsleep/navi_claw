"""Resource commands for the shell CLI. All state changes go through the Gateway."""

import argparse
import json
from pathlib import Path
import sys
from urllib.parse import quote, urlencode
from uuid import uuid4

from .config import DEFAULTS
from .gateway import CATEGORIES
from .web_search import SEARCH_PROVIDERS


def segment(identity):
    return quote(identity, safe="")


def fields(args, *names):
    return {name: getattr(args, name) for name in names if getattr(args, name) is not None}


def confirm(parser, description):
    parser.add_argument("--yes", action="store_true", required=True, help=description)


def wait_if_requested(api, args, receipt):
    identity = receipt.get("job_id") or receipt.get("id")
    if args.wait and identity:
        job = api.wait(identity)
        return {**receipt, **job}
    return receipt


def settings_command(api, args, read_secret):
    if args.operation == "show":
        return api.call("GET", "/state?feed_limit=1")["settings"]
    if args.operation == "usage":
        return api.call("GET", "/state?feed_limit=1")["usage"]
    if args.operation == "check":
        return wait_if_requested(api, args, api.call("POST", "/settings/check"))
    if args.operation == "delete-endpoint":
        return api.call("DELETE", "/settings/endpoints", {"endpoint_id": args.id})
    values = fields(args, *SETTING_FIELDS, "endpoint_id")
    if args.messages_url or args.completions_url:
        if not args.messages_url or not args.completions_url or args.endpoint_id is not None:
            raise ValueError("添加 Endpoint 需要两种完整 URL，且不可同时指定 --endpoint")
        values["endpoint_urls"] = {"messages": args.messages_url, "chat_completions": args.completions_url}
    if args.set_key:
        values["api_key"] = read_secret("DeepSeek API 密钥")
    if args.set_search_key:
        values["web_search_api_key"] = read_secret("搜索服务 API 密钥")
    if not values:
        raise ValueError("请指定要修改的设置，使用 settings set --help 查看选项")
    return api.call("PUT", "/settings", values)


SETTING_CHOICES = {
    "protocol": ("messages", "chat_completions"),
    "notification_policy": ("daily", "strict"),
    "workflow": ("priority_briefing", "deadline_briefing"),
    "web_search_provider": tuple(SEARCH_PROVIDERS),
}
SETTING_FIELDS = tuple(key for key in DEFAULTS if key not in {"endpoint_id", "web_search_enabled"})


def add_settings(sub):
    operations = sub.add_parser("settings", help="模型、通知策略、工作流、搜索和用量").add_subparsers(dest="operation", required=True)
    operations.add_parser("show", help="查看配置和密钥状态")
    operations.add_parser("usage", help="查看当日请求和费用估算")
    operations.add_parser("check", help="测试实际模型连接").add_argument("--wait", action="store_true")
    remove = operations.add_parser("delete-endpoint", help="删除自定义 Endpoint 及对应密钥")
    remove.add_argument("id")
    confirm(remove, "确认删除 Endpoint 和密钥")
    update = operations.add_parser("set", help="修改指定设置")
    for key in SETTING_FIELDS:
        choices = SETTING_CHOICES.get(key)
        decimal = {"request_timeout", "job_timeout", "input_price_per_million", "output_price_per_million"}
        kind = float if key in decimal else type(DEFAULTS[key])
        update.add_argument("--" + key.replace("_", "-"), type=kind, choices=choices)
    update.add_argument("--endpoint", dest="endpoint_id", type=lambda value: "" if value == "official" else value,
                        help="使用自定义配置 ID，或填 official 切回默认地址")
    update.add_argument("--messages-url", help="新增自定义 Endpoint 时的 Messages 完整 URL")
    update.add_argument("--completions-url", help="新增自定义 Endpoint 时的 Chat Completions 完整 URL")
    update.add_argument("--set-key", action="store_true", help="隐藏输入模型密钥")
    update.add_argument("--set-search-key", action="store_true", help="隐藏输入当前搜索服务的密钥")


def record_detail(api, identity, kind=None):
    row = api.call("GET", f"/records/{segment(identity)}")
    if kind and row["kind"] != kind:
        raise ValueError("条目类型与此命令不符")
    return row


def reference(api, identity):
    row = record_detail(api, identity)
    payload = row["payload"]
    data = {"id": row["id"], "kind": row["kind"], "title": payload["title"]}
    if row["kind"] == "feed":
        return data
    data["body"] = payload.get("body") or payload.get("next_step") or ""
    if payload.get("source_url"):
        data["source_url"] = payload["source_url"]
    return data


def records_command(api, args):
    kind, operation = args.command, args.operation
    if operation == "list":
        state = api.call("GET", "/state?feed_limit=1")
        return [row for row in state["records"] if row["kind"] == kind]
    if operation == "add":
        return api.call("POST", f"/records/{kind}", fields(args, *record_fields(kind)))
    if operation == "start":
        route = "/goals/start" if kind == "goal" else f"/ideas/{segment(args.id)}/start"
        values = {"request_id": args.request_id or uuid4().hex}
        if kind == "goal":
            values["category"] = args.category
        return wait_if_requested(api, args, api.call("POST", route, values))
    if operation == "choose":
        return wait_if_requested(api, args, api.call("POST", f"/sessions/{segment(args.session)}/goal-choices", {
            "card_id": args.card_id, "choice_id": args.choice_id, "setup_version": args.version,
        }))
    if operation == "download":
        data = api.download(f"/artifacts/{segment(args.id)}/download")
        if args.output == "-":
            sys.stdout.buffer.write(data)
            return None
        try:
            with Path(args.output).open("xb") as output:
                output.write(data)
        except FileExistsError:
            raise ValueError("输出文件已存在，请选择新路径") from None
        return {"path": args.output, "bytes": len(data)}
    row = record_detail(api, args.id, kind)
    path = f"/records/{segment(args.id)}"
    if operation == "show":
        return row
    if operation == "delete":
        return api.call("DELETE", path)
    if operation in {"complete", "reactivate"}:
        return api.call("POST", f"/goals/{segment(args.id)}/state", {"operation": operation})
    if operation == "pause" or operation == "resume":
        if row["payload"].get("state", "active") not in {"active", "paused"}:
            raise ValueError("已结束的目标不能暂停或继续，请使用 goal reactivate 恢复已完成的目标")
        changes = {"state": "paused" if operation == "pause" else "active"}
    elif operation == "enable" or operation == "disable":
        changes = {"enabled": operation == "enable"}
    else:
        changes = fields(args, *record_fields(kind))
        if not changes:
            raise ValueError("请指定要修改的字段")
    return api.call("PUT", path, {**row["payload"], **changes})


def record_fields(kind):
    return ("title", "body", "next_step") if kind == "goal" else ("title", "body")


def with_wait(parser):
    parser.add_argument("--wait", action="store_true", help="等待作业完成")


def add_records(sub):
    for kind, help_text in {"goal": "目标与目标引导", "memory": "个人记忆", "idea": "查看并开始已有构想", "artifact": "查看及下载成果"}.items():
        operations = sub.add_parser(kind, help=help_text).add_subparsers(dest="operation", required=True)
        operations.add_parser("list")
        operations.add_parser("show").add_argument("id")
        if kind in {"goal", "memory"}:
            add = operations.add_parser("add")
            add.add_argument("title")
            edit = operations.add_parser("edit")
            edit.add_argument("id")
            edit.add_argument("--title")
            for field in record_fields(kind)[1:]:
                add.add_argument("--" + field.replace("_", "-"), default="")
                edit.add_argument("--" + field.replace("_", "-"))
            delete = operations.add_parser("delete")
            delete.add_argument("id")
            confirm(delete, "确认移除目标" if kind == "goal" else "确认永久删除记忆")
            extra = ("pause", "resume", "complete", "reactivate") if kind == "goal" else ("enable", "disable")
            for name in extra:
                operations.add_parser(name).add_argument("id")
        if kind in {"goal", "idea"}:
            start = operations.add_parser("start")
            if kind == "goal":
                start.add_argument("category", choices=CATEGORIES)
                choose = operations.add_parser("choose", help="用 messages 中的 interaction 字段选择引导选项")
                choose.add_argument("session")
                choose.add_argument("card_id")
                choose.add_argument("choice_id")
                choose.add_argument("--version", type=int, required=True, help="当前卡片的 setup_version")
                with_wait(choose)
            else:
                start.add_argument("id")
            start.add_argument("--request-id", help="重试时复用相同 ID，避免重复创建对话")
            with_wait(start)
        if kind == "artifact":
            download = operations.add_parser("download")
            download.add_argument("id")
            download.add_argument("-o", "--output", required=True, help="新文件路径，或用 - 输出原始内容")


def feed_command(api, args):
    operation = args.operation
    if operation == "list":
        return api.call("GET", "/feed/items?" + urlencode(fields(args, "limit", "before")))
    if operation == "show":
        return record_detail(api, args.id, "feed")
    if operation == "sources":
        state = api.call("GET", "/state?feed_limit=1")
        followed = {row["payload"]["source_id"]: row for row in state["records"] if row["kind"] == "source"}
        return [{**row, "subscription": followed.get(row["id"])} for row in state["feed_catalog"]]
    if operation == "follow":
        return api.call("POST", "/feed-sources", {"source_id": args.id})
    if operation == "preview":
        return api.call("POST", "/feed-sources/preview", {"url": args.url})
    if operation == "add-source":
        return api.call("POST", "/feed-sources", {"url": args.url, "title": args.title})
    if operation == "toggle-source":
        return api.call("POST", f"/feed-sources/{segment(args.id)}/toggle")
    if operation == "brief":
        if args.text is None:
            return {"feed_brief": api.call("GET", "/state?feed_limit=1")["feed_brief"]}
        settings = api.call("PUT", "/settings", {"feed_brief": args.text})
        return {"feed_brief": settings["feed_brief"]}
    if operation == "refresh":
        return api.call("POST", "/feed-refresh", timeout=120)
    return wait_if_requested(api, args, api.call("POST", "/feed-generate"))


def add_feed(sub):
    operations = sub.add_parser("feed", help="动态、关注说明和 RSS/Atom 来源").add_subparsers(dest="operation", required=True)
    page = operations.add_parser("list")
    page.add_argument("--limit", type=int, default=20)
    page.add_argument("--before", help="上一页的 next_cursor")
    operations.add_parser("show").add_argument("id")
    operations.add_parser("sources", help="列出订阅与可用预设来源")
    operations.add_parser("follow", help="关注预设来源").add_argument("id")
    operations.add_parser("preview", help="预览公开 RSS/Atom 地址").add_argument("url")
    add = operations.add_parser("add-source", help="添加已确认的公开订阅")
    add.add_argument("url")
    add.add_argument("--title", default="")
    operations.add_parser("toggle-source", help="切换订阅的启用状态").add_argument("id", help="订阅记录 ID，见 feed sources")
    operations.add_parser("brief", help="查看或修改关注说明").add_argument("text", nargs="?")
    operations.add_parser("refresh", help="立即拉取已订阅来源")
    with_wait(operations.add_parser("generate", help="生成新的动态简报"))


def check_items_command(api, args):
    operation = args.operation
    if operation == "replace":
        if args.file == "-":
            data = json.load(sys.stdin)
        else:
            with Path(args.file).open(encoding="utf-8") as source:
                data = json.load(source)
        return api.call("PUT", "/check-items", data)
    current = api.call("GET", "/state?feed_limit=1")["check_items"]
    if operation == "list":
        return current
    if operation == "add":
        data = {"id": uuid4().hex, **fields(args, "title", "body", "priority", "due_at", "done", "source_url")}
        return api.call("PUT", "/check-items", [*current, data])
    if not any(row["id"] == args.id for row in current):
        raise ValueError("关注事项不存在")
    if operation == "delete":
        return api.call("PUT", "/check-items", [row for row in current if row["id"] != args.id])
    changes = fields(args, "title", "body", "priority", "due_at", "done", "source_url")
    if args.clear_due:
        changes["due_at"] = None
    if not changes:
        raise ValueError("请指定要修改的字段")
    return api.call("PUT", "/check-items", [{**row, **changes} if row["id"] == args.id else row for row in current])


def add_check_items(sub):
    operations = sub.add_parser("check-items", help="管理简报和 Heartbeat 关注事项").add_subparsers(dest="operation", required=True)
    operations.add_parser("list")
    add = operations.add_parser("add")
    add.add_argument("title")
    edit = operations.add_parser("edit")
    edit.add_argument("id")
    edit.add_argument("--title")
    for parser in (add, edit):
        parser.add_argument("--body")
        parser.add_argument("--priority", type=float)
        parser.add_argument("--source-url")
        parser.add_argument("--done", action=argparse.BooleanOptionalAction)
        due = parser.add_mutually_exclusive_group()
        due.add_argument("--due-at", type=float, help="截止时间，Unix 秒")
        if parser is edit:
            due.add_argument("--clear-due", action="store_true")
    delete = operations.add_parser("delete")
    delete.add_argument("id")
    confirm(delete, "确认删除关注事项")
    replace = operations.add_parser("replace", help="用完整 JSON 数组替换关注事项")
    replace.add_argument("file", help="JSON 文件路径，或用 - 从标准输入读取")
    confirm(replace, "确认替换全部关注事项")
