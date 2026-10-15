"""What markers wrote back (comments and rubric marks), and an assignment's full instructions.

`feedback` reads the snapshot. `show` asks Canvas for one assignment's instructions and rubric,
so that a draft can be checked against exactly what was asked.
"""
from datetime import timedelta

import cc_changes
import cc_lang
import cc_model
import cc_text
import cc_time
from cc_lang import N, T

RECENT_DAYS = 30
HAND_IN = {"online_upload": N("upload a file on Canvas"), "online_text_entry": N("type the text on Canvas"),
           "online_url": N("hand in a web address on Canvas"), "online_quiz": N("take the quiz on Canvas"),
           "discussion_topic": N("post in the discussion"), "external_tool": N("hand in through an outside site"),
           "media_recording": N("record audio or video on Canvas"),
           "student_annotation": N("annotate the document on Canvas"),
           "on_paper": N("on paper or in class (nothing to upload)"), "none": N("nothing to hand in on Canvas"),
           "not_graded": N("not graded")}


def _num(value):
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def _when(text, tz):
    when = cc_time.parse_iso(text)
    return cc_time.stamp(cc_time.local(when, tz)) if when else ""


def build_view(payload, cfg, at=None, course_id=None, everything=False):
    """Marker feedback per course and assignment, newest first."""
    at = at or cc_time.now()
    tz = cc_time.zone(cfg)
    me = cc_model.sid((payload.get("user") or {}).get("id"))
    my_names = {(payload.get("user") or {}).get(k) for k in ("name", "short_name")} - {None, ""}
    cutoff = at - timedelta(days=RECENT_DAYS)
    courses, count = [], 0
    for c in cc_model.course_table(payload, cfg, at):
        if c["hidden"] or not c["collected"] or course_id not in (None, c["id"]):
            continue
        data = (payload.get("data") or {}).get(c["id"]) or {}
        subs = data.get("submissions")
        row = {"id": c["id"], "label": c["label"], "collected": isinstance(subs, list), "items": []}
        names = {cc_model.sid(a.get("id")): a for a in data.get("assignments") or [] if isinstance(a, dict)}
        for s in subs if isinstance(subs, list) else []:
            comments = [x for x in s.get("submission_comments") or []
                        if isinstance(x, dict) and not cc_changes.is_mine(x, me, my_names)]
            rubric = s.get("rubric_assessment") if isinstance(s.get("rubric_assessment"), dict) else {}
            if not comments and not rubric:
                continue
            times = [cc_time.parse_iso(x.get("created_at")) for x in comments] + [cc_time.parse_iso(s.get("graded_at"))]
            latest = max([t for t in times if t], default=None)
            spoken = max([t for t in times[:-1] if t], default=None)
            if not everything and (latest is None or latest < cutoff):
                continue
            a = names.get(cc_model.sid(s.get("assignment_id")), {})
            points = a.get("points_possible")
            score = s.get("score")
            row["items"].append({
                "key": f"{c['id']}:{cc_model.sid(s.get('assignment_id'))}",
                "title": cc_text.one_line(a.get("name")) or T("an assignment"),
                "url": a.get("html_url") or "", "latest": latest, "spoken": spoken,
                "score_text": (f"{_num(score)}" + (f"/{_num(points)}" if points else "")) if isinstance(score, (int, float)) else "",
                "late_text": T("{n} point(s) taken off for lateness", n=_num(s["points_deducted"])) if s.get("points_deducted") else "",
                "comments": [{"who": cc_text.one_line(x.get("author_name")) or T("someone"), "when": _when(x.get("created_at"), tz),
                              "at": cc_time.parse_iso(x.get("created_at")), "text": cc_text.html_to_text(x.get("comment"))}
                             for x in sorted(comments, key=lambda x: x.get("created_at") or "", reverse=True)],
                "rubric": [{"id": cid, "points": r.get("points"), "comment": cc_text.html_to_text(r.get("comments") or "")}
                           for cid, r in rubric.items() if isinstance(r, dict)]})
        never = cc_time.parse_iso("1970-01-01T00:00:00Z")
        row["items"].sort(key=lambda i: (i["latest"] or never, i["spoken"] or never), reverse=True)
        count += len(row["items"])
        courses.append(row)
    return {"courses": courses, "count": count, "everything": everything, "days": RECENT_DAYS}


def render_text(view, banner=""):
    out = [T("Feedback from your markers") if view["everything"]
           else T("Feedback from your markers (last {days} days)", days=view["days"])]
    if banner:
        out.append(cc_lang.warning(banner))
    for c in view["courses"]:
        if not c["collected"]:
            out += ["", f"== {c['label']}", T("  Feedback for this course was not collected. Collect again.")]
            continue
        if not c["items"]:
            continue
        out += ["", f"== {c['label']}"]
        for i in c["items"]:
            head = f"  {i['title']}" + (f": {i['score_text']}" if i["score_text"] else "")
            out.append(head + (f" ({i['late_text']})" if i["late_text"] else "") + f"  [key {i['key']}]")
            for m in i["comments"]:
                out.append(f"    {m['who']}, {m['when']}:")
                out += ["      " + line for line in m["text"].splitlines() if line.strip()]
            for n, r in enumerate(i["rubric"], 1):
                out.append((T("    Rubric line {n}: {points} points", n=n, points=_num(r["points"])) if r["points"] is not None
                            else T("    Rubric line {n}: no points", n=n)) + (f" — {r['comment']}" if r["comment"] else ""))
            if i["rubric"]:
                out.append(T("    (For what each rubric line means: coach.py show {key})", key=i["key"]))
    out += ["", T("{n} assignment(s) with feedback.", n=view["count"])
            + ("" if view["everything"] or view["count"] else T(" Use --all to look further back."))]
    return "\n".join(out)


def hand_in_words(assignment):
    types = assignment.get("submission_types") or []
    words = [T(HAND_IN[t]) if t in HAND_IN else t.replace("_", " ") for t in types] or [T("not stated")]
    extra = []
    if assignment.get("allowed_extensions"):
        extra.append(T("file types allowed: {types}", types=", ".join(str(x) for x in assignment["allowed_extensions"])))
    attempts = assignment.get("allowed_attempts")
    if isinstance(attempts, int) and attempts > 0:
        extra.append(T("{n} attempt(s) allowed", n=attempts))
    elif attempts == -1:
        extra.append(T("unlimited attempts"))
    return cc_lang.join([T(" or ").join(words)] + extra, "clauses")


def render_details(assignment, item, submission, cfg):
    """One assignment's instructions and rubric as Markdown. Nothing is invented: only what Canvas returned."""
    tz = cc_time.zone(cfg)
    lines = [f"# {assignment.get('name') or item['name']}", "", T("- Course: {course}", course=item["course"])]
    for label, key in ((T("- Due: {when}"), "due_at"), (T("- Closes: {when}"), "lock_at"), (T("- Opens: {when}"), "unlock_at")):
        if assignment.get(key):
            lines.append(label.format(when=_when(assignment[key], tz)))
    if not assignment.get("due_at"):
        lines.append(T("- Due: no due date on Canvas"))
    if assignment.get("points_possible") is not None:
        lines.append(T("- Points: {n}", n=_num(assignment["points_possible"])))
    lines.append(T("- How to hand in: {how}", how=hand_in_words(assignment)))
    if assignment.get("html_url"):
        lines.append(T("- On Canvas: {url}", url=assignment["html_url"]))
    body = cc_text.html_to_text(assignment.get("description"))
    if not body and assignment.get("locked_for_user"):
        why = cc_text.html_to_text(assignment.get("lock_explanation"), show_links=False)
        body = T("(Canvas does not show the instructions yet: {why})", why=why) if why \
            else T("(Canvas does not show the instructions yet.)")
    lines += ["", T("## Instructions"), "", body or T("(Canvas has no instructions text for this item.)")]
    if cc_text.has_hidden_text(assignment.get("description")):
        lines += ["", cc_text.hidden_note()]
    found = [(t, u) for t, u in cc_text.links(assignment.get("description")) if u and not u.startswith("#")]
    if found:
        lines += ["", T("## Links in the instructions"), ""] + [f"- {t}: {u}" for t, u in found]
    rubric = assignment.get("rubric") if isinstance(assignment.get("rubric"), list) else []
    marks = (submission or {}).get("rubric_assessment") if isinstance((submission or {}).get("rubric_assessment"), dict) else {}
    if rubric:
        total = (assignment.get("rubric_settings") or {}).get("points_possible")
        lines += ["", T("## Rubric ({n} points)", n=_num(total)) if total is not None else T("## Rubric"), ""]
        points = lambda v: T("{n} points", n=_num(v)) if isinstance(v, (int, float)) else T("no points")
        for r in rubric:
            lines.append(f"### {r.get('description') or T('Criterion')}"
                         + (f" ({points(r.get('points'))})" if isinstance(r.get("points"), (int, float)) else ""))
            if r.get("long_description"):
                lines.append(cc_text.html_to_text(r["long_description"]))
            for rating in r.get("ratings") or []:
                lines.append(f"- {rating.get('description') or T('Rating')}: {points(rating.get('points'))}"
                             + (f" ({cc_text.html_to_text(rating['long_description'])})" if rating.get("long_description") else ""))
            mine = marks.get(r.get("id"))
            if isinstance(mine, dict):
                lines.append(T("- Your mark: {points}", points=points(mine.get("points")))
                             + (f" — {cc_text.html_to_text(mine['comments'])}" if mine.get("comments") else ""))
            lines.append("")
    else:
        lines += ["", T("## Rubric"), "", T("This assignment has no rubric on Canvas.")]
    return "\n".join(lines).rstrip() + "\n"
