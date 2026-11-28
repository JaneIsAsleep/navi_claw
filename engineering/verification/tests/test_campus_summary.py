import json
from pathlib import Path

import pytest

from navi.gateway import CAMPUS_DASHBOARD_CACHE_SECONDS, Gateway
from navi.model import Reply


ROOT = Path(__file__).resolve().parents[3]


class DashboardModel:
    def __init__(self):
        self.calls = 0

    async def chat(self, **request):
        self.calls += 1
        payload = json.loads(request["messages"][-1]["content"])
        items = {item["id"]: f"模型摘要：{item['title']}" for item in payload["items"]}
        return Reply([{"type": "text", "text": json.dumps({
            "brief": "模型生成的学习总览",
            "items": items,
        }, ensure_ascii=False)}], 100, 40)


@pytest.mark.asyncio
async def test_campus_dashboard_uses_model_summary_and_cache(tmp_path, monkeypatch):
    model = DashboardModel()
    gateway = Gateway(ROOT, tmp_path, model, lambda: 1780000000.0)
    dashboard = {
        "logged_in": True,
        "generated_at": 1780000000.0,
        "summary": {"courses": 1, "messages": 1, "deadlines": 1},
        "recent_messages": [{
            "id": "_1", "title": "Project 5", "course": "Compilers", "published_at": 1779990000.0,
            "published_label": "11月22日 23:47", "summary": "原始摘要", "kind": "assignment",
            "url": "https://bb.sustech.edu.cn/example", "attachments": [],
        }],
        "deadlines": [{
            "id": "_1:due", "title": "Project 5", "course": "Compilers", "kind": "assignment",
            "due_at": 1780100000.0, "due_label": "12月13日 23:59",
            "url": "https://bb.sustech.edu.cn/example", "source": "Blackboard 公告", "summary": "原始摘要",
        }],
    }

    async def raw_dashboard(refresh=False):
        return dashboard

    monkeypatch.setattr(gateway.campus, "dashboard", raw_dashboard)
    first = await gateway.campus_dashboard(True)
    assert first["brief"] == "模型生成的学习总览"
    assert first["recent_messages"][0]["summary"] == "模型摘要：Project 5"
    assert first["model_generated"] is True
    second = await gateway.campus_dashboard(False)
    assert second["cached"] is True
    assert model.calls == 1


@pytest.mark.asyncio
async def test_stale_campus_dashboard_returns_cache_and_schedules_refresh(tmp_path, monkeypatch):
    clock = lambda: 1780000000.0
    gateway = Gateway(ROOT, tmp_path, DashboardModel(), clock)
    gateway.campus_dashboard_cache = {
        "created": clock() - CAMPUS_DASHBOARD_CACHE_SECONDS - 1,
        "value": {
            "brief": "旧总览",
            "summary": {"courses": 1, "messages": 0, "deadlines": 0},
            "recent_messages": [],
            "deadlines": [],
        },
    }
    monkeypatch.setattr(gateway, "_schedule_campus_dashboard_refresh", lambda: True)
    result = await gateway.campus_dashboard(False)
    assert result["cached"] is True
    assert result["stale"] is True
    assert result["refresh_scheduled"] is True
    assert result["brief"] == "旧总览"
