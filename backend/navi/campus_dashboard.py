import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


CAMPUS_TZ = ZoneInfo("Asia/Shanghai")
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

PUBLISHED = re.compile(
    r"发布时间[:：]\s*(\d{4})年(\d{1,2})月(\d{1,2})日"
    r"(?:[^\n]*?(上午|下午)?(\d{1,2})时(\d{1,2})分)?"
)
CHINESE_DATE = re.compile(r"(?:(20\d{2})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日")
ISO_DATE = re.compile(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})")
ENGLISH_DATE = re.compile(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})(?:,\s*(20\d{2}))?", re.I)
TIME = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{2})\s*(AM|PM|a\.m\.|p\.m\.)?", re.I)
CALENDAR_EVENT = re.compile(
    r"([A-Za-z]+,\s+[A-Za-z]+\s+\d{1,2},\s+\d{4}),\s*"
    r"(\d{1,2}:\d{2})(a|p)?(.*)",
    re.S,
)

DEADLINE_WORDS = re.compile(r"截止|deadline|due|submit|提交|before|until|no later than|之前|前完成", re.I)
EXAM_WORDS = re.compile(r"exam|midterm|final|quiz|test|考试|测验|考核|答辩", re.I)
ASSIGNMENT_WORDS = re.compile(r"assignment|homework|coursework|project|report|paper|作业|项目|报告|论文|OJ", re.I)


def _published_at(details):
    match = PUBLISHED.search(details)
    if not match:
        return None
    year, month, day, period, hour, minute = match.groups()
    hour = int(hour or 0)
    if period == "下午" and hour < 12:
        hour += 12
    try:
        return datetime(int(year), int(month), int(day), hour, int(minute or 0), tzinfo=CAMPUS_TZ)
    except ValueError:
        return None


def _course(info):
    match = re.search(r"发布至[:：]\s*(.+)", info or "")
    return " ".join(match.group(1).split()) if match else ""


def _summary(details):
    body = details.split("\n", 1)[1] if "\n" in details else details
    parts = []
    for line in body.splitlines():
        value = " ".join(line.split())
        if not value or re.search(r"\.(?:pdf|docx?|xlsx?|pptx?|zip)$", value, re.I):
            continue
        parts.append(value)
        if len(" ".join(parts)) >= 180:
            break
    text = " ".join(parts)
    return text[:220].rstrip(" ,，。.;；")


def _kind(title, text):
    if EXAM_WORDS.search(title):
        return "exam"
    if ASSIGNMENT_WORDS.search(title):
        return "assignment"
    if ASSIGNMENT_WORDS.search(text):
        return "assignment"
    if DEADLINE_WORDS.search(f"{title}\n{text}"):
        return "deadline"
    return "notice"


def _topic_key(title):
    value = re.sub(r"^\[[^\]]+\]\s*", "", title.strip().lower())
    value = re.sub(r"postponed\s+to.*$", "", value)
    value = re.sub(r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:,\s*\d{4})?", " ", value)
    value = re.sub(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", " ", value)
    value = re.sub(r"\d{1,2}\s*月\s*\d{1,2}\s*日", " ", value)
    return " ".join(re.findall(r"[\w\u4e00-\u9fff]+", value))


def _date_candidates(text, now, default_year=None):
    default_year = default_year or now.year
    candidates = []
    for match in CHINESE_DATE.finditer(text):
        year, month, day = match.groups()
        try:
            candidates.append((match.start(), match.end(), int(year or default_year), int(month), int(day)))
        except ValueError:
            continue
    for match in ISO_DATE.finditer(text):
        year, month, day = match.groups()
        candidates.append((match.start(), match.end(), int(year), int(month), int(day)))
    for match in ENGLISH_DATE.finditer(text):
        month, day, year = match.groups()
        candidates.append((match.start(), match.end(), int(year or default_year), MONTHS[month[:3].lower()], int(day)))
    return candidates


def _nearby_times(text, start, end):
    window_start = max(0, start - 100)
    window_end = min(len(text), end + 100)
    times = []
    for match in TIME.finditer(text, window_start, window_end):
        hour, minute, suffix = match.groups()
        hour = int(hour)
        if suffix and suffix.lower().startswith("p") and hour < 12:
            hour += 12
        if suffix and suffix.lower().startswith("a") and hour == 12:
            hour = 0
        times.append((hour, int(minute)))
    return times


def _deadline_times(text, start, end):
    window_start = max(0, start - 100)
    window_end = min(len(text), end + 100)
    if not DEADLINE_WORDS.search(text[window_start:window_end]):
        return []
    return _nearby_times(text, start, end)


def _deadline_label(value):
    return f"{value.month}月{value.day}日 {value:%H:%M}"


def _parse_announcements(raw_items, now):
    messages = []
    deadline_items = []
    for raw in raw_items:
        title = " ".join((raw.get("title") or "").split())
        details = raw.get("details") or ""
        if not title or not details:
            continue
        published = _published_at(details)
        if published is None:
            continue
        course = _course(raw.get("info") or "")
        course_year = re.search(r"\b(20\d{2})\b", course)
        summary = _summary(details)
        kind = _kind(title, summary)
        identity = raw.get("id") or f"{title}:{published.isoformat()}"
        item = {
            "id": identity,
            "title": title[:240],
            "course": course[:160],
            "published_at": published.timestamp(),
            "published_label": _deadline_label(published),
            "summary": summary,
            "kind": kind,
            "url": "",
            "attachments": [
                {"title": link.get("text", "")[:160], "url": link.get("href", "")}
                for link in raw.get("attachments", [])
                if link.get("text") and link.get("href", "").startswith("https://bb.sustech.edu.cn/")
            ][:5],
        }
        clean_body = re.sub(r"^.*?发布时间[:：][^\n]*\n?", "", details, count=1, flags=re.S)
        seen = set()
        latest_dates = []
        default_year = int(course_year.group(1)) if course_year else published.year
        for start, end, year, month, day in _date_candidates(clean_body, now, default_year):
            nearby = _nearby_times(clean_body, start, end)
            for hour, minute in nearby or [(23, 59)]:
                try:
                    due = datetime(year, month, day, hour, minute, tzinfo=CAMPUS_TZ)
                except ValueError:
                    continue
                latest_dates.append(due)
            candidate_times = _deadline_times(clean_body, start, end)
            if kind in {"assignment", "exam"} and not candidate_times:
                candidate_times = nearby
            for hour, minute in candidate_times:
                try:
                    due = datetime(year, month, day, hour, minute, tzinfo=CAMPUS_TZ)
                except ValueError:
                    continue
                if not now - timedelta(days=1) <= due <= now + timedelta(days=90):
                    continue
                key = (item["id"], due.isoformat())
                if key in seen:
                    continue
                seen.add(key)
                deadline_items.append({
                    "id": f"{item['id']}:{due.isoformat()}",
                    "title": item["title"],
                    "course": item["course"],
                    "kind": kind if kind in {"assignment", "exam"} else "deadline",
                    "due_at": due.timestamp(),
                    "due_label": _deadline_label(due),
                    "url": item["url"],
                    "source": "Blackboard 公告",
                })
        item["latest_at"] = max(latest_dates).timestamp() if latest_dates else None
        messages.append(item)
    messages.sort(key=lambda item: item["published_at"], reverse=True)
    return messages, deadline_items


def _parse_calendar(raw_events, now):
    items = []
    for raw in raw_events:
        match = CALENDAR_EVENT.search(raw.get("text") or "")
        if not match:
            continue
        date_text, time_text, suffix, title = match.groups()
        try:
            day = datetime.strptime(date_text, "%A, %B %d, %Y").replace(tzinfo=CAMPUS_TZ)
            hour, minute = [int(value) for value in time_text.split(":")]
        except ValueError:
            continue
        if suffix == "p" and hour < 12:
            hour += 12
        if suffix == "a" and hour == 12:
            hour = 0
        due = day.replace(hour=hour, minute=minute)
        if not now - timedelta(days=1) <= due <= now + timedelta(days=90):
            continue
        name = " ".join(title.split()) or "Blackboard 事项"
        kind = _kind(name, "")
        items.append({
            "id": f"calendar:{date_text}:{time_text}:{name}",
            "title": name[:240],
            "course": "",
            "kind": kind if kind in {"assignment", "exam"} else "deadline",
            "due_at": due.timestamp(),
            "due_label": _deadline_label(due),
            "url": "",
            "source": "Blackboard 日历",
            "summary": "",
        })
    return items


def build_dashboard(raw_announcements, raw_events, now=None):
    now = now or datetime.now(CAMPUS_TZ)
    messages, announcement_deadlines = _parse_announcements(raw_announcements, now)
    deadlines = announcement_deadlines + _parse_calendar(raw_events, now)
    message_topics = {_topic_key(item["title"]): item for item in messages}
    for item in deadlines:
        matched = message_topics.get(_topic_key(item["title"]))
        if matched:
            item["course"] = item["course"] or matched["course"]
            item["url"] = item["url"] or matched["url"]
            item["summary"] = matched["summary"]
        item.setdefault("summary", "")
    deduped = {}
    for item in deadlines:
        key = (_topic_key(item["title"]), round(item["due_at"] / 60))
        current = deduped.get(key)
        if current is None or (not current["course"] and item["course"]):
            deduped[key] = item
    postponed = {}
    stable = []
    for item in deduped.values():
        if "postpon" not in item["title"].lower():
            stable.append(item)
            continue
        key = (item["course"].casefold(), _topic_key(item["title"]))
        current = postponed.get(key)
        if current is None or item["due_at"] > current["due_at"]:
            postponed[key] = item
    daily = {}
    for item in stable + list(postponed.values()):
        due = datetime.fromtimestamp(item["due_at"], CAMPUS_TZ)
        key = (_topic_key(item["title"]), due.date().isoformat())
        current = daily.get(key)
        if current is None:
            daily[key] = item
            continue
        postponed_item = "postpon" in item["title"].lower()
        if postponed_item and item["due_at"] > current["due_at"]:
            daily[key] = item
        elif not postponed_item and item["due_at"] < current["due_at"]:
            daily[key] = item
    deadlines = sorted(daily.values(), key=lambda item: item["due_at"])
    recent_messages = messages[:6]
    recent_cutoff = (now - timedelta(days=120)).timestamp()
    active_messages = [item for item in messages if item["published_at"] >= recent_cutoff]
    courses = {item["course"] for item in active_messages if item["course"]}
    return {
        "logged_in": True,
        "generated_at": now.timestamp(),
        "summary": {
            "courses": len(courses),
            "messages": len(recent_messages),
            "deadlines": len(deadlines),
        },
        "recent_messages": recent_messages,
        "deadlines": deadlines[:8],
    }
