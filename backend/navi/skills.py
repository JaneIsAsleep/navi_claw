from pathlib import Path
import hashlib
import json
import math


class BriefingSkills:
    names = ("priority_briefing", "deadline_briefing")

    def __init__(self, root: Path, directory: Path):
        self.root = root
        self.data_path = directory / "check_items.json"
        if not self.data_path.exists():
            self.save_items([])

    def items(self):
        value = json.loads(self.data_path.read_text())
        if not isinstance(value, list):
            raise ValueError("关注事项数据需要为条目列表")
        return value

    def save_items(self, items):
        if not isinstance(items, list) or len(items) > 1000:
            raise ValueError("关注事项需要为不超过 1000 条的列表")
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("title"), str):
                raise ValueError("关注事项缺少 ID 或标题")
            if not item["id"] or len(item["id"]) > 100 or not item["title"].strip() or len(item["title"]) > 300:
                raise ValueError("关注事项 ID 或标题长度无效")
            if not isinstance(item.get("priority", 1), (int, float)) or not math.isfinite(item.get("priority", 1)):
                raise ValueError("重要程度需要为数字")
            if item.get("due_at") is not None and (not isinstance(item["due_at"], (int, float)) or not math.isfinite(item["due_at"])):
                raise ValueError("截止时间格式无效")
            if not isinstance(item.get("body", ""), str) or len(item.get("body", "")) > 20000 or not isinstance(item.get("done", False), bool):
                raise ValueError("事项内容或完成状态无效")
            url = item.get("source_url", "")
            if not isinstance(url, str) or url and not url.startswith(("https://", "http://")):
                raise ValueError("来源链接需要使用 http 或 https")
        if len({item["id"] for item in items}) != len(items):
            raise ValueError("关注事项 ID 不能重复")
        temporary = self.data_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(items, ensure_ascii=False, indent=2))
        temporary.replace(self.data_path)

    def description(self, name):
        if name not in self.names:
            raise ValueError("未知工作流扩展")
        return (self.root / "skills" / name / "SKILL.md").read_text()

    def check(self, name: str, now: float, extra_items=()):
        instruction = self.description(name)
        rows = [item for item in [*self.items(), *extra_items] if not item.get("done", False)]
        if name == "priority_briefing":
            rows = [item for item in rows if item.get("priority", 1) >= 2]
            rows.sort(key=lambda item: (-item.get("priority", 1), item.get("due_at") or float("inf"), item["id"]))
        else:
            rows = [item for item in rows if item.get("due_at") is not None and item["due_at"] <= now + 172800]
            rows.sort(key=lambda item: (item["due_at"], item["id"]))
        rows = rows[:10]
        serial = json.dumps([{key: value for key, value in row.items() if key not in {'read_at', 'checked_at'}} for row in rows], ensure_ascii=False, sort_keys=True)
        return {
            "extension": name,
            "extension_version": hashlib.sha256(instruction.encode()).hexdigest()[:12],
            "items": rows,
            "change_token": hashlib.sha256((name + serial).encode()).hexdigest(),
            "source": "Navi 本机关注事项与已订阅官方 RSS" if extra_items else "Navi 本机关注事项",
            "checked_at": now,
        }
