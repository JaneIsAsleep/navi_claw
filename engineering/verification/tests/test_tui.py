import asyncio

import httpx
from textual.widgets import Button, OptionList, Select, TextArea

from navi.gateway import Gateway
from navi.tui import Decision, NaviTerminal, text
from navi.web import create_app
from test_gateway import Clock, Model, ROOT


async def ready(pilot, condition):
    for _ in range(25):
        await pilot.pause(0.05)
        if condition():
            return
    assert condition()


async def test_terminal_chat_navigation_and_shared_settings(tmp_path):
    gateway = Gateway(ROOT, tmp_path, Model(), Clock())
    gateway.settings.update({"api_key": "fake-model-key"})
    app = NaviTerminal(transport=httpx.ASGITransport(app=create_app(gateway)))
    async with app.run_test(size=(105, 31)) as pilot:
        await ready(pilot, lambda: app.online and app.session == gateway.main_session()["id"])
        await pilot.press("f1")
        assert type(app.screen).__name__ == "CommandPalette"
        await pilot.press("escape", "ctrl+n")
        assert app.session == ""
        await pilot.click("#toggle-search")
        await ready(pilot, lambda: app.new_search)
        composer = app.query_one("#composer", TextArea)
        composer.load_text("请写一段测试文本。")
        composer.focus()
        await pilot.press("f8")
        await ready(pilot, lambda: bool(app.stream_id))
        session = app.session
        assert gateway.require("sessions", session)["web_search_enabled"]
        assert not gateway.main_session()["web_search_enabled"]
        await asyncio.wait_for(gateway.run_one(), timeout=3)
        await ready(pilot, lambda: any(row["role"] == "assistant" for row in app.messages))
        assert any("离线测试结果" in row["content"] for row in app.messages)
        app.show_page("settings")
        app.query_one("#provider", Select).value = "brave"
        await pilot.pause()
        assert await pilot.click("#save-settings")
        await ready(pilot, lambda: gateway.settings.current()["web_search_provider"] == "brave")
        await pilot.resize_terminal(60, 25)
        assert not app.query_one("#rail").visible or not app.query_one("#rail").display
        await pilot.press("f2")
        assert app.query_one("#rail").display


async def test_terminal_approval_requires_confirmation_and_exact_hash(tmp_path):
    gateway = Gateway(ROOT, tmp_path, Model(), Clock())
    job = gateway.chat("准备文档")
    action = gateway.propose(job["id"], "export", "简报", "brief.md", "文档全文 [bold]，按原样显示")
    app = NaviTerminal(transport=httpx.ASGITransport(app=create_app(gateway)))
    async with app.run_test(size=(100, 32)) as pilot:
        await ready(pilot, lambda: app.online)
        app.show_page("approvals")
        assert app.query_one("#items", OptionList).option_count == 1
        assert app.query_one("#approve", Button).display
        await pilot.pause()
        assert await pilot.click("#approve")
        assert isinstance(app.screen, Decision)
        await pilot.press("escape")
        assert gateway.require("actions", action["id"])["status"] == "pending"
        assert await pilot.click("#approve")
        assert await pilot.click("#confirm")
        await ready(pilot, lambda: gateway.require("actions", action["id"])["status"] == "approved")
        gateway.deliver()
        assert next((tmp_path / "exports").glob("*brief.md")).read_text() == action["body"]


def test_terminal_text_removes_control_sequences_and_preserves_chinese():
    assert text("中文\n\x1b[31m\x009\x7f") == "中文\n[31m9"
