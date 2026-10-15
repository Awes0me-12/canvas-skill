"""Commands: plan (the day-by-day study plan) and classes (when each course meets, which the plan uses)."""
import json

import cc_deadlines
import cc_lang
import cc_model
import cc_plan
import cc_state
import cc_store
import cc_time
from cc_cli import (FAILED, NEED_LOGIN, OK, REFRESH_ARGS, arg, command, login_hint, no_data, no_such_course, open_file,
                    refresh, say, write_page)
from cc_lang import T

PAGE_NAME = "Study Plan.html"


@command("plan", "A day-by-day study plan for the coming week: one must-do a day, and why.",
         arg("--days", type=int, help="how many days to plan (default 7)"),
         arg("--rest", metavar="DAY", help="this week's rest day: fri, sat, sun, ..., none, or auto to let the plan choose"),
         arg("--keys", action="store_true", help="show each task's key (for: coach.py done KEY)"),
         *REFRESH_ARGS,
         arg("--json", action="store_true", help="print machine-readable JSON instead of text"),
         arg("--open", action="store_true", help="open the page in your browser afterwards"))
def cmd_plan(args):
    cfg = cc_store.load_config()
    if args.days is not None and not 1 <= args.days <= 28:
        raise ValueError(T("--days must be between 1 and 28."))
    rest = (args.rest or "").strip().lower()[:4]
    rest = rest if rest in ("none", "auto") else rest[:3]
    if args.rest and rest not in ("none", "auto") + cc_time.DAY_CODES:
        raise ValueError(T("--rest takes a day (fri, sat, sun, ...), none, or auto."))
    payload, banner, code = refresh(cfg, args.force, args.offline)
    if payload is None:
        return no_data(banner, code)
    plan = cc_plan.build(payload, cfg, days=args.days, rest=rest or None)
    page = write_page(cfg, PAGE_NAME, cc_plan.render_html(plan, banner))
    if args.json:
        print(json.dumps(dict(cc_deadlines.jsonable(plan), banner=banner, page=str(page)), ensure_ascii=False, indent=1))
    else:
        say(cc_plan.render_text(plan, banner, keys=args.keys), T("Page: {path}", path=page))
        if code == NEED_LOGIN:
            say(login_hint())
    if args.open:
        open_file(page)
    return code


@command("classes", "When each course meets (the plan uses it): classes | classes set COURSE DAYS TIME [--section] | "
                    "classes clear COURSE",
         arg("action", nargs="?", default="show", choices=["show", "set", "clear"]),
         arg("values", nargs="*", help='for set: the course, its days and its time, e.g. "HIST 101" tue,thu 15:30'),
         arg("--section", action="store_true", help="this is a discussion section or lab, not the lecture"))
def cmd_classes(args):
    cfg = cc_store.load_config()
    payload, _ = cc_store.latest_snapshot(cfg)
    table = cc_model.course_table(payload, cfg, cc_time.now()) if payload else []
    times = cc_plan.class_times(cfg)
    if args.action == "show":
        labels = {c["id"]: c["label"] for c in table}
        lines = [T("Class times:")]
        for cid, rows in times.items():
            for row in rows:
                days = cc_lang.join(T(cc_time.DAYS[cc_time.DAY_CODES.index(d)]) for d in row["days"])
                kind = T("section") if row["kind"] == "section" else T("lecture")
                lines.append(f"  {labels.get(cid, cid)}: {days} {row['time']} ({kind})")
        missing = [c["label"] for c in table if not c["hidden"] and c["id"] not in times]
        say(*(lines if len(lines) > 1 else lines + [T("  none")]),
            *([T("Not set yet: {courses}", courses=cc_lang.join(missing))] if missing else []),
            T('Set one with: coach.py classes set "<course>" tue,thu 15:30     (add --section for a section or lab)'))
        return OK
    if not args.values:
        raise ValueError(T('Say which course, for example: coach.py classes set "HIST 101" tue,thu 15:30'))
    wanted = 1 if args.action == "clear" else 3
    words = args.values if args.action == "clear" else args.values[:-2]
    if len(args.values) < wanted or not words:
        raise ValueError(T('Say which course, for example: coach.py classes set "HIST 101" tue,thu 15:30'))
    course = cc_state.match_course(table, " ".join(words))
    if course is None:
        return no_such_course(" ".join(words), table)
    kind = "section" if args.section else "lecture"
    rows = [r for r in times.get(course["id"], []) if args.action == "set" and r["kind"] != kind]
    if args.action == "set":
        rows.append({"days": cc_plan.parse_days(args.values[-2]), "time": cc_plan.parse_clock(args.values[-1]), "kind": kind})
    saved = dict(cfg.get("class_times") or {})
    if rows:
        saved[course["id"]] = rows
    else:
        saved.pop(course["id"], None)
    cc_store.update_config(class_times=saved)
    if args.action == "clear":
        say(T("Class times for {course} removed.", course=course["label"]))
    else:
        days = cc_lang.join(T(cc_time.DAYS[cc_time.DAY_CODES.index(d)]) for d in rows[-1]["days"])
        say(T("Saved: {course} meets {days} at {time} ({kind}).", course=course["label"], days=days, time=rows[-1]["time"],
              kind=T("section") if kind == "section" else T("lecture")))
    return OK if course else FAILED
