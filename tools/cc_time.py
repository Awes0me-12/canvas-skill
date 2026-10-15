"""Clock and date wording. Canvas stores times in UTC; people read local time."""
import os
import re
from datetime import datetime, timedelta, timezone

from cc_lang import N, T

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

# Named here, not taken from the computer's settings, so the wording is the same on every machine.
DAYS = (N("Mon"), N("Tue"), N("Wed"), N("Thu"), N("Fri"), N("Sat"), N("Sun"))
LONG_DAYS = (N("Monday"), N("Tuesday"), N("Wednesday"), N("Thursday"), N("Friday"), N("Saturday"), N("Sunday"))
MONTHS = (N("Jan"), N("Feb"), N("Mar"), N("Apr"), N("May"), N("Jun"), N("Jul"), N("Aug"), N("Sep"), N("Oct"),
          N("Nov"), N("Dec"))
DAY_CODES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def now():
    """Current time in UTC. $CANVAS_COACH_NOW pins it for tests."""
    fixed = os.environ.get("CANVAS_COACH_NOW")
    return parse_iso(fixed) if fixed else datetime.now(timezone.utc)


def parse_iso(text):
    """'2026-10-15T06:59:59Z' -> aware UTC datetime. None/garbage -> None."""
    if not text or not isinstance(text, str):
        return None
    try:
        dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def zone(cfg):
    """tzinfo for display, or None meaning 'this computer's timezone'."""
    name = (cfg.get("timezone") or "auto").strip()
    if name.lower() == "auto":
        return None
    if ZoneInfo is None:
        raise ValueError(T("Named timezones need Python 3.9+"))
    try:
        return ZoneInfo(name)
    except Exception:
        raise ValueError(T("Unknown timezone '{name}'. Use a name like America/Los_Angeles, or auto.", name=name))


def local(dt, tz):
    return dt.astimezone(tz) if dt else None


def clock(dt):
    """'11:59 PM' without a leading zero (or '23:59' where the language uses the 24-hour clock)."""
    return re.sub(r"^0(\d)", r"\1", dt.strftime(T("%I:%M %p")))


def weekday(dt, long=False):
    return T((LONG_DAYS if long else DAYS)[dt.weekday()])


def day(dt, with_year=False):
    """'Thu Oct 15' (or '10月15日 周四')"""
    parts = dict(weekday=weekday(dt), month=T(MONTHS[dt.month - 1]), day=dt.day, year=dt.year)
    return T("{weekday} {month} {day}, {year}", **parts) if with_year else T("{weekday} {month} {day}", **parts)


def stamp(dt):
    """'Thu Oct 15, 11:59 PM'"""
    return T("{day}, {clock}", day=day(dt), clock=clock(dt))


def zone_label(dt):
    return dt.strftime("%Z") or dt.strftime("%z")


def relative(due, at):
    """How far `due` is from `at` (both local, aware), in everyday words."""
    days = (due.date() - at.date()).days
    # Subtract in UTC: two times in the same named zone subtract as wall-clock, which is wrong across a clock change.
    seconds = (due.astimezone(timezone.utc) - at.astimezone(timezone.utc)).total_seconds()
    if seconds >= 0:
        if seconds < 3600:
            return T("in {n} min", n=max(1, int(seconds // 60)))
        if days == 0:
            return T("today, in {n} h", n=int(seconds // 3600))
        if days == 1:
            return T("tomorrow")
        return T("in {n} days", n=days)
    seconds = -seconds
    if seconds < 3600:
        return T("{n} min ago", n=max(1, int(seconds // 60)))
    if days == 0:
        return T("{n} h ago", n=int(seconds // 3600))
    if days == -1:
        return T("yesterday")
    return T("{n} days ago", n=-days)


def age_text(minutes):
    if minutes < 1:
        return T("just now")
    if minutes < 90:
        return T("{n} min ago", n=int(minutes))
    if minutes < 48 * 60:
        return T("{n} h ago", n=int(minutes // 60))
    return T("{n} days ago", n=int(minutes // 1440))


def midnight_note(due):
    """Canvas deadlines at 12:00-12:59 AM really belong to the night before."""
    if due.hour == 0:
        return T("just after midnight, so effectively {weekday} night", weekday=weekday(due - timedelta(days=1), long=True))
    return ""
