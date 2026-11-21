import math
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DIGITS = "零一二三四五六七八九"
NUMBER = r"[0-9零〇一二两三四五六七八九十百]+"
UNIT = r"个?半?小时|钟头|分钟|秒钟?|天|周"
DURATION = rf"半\s*(?:个)?(?:小时|钟头|分钟|天)|{NUMBER}\s*(?:{UNIT})"
RELATIVE = re.compile(rf"(?P<duration>{DURATION})\s*(?:之后|以后|后)|每(?:隔)?\s*(?P<interval>{DURATION})")
DAY = re.compile(r"今天|今日|今晚|明天|明早|明晚|后天")
WEEKDAY = re.compile(r"(每|下|本|这)?(?:周|星期)([一二三四五六日天])")
DATE = re.compile(r"(?<!\d)(?:(\d{4})[-年/])?(\d{1,2})[-月/](\d{1,2})(?:日|号)?(?!\d)")
CLOCK = re.compile(rf"(?<![\d:：])(?P<hour>{NUMBER})\s*(?:[:：](?P<colon>[0-9]{{2}})(?!\d)|(?:点|时(?![钟间]))(?:(?P<half>半)|(?P<quarter>[一三])刻|(?P<minute>{NUMBER})\s*分?)?)")
TIME_REPLY = re.compile(r"[每隔明后今本这天周星期早上午下晚间中午凌凌晨零〇两一二三四五六七八九十百点时钟小分秒半刻月日号年个之以\d :：/,\-，。！？?]+")
EPOCH = re.compile(r"\bnext_due\s*(?:为|[:：=])?\s*(\d+(?:\.\d+)?)", re.I)


def timezone_for(value):
    if not isinstance(value, str) or not value or len(value) > 100:
        raise ValueError("时区名称无效")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("时区名称无效") from error


def local_timestamp(value, zone):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?", value):
        raise ValueError("请使用当地日期和时间，格式为 YYYY-MM-DD HH:MM")
    local = datetime.fromisoformat(value)
    options = set()
    for fold in (0, 1):
        stamp = local.replace(tzinfo=zone, fold=fold).timestamp()
        if datetime.fromtimestamp(stamp, zone).replace(tzinfo=None) == local:
            options.add(stamp)
    if not options:
        raise ValueError("该当地时间因夏令时切换不存在，请选择其他时间")
    if len(options) > 1:
        raise ValueError("该当地时间因夏令时切换出现两次，请选择其他时间")
    return options.pop()


def number(value):
    if value.isascii():
        return int(value)
    value = value.replace("两", "二").replace("〇", "零")
    for mark, scale in (("百", 100), ("十", 10)):
        if mark in value:
            left, right = value.split(mark, 1)
            return (number(left) if left else 1) * scale + (number(right) if right else 0)
    return int("".join(str(DIGITS.index(char)) if char in DIGITS else char for char in value))


def duration_seconds(value):
    match = re.fullmatch(rf"(半|{NUMBER})\s*({UNIT})", value)
    if not match:
        raise ValueError("请提供明确的提醒间隔")
    amount, unit = match.groups()
    quantity = 0.5 if amount == "半" else number(amount)
    if "半" in unit:
        quantity += 0.5
    scale = 3600 if unit.endswith(("小时", "钟头")) else 60 if unit == "分钟" else 86400 if unit == "天" else 604800 if unit == "周" else 1
    return quantity * scale


def has_time_reply(text):
    return bool(TIME_REPLY.fullmatch(text.strip()) and (CLOCK.search(text) or RELATIVE.search(text) or DAY.search(text) or DATE.search(text)))


def finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def reminder_timing(text, anchor, zone):
    """Resolve common reminder wording from the user's message in the job's timezone."""
    now = datetime.fromtimestamp(anchor, zone)
    epoch = EPOCH.search(text)
    if epoch:
        return {"next_due": float(epoch[1])}
    relatives = list(RELATIVE.finditer(text))
    dates = list(DATE.finditer(text))
    days = list(DAY.finditer(text))
    weekdays = list(WEEKDAY.finditer(text))
    clocks = list(CLOCK.finditer(text))
    if len(relatives) > 1 or len(clocks) > 1 or len(dates) > 1 or len(weekdays) > 1 or len(days) > 1:
        raise ValueError("检测到多个时间，请说明本次提醒要用哪个时间")
    if relatives and not (clocks or dates or days or weekdays):
        relative = relatives[0]
        seconds = duration_seconds(relative["duration"] or relative["interval"])
        timing = {"next_due": anchor + seconds}
        if relative["interval"]:
            timing["interval_seconds"] = seconds
        return timing
    if not clocks or relatives:
        return None
    clock = clocks[0]
    if ((clock.end() < len(text) and text[clock.end()] in "后钟间")
            or re.search(r"每工作日|工作日|周末|双?休日|每月|每年|每隔|下个月|下月|明年|明晚之后|北京时间|当地时间之外|\b(?:UTC|GMT|tomorrow|daily|weekly)\b", text, re.I)
            or re.search(r"\b(?:a\.?m\.?|p\.?m\.?)\b", text[clock.end():], re.I)):
        return None
    hour = number(clock["hour"])
    minute = 30 if clock["half"] else 15 * number(clock["quarter"]) if clock["quarter"] else number(clock["colon"] or clock["minute"] or "0")
    before = text[max(0, clock.start() - 16):clock.start()]
    if re.search(r"下午|傍晚|晚上|今晚|明晚|每晚|中午|pm\s*$", before, re.I) and 1 <= hour < 12:
        hour += 12
    if re.search(r"凌晨|早上|上午|明早|每天早|am\s*$", before, re.I) and hour == 12:
        hour = 0
    day = now.date()
    repeat = 86400 if re.search(r"每天|每日|每晚|每早", text) else None
    shift = 0
    if dates:
        date = dates[0]
        day = day.replace(year=int(date[1] or day.year), month=int(date[2]), day=int(date[3]))
    elif days:
        word = days[0][0]
        shift = 2 if word == "后天" else 1 if word.startswith("明") else 0
    elif weekdays:
        week = weekdays[0]
        weekday = "一二三四五六日天".index(week[2]) % 7
        delta = weekday - now.weekday()
        shift = delta + 7 if week[1] == "下" else delta if week[1] in {"本", "这"} else delta % 7
        if week[1] == "每":
            repeat = 604800
    day += timedelta(days=shift)
    local = datetime(day.year, day.month, day.day, hour, minute)
    due = local_timestamp(local.isoformat(), zone)
    if due <= anchor and not (days or dates or (weekdays and weekdays[0][1] in {"本", "这", "下"})):
        local += timedelta(days=7 if weekdays else 1)
        due = local_timestamp(local.isoformat(), zone)
    result = {"next_due": due}
    if repeat:
        result["interval_seconds"] = repeat
        result["calendar_timezone"] = zone.key
    return result


def calendar_occurrences(due, interval, zone, now):
    local_due = datetime.fromtimestamp(due, zone).replace(tzinfo=None)
    local_now = datetime.fromtimestamp(now, zone).replace(tzinfo=None)
    days = int(interval // 86400)
    index = max(0, (local_now.date() - local_due.date()).days // days)

    def occurrence(index):
        if index == 0:
            return due
        local = local_due + timedelta(days=days * index)
        try:
            return local_timestamp(local.isoformat(), zone)
        except ValueError:
            return None

    latest = index
    while latest > 0 and ((stamp := occurrence(latest)) is None or stamp > now):
        latest -= 1
    upcoming = max(index, latest + 1)
    while (next_due := occurrence(upcoming)) is None or next_due <= now:
        upcoming += 1
    return occurrence(latest), latest, next_due
