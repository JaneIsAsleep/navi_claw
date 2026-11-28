"""The terminal interface is a client of the same local API as the web app."""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
import json
from urllib.parse import urlencode
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, Label, OptionList, RichLog, Select, Static, TextArea
from textual.widgets.option_list import Option

from .web_search import SEARCH_PROVIDERS

PAGES = {
    "chat": ("对话", "打开会话与消息"),
    "campus": ("校园", "查看 Blackboard 总览与 DDL"),
    "goals": ("目标", "查看目标与下一步"),
    "reminders": ("提醒", "查看提醒排程与投递"),
    "feed": ("动态", "浏览关注内容与来源"),
    "ideas": ("构想", "查看建议并在新对话中开始"),
    "artifacts": ("成果", "查看草稿与已导出文件"),
    "memory": ("记忆", "查看个人记忆"),
    "approvals": ("审批", "查看提案原文、批准或拒绝"),
    "activity": ("活动", "查看作业状态与失败原因"),
    "settings": ("设置", "配置搜索、模型密钥与通知策略"),
}
STATUSES = {
    "pending": "等待处理", "running": "进行中", "succeeded": "已完成", "failed": "失败",
    "timed_out": "已超时", "active": "进行中", "completed": "已完成", "paused": "已暂停",
    "cancelled": "已取消", "approved": "已批准，待执行", "rejected": "已拒绝",
    "delivered": "已投递", "invalidated": "已失效", "uncertain": "待核查",
    "supported": "已验证", "unverified": "未验证", "untested": "未测试", "unsupported": "不支持",
}
ACTIVE_JOBS = {"pending", "running"}


def text(value):
    """Render remote content literally, without terminal escape sequences."""
    return "".join(char for char in str(value or "") if char in "\n\t" or (ord(char) >= 32 and not 127 <= ord(char) <= 159))


def title(value):
    return " ".join(text(value).split())


def timestamp(value, timezone=None):
    if not value:
        return "暂无"
    local = datetime.fromtimestamp(value, ZoneInfo(timezone)) if timezone else datetime.fromtimestamp(value).astimezone()
    return local.strftime("%Y/%m/%d %H:%M %Z")


def status(value):
    return STATUSES.get(value, value or "未知")


class TerminalAPI:
    def __init__(self, client):
        self.client = client

    async def call(self, method, path, values=None):
        options = {"json": values if values is not None else {}} if method != "GET" else {}
        response = await self.client.request(method, "/api" + path, **options)
        if response.is_error:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            raise ValueError(detail or f"请求失败（HTTP {response.status_code}）")
        return response.json()

    async def events(self, identity) -> AsyncIterator[tuple[str, dict]]:
        async with self.client.stream("GET", f"/api/jobs/{identity}/events", timeout=30) as response:
            response.raise_for_status()
            event = ""
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: ") and event:
                    yield event, json.loads(line[6:])


class Decision(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "dismiss(False)", "返回")]

    def __init__(self, row, decision):
        super().__init__()
        self.row = row
        self.decision = decision

    def compose(self) -> ComposeResult:
        label = "批准执行" if self.decision == "approve" else "拒绝提案"
        with Vertical(classes="dialog"):
            yield Label(label, classes="heading")
            with VerticalScroll():
                yield Static(Text(action_details(self.row)))
            with Horizontal(classes="buttons"):
                yield Button("返回", id="back")
                yield Button(label, id="confirm", variant="warning" if self.decision == "approve" else "error")

    def on_mount(self):
        self.query_one("#back", Button).focus()

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        self.dismiss(event.button.id == "confirm")


def action_details(row):
    body = row.get("body", "")
    if row["kind"] == "item_edit":
        edit = json.loads(body)
        labels = {"title": "标题", "body": "内容", "instruction": "提醒内容", "state": "状态",
                  "next_step": "下一步", "next_due": "下次提醒"}
        lines = []
        for field, after in edit["changes"].items():
            before = edit["before"].get(field)
            if field == "next_due":
                before, after = timestamp(before, edit["timezone"]), timestamp(after, edit["timezone"])
            lines.append(f"{labels.get(field, field)}\n  原值  {before if before is not None else '空'}\n  新值  {after}")
        if edit.get("linked_reminders"):
            lines.append("同时结束关联提醒  " + "、".join(r["title"] for r in edit["linked_reminders"]))
        body = "\n\n".join(lines)
    prefix = "写入 Navi 成果目录" if row["kind"] == "export" else "执行目标"
    return text(f"{row['title']}\n版本 {row['version']}  ·  {status(row['status'])}\n{prefix}  {row['target']}"
                f"\n{row.get('source_title') or '无关联对话'}\n\n{body}\n\n{row.get('delivery_error') or row.get('basis') or ''}")


class NaviTerminal(App):
    TITLE = "Navi"
    CSS_PATH = "tui.tcss"
    ENABLE_COMMAND_PALETTE = True
    BINDINGS = [
        Binding("f1", "command_palette", "命令"), Binding("f2", "sidebar", "侧栏"),
        Binding("ctrl+n", "new_chat", "新对话", priority=True),
        Binding("ctrl+enter,f8", "send", "发送", priority=True),
        Binding("f5", "refresh_data", "刷新"), Binding("ctrl+q", "quit", "退出", priority=True),
    ]

    def __init__(self, *, transport=None):
        super().__init__()
        self.client = httpx.AsyncClient(base_url="http://127.0.0.1:8766", timeout=8, transport=transport, trust_env=False)
        self.api = TerminalAPI(self.client)
        self.state = {}
        self.online = False
        self.page = "chat"
        self.session = None
        self.new_search = False
        self.drafts = {}
        self.messages = []
        self.session_results = None
        self.opened = {}
        self.archived = False
        self.selected = None
        self.stream_id = None
        self.stream_text = ""
        self.stream_note = ""
        self.watch_job = None
        self.operation_pending = False
        self.reference = None
        self.feed_extra = []
        self.feed = []
        self.feed_cursor = None
        self.feed_paged = False
        self.campus_dashboard = {}
        self.session_options = None
        self.item_options = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="masthead"):
            yield Static("◆  NAVI", id="wordmark")
            yield Static("正在连接本机 Gateway", id="connection", markup=False)
            yield Static("F1 命令  ·  Ctrl+Q 退出", id="top-keys")
        with Horizontal(id="workspace"):
            with Vertical(id="rail"):
                yield Label("工作台", classes="eyebrow")
                yield OptionList(*(Option(Text(label), id=key) for key, (label, _) in PAGES.items()), id="navigation")
                yield Label("对话历史", classes="eyebrow")
                yield Input(placeholder="搜索标题或正文", id="search-sessions", max_length=100)
                yield OptionList(id="sessions")
                yield Button("＋ 新对话", id="new-chat")
            with Vertical(id="main"):
                yield Static("对话", id="page-title", markup=False)
                with Vertical(id="chat"):
                    yield RichLog(id="transcript", wrap=True, markup=False)
                    yield Static("", id="stream", markup=False)
                    yield Static("", id="chat-note", markup=False)
                    yield OptionList(id="goal-choices")
                    yield TextArea(id="composer", placeholder="和 Navi 说点什么", soft_wrap=True)
                    with Horizontal(classes="buttons", id="chat-tools"):
                        yield Button("发送", id="send", variant="primary")
                        yield Button("联网关闭", id="toggle-search")
                        yield Button("停止", id="stop-job")
                        yield Button("解封", id="unarchive")
                        yield Button("清除引用", id="clear-reference")
                with Vertical(id="library"):
                    yield OptionList(id="items")
                    with VerticalScroll(id="detail-scroll"):
                        yield Static("", id="detail", markup=False)
                    with Horizontal(classes="buttons"):
                        yield Button("批准", id="approve", variant="warning")
                        yield Button("拒绝", id="reject", variant="error")
                        yield Button("带引用讨论", id="discuss")
                        yield Button("开始构想", id="start-idea", variant="primary")
                        yield Button("收到", id="acknowledge")
                        yield Button("加载更多", id="more-feed")
                        yield Button("原对话", id="source-session")
                with VerticalScroll(id="settings"):
                    yield Static("", id="settings-summary", markup=False)
                    yield Label("第三方搜索服务", classes="eyebrow")
                    yield Select([(info[0], key) for key, info in SEARCH_PROVIDERS.items()], id="provider", allow_blank=False)
                    yield Input(placeholder="搜索密钥，留空保留现有值", password=True, id="search-key", max_length=256)
                    yield Label("本机模型连接的密钥", classes="eyebrow")
                    yield Input(placeholder="DeepSeek API 密钥，留空保留现有值", password=True, id="model-key")
                    yield Label("提醒审批策略", classes="eyebrow")
                    yield Select([("日常自动投递", "daily"), ("严格逐条审批", "strict")], id="policy", allow_blank=False)
                    with Horizontal(classes="buttons"):
                        yield Button("保存设置", id="save-settings", variant="primary")
                        yield Button("测试原生搜索", id="check-search")
        yield Footer()

    def on_mount(self):
        self.sync_layout()
        self.render_transcript()
        self.query_one("#composer", TextArea).focus()
        self.set_interval(5, self.action_refresh_data)
        self.action_refresh_data()

    async def on_unmount(self):
        await self.client.aclose()

    def get_system_commands(self, screen):
        for key, (label, description) in PAGES.items():
            yield SystemCommand(label, description, lambda key=key: self.show_page(key))
        yield SystemCommand("新对话", "开始一个独立副对话", self.action_new_chat)
        yield SystemCommand("刷新", "重新读取本机 Gateway 的状态", self.action_refresh_data)
        yield SystemCommand("会话搜索", "按标题和正文检索，包含封存对话", self.focus_search)
        yield SystemCommand("显示或隐藏侧栏", "在窄窗口腾出对话空间", self.action_sidebar)

    def on_resize(self, event):
        self.default_screen.set_class(event.size.width < 85, "compact")
        self.default_screen.set_class(event.size.width < 64 or event.size.height < 26, "tiny")
        self.call_after_refresh(self.render_transcript)

    def focus_search(self):
        self.default_screen.remove_class("rail-hidden")
        self.default_screen.add_class("rail-open")
        self.query_one("#search-sessions", Input).focus()

    def action_sidebar(self):
        screen = self.default_screen
        if screen.has_class("tiny"):
            screen.toggle_class("rail-open")
            screen.remove_class("rail-hidden")
        else:
            screen.toggle_class("rail-hidden")
        if screen.has_class("rail-open") or not (screen.has_class("tiny") or screen.has_class("rail-hidden")):
            self.query_one("#navigation", OptionList).focus()
        elif self.page == "chat":
            self.query_one("#composer", TextArea).focus()

    def show_page(self, page):
        if self.page != page:
            self.selected = None
            self.item_options = None
        self.page = page
        self.default_screen.remove_class("rail-open")
        self.sync_layout()
        if page == "chat":
            self.query_one("#composer", TextArea).focus()
        elif page != "settings":
            self.query_one("#items", OptionList).focus()
        if page in {"feed", "campus"}:
            self.action_refresh_data()

    def sync_layout(self):
        self.query_one("#chat").display = self.page == "chat"
        self.query_one("#library").display = self.page not in {"chat", "settings"}
        self.query_one("#settings").display = self.page == "settings"
        if self.page == "chat":
            self.render_chat_controls()
        else:
            self.query_one("#page-title", Static).update(PAGES[self.page][0])
            if self.page == "settings":
                self.render_settings(reset=True)
            else:
                self.render_library()

    def failure(self, error):
        message = "无法连接本机 Gateway，请检查 ./status.command 或运行 ./start.command --no-browser" if isinstance(error, httpx.RequestError) else text(error)
        self.notify(message, severity="error", timeout=6)

    @work(group="refresh", exclusive=True, exit_on_error=False)
    async def action_refresh_data(self):
        try:
            state = await self.api.call("GET", "/state?feed_limit=20")
            self.online = True
            self.state = state
            if self.page == "feed":
                feed = await self.api.call("GET", "/feed/items")
                self.feed = feed["items"]
                if not self.feed_paged:
                    self.feed_cursor = feed["next_cursor"]
            elif self.page == "campus":
                self.campus_dashboard = await self.api.call("GET", "/campus/dashboard?refresh=false")
            if self.session is None:
                self.session = state["main_session_id"]
            self.render_sidebar()
            self.render_connection()
            if self.page == "chat":
                try:
                    if self.session and not any(row["id"] == self.session for row in state["sessions"]):
                        identity = self.session
                        row = await self.api.call("GET", f"/sessions/{identity}")
                        if self.session == identity:
                            self.opened = row
                            self.archived = row["state"] == "archived"
                    else:
                        self.archived = False
                    await self.load_messages(self.session)
                except ValueError as error:
                    self.failure(error)
                    self.switch_session(state["main_session_id"])
                self.render_chat_controls()
                job = self.active_chat_job()
                if job and self.stream_id != job["id"]:
                    self.attach_stream(job["id"], self.session)
            elif self.page == "settings":
                self.render_settings()
            else:
                self.render_library()
        except (httpx.HTTPError, ValueError) as error:
            if self.online or not self.state:
                self.failure(error)
            self.online = False
            self.render_connection()
            if self.page == "chat":
                self.render_chat_controls()

    def render_connection(self):
        settings = self.state.get("settings", {})
        pending = sum(row["status"] == "pending" for row in self.state.get("actions", []))
        connection = f"● 本机  /  {settings.get('model', 'DeepSeek')}" if self.online else "○ Gateway 离线"
        if self.online and not settings.get("api_key_configured"):
            connection += "  ·  未配置模型密钥"
        if pending:
            connection += f"  ·  {pending} 项待审"
        self.query_one("#connection", Static).update(text(connection))

    def render_sidebar(self):
        widget = self.query_one("#sessions", OptionList)
        rows = self.state.get("sessions", []) if self.session_results is None else self.session_results
        options = []
        for row in rows:
            prefix = "› " if row["id"] == self.session else "  "
            suffix = " [封存]" if row["state"] == "archived" else f" ({row['unread']})" if row.get("unread") else ""
            options.append((row["id"], prefix + title(row["title"]) + suffix))
        if options == self.session_options:
            return
        self.session_options = options
        previous = widget.highlighted
        widget.set_options([Option(Text(label), id=key) for key, label in options] or [Option(Text("无匹配对话"), disabled=True)])
        if previous is not None and previous < len(options):
            widget.highlighted = previous

    @on(OptionList.OptionSelected, "#navigation")
    def navigate(self, event):
        self.show_page(event.option_id)

    @on(OptionList.OptionSelected, "#sessions")
    def choose_session(self, event):
        self.default_screen.remove_class("rail-open")
        self.switch_session(event.option_id)

    @on(Input.Submitted, "#search-sessions")
    @work(group="session-search", exclusive=True, exit_on_error=False)
    async def search_sessions(self, event):
        query = event.value.strip()
        try:
            self.session_results = await self.api.call("GET", "/sessions/search?" + urlencode({"q": query, "scope": "all", "limit": 50})) if query else None
            self.render_sidebar()
            self.query_one("#sessions", OptionList).focus()
        except (httpx.HTTPError, ValueError) as error:
            self.failure(error)

    def action_new_chat(self):
        self.default_screen.remove_class("rail-open")
        self.switch_session("")

    def switch_session(self, identity):
        composer = self.query_one("#composer", TextArea)
        self.drafts[self.session] = composer.text
        self.session = identity
        self.opened = {}
        self.archived = False
        self.reference = None
        self.messages = []
        self.reset_stream()
        composer.load_text(self.drafts.get(identity, ""))
        self.render_transcript()
        self.render_sidebar()
        self.show_page("chat")
        self.open_session(identity)

    @work(group="session", exclusive=True, exit_on_error=False)
    async def open_session(self, identity):
        if not identity:
            return
        try:
            row = await self.api.call("GET", f"/sessions/{identity}")
            if self.session != identity:
                return
            self.opened = row
            self.archived = row["state"] == "archived"
            await self.load_messages(identity)
            self.render_chat_controls()
            job = self.active_chat_job()
            if job and self.stream_id != job["id"]:
                self.attach_stream(job["id"], identity)
            if not self.archived:
                await self.api.call("POST", f"/sessions/{identity}/read")
        except (httpx.HTTPError, ValueError) as error:
            self.failure(error)

    async def load_messages(self, identity):
        rows = await self.api.call("GET", f"/sessions/{identity}/messages") if identity else []
        if self.session == identity and self.messages != rows:
            self.messages = rows
            self.render_transcript()

    def render_transcript(self):
        if self.page != "chat":
            return
        log = self.query_one("#transcript", RichLog)
        log.clear()
        if not self.messages:
            log.write(Text("\n  今天想处理什么？\n", style="bold #dff8f1"))
            log.write(Text("  直接发消息，或用 F1 打开命令面板。\n  可以问 Navi 谁需要跟进、今天有哪些提醒，或请它准备一份简报。", style="#839ca4"))
            return
        for row in self.messages:
            user = row["role"] == "user"
            label = "你" if user else "提醒" if row.get("outbox_id") else "Navi"
            log.write(Text(f"\n{label}  ·  {timestamp(row['created'])}", style="bold #79dfc0" if user else "bold #9dbaff"))
            for reference in row.get("references", []):
                log.write(Text("引用  " + title(reference.get("title")), style="#839ca4"))
            log.write(Text(text(row["content"])))
        log.scroll_end(animate=False)

    def active_chat_job(self):
        return next((row for row in self.state.get("jobs", []) if row.get("session_id") == self.session
                     and row["kind"] in {"chat", "draft", "goal_setup"} and row["status"] in ACTIVE_JOBS), None)

    def render_chat_controls(self):
        row = self.session_row()
        enabled = row.get("web_search_enabled", False) if self.session else self.new_search
        self.query_one("#page-title", Static).update(text(row.get("title") or ("主对话" if self.session is None else "新对话")))
        self.query_one("#toggle-search", Button).label = "联网开启" if enabled else "联网关闭"
        self.query_one("#toggle-search", Button).disabled = not self.online or self.archived
        self.query_one("#send", Button).disabled = not self.online or self.archived or bool(self.stream_id or self.active_chat_job())
        self.query_one("#composer", TextArea).read_only = self.archived
        self.query_one("#unarchive", Button).display = self.archived
        self.query_one("#stop-job", Button).display = bool(self.stream_id or self.active_chat_job())
        self.query_one("#clear-reference", Button).display = bool(self.reference)
        note = self.stream_note
        if self.reference:
            note = "引用  " + title(self.reference["title"])
        self.query_one("#chat-note", Static).update(note or "Ctrl+Enter 或 F8 发送  ·  Enter 换行")
        self.render_goal_choices(row)

    def session_row(self):
        return next((row for row in self.state.get("sessions", []) if row["id"] == self.session), self.opened)

    def current_goal_card(self, row):
        setup = json.loads(row.get("goal_setup_json") or "{}")
        return next((message.get("interaction") for message in self.messages
                     if message["id"] == setup.get("active_card_id")), None)

    def render_goal_choices(self, row):
        widget = self.query_one("#goal-choices", OptionList)
        card = self.current_goal_card(row) if not self.archived else None
        choices = card["choices"] if card else []
        signature = tuple((choice["id"], choice["label"]) for choice in choices)
        if signature != getattr(self, "goal_options", None):
            widget.set_options([Option(Text(label), id=key) for key, label in signature])
            self.goal_options = signature
        widget.display = bool(choices)
        widget.disabled = not self.online or bool(self.active_chat_job() or self.stream_id)

    @on(OptionList.OptionSelected, "#goal-choices")
    def choose_goal(self, event):
        card = self.current_goal_card(self.session_row())
        if card:
            self.perform("goal-choice", {"card_id": card["card_id"], "choice_id": event.option_id,
                                         "setup_version": card["setup_version"]})

    def action_send(self):
        if self.page != "chat" or self.query_one("#send", Button).disabled:
            return
        composer = self.query_one("#composer", TextArea)
        message = composer.text.strip()
        if not message:
            return
        self.perform("send", message)

    def reset_stream(self):
        self.workers.cancel_group(self, "stream")
        self.stream_id = None
        self.stream_text = ""
        self.stream_note = ""
        self.query_one("#stream", Static).update("")

    def attach_stream(self, identity, session):
        self.reset_stream()
        self.stream_id = identity
        self.stream_note = "正在等待 Gateway"
        self.render_chat_controls()
        self.stream_job(identity, session)

    @work(group="stream", exclusive=True, exit_on_error=False)
    async def stream_job(self, identity, session):
        final_note = ""
        try:
            async for event, data in self.api.events(identity):
                if self.session != session or self.stream_id != identity:
                    return
                if event == "delta":
                    self.stream_text += text(data.get("text"))
                    self.query_one("#stream", Static).update(self.stream_text[-1800:])
                elif event == "status":
                    self.stream_note = text(data.get("message"))
                    self.render_chat_controls()
                elif event == "done":
                    final_note = text(data.get("error") or status(data.get("status")))
                    break
            await self.load_messages(session)
        except (httpx.HTTPError, ValueError):
            final_note = "实时连接中断，正在从 Gateway 刷新作业状态"
        finally:
            if self.stream_id == identity and self.session == session:
                self.stream_id, self.stream_text, self.stream_note = None, "", final_note
                self.query_one("#stream", Static).update("")
                if self.page == "chat":
                    self.render_chat_controls()
                self.action_refresh_data()

    def library_rows(self):
        if self.page == "feed":
            rows = [*self.feed, *self.feed_extra]
            return list({row["id"]: row for row in rows}.values())
        if self.page == "campus":
            rows = []
            for item in self.campus_dashboard.get("recent_messages", []):
                rows.append({
                    "id": f"message:{item['id']}",
                    "campus_meta": f"{item.get('course') or 'Blackboard'} · {item.get('published_label', '')}",
                    "payload": {"title": item.get("title", ""), "body": item.get("summary", "")},
                })
            for item in self.campus_dashboard.get("deadlines", []):
                rows.append({
                    "id": f"deadline:{item['id']}",
                    "campus_meta": f"{item.get('course') or item.get('source') or 'Blackboard'} · {item.get('due_label', '')}",
                    "payload": {"title": item.get("title", ""), "body": item.get("summary", "")},
                })
            return rows
        collection = {"reminders": "schedules", "approvals": "actions", "activity": "jobs"}.get(self.page)
        if collection:
            rows = self.state.get(collection, [])
            if self.page == "reminders":
                return [row for row in rows if not row.get("removed_from_reminders")]
            return rows
        kind = {"goals": "goal", "ideas": "idea", "artifacts": "artifact", "memory": "memory"}[self.page]
        return [row for row in self.state.get("records", []) if row["kind"] == kind]

    def render_library(self):
        widget = self.query_one("#items", OptionList)
        rows = self.library_rows()
        options = []
        for row in rows:
            payload = row.get("payload", row)
            label = title(payload.get("title") or row.get("input") or row["kind"])[:90]
            state = row.get("status") or payload.get("state")
            options.append((row["id"], f"{status(state)}  ·  {label}" if state else label))
        if self.selected not in {row["id"] for row in rows}:
            self.selected = rows[0]["id"] if rows else None
        if options != self.item_options:
            self.item_options = options
            widget.set_options([Option(Text(label), id=key) for key, label in options] or [Option(Text("暂无条目"), disabled=True)])
            if self.selected:
                widget.highlighted = widget.get_option_index(self.selected)
        self.render_detail()

    def selection(self):
        return next((row for row in self.library_rows() if row["id"] == self.selected), None)

    @on(OptionList.OptionHighlighted, "#items")
    @on(OptionList.OptionSelected, "#items")
    def item_choice(self, event):
        if self.page not in {"chat", "settings"} and event.option_id:
            if self.selected != event.option_id:
                self.query_one("#detail-scroll").scroll_home(animate=False)
            self.selected = event.option_id
            self.render_detail()

    def render_detail(self):
        row = self.selection()
        detail = ""
        source = None
        if row:
            payload = row.get("payload", row)
            source = row.get("source_session_id") or row.get("session_id") or (row.get("source_session") or {}).get("id")
            if self.page == "approvals":
                detail = action_details(row)
            elif self.page == "reminders":
                kind = {"once": "单次提醒", "recurring": "周期提醒", "heartbeat": "自动检查"}.get(row["kind"], row["kind"])
                detail = f"{row['title']}\n{kind}  ·  {status(row['state'])}\n下次触发  {timestamp(row['next_due']) if row['state'] == 'active' else '暂无'}\n\n{row['instruction']}"
            elif self.page == "activity":
                detail = f"{status(row['status'])}  ·  {timestamp(row['updated'])}\n\n{row['input']}\n\n{row.get('error') or row.get('result') or ''}"
            elif self.page == "campus":
                detail = f"{payload.get('title', '')}\n{row.get('campus_meta', '')}\n\n{payload.get('body') or ''}"
            else:
                detail = f"{payload.get('title', '')}\n{timestamp(row['created'])}\n\n{payload.get('body') or payload.get('abstract') or ''}"
                if payload.get("next_step"):
                    detail += f"\n\n下一步\n{payload['next_step']}"
                if payload.get("filename"):
                    detail += f"\n\n已导出  data/exports/{payload['filename']}"
                if payload.get("sources"):
                    detail += "\n\n来源\n" + "\n".join(f"{item.get('title', '')}  {item.get('url', '')}" for item in payload["sources"])
        if not detail and self.page == "campus":
            detail = text(self.campus_dashboard.get("brief", "尚未读取校园看板。"))
        self.query_one("#detail", Static).update(text(detail) or "此处会显示选中条目的内容。")
        pending = bool(row and self.page == "approvals" and row["status"] == "pending")
        acknowledge = bool(row and self.page == "approvals" and row["kind"] == "notification"
                           and row["status"] == "delivered" and row.get("acknowledged_at") is None)
        visibility = {
            "approve": pending, "reject": pending, "acknowledge": acknowledge,
            "discuss": bool(row and self.page == "feed"), "start-idea": bool(row and self.page == "ideas"),
            "more-feed": self.page == "feed" and bool(self.feed_cursor), "source-session": bool(source),
        }
        for key, visible in visibility.items():
            self.query_one(f"#{key}", Button).display = visible

    def render_settings(self, *, reset=False):
        settings = self.state.get("settings")
        if not settings:
            return
        keys = settings["web_search_keys_configured"]
        configured = "、".join(info[0] for key, info in SEARCH_PROVIDERS.items() if keys.get(key)) or "无"
        model = "密钥已配置" if settings["api_key_configured"] else "密钥未配置"
        summary = (f"{settings['model']}  ·  {settings['protocol']}  ·  {model}\n"
                   f"原生搜索  {status(settings['native_search_check'])}  ·  第三方密钥  {configured}")
        if self.watch_job:
            summary += "\n" + self.watch_job
        self.query_one("#settings-summary", Static).update(text(summary))
        provider = self.query_one("#provider", Select)
        policy = self.query_one("#policy", Select)
        if reset or not getattr(self, "settings_loaded", False):
            provider.value = settings["web_search_provider"]
            policy.value = settings["notification_policy"]
            self.settings_loaded = True

    @on(Button.Pressed)
    def pressed(self, event):
        operation = event.button.id
        if operation == "new-chat":
            self.action_new_chat()
        elif operation == "send":
            self.action_send()
        elif operation == "clear-reference":
            self.reference = None
            self.render_chat_controls()
        elif operation in {"approve", "reject"}:
            row = self.selection()
            if row and row["status"] == "pending":
                self.push_screen(Decision(row, operation), lambda confirmed: self.perform(operation, row) if confirmed else None)
        else:
            row = self.selection() if self.page not in {"chat", "settings"} else None
            self.perform(operation, row)

    @work(group="operation", exit_on_error=False)
    async def perform(self, operation, value=None):
        if self.operation_pending:
            return
        self.operation_pending = True
        try:
            await self.execute(operation, value)
        except (httpx.HTTPError, ValueError) as error:
            self.failure(error)
        finally:
            self.operation_pending = False

    async def execute(self, operation, value):
        identity = self.session
        call = self.api.call
        if operation == "send":
            body = {"text": value, "session_id": identity or None, "new_session": not identity}
            if not identity:
                body["web_search_enabled"] = self.new_search
            if self.reference:
                body["references"] = [self.reference]
            job = await call("POST", "/chat", body)
            # Preserve edits typed while the request was in flight or in another conversation.
            composer = self.query_one("#composer", TextArea)
            if self.session == identity:
                if composer.text.strip() == value:
                    composer.clear()
                    self.drafts.pop(identity, None)
                self.reference = None
                self.session = job["session_id"]
                self.new_search = False
                await self.load_messages(self.session)
                self.attach_stream(job["id"], self.session)
        elif operation == "toggle-search":
            if identity:
                row = self.session_row()
                updated = await call("PUT", f"/sessions/{identity}/web-search", {"enabled": not row.get("web_search_enabled")})
                row.update(updated)
            else:
                self.new_search = not self.new_search
            self.render_chat_controls()
        elif operation == "stop-job":
            job_id = self.stream_id or (self.active_chat_job() or {}).get("id")
            if job_id:
                await call("POST", f"/jobs/{job_id}/cancel")
        elif operation == "unarchive":
            row = await call("PATCH", f"/sessions/{identity}", {"operation": "unarchive"})
            if self.session == identity:
                self.opened = row
                self.archived = False
        elif operation == "goal-choice":
            result = await call("POST", f"/sessions/{identity}/goal-choices", value)
            if self.session == identity and result.get("job_id"):
                self.attach_stream(result["job_id"], identity)
        elif operation in {"approve", "reject"} and value:
            await call("POST", f"/actions/{value['id']}/decision", {"decision": operation, "content_hash": value["content_hash"]})
            self.notify("已提交审批决定")
        elif operation == "acknowledge" and value:
            await call("POST", f"/actions/{value['id']}/dismiss-reminder")
        elif operation == "more-feed" and self.feed_cursor:
            batch = await call("GET", "/feed/items?" + urlencode({"before": self.feed_cursor}))
            self.feed_extra.extend(batch["items"])
            self.feed_cursor = batch["next_cursor"]
            self.feed_paged = True
        elif operation == "source-session" and value:
            source = value.get("source_session_id") or value.get("session_id") or (value.get("source_session") or {}).get("id")
            if source:
                self.switch_session(source)
        elif operation == "discuss" and value:
            self.show_page("chat")
            self.reference = {"kind": "feed", "id": value["id"], "title": value["payload"]["title"]}
            self.render_chat_controls()
        elif operation == "start-idea" and value:
            started = await call("POST", f"/ideas/{value['id']}/start", {"request_id": uuid4().hex})
            self.switch_session(started["session_id"])
            self.attach_stream(started["job_id"], started["session_id"])
        elif operation == "save-settings":
            fields = {
                "web_search_provider": self.query_one("#provider", Select).value,
                "notification_policy": self.query_one("#policy", Select).value,
            }
            for widget, key in (("#search-key", "web_search_api_key"), ("#model-key", "api_key")):
                if entered := self.query_one(widget, Input).value.strip():
                    fields[key] = entered
            self.state["settings"] = await call("PUT", "/settings", fields)
            for widget in ("#search-key", "#model-key"):
                self.query_one(widget, Input).value = ""
            self.render_settings(reset=True)
            self.notify("设置已保存")
        elif operation == "check-search":
            job = await call("POST", "/settings/check-native-search")
            self.watch_probe(job["id"])
        self.action_refresh_data()

    @work(group="probe", exclusive=True, exit_on_error=False)
    async def watch_probe(self, identity):
        self.watch_job = "正在测试 DeepSeek 原生搜索"
        self.render_settings()
        try:
            async for event, data in self.api.events(identity):
                if event == "done":
                    self.watch_job = "原生搜索测试  " + text(data.get("error") or status(data.get("result") or data.get("status")))
                    break
        except (httpx.HTTPError, ValueError) as error:
            self.watch_job = "搜索测试连接中断，可在活动页查看结果"
            self.failure(error)
        self.render_settings()
        self.action_refresh_data()


def main():
    NaviTerminal().run()
