import asyncio
import base64
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Settings
from .feeds import PublicFeeds
from .model import AgentError, DeepSeek
from .skills import BriefingSkills
from .storage import Database, packed, uid


TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}
RECORD_KINDS = {"memory", "goal", "feed", "idea", "artifact"}
VISIBLE_FEED = "kind='feed' AND json_extract(payload,'$.origin')='generated' AND COALESCE(json_extract(payload,'$.display_state'),'')<>'hidden_legacy'"
IDEA_ACTIONS = {"goal_breakdown", "source_summary", "document_draft", "next_step", "task_review"}
CATEGORIES = {"health": "健康", "relationships": "人际关系", "finance": "财务", "career": "职涯", "interests": "兴趣", "productivity": "生产力", "other": "其他"}
TRANSIENT_CONTENT_ERRORS = {'model_timeout', 'model_connection_error', 'model_transport_error', 'model_rate_limited', 'model_server_error', 'sleep_interrupted'}


class APIError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def content_hash(kind, title, target, body, version=1):
    return hashlib.sha256(packed([kind, title, target, body, version]).encode()).hexdigest()


class Gateway:
    def __init__(self, root: Path, directory: Path, model=None, clock=time.time):
        self.root, self.directory, self.clock = root, directory, clock
        self.db = Database(directory)
        self.settings = Settings(self.db, directory)
        self.skills = BriefingSkills(root, directory)
        self.feeds = PublicFeeds(self)
        self.model = model or DeepSeek()
        self.listeners: dict[str, set[asyncio.Queue]] = {}
        self.running = False
        self.active: dict[str, asyncio.Task] = {}
        self.loops: list[asyncio.Task] = []
        self._awake_since = clock()
        self._last_tick = None
        self._sleep_interrupted: set[str] = set()
        with self.db.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO sessions(id,title,kind,created,updated) VALUES(?,'主对话','main',?,?)", (uid(), clock(), clock()))
        self.settings.on_budget_change = self.restore_daily
        if self.db.migrated_v5:
            self.restore_daily(budget_changed=True, migration=True)

    def require(self, table, identity):
        if table not in {"sessions", "jobs", "schedules", "actions", "records", "tasks"}:
            raise ValueError("无效的数据类型")
        row = self.db.one(f"SELECT * FROM {table} WHERE id=?", (identity,))
        if row is None:
            raise ValueError("记录不存在")
        return row

    def snapshot(self, feed_limit=None):
        if feed_limit is not None and (type(feed_limit) is not int or not 1 <= feed_limit <= 50):
            raise ValueError('动态数量应为 1 至 50')
        now = self.clock()
        settings = self.settings.public()
        usage = self.db.one("SELECT COUNT(*) requests,COALESCE(SUM(input_tokens),0) input_tokens,COALESCE(SUM(output_tokens),0) output_tokens,COALESCE(SUM(estimated),0) estimated FROM usage WHERE created>=?", (self.day_start(),))
        usage["cost_configured"] = bool(settings["input_price_per_million"] or settings["output_price_per_million"])
        usage["estimated_cost"] = (usage["input_tokens"] * settings["input_price_per_million"] + usage["output_tokens"] * settings["output_price_per_million"]) / 1_000_000
        usage["currency"] = settings["price_currency"]
        feed_filter = " AND (kind<>'feed' OR id IN (SELECT id FROM records WHERE " + VISIBLE_FEED + " ORDER BY created DESC,id DESC LIMIT ?))" if feed_limit is not None else ''
        records = [{**row, "payload": json.loads(row["payload"])} for row in self.db.all("SELECT * FROM records WHERE (kind<>'idea' OR COALESCE(json_extract(payload,'$.state'),'active')='active') AND COALESCE(json_extract(payload,'$.display_state'),'')<>'hidden_legacy'" + feed_filter + " ORDER BY updated DESC", (feed_limit,) if feed_limit is not None else ())]
        sessions_by_id = {row["id"]: row for row in self.db.all("SELECT * FROM sessions WHERE state<>'deleting'")}
        job_sessions = {row["id"]: row["session_id"] for row in self.db.all("SELECT id,session_id FROM jobs")}
        for row in records:
            payload = row["payload"]
            source_id = payload.get("session_id") or job_sessions.get(payload.get("source_job_id") or payload.get("job_id"))
            row["source_session"] = sessions_by_id.get(source_id)
        return {
            "user": self.db.one("SELECT * FROM users WHERE id='local-user'"),
            "sessions": self.db.all("SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id AND m.unread=1) unread FROM sessions s WHERE state='active' ORDER BY kind='main' DESC,updated DESC"),
            "main_session_id": self.main_session()["id"],
            "schedules": self.db.all("SELECT s.*,t.title,t.instruction,t.state,t.session_id,t.goal_id FROM schedules s JOIN tasks t ON t.id=s.task_id ORDER BY s.created DESC"),
            "jobs": self.db.all("SELECT id,task_id,session_id,trigger_key,kind,status,input,result,error,error_code,recovery_count,attempt,next_run,deadline,created,updated FROM jobs WHERE status IN ('pending','running') OR id IN (SELECT id FROM jobs ORDER BY created DESC LIMIT 100) ORDER BY created DESC,rowid DESC"),
            "actions": self.db.all("SELECT a.*,s.title source_title,s.state source_state,o.state delivery_state,o.error delivery_error FROM actions a LEFT JOIN sessions s ON s.id=a.source_session_id LEFT JOIN outbox o ON o.action_id=a.id ORDER BY a.status='pending' DESC,a.created DESC"),
            "notifications": self.db.all("SELECT * FROM notifications ORDER BY created DESC"),
            "records": records, "settings": settings, "usage": usage,
            "check_items": self.skills.items(), "feed_catalog": self.feeds.catalog(), "now": now,
            "feed_brief": settings["feed_brief"],
            "auto_content": self.auto_content(),
        }

    def feed_items(self, limit=20, before=None):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('动态数量应为 1 至 50')
        conditions, params = VISIBLE_FEED, []
        if before is not None:
            try:
                if not isinstance(before, str) or not 1 <= len(before) <= 256 or not re.fullmatch(r'[A-Za-z0-9_=-]+', before):
                    raise ValueError()
                value = json.loads(base64.b64decode(before + '=' * (-len(before) % 4), altchars=b'-_', validate=True))
                if not isinstance(value, list) or len(value) != 2:
                    raise ValueError()
                created, identity = value
                if type(created) not in (int, float) or not math.isfinite(created) or created < 0 or not isinstance(identity, str) or not 1 <= len(identity) <= 100:
                    raise ValueError()
            except (ValueError, UnicodeError) as error:
                raise ValueError('动态翻页位置无效，请重新加载') from error
            conditions += ' AND (created<? OR (created=? AND id<?))'
            params.extend((created, created, identity))
        rows = self.db.all('SELECT * FROM records WHERE ' + conditions + ' ORDER BY created DESC,id DESC LIMIT ?', (*params, limit + 1))
        cursor = base64.urlsafe_b64encode(packed([rows[limit - 1]['created'], rows[limit - 1]['id']]).encode()).decode().rstrip('=') if len(rows) > limit else None
        return {'items': [{**row, 'payload': json.loads(row['payload'])} for row in rows[:limit]], 'next_cursor': cursor}

    def content_due(self):
        timezone = self.settings.current()["timezone"]
        local = datetime.fromtimestamp(self.clock(), ZoneInfo(timezone))
        due = local.replace(hour=6, minute=0, second=0, microsecond=0)
        if due > local:
            due -= timedelta(days=1)
        return due

    def auto_content(self):
        due = self.content_due()
        result = {"next_due": (due + timedelta(days=1)).timestamp(), "timezone": str(due.tzinfo)}
        for kind in ("feed", "ideas"):
            latest = self.db.one("SELECT id,status,error,error_code,next_run,recovery_count,updated,result FROM jobs WHERE kind=? AND trigger_key LIKE 'daily:%' ORDER BY created DESC,rowid DESC LIMIT 1", (kind,))
            success = self.db.one("SELECT updated FROM jobs WHERE kind=? AND trigger_key LIKE 'daily:%' AND status='succeeded' ORDER BY updated DESC LIMIT 1", (kind,))
            result[kind] = {"latest": latest, "last_success": success["updated"] if success else None}
        return result

    def main_session(self):
        return self.db.one("SELECT * FROM sessions WHERE kind='main'")

    def search_sessions(self, query, recent=False, limit=None, offset=0, scope='title'):
        if not isinstance(query, str) or len(query.strip()) > 100:
            raise ValueError("请输入不超过 100 字的搜索内容")
        if scope not in {'title', 'all'}:
            raise ValueError("搜索范围无效")
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 100) or type(offset) is not int or offset < 0:
            raise ValueError("分页范围无效")
        if not query.strip() and not recent:
            return []
        query = query.strip()
        # Paginate sessions first; only selected message hits need an excerpt scan.
        sql = "SELECT s.id,s.title,s.display_abstract,s.kind,s.state,s.title_manual,s.goal_setup_json,s.created,s.updated,instr(lower(s.title),lower(?))>0 title_hit FROM sessions s WHERE s.state IN ('active','archived')"
        params = [query]
        if query:
            sql += " AND (instr(lower(s.title),lower(?))>0"
            params.append(query)
            if scope == 'all':
                sql += " OR EXISTS(SELECT 1 FROM messages m WHERE m.session_id=s.id AND m.role IN ('user','assistant') AND instr(lower(m.content),lower(?))>0)"
                params.append(query)
            sql += ')'
        sql += ' ORDER BY title_hit DESC,s.updated DESC,s.id'
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        rows = self.db.all(sql, params)
        for row in rows:
            hit = row.pop('title_hit')
            row.update(match_kind='title' if query and hit else 'recent', match_message_id=None, match_excerpt='')
            if query and not hit:
                message = self.db.one("SELECT id,content,instr(lower(content),lower(?)) hit FROM messages WHERE session_id=? AND role IN ('user','assistant') AND instr(lower(content),lower(?))>0 ORDER BY created DESC,id DESC LIMIT 1", (query, row['id'], query))
                if message:
                    start = max(0, message['hit'] - 61)
                    end = start + 158
                    excerpt = ('…' if start else '') + message['content'][start:end] + ('…' if len(message['content']) > end else '')
                    row.update(match_kind='message', match_message_id=message['id'], match_excerpt=excerpt)
        return rows

    def queue_session_meta(self, conn, session_id):
        session = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not session or session["kind"] == "main" or session["display_abstract"] or session["state"] == "deleting":
            return
        rows = conn.execute("SELECT role,content FROM messages WHERE session_id=? AND outbox_id IS NULL ORDER BY created,rowid", (session_id,)).fetchall()
        first = next((row["content"] for row in rows if row["role"] == "user"), None)
        answer = next((row["content"] for row in rows if row["role"] == "assistant"), None)
        if not first or not answer or conn.execute("SELECT id FROM jobs WHERE trigger_key=?", ("meta:" + session_id,)).fetchone():
            return
        self.insert_job(conn, kind="session_meta", session_id=session_id, trigger_key="meta:" + session_id,
                        text=packed({"user": first[:4000], "assistant": answer[:4000], "old_title": session["title"], "rename": not session["title_manual"] and session["title"] in {"新对话", first[:30]}}))

    def open_session(self, identity):
        row = self.require("sessions", identity)
        with self.db.transaction() as conn:
            self.queue_session_meta(conn, identity)
        return row

    def edit_session(self, identity, values):
        if set(values) - {"title", "operation"}:
            raise ValueError("不支持的对话字段")
        now = self.clock()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (identity,)).fetchone()
            if not row or row["state"] == "deleting":
                raise ValueError("对话不存在或正在删除")
            if row["kind"] == "main":
                raise ValueError("主对话不能重命名、封存或删除")
            if "title" in values:
                title = values["title"]
                if not isinstance(title, str) or not title.strip() or len(title.strip()) > 100:
                    raise ValueError("请填写 1 至 100 字的对话标题")
                conn.execute("UPDATE sessions SET title=?,title_manual=1,updated=? WHERE id=?", (title.strip(), now, identity))
            operation = values.get("operation")
            if operation:
                if operation not in {"archive", "unarchive"}:
                    raise ValueError("无效的对话操作")
                conn.execute("UPDATE sessions SET state=?,updated=? WHERE id=?", ("archived" if operation == "archive" else "active", now, identity))
            conn.execute("INSERT INTO audit(kind,data,created) VALUES('session.updated',?,?)", (packed({"session_id": identity, "operation": operation or "rename"}), now))
        return self.require("sessions", identity)

    def deletion_impact(self, identity):
        row = self.require("sessions", identity)
        if row["kind"] == "main":
            raise ValueError("主对话不能删除")
        schedules = self.db.all("SELECT s.id,s.kind,t.title,t.state FROM schedules s JOIN tasks t ON t.id=s.task_id WHERE t.session_id=? AND t.state NOT IN ('cancelled','completed')", (identity,))
        return {"session": row, "reminders": schedules, "running": self.db.all("SELECT id,kind,status FROM jobs WHERE session_id=? AND status IN ('pending','running')", (identity,))}

    def finish_session_deletion(self, conn, identity, now):
        conn.execute("DELETE FROM messages WHERE session_id=?", (identity,))
        conn.execute("UPDATE tasks SET session_id=NULL WHERE session_id=?", (identity,))
        conn.execute("UPDATE jobs SET session_id=NULL WHERE session_id=?", (identity,))
        conn.execute("DELETE FROM sessions WHERE id=? AND kind='secondary' AND state='deleting'", (identity,))
        conn.execute("INSERT INTO audit(kind,data,created) VALUES('session.deleted',?,?)", (packed({"session_id": identity}), now))

    async def delete_session(self, identity):
        now = self.clock()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (identity,)).fetchone()
            if not row:
                raise ValueError("对话不存在")
            if row["kind"] == "main":
                raise ValueError("主对话不能删除")
            conn.execute("UPDATE sessions SET state='deleting' WHERE id=?", (identity,))
            conn.execute("UPDATE schedules SET enabled=0,updated=? WHERE task_id IN (SELECT id FROM tasks WHERE session_id=?)", (now, identity))
            conn.execute("UPDATE tasks SET state='cancelled',updated=? WHERE session_id=? AND state<>'completed'", (now, identity))
            job_ids = [r["id"] for r in conn.execute("SELECT id FROM jobs WHERE session_id=? AND status IN ('pending','running')", (identity,))]
            conn.execute("UPDATE jobs SET status='cancelled',error='所属对话已删除',updated=? WHERE session_id=? AND status IN ('pending','running')", (now, identity))
            conn.execute("UPDATE outbox SET state='cancelled',error='所属对话已删除',updated=? WHERE state='pending' AND action_id IN (SELECT id FROM actions WHERE source_session_id=?)", (now, identity))
            conn.execute("UPDATE actions SET status='invalidated',basis='所属对话已删除，未执行动作失效',updated=? WHERE source_session_id=? AND status IN ('pending','approved')", (now, identity))
        # Await cancellation before removing the FK parent. Synchronous Delivery owns
        # its transaction through the entire irreversible write, so deletion waits.
        executions = [self.active[job_id] for job_id in job_ids if job_id in self.active]
        for execution in executions:
            execution.cancel()
        if executions:
            await asyncio.gather(*executions, return_exceptions=True)
        with self.db.transaction() as conn:
            self.finish_session_deletion(conn, identity, self.clock())
        for job_id in job_ids:
            await self.emit(job_id, "done", {"status": "cancelled"})
        return {"deleted": True, "cancelled_jobs": job_ids}

    def session(self, title="新对话"):
        if not isinstance(title, str) or not title.strip() or len(title) > 100:
            raise ValueError("请填写 1 至 100 字的对话标题")
        identity, now = uid(), self.clock()
        self.db.execute("INSERT INTO sessions(id,title,created,updated) VALUES(?,?,?,?)", (identity, title[:100], now, now))
        return self.require("sessions", identity)

    def record_detail(self, identity):
        row = self.require("records", identity)
        payload = json.loads(row["payload"])
        session_id = payload.get("session_id")
        if not session_id and (payload.get("source_job_id") or payload.get("job_id")):
            job = self.db.one("SELECT session_id FROM jobs WHERE id=?", (payload.get("source_job_id") or payload.get("job_id"),))
            session_id = job["session_id"] if job else None
        source = self.db.one("SELECT * FROM sessions WHERE id=? AND state<>'deleting'", (session_id,)) if session_id else None
        schedules, artifacts, timeline = [], [], []
        if row["kind"] == "goal":
            schedules = self.db.all("SELECT s.*,t.title,t.state FROM schedules s JOIN tasks t ON t.id=s.task_id WHERE t.goal_id=?", (identity,))
            artifacts = [{**item, "payload": json.loads(item["payload"])} for item in self.db.all("SELECT * FROM records WHERE kind='artifact' AND EXISTS(SELECT 1 FROM json_each(records.payload,'$.goal_ids') WHERE value=?) ORDER BY created DESC", (identity,))]
            timeline = self.db.all("SELECT a.* FROM audit a LEFT JOIN jobs j ON j.id=a.job_id LEFT JOIN tasks t ON t.id=j.task_id WHERE json_extract(a.data,'$.record_id')=? OR t.goal_id=? OR json_extract(a.data,'$.task_id') IN (SELECT id FROM tasks WHERE goal_id=?) ORDER BY a.id DESC", (identity, identity, identity))
        return {**row, "payload": payload, "source_session": source, "schedules": schedules, "artifacts": artifacts, "timeline": [{**event, "data": json.loads(event["data"])} for event in timeline]}

    def generate_feed(self):
        brief = self.settings.current()["feed_brief"]
        if not brief.strip():
            raise ValueError("先填写想持续了解的关注说明")
        with self.db.transaction() as conn:
            if conn.execute("SELECT id FROM jobs WHERE kind='feed' AND status IN ('pending','running')").fetchone():
                raise ValueError("关注内容正在生成")
            identity = self.insert_job(conn, kind="feed", text="按关注说明整理已核实的来源与本机事项，保留来源和时间；无相关资料时明确说明。\n关注说明：" + brief)
        return self.require("jobs", identity)

    def messages(self, session_id):
        if self.require("sessions", session_id)['state'] == 'deleting':
            raise ValueError('此话题已不可访问')
        return [{**row, "references": json.loads(row["references_json"]), "interaction": json.loads(row["interaction_json"])} for row in self.db.all("SELECT * FROM messages WHERE session_id=? ORDER BY created,rowid", (session_id,))]

    def job_config(self):
        config = self.settings.current()
        config["skill_instructions"] = self.skills.description(config["workflow"])
        return config

    def insert_job(self, conn, *, kind, text, session_id=None, task_id=None, trigger_key=None, now=None, config=None):
        identity, now = uid(), self.clock() if now is None else now
        conn.execute("INSERT INTO jobs(id,task_id,session_id,trigger_key,kind,status,input,next_run,config,created,updated) VALUES(?,?,?,?,?,'pending',?,?,?,?,?)",
                     (identity, task_id, session_id, trigger_key or f"manual:{identity}", kind, text, now, packed(config or self.job_config()), now, now))
        conn.execute("INSERT INTO audit(job_id,kind,data,created) VALUES(?,'job.queued',?,?)", (identity, packed({"task_id": task_id, "session_id": session_id, "trigger_key": trigger_key or f"manual:{identity}"}), now))
        return identity

    def chat(self, text, session_id=None, kind="chat", references=None, new_session=False):
        if not isinstance(text, str) or not text.strip() or len(text) > 20000:
            raise ValueError("消息长度应为 1 至 20000 个字符")
        if kind not in {"chat", "ideas", "draft", "compact", "connection"}:
            raise ValueError("无效的作业类型")
        references = references or []
        if not isinstance(references, list) or len(references) > 5:
            raise ValueError("最多引用 5 项内容")
        for reference in references:
            if not isinstance(reference, dict) or set(reference) - {"id", "kind", "title", "body", "source_url", "source_ids"} or not all(isinstance(v, str) for key, v in reference.items() if key != 'source_ids') or len(packed(reference)) > 20000:
                raise ValueError("引用内容无效或过长")
            if 'source_ids' in reference and (reference.get('kind') != 'feed' or not isinstance(reference['source_ids'], list) or len(reference['source_ids']) > 5 or not all(isinstance(value, str) for value in reference['source_ids'])):
                raise ValueError('动态引用关联无效')
        verified = []
        for reference in references:
            if reference.get('kind') == 'feed':
                row = self.db.one('SELECT * FROM records WHERE id=? AND ' + VISIBLE_FEED, (reference.get('id'),))
                if not row:
                    raise ValueError('动态引用已失效，请移除后重试；草稿仍保留')
                payload = json.loads(row['payload'])
                reference = {'id': row['id'], 'kind': 'feed', 'title': payload['title'], 'body': payload.get('body', ''), 'source_ids': payload.get('source_ids', []), **({'source_url': payload['source_url']} if payload.get('source_url') else {})}
            verified.append(reference)
        references = verified
        session_id = session_id or (self.session()["id"] if new_session else self.main_session()["id"])
        now = self.clock()
        with self.db.transaction() as conn:
            session = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not session or session["state"] != "active":
                raise ValueError("请先明确解封后继续此对话")
            setup = json.loads(session["goal_setup_json"])
            if setup.get("status") in {"gathering", "confirming"}:
                if conn.execute("SELECT id FROM jobs WHERE session_id=? AND kind='goal_setup' AND status IN ('pending','running')", (session_id,)).fetchone():
                    raise APIError("目标引导正在处理，请稍候")
                if re.fullmatch(r"(确认|保存(这个)?目标)[。！! ]*", text.strip()) and setup.get("status") == "confirming":
                    card = conn.execute("SELECT interaction_json FROM messages WHERE id=?", (setup["active_card_id"],)).fetchone()
                    choice = next(c for c in json.loads(card[0])["choices"] if c["operation"] == "save")
                    # The same persisted draft is saved through the choice API below.
                    return self.save_goal_draft(conn, session_id, setup, choice, now, as_job=True)
                setup.update(version=setup["version"] + 1, user_rounds=setup.get("user_rounds", 0) + 1, active_card_id=None)
                conn.execute("UPDATE sessions SET goal_setup_json=? WHERE id=?", (packed(setup), session_id))
                kind = "goal_setup"
            config = self.job_config()
            previous = conn.execute("SELECT m.role,j.input,j.config,j.status,j.id FROM messages m LEFT JOIN jobs j ON j.id=m.job_id WHERE m.session_id=? ORDER BY m.created DESC,m.rowid DESC LIMIT 1", (session_id,)).fetchone()
            time_reply = bool(re.fullmatch(r'[每明后天周星期早上午下晚间中午零一二三四五六七八九十点时分秒0-9 :：，,。]+', text.strip()))
            if (setup.get('followup') == 'reminder' and setup.get('followup_version') == setup.get('version') and time_reply
                    and previous and previous['role'] == 'assistant' and previous['status'] == 'succeeded'
                    and '先问我提醒时间' in (previous['input'] or '')
                    and json.loads(previous['config']).get('goal_id') == setup.get('saved_goal_id')
                    and (not setup.get('followup_job_id') or setup['followup_job_id'] == previous['id'])):
                config.update(goal_id=setup['saved_goal_id'], reminder_time_reply=True)
            if setup.get('followup'):
                for key in ('followup', 'followup_version', 'followup_job_id'):
                    setup.pop(key, None)
                conn.execute('UPDATE sessions SET goal_setup_json=? WHERE id=?', (packed(setup), session_id))
            identity = self.insert_job(conn, kind=kind, text=text.strip(), session_id=session_id, now=now, config=config)
            conn.execute("INSERT INTO messages(id,session_id,role,content,job_id,created,references_json) VALUES(?,?,?,?,?,?,?)", (uid(), session_id, "user", text.strip(), identity, now, packed(references)))
            conn.execute("UPDATE sessions SET updated=? WHERE id=?", (now, session_id))
        return self.require("jobs", identity)

    def record(self, kind, payload, identity=None):
        if kind not in RECORD_KINDS or not isinstance(payload, dict):
            raise ValueError("无效的条目")
        title = payload.get("title", "")
        if not isinstance(title, str) or not title.strip() or len(title) > 300:
            raise ValueError("请填写不超过 300 字的标题")
        if len(packed(payload)) > 100000:
            raise ValueError("条目内容过长")
        for field in ("body", "next_step", "source", "source_url"):
            if field in payload and not isinstance(payload[field], str):
                raise ValueError("条目文本字段无效")
        if payload.get("source_url") and not payload["source_url"].startswith(("https://", "http://")):
            raise ValueError("来源链接需要使用 http 或 https")
        if kind == "goal" and payload.get("state", "active") not in {"active", "paused", "completed", "cancelled"}:
            raise ValueError("无效的目标状态")
        if kind == "memory" and not isinstance(payload.get("enabled", True), bool):
            raise ValueError("记忆启用状态无效")
        previous = self.db.one("SELECT * FROM records WHERE id=?", (identity,)) if identity else None
        if identity:
            if not previous and kind != "feed":
                raise ValueError("条目不存在")
            if previous and previous["kind"] != kind:
                raise ValueError("条目类型不能修改")
        is_new = previous is None
        identity, now = identity or uid(), self.clock()
        if kind == "goal":
            payload.setdefault("abstract", title[:60])
            if previous and payload.get("state") in {"completed", "cancelled"}:
                return self.goal_state(identity, "complete" if payload["state"] == "completed" else "cancel", payload=payload)["goal"]
            if previous and json.loads(previous["payload"]).get("state") in {"completed", "cancelled"} and payload.get("state") != json.loads(previous["payload"]).get("state"):
                if json.loads(previous["payload"]).get("state") == "completed" and payload.get("state") == "active":
                    return self.goal_state(identity, "reactivate", payload=payload)["goal"]
                raise APIError("已取消目标不能恢复")
        with self.db.transaction() as conn:
            job_id = payload.get("source_job_id") or payload.get("job_id")
            if job_id and is_new:
                job = conn.execute("SELECT status,session_id FROM jobs WHERE id=?", (job_id,)).fetchone()
                if not job or job["status"] == "cancelled":
                    raise ValueError("所属工作已取消，不能保存迟到结果")
                if job["session_id"]:
                    payload["session_id"] = job["session_id"]
            conn.execute("INSERT INTO records VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated=excluded.updated", (identity, kind, packed(payload), now, now))
            if kind == "memory":
                conn.execute("UPDATE sessions SET summary='',summary_through=NULL")
        self.db.event("record.saved", {"record_id": identity, "kind": kind, "state": payload.get("state"), "next_step": payload.get("next_step")}, job_id=job_id, now=now)
        return {**self.require("records", identity), "payload": payload}

    def delete_record(self, identity):
        row = self.require("records", identity)
        if row["kind"] == "goal":
            return self.goal_state(identity, "cancel")
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM records WHERE id=?", (identity,))
            if row["kind"] == "memory":
                conn.execute("UPDATE sessions SET summary='',summary_through=NULL")
        self.db.event("record.deleted", {"record_id": identity, "kind": row["kind"]}, now=self.clock())

    def schedule(self, values):
        title, instruction = values.get("title", ""), values.get("instruction", "")
        kind = values.get("kind", "recurring")
        interval = values.get("interval_seconds", 3600)
        due = values.get("next_due", self.clock() + float(interval))
        if kind not in {"once", "recurring", "heartbeat"} or not isinstance(title, str) or not title.strip() or not isinstance(instruction, str) or not instruction.strip():
            raise ValueError("请填写任务标题、指令和有效类型")
        if len(title) > 300 or len(instruction) > 20000:
            raise ValueError("任务内容过长")
        if not isinstance(due, (int, float)) or not math.isfinite(due):
            raise ValueError("到期时间无效")
        if kind != "once" and (not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval < 1):
            raise ValueError("周期至少为 1 秒")
        session_id = values.get("session_id") or self.main_session()["id"]
        if values.get("goal_id"):
            goal = self.require("records", values["goal_id"])
            if goal["kind"] != "goal":
                raise ValueError("关联目标无效")
            if json.loads(goal["payload"]).get("state", "active") not in {"active", "paused"}:
                raise APIError("该目标已结束，不能添加关联提醒")
        identity, task_id, now = uid(), uid(), self.clock()
        with self.db.transaction() as conn:
            if kind == "heartbeat" and conn.execute("SELECT s.id FROM schedules s JOIN tasks t ON t.id=s.task_id WHERE s.kind='heartbeat' AND t.state IN ('active','paused')").fetchone():
                raise ValueError("每位用户使用一个 Heartbeat 检查任务。请编辑或取消已有 Heartbeat。")
            if values.get("goal_id"):
                current_goal = conn.execute("SELECT payload FROM records WHERE id=?", (values["goal_id"],)).fetchone()
                if not current_goal or json.loads(current_goal[0]).get("state", "active") not in {"active", "paused"}:
                    raise APIError("该目标已结束，不能添加关联提醒")
            session = conn.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not session or session["state"] != "active":
                raise ValueError("提醒归属对话不可用，请先解封")
            conn.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)", (task_id, title, instruction, kind, "active", session_id, values.get("goal_id"), now, now))
            conn.execute("INSERT INTO schedules(id,task_id,kind,interval_seconds,next_due,created,updated) VALUES(?,?,?,?,?,?,?)", (identity, task_id, kind, interval if kind != "once" else None, due, now, now))
        self.db.event("schedule.created", {"schedule_id": identity, "task_id": task_id, "kind": kind}, now=now)
        return self.require("schedules", identity)

    def edit_schedule(self, identity, values):
        row, now = self.require("schedules", identity), self.clock()
        task = self.require("tasks", row["task_id"])
        if task["state"] in {"cancelled", "completed"}:
            raise ValueError("已结束的任务不能恢复")
        if set(values) - {"title", "instruction", "interval_seconds", "next_due", "operation"}:
            raise ValueError("不支持的任务字段")
        operation = values.get("operation")
        if operation and operation not in {"pause", "resume", "cancel", "complete"}:
            raise ValueError("无效的任务操作")
        if operation in {"cancel", "complete"}:
            with self.db.transaction() as conn:
                ids = self.terminate_work(conn, [row["task_id"]], [], "completed" if operation == "complete" else "cancelled", "追踪已结束")
            self.stop_executions(ids)
            return self.require("schedules", identity)
        interval = values.get("interval_seconds", row["interval_seconds"])
        due = values.get("next_due", now + interval if operation == "resume" and interval else max(row["next_due"], now + 1) if operation == "resume" else row["next_due"])
        if row["kind"] != "once" and (not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval < 1):
            raise ValueError("周期至少为 1 秒")
        if not isinstance(due, (int, float)) or not math.isfinite(due):
            raise ValueError("到期时间无效")
        title, instruction = values.get("title", task["title"]), values.get("instruction", task["instruction"])
        if not isinstance(title, str) or not title.strip() or len(title) > 300 or not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 20000:
            raise ValueError("任务标题或指令无效")
        state = {"pause": "paused", "resume": "active", "cancel": "cancelled"}.get(operation, task["state"])
        enabled = int(state == "active")
        with self.db.transaction() as conn:
            conn.execute("UPDATE tasks SET title=?,instruction=?,state=?,updated=? WHERE id=?", (title, instruction, state, now, row["task_id"]))
            conn.execute("UPDATE schedules SET interval_seconds=?,next_due=?,enabled=?,updated=? WHERE id=?", (interval, due, enabled, now, identity))
        self.db.event("schedule.updated", {"schedule_id": identity, "task_id": row["task_id"], "operation": operation or "edit", "future_only": True}, now=now)
        return self.require("schedules", identity)

    def terminate_work(self, conn, task_ids, job_ids, state, reason):
        now = self.clock()
        related = set(job_ids)
        for identity in task_ids:
            conn.execute("UPDATE tasks SET state=?,updated=? WHERE id=? AND state NOT IN ('completed','cancelled')", (state, now, identity))
            conn.execute("UPDATE schedules SET enabled=0,updated=? WHERE task_id=?", (now, identity))
            related.update(row[0] for row in conn.execute("SELECT id FROM jobs WHERE task_id=?", (identity,)))
        for identity in related:
            conn.execute("UPDATE jobs SET status='cancelled',error=?,updated=? WHERE id=? AND status IN ('pending','running')", (reason, now, identity))
            conn.execute("UPDATE actions SET status='invalidated',basis=?,updated=? WHERE job_id=? AND status IN ('pending','approved')", (reason, now, identity))
            conn.execute("UPDATE outbox SET state='cancelled',error=?,updated=? WHERE state='pending' AND action_id IN (SELECT id FROM actions WHERE job_id=?)", (reason, now, identity))
        conn.execute("INSERT INTO audit(kind,data,created) VALUES('tracking.ended',?,?)", (packed({'task_ids': task_ids, 'job_ids': sorted(related), 'state': state}), now))
        return sorted(related)

    def stop_executions(self, identities):
        for identity in identities:
            execution = self.active.get(identity)
            if execution and not execution.done():
                execution.cancel()

    def goal_state(self, identity, operation, payload=None):
        if not isinstance(operation, str) or operation not in {'complete', 'cancel', 'reactivate'}:
            raise ValueError('目标操作无效')
        now, job_ids, tasks = self.clock(), [], []
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM records WHERE id=? AND kind='goal'", (identity,)).fetchone()
            if not row:
                raise APIError('目标不存在', 404)
            old = json.loads(row['payload'])
            current = {**old, **(payload or {})}
            if operation == 'reactivate':
                if old.get('state') != 'completed':
                    raise APIError('只有已完成目标可以恢复')
                current.update(state='active')
                current.pop('completed_at', None)
            else:
                target = 'completed' if operation == 'complete' else 'cancelled'
                if old.get('state') == 'cancelled' and target != 'cancelled':
                    raise APIError('已取消目标不能标记完成')
                tasks = [dict(r) for r in conn.execute("SELECT id,title,state FROM tasks WHERE goal_id=?", (identity,))]
                linked = [r[0] for r in conn.execute("SELECT id FROM jobs WHERE json_extract(config,'$.goal_id')=?", (identity,))]
                job_ids = self.terminate_work(conn, [r['id'] for r in tasks], linked, target, '关联目标已结束')
                current.update(state=target)
                current.setdefault('completed_at' if target == 'completed' else 'cancelled_at', now)
                for session in conn.execute("SELECT id,goal_setup_json FROM sessions WHERE json_extract(goal_setup_json,'$.saved_goal_id')=?", (identity,)).fetchall():
                    setup = json.loads(session['goal_setup_json'])
                    setup.update(status='closed', active_card_id=None, version=setup['version']+1)
                    setup.pop('followup', None)
                    conn.execute('UPDATE sessions SET goal_setup_json=? WHERE id=?', (packed(setup), session['id']))
            conn.execute("UPDATE records SET payload=?,updated=? WHERE id=?", (packed(current), now, identity))
            conn.execute("INSERT INTO audit(kind,data,created) VALUES('goal.state',?,?)", (packed({'record_id': identity, 'operation': operation, 'task_ids': [r['id'] for r in tasks]}), now))
        self.stop_executions(job_ids)
        return {'goal': self.record_detail(identity), 'affected_tasks': [r['id'] for r in tasks], 'cancelled_jobs': job_ids, 'ending': False}

    def cancel_tracking(self, identity):
        schedule = self.db.one('SELECT * FROM schedules WHERE id=? OR task_id=?', (identity, identity))
        if schedule:
            result = self.edit_schedule(schedule['id'], {'operation': 'cancel'})
            return {'status': 'cancelled', 'schedule': result}
        job = self.require('jobs', identity)
        if job['kind'] in {'feed','ideas','session_meta','goal_setup'}:
            raise APIError('请选择用户设置的追踪')
        with self.db.transaction() as conn:
            ids = self.terminate_work(conn, [], [identity], 'cancelled', '用户结束追踪')
        self.stop_executions(ids)
        return {'status': 'cancelled', 'job_id': identity}

    def start_goal(self, category, request_id):
        if not isinstance(category, str) or category not in CATEGORIES:
            raise ValueError('请选择有效的目标类别')
        self.validate_request_id(request_id)
        now = self.clock()
        with self.db.transaction() as conn:
            previous = conn.execute("SELECT * FROM jobs WHERE trigger_key=?", ('goal-start:' + request_id,)).fetchone()
            if previous:
                existing = conn.execute('SELECT goal_setup_json FROM sessions WHERE id=?', (previous['session_id'],)).fetchone()
                if not existing or json.loads(existing[0])['category'] != category:
                    raise APIError('此请求已用于另一个类别')
                return {'session_id': previous['session_id'], 'job_id': previous['id']}
            identity = uid()
            setup = {'category': category, 'step': 0, 'user_rounds': 0, 'version': 0, 'status': 'gathering', 'draft': None, 'active_card_id': None, 'receipts': {}}
            conn.execute("INSERT INTO sessions(id,title,created,updated,goal_setup_json) VALUES(?,'新对话',?,?,?)", (identity, now, now, packed(setup)))
            text = '我想设定一个' + CATEGORIES[category] + '目标，请用简短选项帮助我确定。明确确认后再保存。'
            job_id = self.insert_job(conn, kind='goal_setup', text=text, session_id=identity, trigger_key='goal-start:' + request_id)
            conn.execute("INSERT INTO messages(id,session_id,role,content,job_id,created) VALUES(?,?,'user',?,?,?)", (uid(), identity, text, job_id, now))
        return {'session_id': identity, 'job_id': job_id}

    def append_card(self, conn, session_id, setup, question, choices, job_id=None):
        identity, now = uid(), self.clock()
        card = {'type':'goal_choices', 'card_id':identity, 'setup_version':setup['version'], 'question':question,
                'choices':[{**choice, 'id':uid()} for choice in choices]}
        setup['active_card_id'] = identity
        conn.execute("INSERT INTO messages(id,session_id,role,content,job_id,created,interaction_json) VALUES(?,?,'assistant',?,?,?,?)", (identity, session_id, question, job_id, now, packed(card)))
        conn.execute("UPDATE sessions SET goal_setup_json=?,updated=? WHERE id=?", (packed(setup), now, session_id))
        return card

    def save_goal_draft(self, conn, session_id, setup, choice, now, as_job=False):
        if setup.get('status') != 'confirming' or not setup.get('draft'):
            raise APIError('当前没有可确认的目标草案')
        identity, card_id = uid(), setup['active_card_id']
        job_id = self.insert_job(conn, kind='goal_save', text='确认保存当前目标草案', session_id=session_id, trigger_key='goal-save:' + session_id)
        payload = {**setup['draft'], 'state':'active', 'session_id':session_id, 'source_job_id':job_id}
        conn.execute('INSERT INTO records VALUES(?,?,?,?,?)', (identity, 'goal', packed(payload), now, now))
        conn.execute("UPDATE jobs SET status='succeeded',result=?,updated=? WHERE id=?", ('目标已保存', now, job_id))
        conn.execute("INSERT INTO messages(id,session_id,role,content,job_id,created) VALUES(?,?,'user',?,?,?)", (uid(), session_id, '保存目标：' + payload['abstract'], job_id, now))
        setup.update(status='saved', saved_goal_id=identity, version=setup['version']+1)
        receipt = {'status':'saved', 'goal':{'id':identity, 'kind':'goal', 'payload':payload}, 'job_id':job_id, 'session_id':session_id}
        setup['receipts'][card_id] = {'choice_id':choice['id'], 'response':receipt}
        conn.execute("UPDATE messages SET interaction_json=json_set(interaction_json,'$.selected',?) WHERE id=?", (choice['id'],card_id))
        self.append_card(conn, session_id, setup, '目标已保存：' + payload['abstract'] + '。接下来想补充什么？', [
            {'label':'补行动步骤','value':'请为已保存目标补充行动步骤。','operation':'followup'},
            {'label':'设置站内提醒','value':'请为已保存的这个目标设置站内提醒。先问我提醒时间。','operation':'followup'},
            {'label':'暂不补充','value':'暂不补充','operation':'close'}],job_id)
        conn.execute("INSERT INTO audit(job_id,kind,data,created) VALUES(?,'record.saved',?,?)", (job_id,packed({'record_id':identity,'kind':'goal','state':'active'}),now))
        return dict(conn.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()) if as_job else receipt

    def goal_choice(self, session_id, values):
        if set(values) != {'card_id','choice_id','setup_version'} or type(values.get('setup_version')) is not int or any(not isinstance(values.get(key), str) or not 1 <= len(values[key]) <= 100 for key in ('card_id','choice_id')):
            raise ValueError('目标选项请求字段无效')
        now = self.clock()
        with self.db.transaction() as conn:
            row = conn.execute('SELECT * FROM sessions WHERE id=?',(session_id,)).fetchone()
            if not row:
                raise APIError('对话不存在',404)
            if row['state'] != 'active':
                raise APIError('请先解封此对话')
            setup = json.loads(row['goal_setup_json'])
            receipt = setup.get('receipts',{}).get(values['card_id'])
            if receipt:
                if receipt['choice_id'] == values['choice_id']:
                    return receipt['response']
                raise APIError('这个步骤已选择另一项，请使用当前选项')
            if setup.get('active_card_id') != values['card_id'] or setup.get('version') != values['setup_version']:
                raise APIError('目标步骤已更新，请使用当前选项')
            message = conn.execute('SELECT interaction_json FROM messages WHERE id=? AND session_id=?',(values['card_id'],session_id)).fetchone()
            if not message:
                raise APIError('选项不存在',404)
            card = json.loads(message[0])
            choice = next((c for c in card['choices'] if c['id'] == values['choice_id']),None)
            if not choice:
                raise ValueError('无效的目标选项')
            if choice['operation'] == 'save':
                return self.save_goal_draft(conn,session_id,setup,choice,now)
            conn.execute("UPDATE messages SET interaction_json=json_set(interaction_json,'$.selected',?) WHERE id=?",(choice['id'],values['card_id']))
            setup.update(version=setup['version']+1,active_card_id=None)
            conn.execute("INSERT INTO messages(id,session_id,role,content,created) VALUES(?,?,'user',?,?)",(uid(),session_id,choice['label'],now))
            if choice['operation'] == 'close':
                setup['status']='closed'
                response={'status':'closed'}
                conn.execute("INSERT INTO messages(id,session_id,role,content,created) VALUES(?,?,'assistant',?,?)",(uid(),session_id,'这次引导已结束。',now))
            else:
                kind='chat' if choice['operation']=='followup' else 'goal_setup'
                if kind=='goal_setup':
                    setup.update(status='gathering',user_rounds=setup.get('user_rounds',0)+1)
                config=self.job_config()
                if setup.get('saved_goal_id'):
                    config['goal_id']=setup['saved_goal_id']
                if choice['operation']=='followup' and '提醒' in choice['value']:
                    setup['followup']='reminder'
                    setup['followup_version']=setup['version']
                job_id=self.insert_job(conn,kind=kind,text=choice['value'],session_id=session_id,trigger_key='goal-choice:'+values['card_id'],config=config)
                if setup.get('followup') == 'reminder':
                    setup['followup_job_id']=job_id
                response={'status':'queued','job_id':job_id,'session_id':session_id}
                conn.execute('UPDATE messages SET job_id=? WHERE id=(SELECT id FROM messages WHERE session_id=? ORDER BY created DESC,rowid DESC LIMIT 1)',(job_id,session_id))
            setup['receipts'][values['card_id']]={'choice_id':choice['id'],'response':response}
            conn.execute('UPDATE sessions SET goal_setup_json=?,updated=? WHERE id=?',(packed(setup),now,session_id))
        return response

    def publish_goal_card(self, conn, job, result):
        row=conn.execute('SELECT goal_setup_json,state FROM sessions WHERE id=?',(job['session_id'],)).fetchone()
        if not row or row['state']=='deleting':
            return
        setup=json.loads(row['goal_setup_json'])
        if setup.get('version') != result['setup_version']:
            raise APIError('目标草案已变化，忽略旧结果')
        setup['step']=setup.get('step',0)+1
        if result['stage']=='confirm':
            setup.update(status='confirming',draft=result['draft'])
            question='目标草案：'+result['draft']['abstract']
            choices=[{'label':'保存目标','value':'保存目标','operation':'save'}, {'label':'调整一下','value':'我想调整这份目标，请只问一个需要调整的细节。','operation':'answer'}, {'label':'先不保存','value':'先不保存','operation':'close'}]
        elif result['stage']=='closed':
            setup.update(status='closed',active_card_id=None)
            conn.execute('UPDATE sessions SET goal_setup_json=? WHERE id=?',(packed(setup),job['session_id']))
            conn.execute("INSERT INTO messages(id,session_id,role,content,job_id,created) VALUES(?,?,'assistant',?,?,?)",(uid(),job['session_id'],'这次目标引导已结束，尚未保存目标。',job['id'],self.clock()))
            return
        else:
            setup['status']='gathering'
            question=result['question']
            choices=[{'label':c['label'],'value':c['value'],'operation':'answer'} for c in result['choices']]
        self.append_card(conn,job['session_id'],setup,question,choices,job['id'])
        self.queue_session_meta(conn,job['session_id'])

    def supersede_daily(self, conn, date, now):
        rows = conn.execute("SELECT id FROM jobs WHERE trigger_key LIKE 'daily:%' AND substr(trigger_key,-10)<>? AND status='pending'", (date,)).fetchall()
        for row in rows:
            conn.execute("UPDATE jobs SET status='cancelled',error='已由最近日期的更新接替',error_code='superseded_daily',updated=? WHERE id=?", (now, row['id']))
            conn.execute("INSERT INTO audit(job_id,kind,data,created) VALUES(?,'superseded_daily','{}',?)", (row['id'], now))

    def content_call_available(self, conn, job, config):
        calls = conn.execute('SELECT COUNT(*) FROM usage WHERE job_id=?', (job['id'],)).fetchone()[0]
        tools = conn.execute('SELECT COUNT(*) FROM tool_calls WHERE job_id=?', (job['id'],)).fetchone()[0]
        return calls < config['max_model_calls'] and tools < config['max_tool_calls']

    def daily_budget_available(self, conn, config):
        usage = conn.execute('SELECT COUNT(*),COALESCE(SUM(input_tokens+output_tokens),0) FROM usage WHERE created>=?', (self.day_start(),)).fetchone()
        return (not config['daily_requests'] or usage[0] < config['daily_requests']) and (not config['daily_tokens'] or usage[1] + config['max_output_tokens'] < config['daily_tokens'])

    def restore_daily(self, budget_changed=False, migration=False):
        now, date = self.clock(), self.content_due().date().isoformat()
        settings = self.settings.current()
        with self.db.transaction() as conn:
            self.supersede_daily(conn, date, now)
            rows = conn.execute("SELECT * FROM jobs WHERE trigger_key IN (?,?) AND status IN ('failed','timed_out')", (f'daily:feed:{date}', f'daily:ideas:{date}')).fetchall()
            for row in rows:
                config = json.loads(row['config'])
                config.update({key: settings[key] for key in ('daily_requests', 'daily_tokens')})
                reason = 'daily_budget_changed' if budget_changed and row['error_code'] == 'daily_budget_exhausted' else 'migration_transport_recovery' if migration and row['error_code'] == 'model_transport_error' and row['recovery_count'] < 2 else ''
                if not reason or not self.content_call_available(conn, row, config) or not self.daily_budget_available(conn, config):
                    continue
                conn.execute("UPDATE jobs SET status='pending',config=?,deadline=NULL,next_run=?,updated=? WHERE id=?", (packed(config), now, now, row['id']))
                conn.execute("INSERT INTO audit(job_id,kind,data,created) VALUES(?,'content.recovery_queued',?,?)", (row['id'], packed({'reason': reason, 'next_run': now}), now))

    def delay_content_recovery(self, conn, job, config, code, now):
        if (job['trigger_key'] != f"daily:{job['kind']}:{self.content_due().date().isoformat()}" or code not in TRANSIENT_CONTENT_ERRORS
                or job['recovery_count'] >= 2 or not self.content_call_available(conn, job, config)):
            return None
        next_run = now + (600 if job['recovery_count'] == 0 else 1800)
        conn.execute("INSERT INTO audit(job_id,kind,data,created) VALUES(?,'content.recovery_queued',?,?)", (job['id'], packed({'reason': code, 'next_run': next_run, 'recovery_count': job['recovery_count']}), now))
        return next_run

    def tick(self):
        now = self.clock()
        if self._last_tick is None:
            self._awake_since = now
        if self._last_tick is not None and now - self._last_tick > 30:
            self._awake_since = now
            for identity, execution in tuple(self.active.items()):
                row = self.require('jobs', identity)
                if row['kind'] in {'feed', 'ideas'} and row['trigger_key'].startswith('daily:') and row['deadline'] and row['deadline'] <= now:
                    self._sleep_interrupted.add(identity)
                    execution.cancel()
        self._last_tick = now
        with self.db.transaction() as conn:
            due = self.content_due()
            self.supersede_daily(conn, due.date().isoformat(), now)
            for kind in ("feed", "ideas"):
                trigger = f"daily:{kind}:{due.date().isoformat()}"
                if not conn.execute("SELECT id FROM jobs WHERE trigger_key=?", (trigger,)).fetchone():
                    config = {**self.job_config(), "content_date": due.date().isoformat(), "content_due": due.timestamp()}
                    self.insert_job(conn, kind=kind, text=packed({"content_date": config["content_date"], "timezone": config["timezone"], "brief": config["feed_brief"]}), trigger_key=trigger, now=now, config=config)
            rows = conn.execute("SELECT s.*,t.instruction,t.session_id FROM schedules s JOIN tasks t ON t.id=s.task_id WHERE s.enabled=1 AND t.state='active' AND s.next_due<=?", (now,)).fetchall()
            for row in rows:
                interval = row["interval_seconds"]
                missed = int((now - row["next_due"]) // interval) if interval else 0
                latest_due = row["next_due"] + missed * interval if interval else row["next_due"]
                trigger = f"schedule:{row['id']}:{latest_due:.6f}"
                try:
                    identity = self.insert_job(conn, kind=row["kind"], text=row["instruction"], task_id=row["task_id"], session_id=row["session_id"], trigger_key=trigger, now=now)
                except sqlite3.IntegrityError:
                    identity = None
                conn.execute("UPDATE schedules SET next_due=?,enabled=?,updated=? WHERE id=?", (latest_due + interval if interval else latest_due, int(bool(interval)), now, row["id"]))
                conn.execute("INSERT INTO audit(job_id,kind,data,created) VALUES(?,'schedule.triggered',?,?)", (identity, packed({"schedule_id": row["id"], "due": latest_due, "missed_intervals": missed, "missed_from": row["next_due"] if missed else None}), now))

    def publish_content(self, conn, job, result, now):
        if job["kind"] == "ideas":
            conn.execute("UPDATE records SET payload=json_set(payload,'$.state','superseded') WHERE kind='idea' AND COALESCE(json_extract(payload,'$.state'),'active')='active'")
        elif result['items']:
            conn.execute("UPDATE records SET payload=json_set(payload,'$.display_state','hidden_legacy') WHERE kind='feed' AND json_extract(payload,'$.display_state')='legacy'")
        for item in result["items"]:
            payload = {**item, "job_id": job["id"], "generated_at": now, "content_date": json.loads(job["config"]).get("content_date"), "origin": "generated"}
            if job["kind"] == "ideas":
                payload.update(batch_id=job["id"], state="active")
            conn.execute("INSERT INTO records VALUES(?,?,?,?,?)", (uid(), "idea" if job["kind"] == "ideas" else "feed", packed(payload), now, now))
        conn.execute("INSERT INTO audit(job_id,kind,data,created) VALUES(?,'content.published',?,?)", (job["id"], packed({"kind": job["kind"], "count": len(result["items"]), "source_errors": result.get("source_errors", []), "reason": result.get("reason", "")}), now))

    def start_idea(self, identity, request_id):
        self.validate_request_id(request_id)
        now = self.clock()
        with self.db.transaction() as conn:
            previous = conn.execute("SELECT id,session_id,input FROM jobs WHERE trigger_key=?", ("idea-start:" + request_id,)).fetchone()
            row = conn.execute("SELECT * FROM records WHERE id=? AND kind='idea'", (identity,)).fetchone()
            if previous:
                if not row or identity not in previous["input"]:
                    raise APIError("此请求已经用于另一条构想")
                session = conn.execute("SELECT state FROM sessions WHERE id=?", (previous['session_id'],)).fetchone()
                if not session or session['state'] == 'deleting':
                    raise APIError("原构想对话已删除，请重新开始", 410)
                return {"session_id": previous["session_id"], "job_id": previous["id"]}
            if not row:
                raise APIError("构想不存在", 404)
            payload = json.loads(row["payload"])
            if payload.get("state", "active") != "active" or payload.get("action_kind") not in IDEA_ACTIONS:
                raise APIError("此构想已被新批次替换，请重新选择")
            session_id = uid()
            conn.execute("INSERT INTO sessions(id,title,created,updated) VALUES(?,'新对话',?,?)", (session_id, now, now))
            text = "现在开始这条构想，请直接交付首份可用结果；缺必要输入时只问一个问题。仅授权此工作，保存目标、提醒及导出仍需相应确认。\n" + packed({"idea_id": identity, "action_kind": payload["action_kind"], "title": payload["title"], "body": payload["body"], "includes": payload["includes"], "how_it_works": payload["how_it_works"], "context_refs": payload["context_refs"]})
            job_id = self.insert_job(conn, kind="chat", text=text, session_id=session_id, trigger_key="idea-start:" + request_id)
            conn.execute("INSERT INTO messages(id,session_id,role,content,job_id,created) VALUES(?,?,'user',?,?,?)", (uid(), session_id, text, job_id, now))
        return {"session_id": session_id, "job_id": job_id}

    def validate_request_id(self, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", request_id):
            raise ValueError("请求标识无效")

    def propose(self, job_id, kind, title, target, body):
        if kind not in {"notification", "export"}:
            raise ValueError("当前仅支持站内提醒和审批后的本机文档导出")
        if not all(isinstance(value, str) and value.strip() for value in (title, target, body)) or len(body) > 100000:
            raise ValueError("动作内容无效")
        if kind == "notification" and target != "inbox":
            raise ValueError("普通提醒只能投递到站内消息")
        if kind == "export" and not re.fullmatch(r"[\w\- ]{1,80}\.(md|txt)", target):
            raise ValueError("导出文件名仅允许文字、数字、空格，及 md 或 txt 后缀")
        digest, now = content_hash(kind, title, target, body), self.clock()
        legacy_digest = hashlib.sha256(packed([kind, title, target, body]).encode()).hexdigest()
        identity = uid()
        with self.db.transaction() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job or job["status"] == "cancelled":
                raise ValueError("所属工作已取消，不能提出行动")
            # Existing approvals retain their pre-migration hash. Retried old work
            # must find that same action, including an edited original version.
            previous = conn.execute("SELECT a.* FROM actions a WHERE a.job_id=? AND (a.content_hash=? OR (a.version=1 AND a.content_hash=?) OR EXISTS(SELECT 1 FROM action_versions v WHERE v.action_id=a.id AND (json_extract(v.payload,'$.content_hash')=? OR (v.version=1 AND json_extract(v.payload,'$.content_hash')=?))))", (job_id, digest, legacy_digest, digest, legacy_digest)).fetchone()
            if previous:
                return dict(previous)
            conn.execute("INSERT INTO actions(id,job_id,kind,title,target,body,status,content_hash,basis,created,updated,source_session_id) VALUES(?,?,?,?,?,?,'pending',?,'',?,?,?)", (identity, job_id, kind, title, target, body, digest, now, now, job["session_id"]))
        self.db.event("action.proposed", {"action_id": identity, "content_hash": digest, "kind": kind}, job_id, now)
        job = self.require("jobs", job_id)
        if kind == "notification" and json.loads(job["config"])["notification_policy"] == "daily":
            self.decide(identity, "approve", digest, actor="policy", basis="用户已授权普通站内提醒自动发送；日常策略")
        return self.require("actions", identity)

    def edit_action(self, identity, values):
        row = self.require("actions", identity)
        if row["status"] != "pending":
            raise ValueError("仅可编辑待审批动作")
        if set(values) - {"title", "target", "body"}:
            raise ValueError("不支持的动作字段")
        title, target, body = [values.get(key, row[key]) for key in ("title", "target", "body")]
        if not all(isinstance(value, str) and value.strip() for value in (title, target, body)) or len(body) > 100000:
            raise ValueError("动作内容无效")
        if row["kind"] == "notification" and target != "inbox" or row["kind"] == "export" and not re.fullmatch(r"[\w\- ]{1,80}\.(md|txt)", target):
            raise ValueError("动作目标无效")
        digest = content_hash(row["kind"], title, target, body, row["version"] + 1)
        with self.db.transaction() as conn:
            changed = conn.execute("UPDATE actions SET title=?,target=?,body=?,content_hash=?,version=version+1,updated=? WHERE id=? AND status='pending' AND content_hash=?", (title, target, body, digest, self.clock(), identity, row["content_hash"])).rowcount
            if not changed:
                raise ValueError("动作已经变化，请刷新")
            conn.execute("INSERT INTO action_versions VALUES(?,?,?,?)", (identity, row["version"], packed(row), self.clock()))
        self.db.event("action.edited", {"action_id": identity, "content_hash": digest}, row["job_id"], self.clock())
        return self.require("actions", identity)

    def decide(self, identity, decision, digest, *, actor="local-user", basis="用户逐条审批"):
        if decision not in {"approve", "reject"}:
            raise ValueError("审批决定无效")
        now = self.clock()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM actions WHERE id=?", (identity,)).fetchone()
            if not row or row["status"] != "pending" or row["content_hash"] != digest:
                raise ValueError("动作内容或状态已经变化，请刷新后审批")
            status = "approved" if decision == "approve" else "rejected"
            conn.execute("UPDATE actions SET status=?,basis=?,updated=? WHERE id=?", (status, basis, now, identity))
            approval_id = uid()
            conn.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?,?)", (approval_id, identity, decision, digest, actor, basis, now))
            outbox_id = None
            if decision == "approve":
                outbox_id = uid()
                payload = {key: row[key] for key in ("id", "job_id", "kind", "title", "target", "body", "content_hash")}
                payload.update(source_session_id=row["source_session_id"], version=row["version"])
                conn.execute("INSERT INTO outbox VALUES(?,?,?,'pending','',?,?)", (outbox_id, identity, packed(payload), now, now))
            conn.execute("INSERT INTO audit(job_id,kind,data,created) VALUES(?,'action.decided',?,?)", (row["job_id"], packed({"action_id": identity, "approval_id": approval_id, "outbox_id": outbox_id, "decision": decision, "content_hash": digest, "basis": basis}), now))
        return self.require("actions", identity)

    def deliver(self):
        for row in self.db.all("SELECT * FROM outbox WHERE state='pending' ORDER BY created"):
            payload, now = json.loads(row["payload"]), self.clock()
            try:
                with self.db.transaction() as conn:
                    action = conn.execute("SELECT * FROM actions WHERE id=?", (row["action_id"],)).fetchone()
                    box = conn.execute("SELECT * FROM outbox WHERE id=?", (row["id"],)).fetchone()
                    approval = conn.execute("SELECT id FROM approvals WHERE action_id=? AND decision='approve' AND content_hash=?", (row["action_id"], payload["content_hash"])).fetchone()
                    if box["state"] != "pending":
                        continue
                    if not approval or action["status"] != "approved" or action["content_hash"] != payload["content_hash"]:
                        raise ValueError("批准记录与投递内容不一致")
                    target = "inbox"
                    if payload["kind"] == "notification":
                        conn.execute("INSERT INTO notifications VALUES(?,?,?, ?,1,?)", (uid(), row["id"], payload["title"], payload["body"], now))
                        origin_id = action["source_session_id"]
                        if origin_id:
                            origin = conn.execute("SELECT * FROM sessions WHERE id=?", (origin_id,)).fetchone()
                            if not origin or origin["state"] == "deleting":
                                raise ValueError("提醒归属对话已删除，不能投递")
                            destination = conn.execute("SELECT id FROM sessions WHERE kind='main'").fetchone()["id"] if origin["state"] == "archived" else origin_id
                            forwarded = f"来源：已封存话题「{origin['title']}」\n\n" if origin["state"] == "archived" else ""
                            content = forwarded + payload["title"] + "\n\n" + payload["body"]
                            conn.execute("INSERT INTO messages(id,session_id,role,content,job_id,created,outbox_id,origin_session_id,unread) VALUES(?,?,'assistant',?,?,?,?,?,1)", (uid(), destination, content, payload["job_id"], now, row["id"], origin_id))
                            conn.execute("UPDATE sessions SET updated=? WHERE id=?", (now, destination))
                            target = destination
                    else:
                        folder = self.directory / "exports"
                        folder.mkdir(exist_ok=True)
                        path = folder / f"{payload['id']}_{payload['target']}"
                        # A crash after file creation can be reconciled by its immutable ID and exact bytes.
                        if path.exists():
                            if path.read_text() != payload["body"]:
                                raise ValueError("导出文件存在且内容不同，需要人工核查")
                        else:
                            with path.open("x") as stream:
                                stream.write(payload["body"])
                        target = path.name
                        artifact = {"title": payload["title"], "body": payload["body"], "filename": target, "job_id": payload["job_id"], "action_id": payload["id"]}
                        if action["source_session_id"]:
                            artifact["session_id"] = action["source_session_id"]
                        draft = conn.execute("SELECT payload FROM records WHERE kind='artifact' AND json_extract(payload,'$.job_id')=? LIMIT 1", (payload["job_id"],)).fetchone()
                        if draft:
                            artifact["goal_ids"] = json.loads(draft["payload"]).get("goal_ids", [])
                        linked = conn.execute("SELECT t.goal_id FROM tasks t JOIN jobs j ON j.task_id=t.id WHERE j.id=?", (payload["job_id"],)).fetchone()
                        if linked and linked["goal_id"]:
                            artifact["goal_ids"] = list(set(artifact.get("goal_ids", []) + [linked["goal_id"]]))
                        conn.execute("INSERT OR IGNORE INTO records VALUES(?,?,?,?,?)", (f"artifact-{payload['id']}", "artifact", packed(artifact), now, now))
                    delivery_id = uid()
                    conn.execute("INSERT INTO deliveries VALUES(?,?,?,?,?)", (delivery_id, row["id"], row["action_id"], target, now))
                    conn.execute("UPDATE outbox SET state='delivered',updated=? WHERE id=?", (now, row["id"]))
                    conn.execute("UPDATE actions SET status='delivered',updated=? WHERE id=?", (now, row["action_id"]))
                    conn.execute("INSERT INTO audit(job_id,kind,data,created) VALUES(?,'delivery.completed',?,?)", (payload["job_id"], packed({"action_id": row["action_id"], "outbox_id": row["id"], "delivery_id": delivery_id, "target": target}), now))
            except (OSError, ValueError) as error:
                self.db.execute("UPDATE outbox SET state='uncertain',error=?,updated=? WHERE id=?", (str(error), now, row["id"]))
                self.db.event("delivery.uncertain", {"outbox_id": row["id"], "reason": str(error)}, payload["job_id"], now)

    def audit(self, job_id=None):
        where, params = ("WHERE job_id=?", (job_id,)) if job_id else ("", ())
        events = self.db.all(f"SELECT * FROM audit {where} ORDER BY id DESC", params)
        return [{**row, "data": json.loads(row["data"])} for row in events]

    def action_history(self, identity):
        current = self.require("actions", identity)
        versions = [json.loads(row["payload"]) for row in self.db.all("SELECT payload FROM action_versions WHERE action_id=? ORDER BY version", (identity,))]
        return {"versions": [*versions, current],
                "approvals": self.db.all("SELECT * FROM approvals WHERE action_id=? ORDER BY created", (identity,)),
                "outbox": self.db.all("SELECT * FROM outbox WHERE action_id=?", (identity,)),
                "deliveries": self.db.all("SELECT * FROM deliveries WHERE action_id=?", (identity,))}

    def evidence(self, job_id):
        job = self.require("jobs", job_id)
        job["config"] = json.loads(job["config"])
        actions = self.db.all("SELECT * FROM actions WHERE job_id=?", (job_id,))
        return {"evidence_type": "live" if isinstance(self.model, DeepSeek) else "offline-test-double", "user_id": "local-user", "job": job,
                "attempts": self.db.all("SELECT * FROM attempts WHERE job_id=? ORDER BY number", (job_id,)),
                "tools": [{**row, "arguments": json.loads(row["arguments"]), "result": json.loads(row["result"])} for row in self.db.all("SELECT * FROM tool_calls WHERE job_id=?", (job_id,))],
                "actions": actions, "approvals": self.db.all("SELECT a.* FROM approvals a JOIN actions b ON a.action_id=b.id WHERE b.job_id=?", (job_id,)),
                "outbox": self.db.all("SELECT o.* FROM outbox o JOIN actions a ON o.action_id=a.id WHERE a.job_id=?", (job_id,)),
                "deliveries": self.db.all("SELECT d.* FROM deliveries d JOIN actions a ON d.action_id=a.id WHERE a.job_id=?", (job_id,)),
                "usage": self.db.all("SELECT * FROM usage WHERE job_id=?", (job_id,)), "audit": self.audit(job_id)}

    def day_start(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        local = datetime.fromtimestamp(self.clock(), ZoneInfo(self.settings.current()["timezone"]))
        return local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def reserve_call(self, job_id, input_size, config):
        identity = uid()
        # UTF-8 byte length is a conservative bound until a provider usage receipt arrives.
        reservation = max(1, input_size)
        with self.db.transaction() as conn:
            usage = conn.execute("SELECT COUNT(*) requests,COALESCE(SUM(input_tokens+output_tokens),0) tokens FROM usage WHERE created>=?", (self.day_start(),)).fetchone()
            count = conn.execute("SELECT COUNT(*) FROM usage WHERE job_id=?", (job_id,)).fetchone()[0]
            if count >= config["max_model_calls"]:
                raise AgentError("已达到单作业模型调用上限", code='model_call_limit')
            if config["daily_requests"] and usage["requests"] >= config["daily_requests"] or config["daily_tokens"] and usage["tokens"] + reservation + config["max_output_tokens"] > config["daily_tokens"]:
                raise AgentError("已达到每日调用预算，请在设置中查看或调整限制", code='daily_budget_exhausted')
            conn.execute("INSERT INTO usage VALUES(?,?,?,?,1,?)", (identity, job_id, reservation, config["max_output_tokens"], self.clock()))
        return identity

    def finish_usage(self, identity, reply):
        if reply.input_tokens > 0 and reply.output_tokens > 0:
            self.db.execute("UPDATE usage SET input_tokens=?,output_tokens=?,estimated=0 WHERE id=?", (reply.input_tokens, reply.output_tokens, identity))

    async def emit(self, job_id, event, data):
        for queue in tuple(self.listeners.get(job_id, ())):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait({"event": event, "data": data})

    async def cancel(self, job_id):
        row = self.require("jobs", job_id)
        if row["status"] in TERMINAL:
            return row
        with self.db.transaction() as conn:
            self.terminate_work(conn, [], [job_id], 'cancelled', '用户取消')
        self.db.event("job.cancelled", {}, job_id, self.clock())
        if job_id in self.active:
            self.active[job_id].cancel()
        await self.emit(job_id, "done", {"status": "cancelled"})
        return self.require("jobs", job_id)

    def recover(self):
        now = self.clock()
        with self.db.transaction() as conn:
            for session in conn.execute("SELECT id FROM sessions WHERE state='deleting'").fetchall():
                self.finish_session_deletion(conn, session["id"], now)
            conn.execute("UPDATE attempts SET status='interrupted',ended=?,error='Gateway 重新启动' WHERE status='running'", (now,))
            rows = conn.execute("SELECT * FROM jobs WHERE status='running'").fetchall()
            for row in rows:
                if row['trigger_key'].startswith('daily:') and row['kind'] in {'feed', 'ideas'}:
                    config = json.loads(row['config'])
                    next_run = self.delay_content_recovery(conn, row, config, 'sleep_interrupted', now)
                    conn.execute("UPDATE jobs SET status=?,error='Gateway 中断，清醒后补更新',error_code='sleep_interrupted',deadline=NULL,next_run=?,updated=? WHERE id=?", ('pending' if next_run else 'failed', next_run or now, now, row['id']))
                    conn.execute("INSERT INTO audit(job_id,kind,data,created) VALUES(?,'job.recovered',?,?)", (row['id'], packed({'resumed': bool(next_run), 'recovery_count': row['recovery_count']}), now))
                    continue
                exhausted = row["attempt"] >= 3 or row["deadline"] is not None and row["deadline"] <= now
                conn.execute("UPDATE jobs SET status=?,error=?,next_run=?,updated=? WHERE id=?", ("failed" if exhausted else "pending", "Gateway 中断后恢复" if not exhausted else "恢复时执行期限或尝试次数已用尽", now, now, row["id"]))
                conn.execute("INSERT INTO audit(job_id,kind,data,created) VALUES(?,'job.recovered',?,?)", (row["id"], packed({"resumed": not exhausted}), now))

    async def run_one(self):
        from .agent import Agent
        now = self.clock()
        with self.db.transaction() as conn:
            self.supersede_daily(conn, self.content_due().date().isoformat(), now)
            awake = self._last_tick is not None and now - self._last_tick <= 30 and now - self._awake_since >= 60
            row = conn.execute("SELECT * FROM jobs WHERE status='pending' AND next_run<=? AND (trigger_key NOT LIKE 'daily:%' OR ?) ORDER BY kind IN ('session_meta','feed','ideas'),created,rowid LIMIT 1", (now, awake)).fetchone()
            if not row:
                return False
            job = dict(row)
            config = json.loads(job["config"])
            automatic = job['trigger_key'].startswith('daily:') and job['kind'] in {'feed', 'ideas'}
            if automatic and (not self.content_call_available(conn, job, config) or not self.daily_budget_available(conn, config)):
                code = 'model_call_limit' if not self.content_call_available(conn, job, config) else 'daily_budget_exhausted'
                conn.execute("UPDATE jobs SET status='failed',error=?,error_code=?,updated=? WHERE id=?", ('已达到单作业调用上限' if code == 'model_call_limit' else '已达到每日调用预算，请在设置中查看或调整限制', code, now, job['id']))
                return True
            if automatic and job['deadline'] is None and job['error_code'] in TRANSIENT_CONTENT_ERRORS:
                if job['recovery_count'] >= 2:
                    conn.execute("UPDATE jobs SET status='failed',updated=? WHERE id=?", (now, job['id']))
                    return True
                job['recovery_count'] += 1
                conn.execute('UPDATE jobs SET recovery_count=? WHERE id=?', (job['recovery_count'], job['id']))
                conn.execute("INSERT INTO audit(job_id,kind,data,created) VALUES(?,'content.recovery_started',?,?)", (job['id'], packed({'recovery_count': job['recovery_count']}), now))
            deadline = job["deadline"] or now + config["job_timeout"]
            attempt_id = uid()
            conn.execute("UPDATE jobs SET status='running',attempt=attempt+1,deadline=?,updated=? WHERE id=?", (deadline, now, job["id"]))
            conn.execute("INSERT INTO attempts(id,job_id,number,status,started) VALUES(?,?,?,'running',?)", (attempt_id, job["id"], job["attempt"] + 1, now))
        self.db.event("job.started", {"attempt_id": attempt_id, "model": config["model"], "workflow": config["workflow"]}, job["id"], now)
        await self.emit(job["id"], "status", {"message": "正在处理", "status": "running"})
        execution = asyncio.create_task(Agent(self).execute(job, config))
        self.active[job["id"]] = execution
        status, error, result, error_code = "succeeded", "", "", ''
        retry = False
        try:
            async with asyncio.timeout(max(0, deadline - self.clock())):
                result = await execution
        except TimeoutError:
            status, error = "timed_out", "作业执行期限已到"
            error_code = 'job_timeout'
        except asyncio.CancelledError:
            cancelled = self.require("jobs", job["id"])["status"] == "cancelled"
            status, error = ("cancelled", "用户取消") if cancelled else ("pending", "Gateway 正常停止，等待恢复")
            if not cancelled and automatic:
                status, error, error_code = 'failed', '电脑运行中断，清醒后补更新', 'sleep_interrupted'
        except AgentError as failure:
            error = str(failure)
            error_code = failure.code
            retry = failure.retryable and job["attempt"] < 2 and self.clock() + 2 < deadline
            status = "pending" if retry else "failed"
        except Exception as failure:
            status, error = "failed", f"执行失败：{type(failure).__name__}"
        finally:
            self.active.pop(job["id"], None)
        now = self.clock()
        if automatic and (job['id'] in self._sleep_interrupted or now > deadline and now - (self._last_tick or now) > 30):
            status, error, error_code, result, retry = 'failed', '电脑运行中断，清醒后补更新', 'sleep_interrupted', '', False
        elif automatic and status == 'succeeded' and now > deadline:
            status, error, error_code, result = 'timed_out', '作业执行期限已到，未发布迟到内容', 'job_timeout', ''
        self._sleep_interrupted.discard(job['id'])
        # Explicit cancellation wins even if it arrives while a response finishes.
        if self.require("jobs", job["id"])["status"] == "cancelled":
            status, error, result = "cancelled", "用户取消", ""
        with self.db.transaction() as conn:
            if conn.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()[0] == 'cancelled':
                status, error, result = 'cancelled', '用户取消', ''
                retry = False
            if automatic and job['trigger_key'] != f"daily:{job['kind']}:{self.content_due().date().isoformat()}":
                status, error, error_code, result, retry = 'cancelled', '已由最近日期的更新接替', 'superseded_daily', '', False
            attempt_status = "retrying" if retry else "interrupted" if status == "pending" else status
            next_run = now + 2 if retry else now
            delayed = self.delay_content_recovery(conn, job, config, error_code, now) if automatic and status in {'failed', 'timed_out'} else None
            if delayed:
                status, next_run = 'pending', delayed
                conn.execute('UPDATE jobs SET deadline=NULL WHERE id=?', (job['id'],))
            conn.execute("UPDATE attempts SET status=?,ended=?,error=? WHERE id=?", (attempt_status, now, error, attempt_id))
            stored_result = packed(result) if isinstance(result, dict) else result
            conn.execute("UPDATE jobs SET status=?,result=?,error=?,error_code=?,next_run=?,updated=? WHERE id=?", (status, stored_result, error, error_code, next_run, now, job["id"]))
            session = conn.execute("SELECT state FROM sessions WHERE id=?", (job["session_id"],)).fetchone() if job["session_id"] else None
            if status == "succeeded" and job["kind"] == "session_meta" and session and session["state"] != "deleting":
                meta = json.loads(job["input"])
                current = conn.execute("SELECT * FROM sessions WHERE id=?", (job["session_id"],)).fetchone()
                rename = meta["rename"] and not current["title_manual"] and current["title"] == meta["old_title"]
                conn.execute("UPDATE sessions SET title=?,display_abstract=? WHERE id=?", (result["title"] if rename else current["title"], result["display_abstract"], job["session_id"]))
                conn.execute("INSERT INTO audit(job_id,kind,data,created) VALUES(?,'session.meta',?,?)", (job["id"], packed({"session_id": job["session_id"], "old_title": current["title"], "renamed": rename}), now))
            if status == "succeeded" and job["kind"] == "goal_setup" and isinstance(result, dict):
                self.publish_goal_card(conn, job, result)
            if status == "succeeded" and job["kind"] in {"feed", "ideas"} and isinstance(result, dict):
                self.publish_content(conn, job, result, now)
            if status == "succeeded" and session and session["state"] != "deleting" and result and not isinstance(result, dict):
                if job["kind"] in {"chat", "ideas", "draft", "compact", "connection"}:
                    conn.execute("INSERT INTO messages(id,session_id,role,content,job_id,created) VALUES(?,?,?,?,?,?)", (uid(), job["session_id"], "assistant", result, job["id"], now))
                    conn.execute("UPDATE sessions SET updated=? WHERE id=?", (now, job["session_id"]))
                    self.queue_session_meta(conn, job["session_id"])
                elif conn.execute("SELECT id FROM actions WHERE job_id=? AND status='pending'", (job["id"],)).fetchone():
                    conn.execute("INSERT INTO messages(id,session_id,role,content,job_id,created) VALUES(?,?,?,?,?,?)", (uid(), job["session_id"], "assistant", "这次约定的提醒已准备，等待你审核后投递。", job["id"], now))
            if status == "succeeded" and job["kind"] == "once":
                conn.execute("UPDATE tasks SET state='completed',updated=? WHERE id=?", (now, job["task_id"]))
        self.db.event(f"job.{status}", {"attempt_id": attempt_id, "error": error}, job["id"], now)
        await self.emit(job["id"], "done" if status != "pending" else "status", {"status": status, "result": result, "error": error, "message": "稍后重试" if retry else error})
        return True

    async def start(self):
        self.recover()
        self.running = True

        async def scheduler():
            while self.running:
                try:
                    self.tick()
                    self.deliver()
                except Exception as error:
                    self.db.event("gateway.tick_failed", {"error_type": type(error).__name__}, now=self.clock())
                await asyncio.sleep(0.5)

        async def worker():
            while self.running:
                if not await self.run_one():
                    await asyncio.sleep(0.2)

        self.loops = [asyncio.create_task(scheduler()), asyncio.create_task(worker())]

    async def stop(self):
        self.running = False
        for execution in list(self.active.values()):
            execution.cancel()
        if self.loops:
            await asyncio.gather(*self.loops, return_exceptions=True)
