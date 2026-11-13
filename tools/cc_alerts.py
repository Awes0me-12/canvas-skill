"""Phone alerts: the deadlines that matter, as events with alarms in a calendar of their own.

A desktop notification only shows while this Mac is awake. A calendar event with alarms travels
through the student's calendar account to their phone, so it rings there as well.

What gets an event (settings `alert_min_percent` and `alert_days`): open work due within the coming
days that is an exam, or is worth at least that share of the grade by itself. The kind of item is
not used: Canvas calls a four-point survey a "quiz" too.

The event and its alarms:
  - work that is open for a day or less (a quiz open from 8:00 to 17:00): the event covers the
    opening; alarms when it opens and three hours before it closes;
  - an exam: one hour from its start; alarms a day before and three hours before;
  - anything else: the hour before the deadline; alarms a day before and three hours before it.

Only the calendar named "Canvas Coach" is touched, and in it only the events made here (their ids
are remembered in state.json under "alerts"). Nothing is written until the student switches it on.
"""
import json
import os
import subprocess
import sys
from datetime import timedelta

import cc_lang
import cc_state
import cc_store
import cc_text
import cc_time
from cc_lang import T

CALENDAR = "Canvas Coach"
SHORT = timedelta(hours=24)         # open for no longer than this: the event covers the whole opening
LAST_CALL = timedelta(hours=3)      # the alarm before it closes
DAY_BEFORE = timedelta(hours=24)

# Dates are built from numbers: a date written as text is read differently on every Mac's language setting.
MOMENT = '''
on moment(y, m, d, hh, mm)
  set dt to current date
  set day of dt to 1
  set year of dt to (y as integer)
  set month of dt to (m as integer)
  set day of dt to (d as integer)
  set time of dt to ((hh as integer) * 3600) + ((mm as integer) * 60)
  return dt
end moment
'''
# argv: calendar, title, notes, link, year, month, day, hour, minute, length in minutes, then one alarm
# per argument as minutes from the start (negative: before it). Prints the new event's id.
CREATE = '''
on run argv
  set calName to item 1 of argv
  set startDate to my moment(item 5 of argv, item 6 of argv, item 7 of argv, item 8 of argv, item 9 of argv)
  set endDate to startDate + (((item 10 of argv) as integer) * 60)
  tell application "Calendar"
    if not (exists calendar calName) then make new calendar with properties {name:calName}
    tell calendar calName
      set ev to make new event at end of events with properties {summary:(item 2 of argv), start date:startDate, end date:endDate, description:(item 3 of argv)}
      if (item 4 of argv) is not "" then set url of ev to (item 4 of argv)
      repeat with n from 11 to (count of argv)
        tell ev to make new display alarm at end of display alarms with properties {trigger interval:((item n of argv) as integer)}
      end repeat
      return uid of ev
    end tell
  end tell
end run
''' + MOMENT
# argv: calendar, then the ids of the events to delete. Events that are already gone are skipped.
DELETE = '''
on run argv
  set calName to item 1 of argv
  tell application "Calendar"
    if exists calendar calName then
      tell calendar calName
        repeat with n from 2 to (count of argv)
          delete (every event whose uid is (item n of argv))
        end repeat
      end tell
    end if
  end tell
end run
'''


class AlertError(Exception):
    """The Calendar app could not be used."""


def run_script(script, *argv, timeout=90):
    """Run one AppleScript with arguments and return what it printed. Raises AlertError.

    With $CANVAS_COACH_ALERTS_LOG set (tests, trial runs) nothing reaches the Calendar app: each call
    is written to that file as a line of JSON, and a made-up id comes back.
    """
    log = os.environ.get("CANVAS_COACH_ALERTS_LOG")
    if log:
        if log == "fail":
            raise AlertError("the test asked for a failure")
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"op": "create" if script == CREATE else "delete", "argv": [str(a) for a in argv]},
                                    ensure_ascii=False) + "\n")
            return f"uid-{handle.tell()}"
    if sys.platform != "darwin":
        raise AlertError(T("Phone alerts need a Mac: they are written through its Calendar app."))
    try:
        done = subprocess.run(["osascript", "-e", script, *[str(a) for a in argv]], capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise AlertError(T("The Calendar app did not answer. If macOS is asking for permission, allow it and try again."))
    except OSError as e:
        raise AlertError(T("The Calendar app could not be reached ({why}).", why=e))
    if done.returncode != 0:
        why = cc_text.one_line(done.stderr)[:200]
        if "-1743" in why or "-1744" in why:
            raise AlertError(T("macOS has not allowed this to use the Calendar app. Allow it under System Settings > "
                               "Privacy & Security > Automation, then run: coach.py alerts on"))
        raise AlertError(T("The Calendar app refused ({why}).", why=why))
    return done.stdout.strip()


def _worth(it):
    return it["own_weight"] if it["own_weight"] is not None else it["weight"]


def counts(it, cfg):
    """Whether an item is worth an alarm: an exam, or enough of the grade by itself."""
    if it["exam"]:
        return True
    worth = _worth(it)
    return bool(worth) and worth >= float(cfg.get("alert_min_percent") or 0)


def _event(it, at):
    due, opens = it["deadline"], it["unlock"]
    if it["exam"]:
        start, end, alarms = due, due + timedelta(hours=1), [due - DAY_BEFORE, due - LAST_CALL]
    elif opens and timedelta(0) < due - opens <= SHORT:
        start, end, alarms = opens, due, [a for a in (opens, due - LAST_CALL) if a >= opens]
    else:
        start, end, alarms = due - timedelta(hours=1), due, [due - DAY_BEFORE, due - LAST_CALL]
    alarms = sorted({a for a in alarms if a > at})          # an alarm for a moment already past would ring at once
    title = f"{it['name']} — {it['course']}" + ("" if it["confirmed"] else " (?)")
    notes = cc_lang.join([b for b in (T("due {when}", when=it["due_text"]), it["worth_text"], it["place"]) if b], "clauses")
    minutes = [int((a - start).total_seconds() // 60) for a in alarms]
    return {"key": it["key"], "title": cc_text.one_line(title), "notes": notes, "url": cc_text.web_link(it["url"]) or "",
            "start": start, "end": end, "alarms": alarms, "minutes": minutes,
            "sig": "|".join([start.isoformat(), end.isoformat(), ",".join(str(m) for m in minutes), title])}


def wanted(view, cfg, at=None):
    """{item key: event} for the open work that should ring, soonest first."""
    at = at or cc_time.now()
    limit = at + timedelta(days=int(cfg.get("alert_days") or 7))
    items = [it for sec in view["sections"] if sec["key"] in ("week", "later") for it in sec["items"]
             if not it["done"] and it["status"] not in ("missed", "closed") and it["deadline"]
             and at < it["deadline"] <= limit and counts(it, cfg)]
    return {it["key"]: _event(it, at) for it in sorted(items, key=lambda i: i["deadline"])}


def _record(state):
    saved = state.get("alerts") if isinstance(state.get("alerts"), dict) else {}
    return {str(k): v for k, v in saved.items()
            if isinstance(v, dict) and isinstance(v.get("uid"), str) and isinstance(v.get("sig"), str)}


def _save(cfg, record):
    with cc_store.state_lock(cfg):
        state = cc_state.load(cfg)
        state["alerts"] = record
        cc_state.save(cfg, state)


def sync(cfg, view, at=None, runner=run_script):
    """Bring the calendar in line with the deadline view: add what is new, replace what moved, delete
    what is handed in or over. Returns {"added", "updated", "removed", "kept", "error", "events"}."""
    want = wanted(view, cfg, at)
    record = _record(cc_state.load(cfg))
    result = {"added": 0, "updated": 0, "removed": 0, "kept": 0, "error": "", "events": list(want.values())}
    try:
        stale = [k for k, r in record.items() if k not in want or want[k]["sig"] != r["sig"]]
        if stale:
            runner(DELETE, CALENDAR, *[record[k]["uid"] for k in stale])
        for key in stale:
            del record[key]
            result["removed" if key not in want else "updated"] += 1
        for key, ev in want.items():
            if key in record:
                result["kept"] += 1
                continue
            when = ev["start"].astimezone()             # the Calendar app thinks in this Mac's own timezone
            length = max(1, int((ev["end"] - ev["start"]).total_seconds() // 60))
            uid = runner(CREATE, CALENDAR, ev["title"], ev["notes"], ev["url"], when.year, when.month, when.day,
                         when.hour, when.minute, length, *ev["minutes"])
            record[key] = {"uid": uid, "sig": ev["sig"]}
            result["added"] += key not in stale
    except AlertError as e:
        result["error"] = str(e)
    _save(cfg, record)
    return result


def clear(cfg, runner=run_script):
    """Delete every event made here. Returns (how many, error text)."""
    record = _record(cc_state.load(cfg))
    if not record:
        return 0, ""
    try:
        runner(DELETE, CALENDAR, *[r["uid"] for r in record.values()])
    except AlertError as e:
        return 0, str(e)
    _save(cfg, {})
    return len(record), ""


def summary(result):
    """One line on what a sync did; '' when there was nothing to do."""
    if result["error"]:
        return cc_lang.warning(T("the phone alerts could not be updated: {why}", why=result["error"]))
    if not (result["added"] or result["updated"] or result["removed"]):
        return ""
    return T("Phone alerts: {added} added, {updated} changed, {removed} removed, {kept} as before.",
             added=result["added"], updated=result["updated"], removed=result["removed"], kept=result["kept"])


def lines(events, cfg):
    """One line per event: what it is, when, and when it rings."""
    tz = cc_time.zone(cfg)
    out = []
    for ev in events:
        span = T("{start} to {end}", start=cc_time.stamp(cc_time.local(ev["start"], tz)),
                 end=cc_time.clock(cc_time.local(ev["end"], tz)))
        rings = cc_lang.join(cc_time.stamp(cc_time.local(a, tz)) for a in ev["alarms"]) if ev["alarms"] \
            else T("no alarm left, it is too close")
        out.append("  " + T("{title}: {span}; rings {alarms}", title=ev["title"], span=span, alarms=rings))
    return out
