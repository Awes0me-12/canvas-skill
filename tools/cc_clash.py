"""Deadline clashes: unfinished work from two or more courses falling close together.

The deadline view calls find() and shows the result above the list; the briefing and the study
plan read it from there. The window (48 hours) and the look-ahead (21 days) are settings.
"""
from datetime import timedelta

import cc_time
from cc_lang import T, plural


def counts(it, min_percent=1.0):
    """Whether an item is big enough to make a clash: open, and worth a real share of the grade.

    Small routine items (a 4-point survey in a group of eighteen) would otherwise make every week
    look like a clash. An item counts when it is an exam, when its share of the course grade is at
    least `min_percent` percent, or when that share cannot be told at all.
    """
    if it["done"] or it["status"] in ("missed", "closed"):
        return False
    if it["exam"]:
        return True
    if it["status"] == "offline":
        return False                 # nothing to hand in and not an exam: attendance, participation and the like
    if it["weight"] == 0 or (it["course_weighted"] and it["group_weight"] == 0):
        return False                 # Canvas says it does not count towards the grade
    if not it["manual"] and not it["points"]:
        return False                 # worth no points
    return it["weight"] is None or it["weight"] >= min_percent


def find(items, at, tz, hours=48, days=21, min_percent=1.0):
    """Clashes among `items` (from the deadline view, each with a "deadline"), earliest first.

    A window of `hours` opens at the earliest deadline that still has company: everything due
    inside it forms one clash when it spans two or more courses. The search continues after
    that window, so clashes never overlap and none is longer than the window.
    Each clash: {"start", "end", "label", "items", "keys", "courses", "exams", "text"}.
    """
    limit = at + timedelta(days=days)
    rows = sorted((i for i in items if i.get("deadline") and at <= i["deadline"] <= limit and counts(i, min_percent)),
                  key=lambda i: (i["deadline"], i["course"], i["name"]))
    span = timedelta(hours=hours)
    out, n = [], 0
    while n < len(rows):
        window = [r for r in rows[n:] if r["deadline"] - rows[n]["deadline"] <= span]
        courses = sorted({r["course"] for r in window})
        if len({r["course_id"] for r in window}) < 2:
            n += 1
            continue
        first, last = cc_time.local(window[0]["deadline"], tz), cc_time.local(window[-1]["deadline"], tz)
        label = cc_time.day(first) if first.date() == last.date() else T("{first} to {last}", first=cc_time.day(first),
                                                                         last=cc_time.day(last))
        exams = sum(1 for r in window if r["exam"])
        text = T("{when}: {n} deadlines from {courses} courses within {hours} hours", when=label, n=len(window),
                 courses=len(courses), hours=hours)
        if exams:
            text += plural(exams, " (including {n} exam)", " (including {n} exams)")
        out.append({"start": window[0]["deadline"], "end": window[-1]["deadline"], "label": label, "items": window,
                    "keys": [r["key"] for r in window], "courses": courses, "exams": exams, "text": text})
        n += len(window)
    return out


def mark(items, clashes):
    """Set it["clash"] on every item that sits in a clash, so lists and the plan can say so."""
    inside = {key for c in clashes for key in c["keys"]}
    for it in items:
        it["clash"] = it["key"] in inside
    return items
