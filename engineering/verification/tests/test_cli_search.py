import asyncio
import json
import sys
import warnings

from fastapi.testclient import TestClient
import httpx
import pytest

from navi import cli
from navi.gateway import Gateway
from navi.web import create_app
from test_gateway import Clock, ROOT
from test_native_search import NativeModel


@pytest.fixture
def cli_web(tmp_path, monkeypatch, capsys):
    gateway = Gateway(ROOT, tmp_path, NativeModel("success"), Clock())
    web = TestClient(create_app(gateway))

    def request(method, url, *, json, timeout, trust_env):
        assert trust_env is False
        path = url.removeprefix("http://127.0.0.1:8766")
        if method == "GET" and path.startswith("/api/jobs/"):
            asyncio.run(gateway.run_one())
        return web.request(method, path, json=json)

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["navi", *args])
        cli.main()
        return json.loads(capsys.readouterr().out)

    monkeypatch.setattr(httpx, "request", request)
    return run, web, gateway


def test_cli_and_web_share_per_session_switch(cli_web):
    run, web, gateway = cli_web
    main = gateway.main_session()["id"]
    assert run("web-search") == {"session_id": main, "web_search_enabled": False}

    opened = run("chat", "查找公开资料", "--new-session", "--web-search")
    identity = opened["session_id"]
    assert identity != main
    assert json.loads(opened["config"])["web_search_enabled"] is True
    assert web.get(f"/api/sessions/{identity}").json()["web_search_enabled"]
    assert not run("web-search")["web_search_enabled"]

    assert not run("web-search", "off", "--session", identity)["web_search_enabled"]
    assert web.put(f"/api/sessions/{identity}/web-search", json={"enabled": True}).status_code == 200
    assert run("web-search", "status", "--session", identity)["web_search_enabled"]
    inherited = run("chat", "继续查找资料", "--session", identity)
    assert json.loads(inherited["config"])["web_search_enabled"] is True
    disabled = run("chat", "整理信息", "--session", identity, "--no-web-search")
    assert json.loads(disabled["config"])["web_search_enabled"] is False
    assert not web.get(f"/api/sessions/{identity}").json()["web_search_enabled"]
    assert run("web-search", "on") == {"session_id": main, "web_search_enabled": True}
    assert not run("web-search", "--session", identity)["web_search_enabled"]


def test_cli_settings_and_probe_share_web_settings(cli_web, monkeypatch):
    run, web, gateway = cli_web
    gateway.settings.update({"api_key": "fake-model-key"})
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "fake-search-key")
    result = run("search-settings", "--provider", "brave", "--set-key")
    assert result["web_search_provider"] == "brave"
    assert result["web_search_keys_configured"]["brave"]
    assert "fake-search-key" not in str(result)
    assert gateway.settings.web_search_key("brave") == "fake-search-key"
    assert run("search-settings") == result

    job = run("check-native-search", "--wait")
    assert job["status"] == "succeeded" and job["result"] == "supported"
    assert run("search-settings")["native_search_check"] == "supported"
    assert web.get("/api/state").json()["settings"]["native_search_check"] == "supported"


def test_cli_refuses_search_key_input_that_would_echo(monkeypatch):
    def echo_warning(prompt):
        warnings.warn("无法关闭回显", cli.getpass.GetPassWarning)
        pytest.fail("密钥输入不应继续")

    monkeypatch.setattr(cli.getpass, "getpass", echo_warning)
    with pytest.raises(ValueError, match="支持隐藏输入"):
        cli.read_search_key()


def test_cli_campus_dashboard_uses_shared_api(cli_web, monkeypatch):
    run, _, gateway = cli_web

    async def dashboard(refresh=False):
        assert refresh is True
        return {
            "brief": "校园总览",
            "summary": {"courses": 1, "messages": 1, "deadlines": 1},
            "recent_messages": [{"id": "m1", "title": "近期消息"}],
            "deadlines": [{"id": "d1", "title": "截止事项"}],
        }

    monkeypatch.setattr(gateway, "campus_dashboard", dashboard)
    result = run("campus", "--refresh")
    assert result["brief"] == "校园总览"
    assert result["deadlines"][0]["title"] == "截止事项"
