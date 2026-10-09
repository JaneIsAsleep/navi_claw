from pathlib import Path
import json
import os
import math

from .storage import Database, packed


DEFAULTS = {
    "model": "deepseek-flash",
    "protocol": "messages",
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
}


class Settings:
    def __init__(self, db: Database, directory: Path):
        self.db = db
        self.key_path = directory / ".credentials.json"
        self.on_budget_change = None

    def current(self):
        values = dict(DEFAULTS)
        values.update({row["key"]: json.loads(row["value"]) for row in self.db.all("SELECT * FROM settings")})
        return values

    def api_key(self):
        if self.key_path.exists():
            return json.loads(self.key_path.read_text()).get("deepseek_api_key", "")
        return os.environ.get("DEEPSEEK_API_KEY", "")

    def public(self):
        return {**self.current(), "api_key_configured": bool(self.api_key())}

    def update(self, values: dict):
        before = self.current()
        valid = {}
        for key, value in values.items():
            if key == "api_key":
                if not isinstance(value, str):
                    raise ValueError("密钥必须是文本")
                continue
            if key not in DEFAULTS:
                raise ValueError(f"不支持的设置：{key}")
            if key == "feed_brief":
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
            for key, value in valid.items():
                conn.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, packed(value)))
        if "api_key" in values:
            temporary = self.key_path.with_suffix(".tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump({"deepseek_api_key": values["api_key"].strip()}, stream)
            os.chmod(temporary, 0o600)
            temporary.replace(self.key_path)
        if self.on_budget_change and any(key in valid and before[key] != valid[key] for key in ('daily_requests', 'daily_tokens')):
            self.on_budget_change(budget_changed=True)
        return self.public()
