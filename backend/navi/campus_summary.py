import json
import re
from datetime import datetime


SYSTEM_PROMPT = """你是 Navi 的校园学习助理。你会收到 Blackboard 的结构化事实，包括近期消息和临近截止事项。作业和考试已经统一归入截止事项。
请完成两项工作：
1. 写一段不超过 90 个汉字的学习总览，指出最需要关注的事项。
2. 为每个条目写一句不超过 80 个汉字的任务摘要，说明要做什么或有什么变化。

只输出 JSON 对象，格式如下：
{"brief":"总览","items":{"原始ID":"任务摘要"}}

必须遵守：
- 只能使用输入中已有的事实，禁止补充日期、提交方式、分数、链接或要求。
- 不得修改、创建或遗漏 ID；items 必须覆盖输入中的全部 ID。
- 没有正文依据时，摘要只复述标题表达的任务，不猜测细节。
- 使用简洁中文；截止任务优先说明任务对象和截止时间；普通消息说明关键变化。
- 不输出 Markdown、代码块、解释或 JSON 之外的任何文字。"""


def model_payload(dashboard, now=None):
    now = now or datetime.now().astimezone()
    source_items = list(dashboard.get("recent_messages", []))
    def item_key(item):
        return (" ".join(item.get("title", "").casefold().split()), " ".join(item.get("course", "").casefold().split()))

    by_key = {item_key(item): item for item in source_items}
    entries = {}

    def add(item, area):
        identity = item["id"]
        if identity in entries:
            return
        entries[identity] = {
            "id": identity,
            "area": area,
            "title": item.get("title", ""),
            "course": item.get("course", ""),
            "kind": item.get("kind", ""),
            "published": item.get("published_label", ""),
            "due": item.get("due_label", ""),
            "source": item.get("source", ""),
            "fact": item.get("summary", "")[:180],
        }

    for item in dashboard.get("deadlines", []):
        add(by_key.get(item_key(item)) or item, "deadline")
    for area in ("recent_messages",):
        for item in dashboard.get(area, []):
            add(item, area)
    return {
        "current_time": now.isoformat(timespec="minutes"),
        "items": list(entries.values()),
    }


def _json_object(text):
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end < start:
        raise ValueError("模型未返回 JSON 对象")
    return json.loads(value[start:end + 1])


def parse_model_summary(text):
    parsed = _json_object(text)
    brief = " ".join(str(parsed.get("brief", "")).split())[:180]
    raw_items = parsed.get("items")
    if not brief or not isinstance(raw_items, dict):
        raise ValueError("模型看板结果缺少总览或条目摘要")
    return {"brief": brief, "items": raw_items}


def merge_model_summaries(dashboard, batches):
    brief = next((batch["brief"] for batch in batches if batch.get("brief")), "")
    if not brief:
        raise ValueError("模型看板结果缺少总览")
    raw_items = {}
    for batch in batches:
        raw_items.update(batch.get("items", {}))
    result = json.loads(json.dumps(dashboard, ensure_ascii=False))
    result["brief"] = brief
    by_key = {}
    for area in ("recent_messages",):
        for item in result.get(area, []):
            summary = raw_items.get(item["id"])
            if isinstance(summary, str) and summary.strip():
                by_key[(" ".join(item.get("title", "").casefold().split()), " ".join(item.get("course", "").casefold().split()))] = " ".join(summary.split())[:180]
    for area in ("recent_messages", "deadlines"):
        for item in result.get(area, []):
            summary = raw_items.get(item["id"])
            if not isinstance(summary, str):
                summary = by_key.get((" ".join(item.get("title", "").casefold().split()), " ".join(item.get("course", "").casefold().split())))
            if isinstance(summary, str) and summary.strip():
                item["summary"] = " ".join(summary.split())[:180]
    result["model_generated"] = True
    return result


def apply_model_summary(dashboard, text):
    return merge_model_summaries(dashboard, [parse_model_summary(text)])
