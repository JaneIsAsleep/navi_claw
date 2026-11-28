import asyncio
import json
import time

import httpx
import pytest

from navi.campus import CampusService, CampusSessionExpired, PAGE_CACHE_SECONDS
from navi.web import create_app


CAS_PAGE = '''
<html><body>
<form id="fm1" method="post" action="login">
<input name="username" />
<input name="password" type="password" />
<input type="hidden" name="execution" value="execution-value" />
<input type="hidden" name="_eventId" value="submit" />
</form>
</body></html>
'''


def portal(title, text, link):
    return f'<html><head><title>{title}</title></head><body><p>{text}</p><a href="{link}">课程详情</a></body></html>'


@pytest.mark.asyncio
async def test_cas_login_only_persists_cookies_and_reads_same_origin_pages(tmp_path):
    calls = []

    def handler(request):
        calls.append((request.method, str(request.url)))
        if str(request.url).startswith("https://tis.sustech.edu.cn/cas?ticket="):
            return httpx.Response(302, headers={"location": "https://tis.sustech.edu.cn/", "set-cookie": "TIS=session; Path=/"})
        if str(request.url).startswith("https://tis.sustech.edu.cn/cas"):
            return httpx.Response(302, headers={"location": "https://cas.sustech.edu.cn/cas/login?service=https%3A%2F%2Ftis.sustech.edu.cn%2Fcas"})
        if str(request.url).startswith("https://cas.sustech.edu.cn/cas/login") and request.method == "GET":
            return httpx.Response(200, text=CAS_PAGE, headers={"set-cookie": "CAS_TGC=secret-tgc; Path=/; Secure; HttpOnly"})
        if str(request.url).startswith("https://cas.sustech.edu.cn/cas/login") and request.method == "POST":
            assert b"username=student" in request.content
            assert b"execution=execution-value" in request.content
            return httpx.Response(302, headers={"location": "https://tis.sustech.edu.cn/cas?ticket=ST-1"})
        if str(request.url) == "https://tis.sustech.edu.cn/":
            return httpx.Response(200, text=portal("教学平台", "本周课程", "https://tis.sustech.edu.cn/course/1"))
        if str(request.url).startswith("https://bb.sustech.edu.cn/webapps/bb-sso"):
            return httpx.Response(302, headers={"location": "https://bb.sustech.edu.cn/ultra/course", "set-cookie": "BB=session; Path=/"})
        if str(request.url).startswith("https://bb.sustech.edu.cn/"):
            return httpx.Response(200, text=portal("Blackboard", "课程通知", "https://bb.sustech.edu.cn/ultra/course/1"))
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    service = CampusService(tmp_path, transport=httpx.MockTransport(handler))
    result = await service.login("student", "not-persisted")
    assert result["logged_in"] is True
    assert service.status()["logged_in"] is True
    persisted = (tmp_path / ".campus-session").read_bytes()
    assert b"not-persisted" not in persisted
    overview = await service.overview()
    assert [page["name"] for page in overview["services"]] == ["教学管理与服务平台", "Blackboard 课程平台"]
    assert overview["services"][0]["links"][0]["url"] == "https://tis.sustech.edu.cn/course/1"
    assert overview["services"][1]["links"][0]["url"] == "https://bb.sustech.edu.cn/ultra/course/1"


@pytest.mark.asyncio
async def test_campus_page_rejects_external_urls(tmp_path):
    service = CampusService(tmp_path)
    with pytest.raises(ValueError, match="南科大校园平台"):
        await service.read_page("tis", "https://example.com/private")


@pytest.mark.asyncio
async def test_campus_overview_requires_login(tmp_path):
    service = CampusService(tmp_path)
    with pytest.raises(CampusSessionExpired):
        await service.overview()


@pytest.mark.asyncio
async def test_browser_login_starts_as_async_task(tmp_path, monkeypatch):
    service = CampusService(tmp_path)

    async def finish():
        service.browser_login_state = {"status": "succeeded", "error": ""}

    monkeypatch.setattr(service, "_browser_login", finish)
    assert service.start_browser_login()["status"] == "waiting"
    await service.browser_login_task
    assert service.browser_login_status()["status"] == "succeeded"


@pytest.mark.asyncio
async def test_browser_login_is_single_instance(tmp_path, monkeypatch):
    service = CampusService(tmp_path)
    calls = 0

    async def run_login():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        service.browser_login_state = {"status": "succeeded", "phase": "", "error": ""}

    monkeypatch.setattr(service, "_run_browser_login", run_login)
    monkeypatch.setattr(service, "_profile_logged_in", lambda: calls > 0)
    monkeypatch.setattr(service, "_read_session", lambda: [{"name": "SESSION"}])
    await asyncio.gather(service._browser_login(), service._browser_login())
    assert calls == 1


def test_campus_session_round_trips_cookies_and_storage(tmp_path):
    service = CampusService(tmp_path)
    service._write_session(
        [{"name": "SESSION", "value": "cookie-value", "domain": "tis.sustech.edu.cn", "path": "/"}],
        {"https://tis.sustech.edu.cn": {"local": {"user": "{}"}, "session": {"auth": "token"}}},
    )
    state = service._read_state()
    assert state["cookies"][0]["name"] == "SESSION"
    assert state["storage"]["https://tis.sustech.edu.cn"]["session"]["auth"] == "token"


def test_playwright_storage_state_round_trips(tmp_path):
    service = CampusService(tmp_path)
    storage_state = {
        "cookies": [{"name": "SESSION", "value": "cookie-value", "domain": "tis.sustech.edu.cn", "path": "/"}],
        "origins": [{
            "origin": "https://tis.sustech.edu.cn",
            "localStorage": [{"name": "user", "value": "student"}],
        }],
    }
    service._write_storage_state(
        storage_state,
        {"https://tis.sustech.edu.cn": {"local": {"user": "student"}, "session": {"tab": "1"}}},
    )
    assert service._playwright_storage_state() == storage_state
    assert "sessionStorage.setItem" in service._playwright_storage_script()


def test_page_cache_expires_and_respects_requested_limit(tmp_path):
    service = CampusService(tmp_path)
    url = "https://bb.sustech.edu.cn/"
    service._cache_page("bb", url, {
        "service": "bb",
        "title": "Blackboard",
        "url": url,
        "text": "x" * 100,
        "text_length": 100,
        "truncated": False,
        "links": [],
        "cached": False,
    })
    cached = service._cached_page("bb", url, 20)
    assert cached["cached"] is True
    assert cached["text"] == "x" * 20
    assert cached["truncated"] is True
    service.page_cache[("bb", url)]["cached_at"] = time.time() - PAGE_CACHE_SECONDS - 1
    assert service._cached_page("bb", url, 20) is None


@pytest.mark.asyncio
async def test_page_links_keep_only_unique_campus_pages(tmp_path):
    service = CampusService(tmp_path)

    class Page:
        async def eval_on_selector_all(self, selector, script):
            return [
                {"title": "Blackboard", "url": "https://bb.sustech.edu.cn/webapps/portal"},
                {"title": "Blackboard again", "url": "https://bb.sustech.edu.cn/webapps/portal"},
                {"title": "Logout", "url": "https://bb.sustech.edu.cn/webapps/login/?action=logout"},
                {"title": "External", "url": "https://example.com/private"},
                {"title": "Courses", "url": "https://bb.sustech.edu.cn/webapps/portal/execute/tabs/tabAction?tab_tab_group_id=_2_1"},
            ]

    links = await service._page_links(Page())
    assert [link["title"] for link in links] == ["Blackboard", "Courses"]


@pytest.mark.asyncio
async def test_browser_login_endpoint_starts_without_loop_error(tmp_path, monkeypatch):
    class Gateway:
        root = tmp_path

        def __init__(self):
            self.campus = CampusService(tmp_path)

        @staticmethod
        def clear_campus_dashboard_cache():
            return None

    gateway = Gateway()

    async def finish():
        gateway.campus.browser_login_state = {"status": "succeeded", "phase": "", "error": ""}

    monkeypatch.setattr(gateway.campus, "_browser_login", finish)
    app = create_app(gateway)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as web:
        response = await web.post("/api/campus/browser-login", json={})
    assert response.status_code == 200
    assert response.json()["status"] == "waiting"
    await gateway.campus.browser_login_task


@pytest.mark.asyncio
async def test_browser_overview_keeps_working_service_when_other_platform_expires(tmp_path, monkeypatch):
    service = CampusService(tmp_path)
    service._write_storage_state({
        "cookies": [{"name": "SESSION", "value": "cookie-value", "domain": "tis.sustech.edu.cn", "path": "/"}],
        "origins": [],
    })

    class Page:
        def __init__(self, context):
            self.context = context

        async def close(self):
            return None

    class Context:
        async def new_page(self):
            return Page(self)

        async def storage_state(self):
            return {
                "cookies": [{"name": "BB", "value": "bb-value", "domain": "bb.sustech.edu.cn", "path": "/"}],
                "origins": [],
            }

        async def close(self):
            return None

    class Browser:
        async def close(self):
            return None

    class Playwright:
        async def stop(self):
            return None

    async def read_context():
        return Browser(), Context(), Playwright()

    async def capture_storage(page, storage):
        return None

    async def browser_page(page, campus_service):
        if campus_service == "tis":
            raise CampusSessionExpired("教学管理与服务平台会话已过期，请重新登录 CAS")
        return {
            "id": "bb",
            "service": "bb",
            "name": "Blackboard 课程平台",
            "title": "Blackboard",
            "url": "https://bb.sustech.edu.cn/",
            "text": "课程通知",
            "links": [],
        }

    monkeypatch.setattr(service, "_read_context", read_context)
    monkeypatch.setattr(service, "_capture_storage", capture_storage)
    monkeypatch.setattr(service, "_browser_page", browser_page)
    result = await service._browser_overview()
    assert [item["service"] for item in result["services"]] == ["bb"]
    assert result["errors"][0]["service"] == "tis"
    assert service._read_session()
