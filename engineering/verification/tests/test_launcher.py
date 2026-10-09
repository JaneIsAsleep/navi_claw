import json
import sys

import pytest

from navi import launcher


def test_health_handles_connection_reset_during_owned_shutdown(monkeypatch):
    def disconnected(*args, **kwargs):
        raise ConnectionResetError('shutdown connection reset')
    monkeypatch.setattr(launcher.urllib.request, 'urlopen', disconnected)
    assert launcher.health() is None


def test_launcher_refuses_occupied_port_without_spawning_or_killing(monkeypatch):
    class Occupied:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(sys, "argv", ["launcher", "start", "--no-browser"])
    monkeypatch.setattr(launcher, "health", lambda: None)
    monkeypatch.setattr(launcher.socket, "create_connection", lambda *args, **kwargs: Occupied())
    def forbidden(*args, **kwargs): raise AssertionError("不应创建或停止任何进程")
    monkeypatch.setattr(launcher.subprocess, "Popen", forbidden)
    with pytest.raises(SystemExit, match="端口被其他程序占用"):
        launcher.main()


def test_stop_waits_for_owned_process_after_http_disconnect(tmp_path, monkeypatch, capsys):
    state = {"name": "Navi", "project_id": launcher.PROJECT, "pid": 1234, "instance": "fixed", "ready": True}
    (tmp_path / "runtime.json").write_text(json.dumps({"pid":1234, "instance":"fixed", "token":"fixed-test-token"}))
    monkeypatch.setattr(launcher, "DATA", tmp_path)
    monkeypatch.setattr(sys, "argv", ["launcher", "stop"])
    states = iter([state])
    monkeypatch.setattr(launcher, "health", lambda: next(states, None))
    monkeypatch.setattr(launcher.time, "sleep", lambda seconds: None)
    calls = []
    def alive(pid, signal):
        assert pid == 1234 and signal == 0
        calls.append(pid)
        if len(calls) == 3:
            raise ProcessLookupError
    monkeypatch.setattr(launcher.os, "kill", alive)
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(launcher.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    launcher.main()
    assert len(calls) == 3, "HTTP 断开后仍必须等待已核对身份的旧进程真正退出"
    assert "已正常停止" in capsys.readouterr().out
