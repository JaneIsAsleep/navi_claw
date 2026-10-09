import asyncio
from contextlib import asynccontextmanager
import json
import os
import signal

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles

from .gateway import TERMINAL, APIError
from .feed_http import FeedReadError
from .storage import packed


async def body(request, object_only=True):
    try:
        value = await request.json()
    except json.JSONDecodeError as error:
        raise ValueError("请求 JSON 格式无效") from error
    if object_only and not isinstance(value, dict):
        raise ValueError("请求内容需要为对象")
    return value


def create_app(gateway, instance="test", project_id="test", shutdown_token=None):
    @asynccontextmanager
    async def lifespan(app):
        await gateway.start()
        yield
        await gateway.stop()

    app = FastAPI(title="Navi", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        host = request.headers.get("host", "").split(":")[0]
        if host not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"detail": "Navi 只接受本机入口"}, 403)
        origin = request.headers.get("origin")
        if origin and origin not in {"http://127.0.0.1:8766", "http://localhost:8766", "http://testserver"}:
            return JSONResponse({"detail": "请求来源不属于 Navi 本机界面"}, 403)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.headers.get("content-type", "").split(";")[0] != "application/json":
            return JSONResponse({"detail": "修改请求需要 JSON"}, 415)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if 'Cache-Control' not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": str(error)}, status_code=error.status if isinstance(error, APIError) else 400)

    @app.get("/api/health")
    def health():
        return {"name": "Navi", "ready": gateway.running, "instance": instance, "project_id": project_id, "pid": os.getpid()}

    @app.post("/api/shutdown")
    async def shutdown(request: Request):
        if not shutdown_token or request.headers.get("X-Navi-Owner") != shutdown_token:
            return JSONResponse({"detail": "需要本项目停止入口"}, 403)
        asyncio.get_running_loop().call_later(0.2, os.kill, os.getpid(), signal.SIGTERM)
        return {"stopping": True}

    @app.get("/api/state")
    def state(feed_limit: str | None = None):
        try:
            limit = int(feed_limit) if feed_limit is not None else None
        except ValueError as error:
            raise ValueError('动态数量应为 1 至 50') from error
        return gateway.snapshot(limit)

    @app.get('/api/feed/items')
    def feed_items(limit: str = '20', before: str | None = None):
        try:
            count = int(limit)
        except ValueError as error:
            raise ValueError('动态数量应为 1 至 50') from error
        return gateway.feed_items(count, before)

    @app.get('/api/feed-media/{identity}')
    async def feed_media(identity: str):
        try:
            data, mime = await gateway.feeds.media(identity)
        except ValueError as error:
            raise APIError(str(error), 404) from error
        except FeedReadError as error:
            raise APIError(str(error), 502) from error
        return Response(data, media_type=mime, headers={'Cache-Control': 'private, max-age=86400'})

    @app.post("/api/sessions")
    async def new_session(request: Request):
        values = await body(request)
        return gateway.session(str(values.get("title", "新对话")))

    @app.get("/api/sessions/search")
    def search_sessions(q: str = "", recent: bool = False, limit: int = 20, offset: int = 0, scope: str = 'title'):
        return gateway.search_sessions(q, recent, limit, offset, scope)

    @app.get("/api/sessions/{identity}")
    def session(identity: str):
        return gateway.open_session(identity)

    @app.patch("/api/sessions/{identity}")
    async def edit_session(identity: str, request: Request):
        return gateway.edit_session(identity, await body(request))

    @app.get("/api/sessions/{identity}/deletion-impact")
    def deletion_impact(identity: str):
        return gateway.deletion_impact(identity)

    @app.delete("/api/sessions/{identity}")
    async def delete_session(identity: str):
        return await gateway.delete_session(identity)

    @app.post("/api/sessions/{identity}/read")
    def read_session(identity: str):
        gateway.require("sessions", identity)
        gateway.db.execute("UPDATE messages SET unread=0 WHERE session_id=?", (identity,))
        return {"read": True}

    @app.get("/api/sessions/{identity}/messages")
    def messages(identity: str):
        return gateway.messages(identity)

    @app.post("/api/chat")
    async def chat(request: Request):
        values = await body(request)
        return gateway.chat(values.get("text"), values.get("session_id"), values.get("kind", "chat"), values.get("references"), values.get("new_session", False))

    @app.post("/api/goals/start")
    async def start_goal(request: Request):
        values = await body(request)
        if set(values) != {"category", "request_id"}:
            raise ValueError("目标开始请求字段无效")
        return gateway.start_goal(values["category"], values["request_id"])

    @app.post("/api/sessions/{identity}/goal-choices")
    async def goal_choice(identity: str, request: Request):
        return gateway.goal_choice(identity, await body(request))

    @app.post("/api/goals/{identity}/state")
    async def goal_state(identity: str, request: Request):
        values = await body(request)
        if set(values) != {"operation"}:
            raise ValueError("目标状态请求字段无效")
        return gateway.goal_state(identity, values["operation"])

    @app.get("/api/jobs/{identity}")
    def job(identity: str):
        row = gateway.require("jobs", identity)
        row["config"] = json.loads(row["config"])
        return row

    @app.post("/api/jobs/{identity}/cancel")
    async def cancel(identity: str):
        return await gateway.cancel(identity)

    @app.get("/api/jobs/{identity}/events")
    async def events(identity: str, request: Request):
        gateway.require("jobs", identity)

        async def stream():
            queue = asyncio.Queue(maxsize=1000)
            gateway.listeners.setdefault(identity, set()).add(queue)
            try:
                row = gateway.require("jobs", identity)
                if row["status"] in TERMINAL:
                    yield f"event: done\ndata: {packed({'status': row['status'], 'result': row['result'], 'error': row['error']})}\n\n"
                    return
                yield "event: status\ndata: " + packed({"status": row["status"], "message": "等待处理" if row["status"] == "pending" else "正在处理，可在活动页查看"}) + "\n\n"
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(queue.get(), 10)
                        yield f"event: {event['event']}\ndata: {packed(event['data'])}\n\n"
                        if event["event"] == "done":
                            return
                    except TimeoutError:
                        yield ": alive\n\n"
            finally:
                gateway.listeners.get(identity, set()).discard(queue)
                if not gateway.listeners.get(identity):
                    gateway.listeners.pop(identity, None)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/api/jobs/{identity}/evidence")
    def evidence(identity: str):
        return gateway.evidence(identity)

    @app.get("/api/audit")
    def audit(job_id: str | None = None):
        return gateway.audit(job_id)

    @app.post("/api/schedules")
    async def schedule(request: Request):
        return gateway.schedule(await body(request))

    @app.patch("/api/schedules/{identity}")
    async def edit_schedule(identity: str, request: Request):
        return gateway.edit_schedule(identity, await body(request))

    @app.get("/api/schedules/{identity}/history")
    def schedule_history(identity: str):
        row = gateway.require("schedules", identity)
        return [gateway.evidence(job["id"]) for job in gateway.db.all("SELECT id FROM jobs WHERE task_id=? ORDER BY created DESC", (row["task_id"],))]

    @app.post("/api/records/{kind}")
    async def record(kind: str, request: Request):
        return gateway.record(kind, await body(request))

    @app.get("/api/records/{identity}")
    def record_detail(identity: str):
        return gateway.record_detail(identity)

    @app.put("/api/records/{identity}")
    async def edit_record(identity: str, request: Request):
        row = gateway.require("records", identity)
        return gateway.record(row["kind"], await body(request), identity)

    @app.delete("/api/records/{identity}")
    def delete_record(identity: str):
        gateway.delete_record(identity)
        return {"deleted": True}

    @app.get("/api/actions/{identity}/history")
    def action_history(identity: str):
        return gateway.action_history(identity)

    @app.put("/api/actions/{identity}")
    async def edit_action(identity: str, request: Request):
        return gateway.edit_action(identity, await body(request))

    @app.post("/api/actions/{identity}/decision")
    async def decide(identity: str, request: Request):
        values = await body(request)
        return gateway.decide(identity, values.get("decision"), values.get("content_hash"))

    @app.post("/api/notifications/read")
    def read_notifications():
        gateway.db.execute("UPDATE notifications SET unread=0")
        return {"read": True}

    @app.put("/api/settings")
    async def settings(request: Request):
        result = gateway.settings.update(await body(request))
        gateway.db.event("settings.updated", {"workflow": result["workflow"], "policy": result["notification_policy"]}, now=gateway.clock())
        return result

    @app.post("/api/settings/check")
    async def check_connection():
        with gateway.db.transaction() as conn:
            identity = gateway.insert_job(conn, kind="connection", text="请简短回复，确认真实 API 已返回结果。")
        return gateway.require("jobs", identity)

    @app.put("/api/check-items")
    async def check_items(request: Request):
        gateway.skills.save_items(await body(request, object_only=False))
        gateway.db.event("check_items.updated", {"count": len(gateway.skills.items())}, now=gateway.clock())
        return gateway.skills.items()

    @app.post("/api/feed-sources")
    async def follow_source(request: Request):
        values = await body(request)
        if set(values) == {'source_id'}:
            return gateway.feeds.follow(values['source_id'])
        if 'url' in values and not set(values) - {'url', 'title'}:
            try:
                return await gateway.feeds.add_custom(values['url'], values.get('title', ''))
            except FeedReadError as error:
                raise APIError(str(error), 502) from error
        raise ValueError('预设来源与自定义地址只能选择一种')

    @app.post('/api/feed-sources/preview')
    async def preview_source(request: Request):
        values = await body(request)
        if set(values) != {'url'}:
            raise ValueError('预览只接受订阅地址')
        try:
            return await gateway.feeds.preview(values['url'])
        except FeedReadError as error:
            raise APIError(str(error), 502) from error

    @app.post("/api/ideas/{identity}/start")
    async def start_idea(identity: str, request: Request):
        values = await body(request)
        if set(values) != {"request_id"}:
            raise ValueError("只接受请求标识")
        return gateway.start_idea(identity, values["request_id"])

    @app.post("/api/feed-sources/{identity}/toggle")
    async def toggle_source(identity: str):
        row = gateway.require("records", identity)
        if row["kind"] != "source":
            raise ValueError("来源不存在")
        payload = json.loads(row["payload"])
        payload["enabled"] = not payload.get("enabled", True)
        gateway.db.execute("UPDATE records SET payload=?,updated=? WHERE id=?", (packed(payload), gateway.clock(), identity))
        gateway.db.event("source.toggled", {"source_id": payload["source_id"], "enabled": payload["enabled"]})
        return payload

    @app.post("/api/feed-refresh")
    async def refresh_feed():
        return await gateway.feeds.refresh()

    @app.post("/api/feed-generate")
    async def generate_feed():
        return gateway.generate_feed()

    @app.get("/api/artifacts/{identity}/download")
    def download(identity: str):
        row = gateway.require("records", identity)
        if row["kind"] != "artifact":
            raise ValueError("成果不存在")
        payload = json.loads(row["payload"])
        filename = payload.get("filename")
        if filename:
            folder = (gateway.directory / "exports").resolve()
            path = (folder / filename).resolve()
            if path.parent != folder or not path.is_file():
                raise ValueError("成果文件不存在")
            return FileResponse(path, filename=filename)
        # Downloading a draft is an explicit user browser action, separate from background file writes.
        return StreamingResponse(iter([payload.get("body", "").encode()]), media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="Navi-draft.md"'})

    dist = gateway.root / "frontend" / "dist"
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/")
    def home():
        if not (dist / "index.html").exists():
            return JSONResponse({"detail": "前端尚未构建，请执行 ./setup"}, 503)
        return FileResponse(dist / "index.html")

    return app
