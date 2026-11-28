import asyncio
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import time
from urllib.parse import urlparse

import httpx

from .campus_dashboard import build_dashboard


CAS_LOGIN = "https://cas.sustech.edu.cn/cas/login"
CAS_ORIGIN = "https://cas.sustech.edu.cn"
SERVICES = {
    "tis": {
        "name": "教学管理与服务平台",
        "login": "https://tis.sustech.edu.cn/cas",
        "home": "https://tis.sustech.edu.cn/",
    },
    "bb": {
        "name": "Blackboard 课程平台",
        "login": "https://bb.sustech.edu.cn/webapps/bb-sso-BBLEARN/index.jsp",
        "home": "https://bb.sustech.edu.cn/",
    },
}
ALLOWED_HOSTS = {"tis.sustech.edu.cn", "bb.sustech.edu.cn"}
SESSION_VERSION = 2
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36 Navi/0.1"
OVERVIEW_TEXT_LIMIT = 12000
PAGE_TEXT_LIMIT = 30000
TOOL_TEXT_LIMIT = 12000
PAGE_CACHE_SECONDS = 30
BB_ANNOUNCEMENTS_URL = (
    "https://bb.sustech.edu.cn/webapps/blackboard/execute/announcement"
    "?method=search&context=mybb&handle=my_announcements"
    "&tabId=_1_1&forwardUrl=index.jsp"
)
BB_CALENDAR_URL = (
    "https://bb.sustech.edu.cn/webapps/blackboard/execute/viewCalendar"
    "?calcontext=personal&view=day&filter=showAll"
    "&tabId=_1_1&forwardUrl=index.jsp"
)


class CampusError(Exception):
    pass


class CampusAuthError(CampusError):
    pass


class CampusSessionExpired(CampusError):
    pass


class _PortalParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._ignored = False
        self._title_parts = []
        self._text_parts = []
        self.links = []
        self._link = None
        self._link_text = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag in {"script", "style"}:
            self._ignored = True
        elif tag == "a":
            href = attributes.get("href", "").strip()
            if href:
                self._link = href
                self._link_text = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in {"script", "style"}:
            self._ignored = False
        elif tag == "a" and self._link:
            text = " ".join(self._link_text).strip()
            if text:
                self.links.append({"title": text[:240], "url": self._link[:2048]})
            self._link = None
            self._link_text = []

    def handle_data(self, data):
        text = " ".join(data.split())
        if not text or self._ignored:
            return
        if self._in_title:
            self._title_parts.append(text)
        else:
            self._text_parts.append(text)
        if self._link:
            self._link_text.append(text)

    def result(self):
        return {
            "title": " ".join(self._title_parts).strip()[:240],
            "text": "\n".join(self._text_parts).strip()[:PAGE_TEXT_LIMIT],
            "links": self.links[:200],
        }


class _DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data):
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _dpapi(data, protect):
    if os.name != "nt":
        return data
    source, buffer = _blob(data)
    output = _DataBlob()
    crypt = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if protect:
        ok = crypt(
            ctypes.byref(source),
            "Navi campus session",
            None,
            None,
            None,
            0,
            ctypes.byref(output),
        )
    else:
        ok = crypt(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output))
    if not ok:
        raise CampusError("无法访问本机加密凭据")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.windll.kernel32.LocalFree(output.data)
        del buffer


class CampusService:
    def __init__(self, directory: Path, transport=None):
        self.directory = Path(directory)
        self.session_path = self.directory / ".campus-session"
        self.profile_path = self.directory / "edge-profile"
        self.profile_marker = self.directory / "campus-profile.json"
        self.transport = transport
        self.browser_login_task = None
        self.browser_login_state = {"status": "idle", "phase": "", "error": ""}
        self.browser = None
        self.browser_context = None
        self.playwright = None
        self.browser_login_page = None
        self.browser_login_context = None
        self.browser_login_popup_tasks = set()
        self.browser_lock = asyncio.Lock()
        self.browser_login_lock = asyncio.Lock()
        self.page_cache = {}
        self.dashboard_cache = None

    def _client(self):
        return httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
            transport=self.transport,
        )

    @staticmethod
    def _storage_origins(storage):
        origins = []
        for origin, values in (storage or {}).items():
            local = values.get("local", {}) if isinstance(values, dict) else {}
            if local:
                origins.append({
                    "origin": origin,
                    "localStorage": [{"name": key, "value": value} for key, value in local.items()],
                })
        return origins

    def _write_storage_state(self, storage_state, storage=None):
        raw = json.dumps({
            "version": SESSION_VERSION,
            "storage_state": storage_state,
            "storage": storage or {},
        }, separators=(",", ":")).encode()
        payload = b"DPAPI\0" + _dpapi(raw, True) if os.name == "nt" else raw
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.session_path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        os.chmod(temporary, 0o600)
        temporary.replace(self.session_path)

    def _write_session(self, cookies, storage=None):
        self._write_storage_state({
            "cookies": cookies,
            "origins": self._storage_origins(storage),
        }, storage)

    def _read_state(self):
        try:
            payload = self.session_path.read_bytes()
        except OSError:
            return {"cookies": [], "origins": [], "storage": {}, "storage_state": {}}
        if payload.startswith(b"DPAPI\0"):
            payload = _dpapi(payload[6:], False)
        try:
            data = json.loads(payload)
            if isinstance(data, list):
                return {
                    "cookies": data,
                    "origins": [],
                    "storage": {},
                    "storage_state": {"cookies": data, "origins": []},
                }
            storage_state = data.get("storage_state", {})
            if not isinstance(storage_state, dict):
                storage_state = {}
            cookies = storage_state.get("cookies", data.get("cookies", []))
            origins = storage_state.get("origins", [])
            storage = data.get("storage", {})
            return {
                "cookies": cookies if isinstance(cookies, list) else [],
                "origins": origins if isinstance(origins, list) else [],
                "storage": storage if isinstance(storage, dict) else {},
                "storage_state": storage_state,
            }
        except (UnicodeDecodeError, ValueError):
            return {"cookies": [], "origins": [], "storage": {}, "storage_state": {}}

    def _read_session(self):
        return self._read_state()["cookies"]

    def _playwright_cookies(self):
        cookies = []
        for item in self._read_session():
            if not all(item.get(key) for key in ("name", "value", "domain")):
                continue
            cookie = {
                "name": item["name"],
                "value": item["value"],
                "domain": item["domain"],
                "path": item.get("path") or "/",
                "secure": bool(item.get("secure", True)),
                "httpOnly": bool(item.get("httpOnly", True)),
            }
            if item.get("sameSite") in {"Strict", "Lax", "None"}:
                cookie["sameSite"] = item["sameSite"]
            cookies.append(cookie)
        return cookies

    def _playwright_storage_state(self):
        state = self._read_state()
        if state["origins"]:
            return {
                "cookies": state["cookies"],
                "origins": state["origins"],
            }
        cookies = self._playwright_cookies()
        origins = self._storage_origins(state["storage"])
        if not cookies and not origins:
            return None
        return {"cookies": cookies, "origins": origins}

    def _playwright_storage_script(self):
        storage = self._read_state()["storage"]
        if not storage:
            return ""
        packed_storage = json.dumps(storage, ensure_ascii=True, separators=(",", ":"))
        return f"""
(() => {{
  const all = {packed_storage};
  const values = all[location.origin];
  if (!values) return;
  for (const [key, value] of Object.entries(values.session || {{}})) sessionStorage.setItem(key, value);
}})();
"""

    @staticmethod
    def _cookie_header(cookies, url):
        host = (urlparse(url).hostname or "").lower()
        values = []
        for cookie in cookies:
            domain = str(cookie.get("domain") or host).lstrip(".").lower()
            if host == domain or host.endswith("." + domain):
                values.append(f"{cookie['name']}={cookie['value']}")
        return {"Cookie": "; ".join(values)} if values else {}

    def _debug(self, message):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / "campus-debug.log").open("a", encoding="utf-8") as stream:
                stream.write(f"[{datetime.now(timezone.utc).isoformat()}] {message}\n")
        except OSError:
            pass

    def status(self):
        cookies = self._read_session()
        return {
            "logged_in": self._profile_logged_in() or bool(cookies) or self._live_context() is not None,
            "services": [{"id": key, "name": value["name"]} for key, value in SERVICES.items()],
        }

    def _profile_logged_in(self):
        try:
            return json.loads(self.profile_marker.read_text()).get("logged_in") is True
        except (OSError, ValueError, AttributeError):
            return False

    def _clear_profile_marker(self):
        try:
            self.profile_marker.unlink()
        except FileNotFoundError:
            pass

    def _clear_session(self):
        try:
            self.session_path.unlink()
        except FileNotFoundError:
            pass
        self.page_cache.clear()
        self.dashboard_cache = None

    def _cached_page(self, service, url, limit):
        entry = self.page_cache.get((service, url))
        if not entry or time.time() - entry["cached_at"] > PAGE_CACHE_SECONDS:
            return None
        return {
            **entry["value"],
            "text": entry["value"]["text"][:limit],
            "truncated": entry["value"]["text_length"] > limit,
            "cached": True,
        }

    def _cache_page(self, service, url, value):
        self.page_cache[(service, url)] = {
            "cached_at": time.time(),
            "value": {
                **value,
                "text": value["text"][:PAGE_TEXT_LIMIT],
                "cached": False,
            },
        }

    async def _close_browser(self):
        context, browser, playwright = self.browser_context, self.browser, self.playwright
        self.browser_context = None
        self.browser = None
        self.playwright = None
        self.browser_login_page = None
        self.browser_login_context = None
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                pass

    def _live_context(self):
        if self.browser is None or self.browser_context is None:
            return None
        try:
            if self.browser.is_connected():
                return self.browser_context
        except Exception:
            pass
        return None

    async def close(self):
        task = self.browser_login_task
        self.browser_login_task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._close_browser()

    def _mark_logged_in(self):
        access = datetime.now(timezone.utc).isoformat()
        self.profile_marker.write_text(
            json.dumps({"logged_in": True, "latest_access": access}, separators=(",", ":"))
        )
        os.chmod(self.profile_marker, 0o600)
        return access

    async def logout(self):
        if self.browser_login_task and not self.browser_login_task.done():
            self.browser_login_task.cancel()
            await asyncio.gather(self.browser_login_task, return_exceptions=True)
        self.browser_login_task = None
        self.browser_login_state = {"status": "idle", "phase": "", "error": ""}
        self._clear_session()
        self._clear_profile_marker()
        await self._close_browser()
        shutil.rmtree(self.profile_path, ignore_errors=True)
        return self.status()

    def browser_login_status(self):
        return dict(self.browser_login_state)

    def start_browser_login(self):
        if self.browser_login_task and not self.browser_login_task.done():
            return self.browser_login_status()
        self.page_cache.clear()
        self.dashboard_cache = None
        self.browser_login_state = {"status": "waiting", "phase": "opening", "error": ""}
        self.browser_login_task = asyncio.create_task(self._browser_login())
        return self.browser_login_status()

    async def _launch_browser(self, playwright, headless, args=None):
        last_error = None
        for channel in ("msedge", "chrome"):
            try:
                options = {"channel": channel, "headless": headless}
                if args:
                    options["args"] = args
                return await playwright.chromium.launch(**options)
            except Exception as error:
                last_error = error
        raise CampusError("无法启动 Edge 或 Chrome 浏览器") from last_error

    async def _browser_context(self, playwright, headless, args=None):
        browser = await self._launch_browser(playwright, headless, args)
        options = {"storage_state": self._playwright_storage_state()}
        if headless:
            options["viewport"] = {"width": 1280, "height": 900}
        else:
            options["no_viewport"] = True
        context = await browser.new_context(**options)
        storage_script = self._playwright_storage_script()
        if storage_script:
            await context.add_init_script(storage_script)
        return browser, context

    @staticmethod
    async def _page_storage(page):
        return await page.evaluate(
            """() => ({
                local: Object.fromEntries(Object.keys(localStorage).map(key => [key, localStorage.getItem(key)])),
                session: Object.fromEntries(Object.keys(sessionStorage).map(key => [key, sessionStorage.getItem(key)]))
            })"""
        )

    async def _persist_browser_state(self, context, storage):
        state = await context.storage_state()
        self._write_storage_state(state, storage)
        return self._mark_logged_in()

    async def _capture_storage(self, page, storage):
        parsed = urlparse(page.url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
            return
        origin = f"{parsed.scheme}://{parsed.netloc}"
        storage[origin] = await self._page_storage(page)

    async def _read_context(self):
        context = self._live_context()
        if context is not None:
            return self.browser, context, None
        from playwright.async_api import async_playwright

        playwright = await async_playwright().start()
        try:
            browser, context = await self._browser_context(playwright, True)
        except Exception:
            await playwright.stop()
            raise
        self.playwright = playwright
        self.browser = browser
        self.browser_context = context
        return browser, context, None

    @staticmethod
    async def _minimize_page(page):
        try:
            session = await page.context.new_cdp_session(page)
            window = await session.send("Browser.getWindowForTarget")
            await session.send(
                "Browser.setWindowBounds",
                {"windowId": window["windowId"], "bounds": {"windowState": "minimized"}},
            )
            await session.detach()
        except Exception:
            pass

    @staticmethod
    async def _restore_page(page):
        try:
            session = await page.context.new_cdp_session(page)
            window = await session.send("Browser.getWindowForTarget")
            await session.send(
                "Browser.setWindowBounds",
                {
                    "windowId": window["windowId"],
                    "bounds": {
                        "windowState": "normal",
                        "left": 80,
                        "top": 80,
                        "width": 1100,
                        "height": 800,
                    },
                },
            )
            await session.detach()
        except Exception:
            pass

    @staticmethod
    async def _tis_ready(page):
        try:
            user_ready = bool(await page.evaluate("localStorage.getItem('user')"))
        except Exception:
            user_ready = False
        if not user_ready:
            return False
        for label in ("CAS用户登录", "非CAS用户登录"):
            locator = page.get_by_text(label, exact=True)
            if await locator.count() and await locator.first.is_visible():
                return False
        return True

    async def _ensure_service_ready(self, page, service):
        current = urlparse(page.url)
        tis_ready = True
        if service == "tis":
            tis_ready = await self._tis_ready(page)
            deadline = asyncio.get_running_loop().time() + 10
            while not tis_ready and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.5)
                tis_ready = await self._tis_ready(page)
            current = urlparse(page.url)
        if current.hostname == "cas.sustech.edu.cn" or not tis_ready:
            try:
                cookie_names = [
                    item["name"]
                    for item in await page.context.cookies()
                    if "sustech.edu.cn" in item.get("domain", "")
                ]
            except Exception:
                cookie_names = []
            self._debug(json.dumps({
                "session_expired": service,
                "host": current.hostname,
                "path": current.path,
                "cookies": cookie_names,
            }, ensure_ascii=False))
            if service == "tis":
                raise CampusSessionExpired("教学管理与服务平台会话已过期，请重新登录 CAS")
            raise CampusSessionExpired("Blackboard 会话已过期，请重新登录 CAS")

    async def _browser_login(self):
        async with self.browser_login_lock:
            if self._profile_logged_in() and self._read_session():
                self.browser_login_state = {
                    **self.browser_login_state,
                    "status": "succeeded",
                    "phase": "",
                    "error": "",
                }
                return
            await self._run_browser_login()

    async def _run_browser_login(self):
        if os.name != "nt":
            self.browser_login_state = {
                "status": "failed",
                "phase": "",
                "error": "浏览器登录当前仅支持 Windows；Linux 可使用直接登录。",
            }
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.browser_login_state = {"status": "failed", "phase": "", "error": "缺少浏览器登录组件。"}
            return
        browser = None
        context = None
        playwright = None
        keep_open = False
        close_extra_page = None
        try:
            await self._close_browser()
            playwright = await async_playwright().start()
            browser, context = await self._browser_context(
                playwright,
                False,
                [
                    "--window-position=80,80",
                    "--window-size=1100,800",
                ],
            )
            page = await context.new_page()
            self.browser_login_page = page
            self.browser_login_context = context

            def close_extra_page(extra):
                if extra is page:
                    return
                task = asyncio.create_task(extra.close())
                self.browser_login_popup_tasks.add(task)
                task.add_done_callback(self.browser_login_popup_tasks.discard)

            context.on("page", close_extra_page)
            await self._restore_page(page)
            self.browser_login_state = {"status": "waiting", "phase": "cas", "error": ""}
            await page.goto(SERVICES["tis"]["login"], wait_until="domcontentloaded", timeout=30000)
            deadline = asyncio.get_running_loop().time() + 300
            while True:
                if page.is_closed():
                    raise CampusAuthError("浏览器在登录完成前关闭")
                current = urlparse(page.url)
                if current.hostname == "tis.sustech.edu.cn" and not current.path.startswith("/cas"):
                    if await self._tis_ready(page):
                        break
                if asyncio.get_running_loop().time() >= deadline:
                    raise CampusAuthError("CAS 登录等待超时")
                await asyncio.sleep(0.5)

            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            storage = {"https://tis.sustech.edu.cn": await self._page_storage(page)}
            self.browser_login_state = {"status": "waiting", "phase": "blackboard", "error": ""}
            await page.goto(SERVICES["bb"]["login"], wait_until="domcontentloaded", timeout=30000)
            deadline = asyncio.get_running_loop().time() + 90
            while True:
                if page.is_closed():
                    raise CampusAuthError("浏览器在 Blackboard 登录完成前关闭")
                current = urlparse(page.url)
                cookies = await context.cookies()
                bb_cookie = any(
                    item.get("value") and "bb.sustech.edu.cn" in item.get("domain", "")
                    for item in cookies
                )
                if current.hostname == "bb.sustech.edu.cn" and bb_cookie:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise CampusAuthError("Blackboard 会话建立超时")
                await asyncio.sleep(0.5)

            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            bb_origin = f"{urlparse(page.url).scheme}://{urlparse(page.url).netloc}"
            storage[bb_origin] = await self._page_storage(page)
            self.browser_login_state = {"status": "waiting", "phase": "validating", "error": ""}
            cookies = [
                {"domain": item.get("domain"), "path": item.get("path"), "name": item.get("name")}
                for item in await context.cookies()
                if item.get("value") and "sustech.edu.cn" in item.get("domain", "")
            ]
            if not any(item["name"] == "SESSION" and "tis.sustech.edu.cn" in item["domain"] for item in cookies):
                raise CampusAuthError("浏览器登录后未获得教学平台会话")
            self._debug(
                "browser login completed " + json.dumps(
                    {"cookies": cookies, "tis_user_ready": True},
                    ensure_ascii=False,
                )
            )
            access = await self._persist_browser_state(context, storage)
            if close_extra_page is not None:
                context.remove_listener("page", close_extra_page)
                close_extra_page = None
            self.playwright = playwright
            self.browser = browser
            self.browser_context = context
            keep_open = True
            self.browser_login_state = {
                "status": "succeeded",
                "phase": "",
                "error": "",
                "latest_access": access,
            }
            await self._minimize_page(page)
        except asyncio.CancelledError:
            self.browser_login_state = {"status": "idle", "phase": "", "error": ""}
            raise
        except Exception as error:
            self.browser_login_state = {"status": "failed", "phase": "", "error": str(error)}
        finally:
            if context is not None and close_extra_page is not None:
                try:
                    context.remove_listener("page", close_extra_page)
                except Exception:
                    pass
            if not keep_open and context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
                self.browser_login_page = None
                self.browser_login_context = None
            if not keep_open and browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            if not keep_open and playwright is not None:
                try:
                    await playwright.stop()
                except Exception:
                    pass

    async def _validate_cookies(self, cookies):
        results = []
        async with self._client() as client:
            for service in SERVICES:
                url = SERVICES[service]["home"]
                response = await client.get(url, headers=self._cookie_header(cookies, url))
                logged_out = self._is_service_login(service, str(response.url), response.text)
                results.append({
                    "service": service,
                    "ok": not logged_out,
                    "status": response.status_code,
                    "host": urlparse(str(response.url)).hostname,
                    "path": urlparse(str(response.url)).path,
                })
        return results

    async def _wait_page_content(self, page, service, url):
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if service == "tis":
            try:
                await page.wait_for_function(
                    "() => Boolean(localStorage.getItem('user'))",
                    timeout=7000,
                )
            except Exception:
                pass
        try:
            await page.wait_for_function(
                """() => {
                    const text = document.body ? document.body.innerText.trim() : "";
                    return text.length >= 120 || document.querySelectorAll("a[href]").length >= 8;
                }""",
                timeout=4000,
            )
        except Exception:
            pass
        await page.wait_for_timeout(700)
        await self._ensure_service_ready(page, service)
        try:
            body = await page.locator("body").inner_text(timeout=5000)
        except Exception:
            body = ""
        if service == "bb" and urlparse(url).path in {"", "/"} and len(body) < 1000:
            await page.wait_for_timeout(1200)
            try:
                body = await page.locator("body").inner_text(timeout=5000)
            except Exception:
                pass
        return body

    @staticmethod
    async def _page_links(page):
        links = await page.eval_on_selector_all(
            "a[href]",
            """nodes => nodes.map(node => ({
                title: (node.innerText || node.getAttribute('aria-label') || '').trim().slice(0, 240),
                url: node.href
            })).filter(item => item.title && item.url).slice(0, 200)""",
        )
        filtered = []
        seen = set()
        for link in links:
            parsed = urlparse(link["url"])
            if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
                continue
            if parsed.path.startswith("/webapps/login/"):
                continue
            if link["url"] in seen:
                continue
            seen.add(link["url"])
            filtered.append(link)
        return filtered[:200]

    async def _browser_page(self, page, service):
        body = await self._wait_page_content(page, service, SERVICES[service]["home"])
        title = await page.title()
        return {
            "id": service,
            "service": service,
            "name": SERVICES[service]["name"],
            "title": title,
            "url": str(page.url),
            "text": body[:OVERVIEW_TEXT_LIMIT],
            "text_length": len(body),
            "truncated": len(body) > OVERVIEW_TEXT_LIMIT,
            "links": await self._page_links(page),
        }

    async def _browser_overview(self, services=None, refresh=False):
        async with self.browser_lock:
            state = self._playwright_storage_state()
            if not state:
                self._clear_profile_marker()
                raise CampusSessionExpired("尚未登录校园平台")
            targets = [services] if isinstance(services, str) else list(services or SERVICES)
            browser, context, playwright = await self._read_context()
            try:
                storage = dict(self._read_state()["storage"])
                pages = []
                errors = []
                changed = False
                for service in targets:
                    try:
                        cached = None if refresh else self._cached_page(
                            service, SERVICES[service]["home"], OVERVIEW_TEXT_LIMIT
                        )
                        if cached is not None:
                            pages.append(cached)
                            continue
                        page = await context.new_page()
                        try:
                            value = await self._browser_page(page, service)
                            self._cache_page(service, SERVICES[service]["home"], value)
                            pages.append(value)
                            await self._capture_storage(page, storage)
                            changed = True
                        finally:
                            await page.close()
                    except CampusError as error:
                        errors.append({"service": service, "error": str(error)})
                if pages:
                    if changed:
                        await self._persist_browser_state(context, storage)
                    return {
                        "logged_in": True,
                        "fetched_at": time.time(),
                        "services": pages,
                        "errors": errors,
                    }
                self._clear_profile_marker()
                self._clear_session()
                message = errors[0]["error"] if errors else "校园平台会话已过期，请重新登录 CAS"
                raise CampusSessionExpired(message)
            finally:
                if playwright is not None:
                    await context.close()
                    await browser.close()
                    await playwright.stop()

    async def _browser_read_page(self, service, url, limit=PAGE_TEXT_LIMIT, refresh=False):
        if service not in SERVICES:
            raise ValueError("未知校园平台")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
            raise ValueError("仅允许读取南科大校园平台地址")
        async with self.browser_lock:
            cached = None if refresh else self._cached_page(service, url, limit)
            if cached is not None:
                return cached
            browser, context, playwright = await self._read_context()
            try:
                page = await context.new_page()
                try:
                    body = await self._wait_page_content(page, service, url)
                    storage = dict(self._read_state()["storage"])
                    await self._capture_storage(page, storage)
                    await self._persist_browser_state(context, storage)
                    value = {
                        "service": service,
                        "url": str(page.url),
                        "title": await page.title(),
                        "text": body[:limit],
                        "text_length": len(body),
                        "truncated": len(body) > limit,
                        "links": await self._page_links(page),
                        "cached": False,
                    }
                    self._cache_page(service, url, value)
                    return value
                finally:
                    await page.close()
            finally:
                if playwright is not None:
                    await context.close()
                    await browser.close()
                    await playwright.stop()

    async def _browser_dashboard(self):
        async with self.browser_lock:
            state = self._playwright_storage_state()
            if not state:
                self._clear_profile_marker()
                raise CampusSessionExpired("尚未登录校园平台")
            browser, context, playwright = await self._read_context()
            try:
                storage = dict(self._read_state()["storage"])

                async def announcements():
                    page = await context.new_page()
                    try:
                        await self._wait_page_content(page, "bb", BB_ANNOUNCEMENTS_URL)
                        return await page.evaluate(
                            """() => [...document.querySelectorAll('#announcementList > li.clearfix')]
                                .slice(0, 40)
                                .map(item => ({
                                    id: item.id,
                                    title: (item.querySelector('h3.item')?.innerText || '').trim(),
                                    info: (item.querySelector('.announcementInfo')?.innerText || '').trim(),
                                    details: (item.querySelector('.details')?.innerText || '').trim().slice(0, 8000),
                                    attachments: [...item.querySelectorAll('a[href]')]
                                        .map(link => ({text: (link.innerText || '').trim(), href: link.href}))
                                        .slice(0, 8)
                                }))"""
                        )
                    finally:
                        await page.close()

                async def events():
                    page = await context.new_page()
                    try:
                        await self._wait_page_content(page, "bb", BB_CALENDAR_URL)
                        return await page.evaluate(
                            """() => [...document.querySelectorAll('.fc-event')]
                                .map(item => ({text: (item.innerText || '').trim()}))
                                .filter(item => item.text)"""
                        )
                    finally:
                        await page.close()

                announcement_items, calendar_events = await asyncio.gather(
                    announcements(),
                    events(),
                )
                if not announcement_items:
                    raise CampusError("Blackboard 公告结构未能识别")
                await self._persist_browser_state(context, storage)
                return announcement_items, calendar_events
            finally:
                if playwright is not None:
                    await context.close()
                    await browser.close()
                    await playwright.stop()

    async def dashboard(self, refresh=False):
        if (
            not refresh
            and self.dashboard_cache
            and time.time() - self.dashboard_cache["cached_at"] <= PAGE_CACHE_SECONDS
        ):
            return {**self.dashboard_cache["value"], "cached": True}
        announcements, events = await self._browser_dashboard()
        value = build_dashboard(announcements, events)
        self.dashboard_cache = {"cached_at": time.time(), "value": value}
        return {**value, "cached": False}

    @staticmethod
    def _execution(html):
        match = re.search(r'name="execution"\s+value="([^"]+)"', html)
        return match.group(1) if match else ""

    @staticmethod
    def _is_cas_login(url, html):
        return urlparse(url).netloc == "cas.sustech.edu.cn" and (
            "/cas/login" in url or 'id="fm1"' in html or "name=\"execution\"" in html
        )

    @staticmethod
    def _is_service_login(service, url, html):
        if CampusService._is_cas_login(url, html):
            return True
        if service == "tis":
            return "非CAS用户登录" in html or 'action="/authentication/form"' in html
        return False

    async def login(self, username, password):
        if not isinstance(username, str) or not username.strip():
            raise CampusAuthError("请输入 CAS 用户名")
        if not isinstance(password, str) or not password:
            raise CampusAuthError("请输入 CAS 密码")
        async with self._client() as client:
            response = await client.get(SERVICES["tis"]["login"])
            cas_url = str(response.url)
            if not self._is_cas_login(cas_url, response.text):
                raise CampusAuthError("无法进入南科大 CAS 登录页")
            execution = self._execution(response.text)
            if not execution:
                raise CampusAuthError("CAS 登录页缺少必要参数")
            if "captcha" in response.text.lower() and 'name="captcha"' in response.text:
                raise CampusAuthError("CAS 要求验证码，请先在浏览器完成登录")
            result = await client.post(
                cas_url,
                data={
                    "username": username.strip(),
                    "password": password,
                    "_eventId": "submit",
                    "geolocation": "",
                    "execution": execution,
                },
                headers={"Referer": cas_url, "Origin": CAS_ORIGIN},
            )
            result_url = str(result.url)
            if self._is_cas_login(result_url, result.text):
                message = "CAS 登录失败，请检查用户名和密码"
                if "captcha" in result.text.lower():
                    message = "CAS 要求验证码，请先在浏览器完成登录"
                raise CampusAuthError(message)

            tis = await client.get(SERVICES["tis"]["home"])
            if self._is_service_login("tis", str(tis.url), tis.text):
                raise CampusAuthError("CAS 登录成功，但教学平台会话建立失败")
            bb = await client.get(SERVICES["bb"]["login"])
            if self._is_cas_login(str(bb.url), bb.text):
                raise CampusAuthError("CAS 登录成功，但 Blackboard 会话建立失败")

            cookies = [
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain or "",
                    "path": cookie.path or "/",
                }
                for cookie in client.cookies.jar
                if cookie.value
            ]
            if not cookies:
                raise CampusAuthError("CAS 登录后未获得有效会话")
            self._write_session(cookies)
        return {
            **self.status(),
            "latest_access": datetime.now(timezone.utc).isoformat(),
        }

    async def _service_page(self, client, service, cookies):
        url = SERVICES[service]["home"]
        response = await client.get(url, headers=self._cookie_header(cookies, url))
        if (urlparse(str(response.url)).hostname or "") not in ALLOWED_HOSTS:
            raise CampusError("校园平台返回了非预期地址")
        if self._is_service_login(service, str(response.url), response.text):
            raise CampusSessionExpired("校园平台会话已过期，请重新登录 CAS")
        parser = _PortalParser()
        parser.feed(response.text)
        data = parser.result()
        links = []
        seen = set()
        for link in data["links"]:
            absolute = httpx.URL(str(response.url)).join(link["url"])
            host = absolute.host
            if host not in ALLOWED_HOSTS:
                continue
            key = str(absolute)
            if key in seen:
                continue
            seen.add(key)
            links.append({"title": link["title"], "url": key})
        return {
            "id": service,
            "name": SERVICES[service]["name"],
            "title": data["title"],
            "text": data["text"],
            "links": links[:200],
        }

    async def overview(self, service=None, refresh=False):
        if service is not None and service not in SERVICES:
            raise ValueError("未知校园平台")
        targets = [service] if service else list(SERVICES)
        if self._live_context() is not None or self._profile_logged_in():
            return await self._browser_overview(targets, refresh)
        cookies = self._read_session()
        if not cookies:
            raise CampusSessionExpired("尚未登录校园平台")
        async with self._client() as client:
            pages = []
            for target in targets:
                pages.append(await self._service_page(client, target, cookies))
            return {
                "logged_in": True,
                "fetched_at": time.time(),
                "services": pages,
            }

    async def read_page(self, service, url, limit=PAGE_TEXT_LIMIT, refresh=False):
        if self._live_context() is not None or self._profile_logged_in():
            return await self._browser_read_page(service, url, limit, refresh)
        if service not in SERVICES:
            raise ValueError("未知校园平台")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
            raise ValueError("仅允许读取南科大校园平台地址")
        cookies = self._read_session()
        if not cookies:
            raise CampusSessionExpired("尚未登录校园平台")
        async with self._client() as client:
            response = await client.get(url, headers=self._cookie_header(cookies, url))
            if (urlparse(str(response.url)).hostname or "") not in ALLOWED_HOSTS:
                raise CampusError("校园平台返回了非预期地址")
            if self._is_cas_login(str(response.url), response.text):
                raise CampusSessionExpired("校园平台会话已过期，请重新登录 CAS")
            parser = _PortalParser()
            parser.feed(response.text)
            data = parser.result()
            text_length = len(data["text"])
            data["text"] = data["text"][:limit]
            return {
                "service": service,
                "url": str(response.url),
                **data,
                "text_length": text_length,
                "truncated": text_length > limit,
            }
