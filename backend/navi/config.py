from pathlib import Path
import hashlib
import json
import os
import math
from uuid import uuid4

from .endpoints import OFFICIAL_URLS, legacy_endpoint_urls, request_url, validate_endpoint
from .storage import Database, packed
from .web_search import DEFAULT_PROVIDER, SEARCH_PROVIDERS


DEFAULTS = {
    "model": "deepseek-flash",
    "protocol": "messages",
    "endpoint_id": "",
    "workflow": "priority_briefing",
    "notification_policy": "daily",
    "timezone": "Asia/Taipei",
    "request_timeout": 60,
    "job_timeout": 180,
    "max_model_calls": 5,
    "max_tool_calls": 10,
    "max_output_tokens": 4096,
    "daily_requests": 0,
    "daily_tokens": 0,
    "input_price_per_million": 0,
    "output_price_per_million": 0,
    "price_currency": "USD",
    "feed_brief": "",
    "web_search_enabled": False,
    "web_search_provider": DEFAULT_PROVIDER,
}


class Settings:
    def __init__(self, db: Database, directory: Path):
        self.db = db
        self.key_path = directory / ".credentials.json"
        self.on_budget_change = None
        self.migrate_endpoints()

    @staticmethod
    def store(conn, key, value):
        conn.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, packed(value)))

    def migrate_endpoints(self):
        with self.db.transaction() as conn:
            rows = {row["key"]: json.loads(row["value"]) for row in conn.execute(
                "SELECT key,value FROM settings WHERE key IN ('endpoint_url','endpoint_history','endpoint_profiles')"
            )}
            if "endpoint_profiles" in rows or not rows:
                return
            active = rows.get("endpoint_url", "")
            endpoints = dict.fromkeys(url for url in [active, *rows.get("endpoint_history", [])] if url)
            credentials = self.credentials()
            keys = credentials.get("endpoint_keys", {})
            profile_keys = {}
            profiles = []
            active_id = ""
            for url in endpoints:
                profile_id = uuid4().hex
                profiles.append({"id": profile_id, "urls": legacy_endpoint_urls(url)})
                if url in keys:
                    profile_keys[profile_id] = keys[url]
                if url == active:
                    active_id = profile_id
            if keys:
                credentials["endpoint_profile_keys"] = profile_keys
                self.write_credentials(credentials)
            self.store(conn, "endpoint_id", active_id)
            self.store(conn, "endpoint_profiles", profiles)
            conn.execute("DELETE FROM settings WHERE key IN ('endpoint_url','endpoint_history')")
        if "endpoint_keys" in credentials:
            del credentials["endpoint_keys"]
            self.write_credentials(credentials)

    def current(self):
        values = dict(DEFAULTS)
        rows = {row["key"]: json.loads(row["value"]) for row in self.db.all("SELECT * FROM settings")}
        values.update({key: value for key, value in rows.items() if key in DEFAULTS})
        if "web_search_provider" not in rows and (rows.get("web_search_enabled") or self.web_search_key("brave")):
            values["web_search_provider"] = "brave"
        profile = next((profile for profile in rows.get("endpoint_profiles", []) if profile["id"] == values["endpoint_id"]), None)
        if profile:
            values["endpoint_urls"] = profile["urls"]
        return values

    @staticmethod
    def profiles(conn):
        row = conn.execute("SELECT value FROM settings WHERE key='endpoint_profiles'").fetchone()
        return json.loads(row["value"]) if row else []

    def endpoint_profiles(self):
        row = self.db.one("SELECT value FROM settings WHERE key='endpoint_profiles'")
        return json.loads(row["value"]) if row else []

    def credentials(self):
        if self.key_path.exists():
            return json.loads(self.key_path.read_text())
        return {}

    def api_key(self, config=None):
        if config is None:
            config = self.current()
        profile_id = config.get("endpoint_id", "")
        credentials = self.credentials()
        if profile_id or config.get("endpoint_url"):
            urls = config.get("endpoint_urls") if profile_id else legacy_endpoint_urls(config["endpoint_url"])
            matches = [
                profile for profile in self.endpoint_profiles()
                if profile["urls"] == urls and (not profile_id or profile["id"] == profile_id)
            ]
            if len(matches) == 1:
                return credentials.get("endpoint_profile_keys", {}).get(matches[0]["id"], "")
            return ""
        return credentials.get("deepseek_api_key", os.environ.get("DEEPSEEK_API_KEY", ""))

    def public(self):
        config = self.current()
        return {
            **{key: value for key, value in config.items() if key != "endpoint_urls"},
            "api_key_configured": bool(self.api_key(config)),
            "web_search_keys_configured": {provider: bool(self.web_search_key(provider)) for provider in SEARCH_PROVIDERS},
            "endpoint_profiles": self.endpoint_profiles(),
            "official_endpoints": OFFICIAL_URLS,
            "native_search_check": self.native_search_check(config),
        }

    def native_search_identity(self, config):
        key = self.api_key(config)
        if not key:
            return None
        return hashlib.sha256(packed([config.get("endpoint_id"), request_url(config), config["protocol"], config["model"], key]).encode()).hexdigest()

    def native_search_check(self, config):
        identity = self.native_search_identity(config)
        if identity:
            row = self.db.one("SELECT value FROM settings WHERE key=?", ("native_search:" + identity,))
            if row:
                return json.loads(row["value"])
        return "untested"

    def save_native_search_check(self, identity, status):
        if identity:
            with self.db.transaction() as conn:
                self.store(conn, "native_search:" + identity, status)

    def web_search_key(self, provider):
        credentials = self.credentials()
        legacy = credentials.get("brave_search_api_key") if provider == "brave" else None
        return credentials.get("web_search_keys", {}).get(provider) or legacy or os.environ.get(SEARCH_PROVIDERS[provider][2], "")

    def update(self, values: dict):
        before = self.current()
        valid = {}
        for key, value in values.items():
            if key == "api_key":
                if not isinstance(value, str):
                    raise ValueError("密钥必须是文本")
                continue
            if key == "web_search_api_key":
                if not isinstance(value, str) or len(value) > 256 or any(not 33 <= ord(char) <= 126 for char in value.strip()):
                    raise ValueError("搜索密钥格式无效")
                continue
            if key in {"endpoint_id", "endpoint_urls"}:
                continue
            if key not in DEFAULTS:
                raise ValueError(f"不支持的设置：{key}")
            if key == "web_search_enabled":
                if type(value) is not bool:
                    raise ValueError("联网搜索开关须为布尔值")
            elif key == "web_search_provider":
                if not isinstance(value, str) or value not in SEARCH_PROVIDERS:
                    raise ValueError("不支持的搜索服务")
            elif key == "feed_brief":
                if not isinstance(value, str) or len(value) > 2000:
                    raise ValueError("关注说明最多 2000 字")
            elif key in {"protocol", "workflow", "notification_policy"}:
                allowed = {"protocol": {"messages", "chat_completions"}, "workflow": {"priority_briefing", "deadline_briefing"}, "notification_policy": {"daily", "strict"}}[key]
                if value not in allowed:
                    raise ValueError(f"无效的设置：{key}")
            elif isinstance(DEFAULTS[key], (int, float)):
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0 or ("price" not in key and key not in {'daily_requests', 'daily_tokens'} and value < 1):
                    raise ValueError(f"无效的数值：{key}")
            elif not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError(f"无效的设置：{key}")
            valid[key] = value
        if "timezone" in valid:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            try:
                ZoneInfo(valid["timezone"])
            except (ZoneInfoNotFoundError, ValueError) as error:
                raise ValueError("时区名称无效") from error
        with self.db.transaction() as conn:
            profile_id = values.get("endpoint_id", before["endpoint_id"])
            profiles = self.profiles(conn)
            profile = next((profile for profile in profiles if profile["id"] == profile_id), None)
            if not isinstance(profile_id, str) or (profile_id and not profile):
                raise ValueError("该自定义 Endpoint 不存在")
            if "endpoint_urls" in values:
                urls = values["endpoint_urls"]
                if not isinstance(urls, dict) or urls.keys() != OFFICIAL_URLS.keys():
                    raise ValueError("请填写两种协议的完整 Endpoint URL")
                urls = {protocol: validate_endpoint(urls[protocol]) for protocol in OFFICIAL_URLS}
                profile = next((profile for profile in profiles if profile["urls"] == urls), None)
                if not profile:
                    profile = {"id": uuid4().hex, "urls": urls}
                    self.store(conn, "endpoint_profiles", [profile, *profiles])
                profile_id = profile["id"]
            if "endpoint_id" in values or "endpoint_urls" in values:
                valid["endpoint_id"] = profile_id
            if values.get("api_key", "").strip():
                self.save_api_key(profile_id, values["api_key"].strip())
            if values.get("web_search_api_key", "").strip():
                credentials = self.credentials()
                provider = valid.get("web_search_provider", before["web_search_provider"])
                credentials.setdefault("web_search_keys", {})[provider] = values["web_search_api_key"].strip()
                valid.setdefault("web_search_provider", provider)
                self.write_credentials(credentials)
            if "web_search_enabled" in valid:
                valid.setdefault("web_search_provider", before["web_search_provider"])
            for key, value in valid.items():
                self.store(conn, key, value)
        if self.on_budget_change and any(key in valid and before[key] != valid[key] for key in ('daily_requests', 'daily_tokens')):
            self.on_budget_change(budget_changed=True)
        return self.public()

    def delete_endpoint(self, profile_id):
        if profile_id == "":
            raise ValueError("DeepSeek 官方默认 Endpoint 不可删除")
        with self.db.transaction() as conn:
            profiles = self.profiles(conn)
            if not any(profile["id"] == profile_id for profile in profiles):
                raise ValueError("该自定义 Endpoint 不存在")
            active = conn.execute("SELECT value FROM settings WHERE key='endpoint_id'").fetchone()
            credentials = self.credentials()
            keys = credentials.get("endpoint_profile_keys", {})
            if profile_id in keys:
                del keys[profile_id]
                self.write_credentials(credentials)
            self.store(conn, "endpoint_profiles", [profile for profile in profiles if profile["id"] != profile_id])
            if active and json.loads(active["value"]) == profile_id:
                self.store(conn, "endpoint_id", "")
        return self.public()

    def save_api_key(self, profile_id, api_key):
        credentials = self.credentials()
        if profile_id:
            credentials.setdefault("endpoint_profile_keys", {})[profile_id] = api_key
        else:
            credentials["deepseek_api_key"] = api_key
        self.write_credentials(credentials)

    def write_credentials(self, credentials):
        temporary = self.key_path.with_suffix(".json.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(credentials, stream)
        os.chmod(temporary, 0o600)
        temporary.replace(self.key_path)
