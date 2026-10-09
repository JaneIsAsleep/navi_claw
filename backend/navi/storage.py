from contextlib import contextmanager, closing
from pathlib import Path
import json
import sqlite3
import time
import uuid


def uid() -> str:
    return uuid.uuid4().hex


def packed(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', summary_through TEXT, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id), role TEXT NOT NULL, content TEXT NOT NULL, job_id TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS records_kind ON records(kind);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, title TEXT NOT NULL, instruction TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL, session_id TEXT REFERENCES sessions(id), goal_id TEXT, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), kind TEXT NOT NULL, interval_seconds REAL, next_due REAL NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, last_seen TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id), session_id TEXT REFERENCES sessions(id), trigger_key TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, input TEXT NOT NULL, result TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', attempt INTEGER NOT NULL DEFAULT 0, next_run REAL NOT NULL, deadline REAL, config TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), number INTEGER NOT NULL, status TEXT NOT NULL, started REAL NOT NULL, ended REAL, error TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS tool_calls(id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), name TEXT NOT NULL, arguments TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY, job_id TEXT REFERENCES jobs(id), kind TEXT NOT NULL, title TEXT NOT NULL, target TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL, content_hash TEXT NOT NULL, basis TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, action_id TEXT NOT NULL REFERENCES actions(id), decision TEXT NOT NULL, content_hash TEXT NOT NULL, actor TEXT NOT NULL, basis TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY, action_id TEXT UNIQUE NOT NULL REFERENCES actions(id), payload TEXT NOT NULL, state TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY, outbox_id TEXT UNIQUE NOT NULL REFERENCES outbox(id), action_id TEXT NOT NULL REFERENCES actions(id), target TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY, outbox_id TEXT UNIQUE NOT NULL REFERENCES outbox(id), title TEXT NOT NULL, body TEXT NOT NULL, unread INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, kind TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS usage(id TEXT PRIMARY KEY, job_id TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, estimated INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id, created);
CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status, next_run);
CREATE TABLE IF NOT EXISTS action_versions(action_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(action_id,version));
"""


class Database:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "navi.sqlite3"
        if self.path.exists():
            with closing(self.connect()) as conn:
                if conn.execute("PRAGMA user_version").fetchone()[0] < 2:
                    backup_path = directory / "navi.before-refinement.sqlite3"
                    if not backup_path.exists():
                        with closing(sqlite3.connect(backup_path)) as backup:
                            conn.backup(backup)
        with closing(self.connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            migrations = {
                "sessions": {"kind": "TEXT NOT NULL DEFAULT 'secondary'", "state": "TEXT NOT NULL DEFAULT 'active'", "title_manual": "INTEGER NOT NULL DEFAULT 0"},
                "messages": {"references_json": "TEXT NOT NULL DEFAULT '[]'", "outbox_id": "TEXT", "origin_session_id": "TEXT", "unread": "INTEGER NOT NULL DEFAULT 0"},
                "actions": {"source_session_id": "TEXT", "version": "INTEGER NOT NULL DEFAULT 1"},
            }
            for table, fields in migrations.items():
                existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                for name, definition in fields.items():
                    if name not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS main_session ON sessions(kind) WHERE kind='main'")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS delivered_message ON messages(outbox_id) WHERE outbox_id IS NOT NULL")
            # Only an existing, verifiable job relationship is migrated.
            conn.execute("UPDATE actions SET source_session_id=(SELECT session_id FROM jobs WHERE jobs.id=actions.job_id) WHERE source_session_id IS NULL")
            if conn.execute("PRAGMA user_version").fetchone()[0] < 2:
                conn.execute("PRAGMA user_version=2")
            conn.execute("INSERT OR IGNORE INTO users VALUES(?,?,?)", ("local-user", "你", time.time()))
            conn.commit()
        self.migrate(3, {"sessions": {"display_abstract": "TEXT NOT NULL DEFAULT ''"}})
        self.migrate(4, {"sessions": {"goal_setup_json": "TEXT NOT NULL DEFAULT '{}'"}, "messages": {"interaction_json": "TEXT NOT NULL DEFAULT '{}'"}})
        self.migrated_v5 = self.migrate(5, {'jobs': {'error_code': "TEXT NOT NULL DEFAULT ''", 'recovery_count': 'INTEGER NOT NULL DEFAULT 0'}}, self.migrate_v5_data)

    def migrate(self, version, migrations, update_data=None):
        with closing(self.connect()) as conn:
            if conn.execute("PRAGMA user_version").fetchone()[0] >= version:
                return False
            backup_path = self.path.parent / f"navi.before-muse-revision-v{version}.sqlite3"
            if version >= 5:
                backup_path = self.path.parent / 'backups' / backup_path.name
                backup_path.parent.mkdir(exist_ok=True)
            if not backup_path.exists():
                with closing(sqlite3.connect(backup_path)) as backup:
                    conn.backup(backup)
            for table, fields in migrations.items():
                existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                for name, definition in fields.items():
                    if name not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            if update_data:
                update_data(conn)
            conn.execute(f"PRAGMA user_version={version}")
            conn.commit()
            return True

    def migrate_v5_data(self, conn):
        old = {row['key']: json.loads(row['value']) for row in conn.execute("SELECT * FROM settings WHERE key IN ('daily_requests','daily_tokens')")}
        for key in ('daily_requests', 'daily_tokens'):
            conn.execute("INSERT INTO settings VALUES(?,'0') ON CONFLICT(key) DO UPDATE SET value='0'", (key,))
        conn.execute("UPDATE jobs SET error_code='daily_budget_exhausted' WHERE error='已达到每日调用预算，请在设置中查看或调整限制'")
        conn.execute("UPDATE jobs SET error_code='model_transport_error' WHERE error='DeepSeek 请求超时或网络连接失败'")
        marked = [row['id'] for row in conn.execute("SELECT id FROM records WHERE kind='feed' AND json_extract(payload,'$.origin') IS NULL")]
        has_generated = conn.execute("SELECT id FROM records WHERE kind='feed' AND json_extract(payload,'$.origin')='generated' LIMIT 1").fetchone()
        conn.execute("UPDATE records SET payload=json_set(payload,'$.display_state','legacy') WHERE kind='feed' AND json_extract(payload,'$.origin') IS NULL")
        if has_generated:
            conn.execute("UPDATE records SET payload=json_set(payload,'$.display_state','hidden_legacy') WHERE kind='feed' AND json_extract(payload,'$.display_state')='legacy'")
        conn.execute("INSERT INTO audit(kind,data,created) VALUES('migration.v5',?,?)", (packed({'previous_daily_limits': old, 'legacy_feed_ids': marked, 'hidden_after_generated': bool(has_generated)}), time.time()))

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def all(self, sql: str, params=()):
        with closing(self.connect()) as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params=()):
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params=()):
        with self.transaction() as conn:
            cursor = conn.execute(sql, params)
            return cursor.rowcount

    def event(self, kind: str, data: dict, job_id=None, now=None):
        self.execute("INSERT INTO audit(job_id,kind,data,created) VALUES(?,?,?,?)", (job_id, kind, packed(data), time.time() if now is None else now))
