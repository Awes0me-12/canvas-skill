"""Where the student stands in each course, and what is needed to reach a target.

Two numbers are shown side by side and never mixed up: what Canvas itself reports, and our
own breakdown by assignment group. The breakdown ignores drop rules and marks the teacher has
not released, so it is labelled as an estimate everywhere.
"""
import cc_deadlines
import cc_lang
import cc_model
import cc_text
import cc_time
from cc_lang import T


def _num(value):
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def _pct(value, earned=0):
    if value is None:
        return T("extra credit") if earned else T("no marks yet")
    return f"{value:.1f}%"


def _score(a):
    """The numeric score, or None when the item is not graded (or excused, which never counts)."""
    sub = a.get("submission") or {}
    score = sub.get("score")
    if sub.get("excused") or sub.get("workflow_state") == "pending_review":   # Canvas leaves both out of the total
        return None
    return float(score) if isinstance(score, (int, float)) and not isinstance(score, bool) else None


def _rules(group):
    rules = group.get("rules") or {}
    parts = [T("drops the lowest {n}", n=int(rules[key])) if key == "drop_lowest" else T("drops the highest {n}", n=int(rules[key]))
             for key in ("drop_lowest", "drop_highest") if rules.get(key)]
    return cc_lang.join(parts)


def course_grades(payload, cfg, row):
    """Everything known about one course's grade. `row` comes from cc_model.course_table."""
    cid = row["id"]
    raw = next((c for c in payload.get("courses") or [] if cc_model.sid(c.get("id")) == cid), {})
    data = (payload.get("data") or {}).get(cid) or {}
    out = {"id": cid, "label": row["label"], "name": row["name"], "weighted": row["weighted"],
           "collected": isinstance(data.get("assignments"), list) and isinstance(data.get("assignment_groups"), list),
           "hidden_by_teacher": bool(raw.get("hide_final_grades")), "canvas_score": None, "canvas_grade": None,
           "groups": [], "estimate": None, "has_rules": False, "partial": bool(payload.get("slim"))}
    enrollments = [e for e in raw.get("enrollments") or [] if isinstance(e, dict)]
    mine = next((e for e in enrollments if "student" in str(e.get("type") or "").lower()), enrollments[0] if enrollments else {})
    if isinstance(mine.get("computed_current_score"), (int, float)):
        out["canvas_score"], out["canvas_grade"] = float(mine["computed_current_score"]), mine.get("computed_current_grade")
    if not out["collected"] or out["partial"]:
        return out          # a fallback-route collection leaves old graded work out: any sum would be wrong
    for g in sorted(data["assignment_groups"], key=lambda g: g.get("position") or 0):
        rows = [a for a in data["assignments"] if cc_model.sid(a.get("assignment_group_id")) == cc_model.sid(g.get("id"))
                and not a.get("omit_from_final_grade") and not (a.get("submission") or {}).get("excused")]
        if not rows and not (row["weighted"] and float(g.get("group_weight") or 0) > 0):
            continue                                    # an empty group that counts for nothing is only noise
        graded = [a for a in rows if _score(a) is not None]
        possible = sum(float(a.get("points_possible") or 0) for a in graded)
        earned = sum(_score(a) for a in graded)
        out["groups"].append({
            "name": str(g.get("name") or "").strip(), "weight": float(g.get("group_weight") or 0),
            "posted": len(rows), "graded": len(graded), "earned": earned, "possible": possible,
            "remaining": sum(float(a.get("points_possible") or 0) for a in rows if _score(a) is None),
            "percent": 100 * earned / possible if possible > 0 else None, "rules": _rules(g)})
    out["has_rules"] = any(g["rules"] for g in out["groups"])
    marked = [g for g in out["groups"] if g["percent"] is not None]
    if row["weighted"]:
        weight = sum(g["weight"] for g in marked)
        if weight > 0:      # as Canvas does: scale up only when the weights in play fall short of 100
            out["estimate"] = sum(g["weight"] * g["percent"] for g in marked) / (weight if weight < 100 else 100.0)
    else:                   # every group counts, so points earned on a 0-point (extra credit) item are kept
        possible = sum(g["possible"] for g in out["groups"])
        if possible > 0:
            out["estimate"] = 100 * sum(g["earned"] for g in out["groups"]) / possible
    return out


def needed(course, target):
    """What average is needed on everything not yet graded to finish at `target` percent.

    final(x) = a + b * x, where x is the share (0..1) earned on all remaining work.
    Returns {"case": secured | needs | unreachable | settled | unknown, "need", "best", "worst"} in percent.
    """
    a = b = 0.0
    if not course["collected"] or course["partial"]:
        return {"case": "unknown"}
    if course["weighted"]:
        groups = [g for g in course["groups"] if g["weight"] > 0]
        total = sum(g["weight"] for g in groups)
        if total <= 0:
            return {"case": "unknown"}
        scale = total if total < 100 else 100.0        # Canvas scales up only when the weights fall short of 100
        for g in groups:
            size = g["possible"] + g["remaining"]
            if size > 0:
                a += g["weight"] * g["earned"] / size / scale
                b += g["weight"] * g["remaining"] / size / scale
            else:                                       # nothing posted yet: all of this group is still to come
                b += g["weight"] / scale
    else:
        size = sum(g["possible"] + g["remaining"] for g in course["groups"])
        if size <= 0:
            return {"case": "unknown"}
        a = sum(g["earned"] for g in course["groups"]) / size
        b = sum(g["remaining"] for g in course["groups"]) / size
    best, worst = 100 * (a + b), 100 * a
    if b <= 1e-9:
        return {"case": "settled", "best": best, "worst": worst}
    x = (target / 100 - a) / b
    case = "secured" if x <= 0 else "unreachable" if x > 1 + 1e-9 else "needs"
    return {"case": case, "need": 100 * x, "best": best, "worst": worst}


def build_view(payload, cfg, at=None, course_id=None, target=None):
    at = at or cc_time.now()
    rows = [c for c in cc_model.course_table(payload, cfg, at) if not c["hidden"] and (course_id in (None, c["id"]))]
    courses = [course_grades(payload, cfg, r) for r in rows]
    for c in courses:
        c["target"] = needed(c, target) if target is not None else None
    collected = cc_time.parse_iso(payload.get("collected_at"))
    age = max(0.0, (at - collected).total_seconds() / 60) if collected else None
    notes = [T("The estimate counts only graded work and does not apply drop rules; Canvas's own number is the official one.")]
    if target is not None:
        notes.append(T("The target sum assumes no items are added, dropped or re-weighted, and that empty groups will "
                       "count for their full weight."))
    return {"courses": courses, "target": target, "host": payload.get("host") or "", "notes": notes,
            "age_text": T("unknown") if age is None else cc_time.age_text(age),
            "stale": age is None or age > cc_deadlines.STALE_HOURS * 60,
            "collected_text": cc_time.stamp(cc_time.local(collected, cc_time.zone(cfg))) if collected else T("unknown")}


def _target_line(c, target):
    t = c["target"]
    goal = f"{target:g}"
    if t["case"] == "unknown":
        return T("  For {goal}%: cannot be worked out from this collection.", goal=goal)
    if t["case"] == "settled":
        return T("  For {goal}%: nothing is left to be graded; the estimate stays at about {n}%.", goal=goal, n=f"{t['worst']:.1f}")
    if t["case"] == "secured":
        return T("  For {goal}%: already safe, even with nothing more earned (estimate at least {n}%).", goal=goal,
                 n=f"{t['worst']:.1f}")
    if t["case"] == "unreachable":
        return T("  For {goal}%: not reachable by this estimate; full marks on everything left gives about {n}%.",
                 goal=goal, n=f"{t['best']:.1f}")
    return T("  For {goal}%: you need about {n}% on average on everything not yet graded.", goal=goal, n=f"{t['need']:.0f}")


def _canvas_words(c):
    return f"{_num(c['canvas_score'])}%" + (f" ({c['canvas_grade']})" if c["canvas_grade"] else "")


def render_text(view, banner=""):
    out = [T("Grades (target {goal}%)", goal=f"{view['target']:g}") if view["target"] is not None else T("Grades")]
    if banner:
        out.append(cc_lang.warning(banner))
    if view["stale"]:
        out.append(cc_lang.warning(T("this data is old (collected {age}).", age=view["age_text"])))
    for c in view["courses"]:
        out += ["", f"== {c['label']}"]
        if c["hidden_by_teacher"]:
            out.append(T("  Canvas: the teacher has hidden the course total."))
        elif c["canvas_score"] is None:
            out.append(T("  Canvas shows no overall grade yet."))
        else:
            out.append(T("  Canvas shows: {grade} for the work graded so far.", grade=_canvas_words(c)))
        if not c["collected"]:
            out.append(T("  The assignments of this course were not collected, so there is no breakdown. Collect again."))
            continue
        if c["partial"]:
            out.append(T("  No breakdown: this collection came by the fallback route, which leaves out finished work "
                         "older than a month. For grades, collect with: coach.py fetch-script --full"))
            continue
        out.append(T("  By assignment group (graded work only):"))
        for g in c["groups"]:
            if not g["posted"]:
                detail = T("nothing posted yet")
            elif not g["graded"]:
                detail = T("{graded} of {posted} graded", graded=0, posted=g["posted"])
            else:
                detail = T("{graded} of {posted} graded, {points} points = {percent}", graded=g["graded"], posted=g["posted"],
                           points=f"{_num(g['earned'])}/{_num(g['possible'])}", percent=_pct(g["percent"], g["earned"]))
            weight = T(" ({n}% of grade)", n=_num(g["weight"])) if c["weighted"] else ""
            out.append(f"    {g['name']}{weight}: {detail}" + (f" [{g['rules']}]" if g["rules"] else ""))
        if c["estimate"] is None:
            out.append(T("  Estimate: nothing is graded yet."))
        else:
            out.append(T("  Estimate from this breakdown: {n}% so far.", n=f"{c['estimate']:.1f}"))
            if c["canvas_score"] is not None and abs(c["estimate"] - c["canvas_score"]) > 1:
                out.append(T("  It differs from Canvas because this breakdown ignores drop rules and marks the teacher "
                             "has not released."))
        if c["target"] is not None:
            out.append(_target_line(c, view["target"]))
    if not view["courses"]:
        out += ["", T("No current course to show.")]
    out += [""] + [cc_lang.note(n) for n in view["notes"]]
    out += ["", T("Data collected {when} ({age}) from {host}.", when=view["collected_text"], age=view["age_text"],
                  host=view["host"])]
    return "\n".join(out)


def render_html(view, banner=""):
    e = cc_text.escape
    out = cc_deadlines.page_head(T("Grades")) + [
        f"<h1>{e(T('Grades'))}</h1>",
        '<p class="sub">' + e(T("Collected {when}. Canvas's number is official; the breakdown is an estimate.",
                                when=view["collected_text"])) + "</p>"]
    alerts = ([banner] if banner else []) + ([cc_deadlines.stale_alert(view)] if view["stale"] else [])
    out += [f'<div class="banner">{e(a)}</div>' for a in alerts]
    for c in view["courses"]:
        canvas = (T("hidden by the teacher") if c["hidden_by_teacher"] else T("no overall grade yet")
                  if c["canvas_score"] is None else _canvas_words(c))
        out.append(f'<h2 class="course" style="border-left:4px solid hsl({cc_deadlines._hue(c["id"])} 55% 50%);'
                   f'padding-left:10px">{e(c["label"])}</h2>')
        out.append('<p class="sub">' + T("Canvas shows: {grade}", grade=f"<b>{e(canvas)}</b>")
                   + (e(T(" · estimate {n}%", n=f"{c['estimate']:.1f}")) if c["estimate"] is not None else "") + "</p>")
        if not c["collected"]:
            out.append('<div class="banner">' + e(T("The assignments of this course were not collected, so there is no "
                                                    "breakdown.")) + "</div>")
        elif c["partial"]:
            out.append('<div class="banner">' + e(T("No breakdown: this collection left out finished work older than a "
                                                    "month.")) + "</div>")
        if c["collected"] and c["groups"]:
            out.append("<ul>")
            for g in c["groups"]:
                detail = (T("nothing posted yet") if not g["posted"]
                          else T("{graded} of {posted} graded", graded=g["graded"], posted=g["posted"])
                          + (" · " + T("{n} pts", n=f"{_num(g['earned'])}/{_num(g['possible'])}") if g["graded"] else ""))
                out.append('<li style="--c:var(--line)"><div class="body"><div class="name">' + e(g["name"]) + "</div>"
                           f'<div class="meta">{e(detail)}' + (f" · {e(g['rules'])}" if g["rules"] else "") + "</div></div>"
                           f'<div class="when">{e(_pct(g["percent"], g["earned"]) if g["graded"] else "")}'
                           + ("<small>" + e(T("{n}% of grade", n=_num(g["weight"]))) + "</small>" if c["weighted"] else "")
                           + "</div></li>")
            out.append("</ul>")
        if c["target"] is not None:
            out.append(f'<p class="sub">{e(_target_line(c, view["target"]).strip())}</p>')
    out.append('<div class="notes">' + "".join(f"<p>{e(n)}</p>" for n in view["notes"]) + "</div>")
    out.append("<footer>" + e(T("Collected {when} from {host}. Made by Canvas Coach.", when=view["collected_text"],
                                host=view["host"])) + "</footer>")
    out.append("</main></body></html>")
    return "\n".join(out)
