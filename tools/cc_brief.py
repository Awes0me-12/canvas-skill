"""The short briefing: today's must-do, what needs attention in the next two days, and what moved on Canvas.

Made for a notification and a glance, not for planning; `deadlines`, `week` and `plan` are the full views.
"""
import subprocess
import sys
from datetime import timedelta

import cc_deadlines
import cc_lang
import cc_time
from cc_lang import T, plural

CLASH_DAYS = 7      # the briefing only mentions clashes this close


def build(payload, cfg, at=None, hours=48):
    """Pick the urgent items out of the deadline view."""
    at = at or cc_time.now()
    view = cc_deadlines.build_view(payload, cfg, at=at)
    limit = at + timedelta(hours=hours)
    by = {sec["key"]: sec["items"] for sec in view["sections"]}
    upcoming = [i for i in by["week"] + by["later"] if i["deadline"] and i["deadline"] <= limit]
    return {
        "hours": hours, "now_text": view["now_text"], "collected_text": view["collected_text"],
        "age_text": view["age_text"], "stale": view["stale"], "warnings": view["warnings"],
        "soon": [i for i in upcoming if not i["done"] and i["status"] != "offline"],
        "in_class": [i for i in upcoming if i["status"] == "offline"],
        "overdue": [i for i in by["overdue"] if i["status"] == "overdue" and i["kind"] != "external tool"],
        "check_elsewhere": [i for i in by["overdue"] if i["kind"] == "external tool"],
        "clashes": [c for c in view["clashes"] if c["start"] <= at + timedelta(days=CLASH_DAYS)],
        "clash_days": CLASH_DAYS, "must": None, "view": view,
    }


def _short(it):
    return f"{it['name']} ({it['course']}) {it['due_day']} {it['due_time']}".strip() + ("" if it["confirmed"] else " (?)")


def headline(brief, changes=()):
    """A few words for a notification title, or '' when nothing needs attention."""
    parts = []
    if brief["soon"]:
        parts.append(T("{n} due in {hours} h", n=len(brief["soon"]), hours=brief["hours"]))
    if brief["overdue"]:
        parts.append(T("{n} overdue", n=len(brief["overdue"])))
    if brief["in_class"]:
        parts.append(T("{n} in class", n=len(brief["in_class"])))
    if changes:
        parts.append(plural(len(changes), "{n} change", "{n} changes"))
    return cc_lang.join(parts)


def notification(brief, changes=(), banner="", logged_out=False):
    """(title, message) for a desktop notification, or None when there is nothing worth one."""
    if logged_out:
        return (T("Canvas Coach: please log in"),
                T("Your Canvas login has expired. Ask your assistant to check Canvas and log in again."))
    title = headline(brief, changes)
    must = brief.get("must")
    if not title and not must:
        return None
    lines = [T("Must-do today: {task}", task=must["short"])] if must else []
    lines += [_short(i) for i in (brief["overdue"] + brief["soon"] + brief["in_class"])[:3]
              if not must or i["key"] != must.get("key")]
    lines += [c["text"] for c in brief["clashes"][:1]]
    lines += [c["text"] for c in list(changes)[:2]]
    if banner:
        lines.append(T("Could not refresh; this is the last known list."))
    return T("Canvas: {headline}", headline=title or T("today's plan")), cc_lang.join(lines, "clauses")[:300]


def render_text(brief, changes_lines=(), banner="", extra=()):
    out = [T("Canvas brief, {now}", now=brief["now_text"])]
    if banner:
        out.append(cc_lang.warning(banner))
    if brief["stale"]:
        out.append(cc_lang.warning(T("this data is old (collected {age}).", age=brief["age_text"])))
    out += [cc_lang.warning(w) for w in brief["warnings"]]
    out += [T("NOTE: {text}", text=line) for line in extra]
    if brief.get("must"):
        out += ["", T("Must-do today: {task}", task=brief["must"]["text"])]
        if brief["must"].get("why"):
            out.append("  " + T("Why: {why}", why=brief["must"]["why"]))
    groups = (("overdue", T("Overdue and still open")), ("soon", T("Due in the next {hours} hours", hours=brief["hours"])),
              ("in_class", T("In class or on paper in the next {hours} hours", hours=brief["hours"])),
              ("check_elsewhere", T("Past due on an outside site (check there)")))
    for key, title in groups:
        if brief[key]:
            out += ["", T("{title} ({n})", title=title, n=len(brief[key]))]
            out += ["  " + cc_deadlines._line(i, "overdue" if key in ("overdue", "check_elsewhere") else "later")
                    for i in brief[key]]
    if brief["clashes"]:
        out += [""] + cc_deadlines.clash_lines(dict(brief["view"], clashes=brief["clashes"], clash_days=brief["clash_days"]))
    if changes_lines:
        out += [""] + list(changes_lines)
    if not any(brief[key] for key, _ in groups) and not changes_lines and not brief["clashes"]:
        out += ["", T("Nothing is due in the next {hours} hours and nothing changed on Canvas.", hours=brief["hours"])]
    out += ["", T("Data collected {when} ({age}).", when=brief["collected_text"], age=brief["age_text"])]
    return "\n".join(out)


def notify(title, message):
    """Show a desktop notification (macOS). Returns True when one was shown."""
    if sys.platform != "darwin":
        return False
    script = ["-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
              "-e", "end run"]
    try:
        return subprocess.run(["osascript", *script, title, message], capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
