"""Calendar files (.ics): write the deadlines as one, and read Canvas's own calendar feed.

Canvas gives every user a private calendar link. The collector saves it in the settings
(cfg["calendar_feed"]); it works without a login, so it is the backup when a collection
fails. The link is a secret: it must never be printed, logged or put in an error message.
"""
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import cc_model
import cc_time
from cc_lang import T

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

FILE_NAME = "Canvas deadlines.ics"
MAX_BYTES = 5 * 1024 * 1024
ASSIGNMENT_UID = re.compile(r"^event-assignment-(\d+)$")
EVENT_COURSE = re.compile(r"course_(\d+)")       # Canvas names the course in each event's URL


class FeedError(Exception):
    pass


# ---------------------------------------------------------------- writing

def escape(text):
    text = str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    for line_break in ("\r\n", "\n", "\r", "\u2028", "\u2029"):
        text = text.replace(line_break, "\\n")
    return text


def fold(line):
    """Split a line so no piece is longer than 75 bytes (RFC 5545), never inside a character."""
    pieces, current, size = [], "", 0
    for ch in line:
        width = len(ch.encode("utf-8"))
        if size + width > (75 if not pieces else 74):
            pieces.append(current)
            current, size = "", 0
        current += ch
        size += width
    pieces.append(current)
    return "\r\n ".join(pieces)


def _utc(when):
    return when.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def export(view, host, include_done=False, alarm_hours=None, at=None):
    """(calendar text, number of events) for a deadline view (cc_deadlines.build_view)."""
    at = at or cc_time.now()
    site = urlparse(host).hostname or "canvas"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Canvas Coach//Deadlines//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:" + escape(T("Canvas deadlines (Canvas Coach)"))]
    count = 0
    for sec in view["sections"]:
        for it in sec["items"]:
            if not it.get("deadline") or (it["done"] and not include_done):
                continue
            count += 1
            word = (T("closes: ") if it.get("closes_only") else "") + ("(?) " if not it.get("confirmed", True) else "")
            notes = [it["status_text"], it["worth_text"]] + list(it["notes"]) + ([it["url"]] if it["url"] else [])
            lines += ["BEGIN:VEVENT", f"UID:canvas-coach-{it['key']}@{site}", f"DTSTAMP:{_utc(at)}",
                      f"DTSTART:{_utc(it['deadline'])}", f"DTEND:{_utc(it['deadline'])}",
                      "SUMMARY:" + escape(f"{word}{it['name']} [{it['course']}]"),
                      "DESCRIPTION:" + escape("\n".join(n for n in notes if n))]
            if it["url"]:
                lines.append("URL:" + it["url"])
            if alarm_hours:
                lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:" + escape(it["name"]),
                          f"TRIGGER:-PT{int(alarm_hours)}H", "END:VALARM"]
            lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n", count


# ---------------------------------------------------------------- reading

def unescape(text):
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), text)


def _when(value, params):
    """(UTC datetime or None, all-day?) from a DTSTART value and its parameters."""
    value = value.strip()
    try:
        if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
            return datetime.strptime(value[:8], "%Y%m%d").replace(tzinfo=timezone.utc), True
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc), False
        naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
        zone = ZoneInfo(params["TZID"]) if params.get("TZID") and ZoneInfo else None
        local = naive.replace(tzinfo=zone) if zone else naive.astimezone()
        return local.astimezone(timezone.utc), False
    except Exception:
        return None, False


def parse(text):
    """Events from calendar text: [{"uid", "summary", "start", "all_day", "url", "description"}]. Never raises."""
    events, current, inner = [], None, 0
    unfolded = re.sub(r"\r?\n[ \t]", "", text or "")
    for line in unfolded.splitlines():
        if line == "BEGIN:VEVENT":
            current, inner = {"uid": "", "summary": "", "start": None, "all_day": False, "url": "", "description": ""}, 0
        elif line == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
        elif current is not None and line.startswith("BEGIN:"):
            inner += 1                      # a reminder (VALARM) inside the event has its own DESCRIPTION
        elif current is not None and line.startswith("END:"):
            inner = max(0, inner - 1)
        elif current is not None and not inner and ":" in line:
            head, value = line.split(":", 1)
            name, *raw = head.split(";")
            params = dict(p.split("=", 1) for p in raw if "=" in p)
            name = name.upper()
            if name == "DTSTART":
                current["start"], current["all_day"] = _when(value, params)
            elif name in ("UID", "URL"):
                current[name.lower()] = value.strip()
            elif name in ("SUMMARY", "DESCRIPTION"):
                current[name.lower()] = unescape(value)
    return events


def fetch(url, timeout=20):
    """Download the calendar text. Raises FeedError with a reason that does not contain the link."""
    parts = urlparse(url or "")
    local = parts.hostname in ("127.0.0.1", "localhost", "::1")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise FeedError(T("The saved calendar link is not a secure address."))
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "canvas-coach"})
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            data = answer.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise FeedError(T("The Canvas calendar answered with error {code}.", code=e.code))
    except Exception as e:
        raise FeedError(T("The Canvas calendar could not be reached ({why}).", why=type(e).__name__))
    if len(data) > MAX_BYTES:
        raise FeedError(T("The Canvas calendar is unexpectedly large."))
    text = data.decode("utf-8", "replace")
    if "BEGIN:VCALENDAR" not in text[:2000]:
        raise FeedError(T("The saved calendar link no longer returns a calendar (it may have been reset on Canvas)."))
    return text


def _same_day(due, day, tz, course_tz):
    """An all-day event stands for "by the end of that day" where the teacher is: accept either clock."""
    days = {cc_time.local(due, tz).date()}
    if course_tz and ZoneInfo:
        try:
            days.add(due.astimezone(ZoneInfo(course_tz)).date())
        except Exception:
            pass
    return day in days


def cross_check(events, payload, cfg, at=None, limit=15):
    """Plain sentences where the calendar and the snapshot disagree. Empty when they agree."""
    at = at or cc_time.now()
    tz = cc_time.zone(cfg)
    stamp = lambda when: cc_time.stamp(cc_time.local(when, tz))
    today = cc_time.local(at, tz).date()
    shown = {i["id"]: i for i in cc_model.items(payload, cfg, at)}
    known = {cc_model.sid(a.get("id")) for d in (payload.get("data") or {}).values() if isinstance(d, dict)
             for a in (d.get("assignments") if isinstance(d.get("assignments"), list) else [])}
    hidden = {c["id"] for c in cc_model.course_table(payload, cfg, at) if c["hidden"]}
    out = []
    for e in events:
        start = e.get("start")
        if not start:
            continue
        course = EVENT_COURSE.search(e.get("url") or "")
        if course and course.group(1) in hidden:
            continue                                   # a course the student does not follow here
        # Canvas writes a deadline at 11:59 PM, and any all-day event, as a plain date: no clock, no timezone.
        all_day = bool(e.get("all_day"))
        said = T("on {day}", day=cc_time.day(start)) if all_day else stamp(start)
        soon = (today <= start.date() <= today + timedelta(days=14)) if all_day else (at <= start <= at + timedelta(days=14))
        m = ASSIGNMENT_UID.match(e.get("uid") or "")
        if m:
            aid = m.group(1)
            if aid not in known:
                if (start.date() >= today - timedelta(days=1)) if all_day else (start >= at - timedelta(days=1)):
                    out.append(T("New on the Canvas calendar: {name}, due {when}.", name=e["summary"], when=said))
            elif aid in shown:
                due = shown[aid]["due"]
                same = due is not None and (_same_day(due, start.date(), tz, shown[aid]["course_tz"]) if all_day
                                            else abs((due - start).total_seconds()) <= 60)
                if not same:
                    was = stamp(due) if due else T("no due date")
                    out.append(T("{name}: the calendar now says due {when}; the last collection said {was}.",
                                 name=f"{shown[aid]['course']} · {shown[aid]['name']}", when=said, was=was))
        elif not (e.get("uid") or "").startswith("event-assignment") and soon:
            out.append(T("Calendar event: {name}, {when}.", name=e["summary"], when=said))
    if len(out) > limit:
        out = out[:limit] + [T("... and {n} more differences (run: coach.py feed).", n=len(out) - limit)]
    return out


def backup_notes(cfg, payload, fetcher=fetch):
    """What the calendar link reveals when a normal collection failed. Never raises."""
    try:
        link = cfg.get("calendar_feed")
        if not link or payload is None:
            return []
        return cross_check(parse(fetcher(link)), payload, cfg) or \
            [T("The Canvas calendar shows no new or moved deadlines since the last collection.")]
    except Exception:
        return [T("The Canvas calendar could not be read either.")]
