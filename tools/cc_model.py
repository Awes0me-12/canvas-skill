"""Turn raw Canvas data into plain facts about each assignment.

Nothing here prints or formats; views (cc_deadlines.py and future ones) build on it.
"""
import re
from datetime import timedelta

import cc_text
import cc_time
from cc_lang import T

DONE = ("submitted", "graded", "excused")
NO_UPLOAD = ("none", "on_paper", "not_graded")
TERM_GRACE_DAYS = 14
# A title that names a sit-down exam ("Midterm 2", "Final", "期中考试"), not a "Final Project" or "Final Paper".
EXAM = re.compile(r"\bmid-?terms?\b|\bexam(?:s|ination)?\b|\bfinals?\b(?!\s+(?:project|paper|essay|draft|report|"
                  r"presentation|portfolio|reflection|notebook|slides|video))|期中|期末|考试", re.I)


def looks_like_exam(name):
    return bool(EXAM.search(str(name or "")))


def sid(value):
    return "" if value is None else str(value)


def _rows(course_data, name):
    rows = course_data.get(name) if isinstance(course_data, dict) else None
    return rows if isinstance(rows, list) else []


def _roles(course):
    """How the user is enrolled: {'student'}, {'ta'}, ... Empty when Canvas did not say."""
    return {str(e.get("type") or "").lower().replace("enrollment", "")
            for e in course.get("enrollments") or [] if e.get("type")}


def _has_current_work(course_data, cutoff):
    """True when anything in the course is due or closes after `cutoff`."""
    for a in _rows(course_data, "assignments"):
        for key in ("due_at", "lock_at"):
            when = cc_time.parse_iso(a.get(key))
            if when and when > cutoff:
                return True
    return False


def course_table(payload, cfg, at):
    """Every course in the payload, with whether (and why not) it is shown."""
    ignore = {sid(x) for x in cfg.get("ignore_courses") or []}
    include = {sid(x) for x in cfg.get("include_courses") or []}
    names = cfg.get("course_names") or {}
    tz = cc_time.zone(cfg)
    data = payload.get("data") or {}
    cutoff = at - timedelta(days=TERM_GRACE_DAYS)
    errors = {}
    for e in payload.get("errors") or []:
        errors.setdefault(sid(e.get("course_id")), []).append(e)
    rows = []
    for c in payload.get("courses") or []:
        cid = sid(c.get("id"))
        term = c.get("term") or {}
        ends = [d for d in (cc_time.parse_iso(term.get("end_at")), cc_time.parse_iso(c.get("end_at"))) if d]
        end = max(ends) if ends else None
        roles = _roles(c)
        hidden = why = ""      # `hidden` is the reason in words (empty = shown); `hidden_code` is for the code to test
        if c.get("access_restricted_by_date"):
            why, hidden = "restricted", T("not open to you right now")
        elif cid in ignore:
            why, hidden = "ignored", T("ignored by you")
        elif cid in include:
            pass
        elif roles and "student" not in roles:
            why, hidden = "not_student", T("you are not a student in it ({roles})", roles=", ".join(sorted(roles)))
        elif end and end < cutoff and not _has_current_work(data.get(cid), cutoff):
            # Dates alone are not proof: Canvas still lists it as active, so current work keeps it visible.
            why, hidden = "term_ended", T("its term ended {day}", day=cc_time.day(cc_time.local(end, tz), with_year=True))
        rows.append({
            "id": cid,
            "label": cc_text.one_line(names.get(cid) or c.get("course_code") or c.get("name"))
            or T("Course {id}", id=cid),
            "name": cc_text.one_line(c.get("name")),
            "term": cc_text.one_line(term.get("name")),
            "time_zone": c.get("time_zone") if isinstance(c.get("time_zone"), str) else "",
            "weighted": bool(c.get("apply_assignment_group_weights")),
            "hidden": hidden, "hidden_code": why,
            "collected": cid in data,
            "errors": errors.get(cid, []),
        })
    return rows


def _groups(course_data):
    """Assignment groups by id, each with points_total (from the collector, or counted here)."""
    assignments = _rows(course_data, "assignments")
    groups = {}
    for g in _rows(course_data, "assignment_groups"):
        g = dict(g)
        mine = [a for a in assignments if sid(a.get("assignment_group_id")) == sid(g.get("id"))
                and not a.get("omit_from_final_grade")]
        if g.get("points_total") is None:
            g["points_total"] = sum(float(a.get("points_possible") or 0) for a in mine)
        if g.get("assignment_count") is None:
            g["assignment_count"] = len(mine)
        groups[sid(g.get("id"))] = g
    return groups


def weight_percent(a, weighted, groups, course_points):
    """Rough share of the final grade, in percent. None when it cannot be told.

    An estimate for sorting and for later grade maths, not for display: it assumes no more
    items will be added to the group, and ignores drop-lowest rules and extra credit. Early
    in a term that overstates things badly (2 homeworks posted = "50% each"), so the list
    shows the group's weight as Canvas states it instead.
    """
    if a.get("omit_from_final_grade"):
        return 0.0
    points = float(a.get("points_possible") or 0)
    if points <= 0:
        return None
    if weighted:
        g = groups.get(sid(a.get("assignment_group_id")))
        if not g or not g.get("group_weight") or not g.get("points_total"):
            return None
        return min(100.0, float(g["group_weight"]) * points / float(g["points_total"]))
    return min(100.0, 100.0 * points / course_points) if course_points else None


def module_locked(a):
    """Locked because earlier module items are unfinished, which is not the same as closed."""
    return bool((a.get("lock_info") or {}).get("context_module"))


def classify(a, at):
    """One word for where this assignment stands for the student right now.

    The rule of thumb: only call something done when Canvas shows a real hand-in.
    A grade alone is not proof, because teachers type zeros for work that never arrived.
    """
    sub = a.get("submission") or {}
    state = sub.get("workflow_state") or "unsubmitted"
    types = a.get("submission_types") or []
    due = cc_time.parse_iso(a.get("due_at"))
    lock = cc_time.parse_iso(a.get("lock_at"))
    unlock = cc_time.parse_iso(a.get("unlock_at"))
    not_open = bool(unlock and unlock > at)
    past_due = bool(due and due < at)
    no_upload = bool(types) and all(t in NO_UPLOAD for t in types)
    off_canvas = bool(types) and all(t in NO_UPLOAD or t == "external_tool" for t in types)
    if sub.get("excused"):
        return "excused"
    if not sub.get("missing") and not sub.get("redo_request"):
        if sub.get("submitted_at") or state in ("submitted", "pending_review"):
            return "graded" if state == "graded" else "submitted"
        if state == "graded" and (off_canvas or float(sub.get("score") or 0) > 0):
            return "graded"  # marked without a Canvas hand-in: paper exam, outside tool, or work given in person
    if no_upload:
        # Nothing to upload: in class, on paper, or ungraded. Only the teacher's "missing" mark says otherwise.
        return "missed" if sub.get("missing") and past_due else "offline"
    closed = bool(lock and lock < at) or bool(a.get("locked_for_user") and past_due and not not_open
                                              and not module_locked(a))
    if past_due:
        return "missed" if closed else "overdue"
    return "closed" if closed else "todo"


def kind_of(a):
    types = a.get("submission_types") or []
    if a.get("is_quiz_assignment") or a.get("is_quiz_lti_assignment") or "online_quiz" in types:
        return "quiz"
    if "discussion_topic" in types:
        return "discussion"
    if "external_tool" in types:
        return "external tool"
    return "assignment"


def blank_item(**fields):
    """An item with every field the views read, so items built elsewhere (manual deadlines) fit in."""
    item = {"key": "", "course_id": "", "course": "", "course_name": "", "id": "", "name": "", "url": "",
            "kind": "assignment", "status": "todo", "done": False, "due": None, "lock": None, "unlock": None,
            "not_open": False, "locked": False, "module_locked": False, "redo": False, "zero_without_handin": False,
            "points": None, "weight": None, "group": "", "group_weight": None, "group_count": None,
            "course_weighted": False, "course_points": 0, "submitted_at": None, "score": None, "grade": None,
            "late": False, "manual": False, "note": "", "pending_review": False, "course_tz": "",
            # Stage 2 and later: what the student added or corrected, and what the plan and clash checks read.
            "exam": False, "own_due": False, "canvas_due": None, "confirmed": True, "alt_dates": [], "place": "",
            "source": "", "hidden_text": False, "own_weight": None, "clash": False, "canvas_moved": False}
    item.update(fields)
    return item


def items(payload, cfg, at):
    """One dict per assignment in the courses that are shown."""
    host = payload.get("host") or ""
    out = []
    for course in course_table(payload, cfg, at):
        if course["hidden"] or not course["collected"]:
            continue
        data = payload["data"].get(course["id"]) or {}
        groups = _groups(data)
        course_points = sum(float(g.get("points_total") or 0) for g in groups.values())
        for a in _rows(data, "assignments"):
            sub = a.get("submission") or {}
            status = classify(a, at)
            done = status in DONE
            unlock = cc_time.parse_iso(a.get("unlock_at"))
            url = a.get("html_url") or ""
            group = groups.get(sid(a.get("assignment_group_id"))) or {}
            out.append(blank_item(**{
                "key": f"{course['id']}:{sid(a.get('id'))}",
                "course_id": course["id"], "course": course["label"], "course_name": course["name"],
                "id": sid(a.get("id")), "name": cc_text.one_line(a.get("name")) or T("(untitled)"),
                "exam": looks_like_exam(a.get("name")), "hidden_text": a.get("hidden_text") is True,
                "course_tz": course["time_zone"],
                # A quiz waiting for manual marking carries a partial score that Canvas does not count yet.
                "pending_review": sub.get("workflow_state") == "pending_review",
                "url": url if host and url.startswith(host + "/") else "",
                "kind": kind_of(a), "status": status, "done": done,
                "due": cc_time.parse_iso(a.get("due_at")),
                "lock": cc_time.parse_iso(a.get("lock_at")),
                "unlock": unlock, "not_open": bool(unlock and unlock > at),
                "locked": bool(a.get("locked_for_user")), "module_locked": module_locked(a),
                "redo": bool(sub.get("redo_request")) and not done,
                "zero_without_handin": (not done and sub.get("workflow_state") == "graded"
                                        and sub.get("score") == 0 and not sub.get("submitted_at")),
                "points": a.get("points_possible"),
                "weight": weight_percent(a, course["weighted"], groups, course_points),
                "group": cc_text.one_line(group.get("name")),
                "group_weight": group.get("group_weight") if course["weighted"] else None,
                "group_count": group.get("assignment_count"),
                "course_weighted": course["weighted"], "course_points": course_points,
                "submitted_at": cc_time.parse_iso(sub.get("submitted_at")),
                "score": sub.get("score"), "grade": sub.get("grade"), "late": bool(sub.get("late")),
            }))
    return out
