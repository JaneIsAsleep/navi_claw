import hashlib
import json
import unicodedata
from datetime import datetime

from .reminder_time import CLOCK, DATE, DAY, RELATIVE, WEEKDAY, calendar_occurrences, finite_number, number, reminder_timing, timezone_for
from .storage import packed


ITEM_EDIT = 'item_edit'
KINDS = ('goal', 'reminder')
STATES = ('active', 'paused', 'completed', 'cancelled')
FIELDS = {'goal': {'title', 'body', 'next_step', 'state'}, 'reminder': {'title', 'instruction', 'next_due', 'state'}}
PAGE_SIZE = 24
REMINDER_ROWS = (
    'SELECT s.*,t.title,t.instruction,t.state,t.session_id,t.goal_id,t.updated AS task_updated '
    'FROM schedules s JOIN tasks t ON t.id=s.task_id '
    "WHERE t.state IN ('active','paused') AND (s.kind<>'once' OR s.enabled=1 OR t.state='paused')"
)


def name_key(value):
    return ''.join(char for char in unicodedata.normalize('NFKC', value).casefold() if char.isalnum())


def search_edit_targets(gateway, kind, query, offset, session_id, timezone):
    if kind == 'goal':
        rows = gateway.db.all("SELECT id,payload FROM records WHERE kind='goal' AND COALESCE(json_extract(payload,'$.state'),'active')<>'cancelled' ORDER BY updated DESC,id")
        rows = [{**json.loads(row['payload']), 'id': row['id']} for row in rows]
        text_fields = ('body', 'next_step')
    else:
        rows = gateway.db.all(REMINDER_ROWS + ' ORDER BY s.updated DESC,s.id')
        text_fields = ('instruction',)
    key = name_key(query)
    exact = [row for row in rows if key and key == row['id']]
    titles = [row for row in rows if key and key in name_key(row['title'])]
    details = [row for row in rows if key and any(key in name_key(row.get(field, '') or '') for field in text_fields)]
    matched, scope = (exact, 'exact') if exact else (titles, 'title') if titles else (details, 'details') if details else (rows, 'catalog')
    matched = sorted(matched, key=lambda row: row.get('session_id') != session_id)
    zone = timezone_for(timezone)
    candidates = []
    for row in matched[offset:offset + PAGE_SIZE]:
        candidate = {'id': row['id'], 'title': row['title'], 'state': row.get('state', 'active'),
                     'in_this_chat': row.get('session_id') == session_id}
        candidate.update({field: (row.get(field) or '')[:240] for field in text_fields})
        if kind == 'reminder':
            candidate['next_local'] = datetime.fromtimestamp(row['next_due'], zone).isoformat(timespec='minutes')
            candidate['interval_seconds'] = row['interval_seconds']
        candidates.append(candidate)
    end = offset + len(candidates)
    return {'kind': kind, 'query': query, 'offset': offset, 'scope': scope, 'candidates': candidates,
            'next_offset': end if end < len(matched) else None}


def current_item(gateway, kind, identity, *, conn=None):
    if kind == 'goal':
        row = gateway.db.one("SELECT * FROM records WHERE id=? AND kind='goal'", (identity,), conn=conn)
        if row:
            values = json.loads(row['payload'])
            if values.setdefault('state', 'active') != 'cancelled':
                return row, values
    elif kind == 'reminder':
        row = gateway.db.one(REMINDER_ROWS + ' AND s.id=?', (identity,), conn=conn)
        if row:
            return row, row
    raise ValueError('该记录已结束或已不存在，请重新发起修改。')


def snapshot(gateway, kind, row, changes, conn):
    linked = []
    if kind == 'goal' and changes.get('state') in {'completed', 'cancelled'}:
        linked = gateway.db.all("SELECT id,title,state FROM tasks WHERE goal_id=? AND state IN ('active','paused') ORDER BY id", (row['id'],), conn=conn)
    # Heartbeat bookkeeping does not change what the user approved.
    data = {key: value for key, value in row.items() if key != 'last_seen'}
    return hashlib.sha256(packed([data, linked]).encode()).hexdigest(), linked


def reminder_edit_due(when, current, anchor, timezone):
    zone = timezone_for(current['calendar_timezone'] or timezone)
    previous = datetime.fromtimestamp(current['next_due'], zone)
    when = when.strip()
    clock = CLOCK.fullmatch(when)
    if clock and not clock['hour'].startswith('0') and 1 <= number(clock['hour']) < 12 and previous.hour >= 12:
        when = '晚上' + when
    if CLOCK.search(when) and not any(pattern.search(when) for pattern in (DAY, DATE, WEEKDAY, RELATIVE)):
        if current['kind'] == 'once':
            when = previous.date().isoformat() + ' ' + when
        elif current['interval_seconds'] == 604800:
            when = '每周' + '一二三四五六日'[previous.weekday()] + when
    try:
        timing = reminder_timing(when, anchor, zone)
    except OverflowError as error:
        raise ValueError('提醒时间无效，请重新指定。') from error
    if not timing or not finite_number(timing['next_due']):
        raise ValueError('无法确定新的提醒时间，请用明确的日期或时刻。')
    if timing.get('interval_seconds', current['interval_seconds']) != current['interval_seconds']:
        raise ValueError('请指定新的提醒时间。此操作沿用原重复周期。')
    return timing['next_due'], zone.key


def validate_changes(kind, changes, *, conversational=False):
    keys = FIELDS[kind] | {'when'} if conversational and kind == 'reminder' else FIELDS[kind]
    if conversational:
        keys = keys - {'next_due'}
    if not changes or set(changes) - keys:
        raise ValueError('修改参数为空或包含该记录不支持的字段。')
    for key, value in changes.items():
        if key == 'next_due':
            if not finite_number(value):
                raise ValueError('提醒时间无效。')
        elif key == 'state':
            if value not in STATES:
                raise ValueError('状态无效。')
        else:
            maximum = 300 if key in {'title', 'when'} else 20000
            if not isinstance(value, str) or len(value) > maximum or key in {'title', 'when', 'instruction'} and not value.strip():
                raise ValueError('标题、时间或内容的格式无效。')
    if kind == 'reminder' and changes.get('state') in {'cancelled', 'completed'} and len(changes) > 1:
        raise ValueError('请将结束提醒作为独立操作。')


def prepare_item_edit(gateway, kind, identity, edits, anchor, timezone, conn):
    validate_changes(kind, edits, conversational=True)
    row, current = current_item(gateway, kind, identity, conn=conn)
    changes = dict(edits)
    if kind == 'goal' and current['state'] == 'completed' and changes.get('state') == 'paused':
        raise ValueError('请先恢复已完成的目标，再暂停。')
    if kind == 'reminder':
        timezone = row['calendar_timezone'] or timezone
        if 'when' in changes:
            changes['next_due'], timezone = reminder_edit_due(changes.pop('when'), current, anchor, timezone)
        elif changes.get('state') == 'active' and current['state'] == 'paused':
            now = gateway.clock()
            if row['calendar_timezone']:
                due = row['next_due']
                if due <= now:
                    _, _, due = calendar_occurrences(due, row['interval_seconds'], timezone_for(timezone), now)
            else:
                due = now + row['interval_seconds'] if row['interval_seconds'] else max(row['next_due'], now + 1)
            changes['next_due'] = due
        if 'next_due' in changes and changes['next_due'] <= gateway.clock():
            raise ValueError('新的提醒时间已过，请指定未来时间。')
    resume = kind == 'reminder' and current['state'] == 'paused' and changes.get('state') == 'active'
    before = {key: current.get(key) for key, value in changes.items() if current.get(key) != value or resume and key == 'next_due'}
    if not before:
        raise ValueError('记录已经是你指定的值，无需修改。')
    changes = {key: changes[key] for key in before}
    expected, linked = snapshot(gateway, kind, row, changes, conn)
    body = {'object_kind': kind, 'before': before, 'changes': changes, 'expected': expected, 'timezone': timezone,
            'linked_reminders': linked}
    return current['title'], packed(body)


def apply_item_edit(gateway, conn, target, body, stop_ids):
    edit = json.loads(body)
    kind, changes = edit['object_kind'], edit['changes']
    validate_changes(kind, changes)
    row, _ = current_item(gateway, kind, target, conn=conn)
    if snapshot(gateway, kind, row, changes, conn)[0] != edit['expected']:
        raise ValueError('原记录在审批期间已变化。请重新发起修改并审批。')
    if 'next_due' in changes and changes['next_due'] <= gateway.clock():
        raise ValueError('批准的时间已经过去。请重新指定新的提醒时间。')
    if kind == 'goal':
        gateway.record('goal', {**json.loads(row['payload']), **changes}, target, conn=conn, stop_ids=stop_ids)
    else:
        values = dict(changes)
        state = values.pop('state', None)
        if state is not None:
            values['operation'] = dict(active='resume', paused='pause', completed='complete', cancelled='cancel')[state]
        gateway.edit_schedule(target, values, conn=conn, stop_ids=stop_ids)
