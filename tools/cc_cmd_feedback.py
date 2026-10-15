"""Commands: feedback (what markers wrote) and show (one assignment's instructions and rubric)."""
import json

import cc_deadlines
import cc_feedback
import cc_lang
import cc_model
import cc_state
import cc_store
import cc_text
import cc_time
from cc_cli import (FAILED, NEED_LOGIN, OK, REFRESH_ARGS, arg, command, data_age, fetch_extra, login_hint, no_data,
                    no_such_course, refresh, say)
from cc_lang import T, note, warning

KEEP = ["id", "name", "description", "due_at", "lock_at", "unlock_at", "points_possible", "submission_types",
        "allowed_extensions", "allowed_attempts", "grading_type", "rubric", "rubric_settings", "html_url",
        "locked_for_user", "lock_explanation"]


@command("feedback", "Comments and rubric marks your markers left, newest first.",
         arg("course", nargs="*", help="a few words of one course's name (default: all current courses)"),
         arg("--all", action="store_true", help="everything, not only the last 30 days"),
         *REFRESH_ARGS,
         arg("--json", action="store_true", help="print machine-readable JSON instead of text"))
def cmd_feedback(args):
    cfg = cc_store.load_config()
    payload, banner, code = refresh(cfg, args.force, args.offline)
    if payload is None:
        return no_data(banner, code)
    course_id = None
    if args.course:
        table = cc_model.course_table(payload, cfg, cc_time.now())
        match = cc_state.match_course(table, " ".join(args.course))
        if match is None:
            return no_such_course(" ".join(args.course), table)
        course_id = match["id"]
    view = cc_feedback.build_view(payload, cfg, course_id=course_id, everything=args.all)
    if args.json:
        print(json.dumps(dict(cc_deadlines.jsonable(view), banner=banner), ensure_ascii=False, indent=1))
    else:
        say(cc_feedback.render_text(view, banner), T("Data collected {when}.", when=data_age(payload, cfg)))
        if code == NEED_LOGIN:
            say(login_hint())
    return code


def _saved_path(cfg, item, title):
    return cc_store.root(cfg) / cc_text.safe_name(item["course"]) / "assignments" / (cc_text.safe_name(title) + ".md")


@command("show", "One assignment in full: instructions, how to hand in, and the rubric (asks Canvas now).",
         arg("words", nargs="+", help="the item's key, or a few words of its course and title"),
         *REFRESH_ARGS,
         arg("--json", action="store_true", help="print what Canvas returned as JSON"))
def cmd_show(args):
    cfg = cc_store.load_config()
    payload, banner, code = refresh(cfg, args.force, args.offline)
    if payload is None:
        return no_data(banner, code)
    if banner:
        say(warning(banner))
    view = cc_deadlines.build_view(payload, cfg, show_all=True)
    try:
        item = cc_state.find([i for sec in view["sections"] for i in sec["items"]], args.words)
    except LookupError as e:
        say(str(e))
        return FAILED
    if item["manual"]:
        say(f"{item['course']} · {item['name']}",
            T("You added this yourself; due {when}.", when=item["due_text"] or T("no date")),
            *[x for x in [item["status_text"], item["worth_text"]] + item["notes"] if x and x != item["note"]],
            *([note(item["note"])] if item["note"] else []), *([item["url"]] if item["url"] else []))
        return OK
    if args.offline:                         # no Canvas: the copy saved last time is all there is
        saved = _saved_path(cfg, item, item["name"])
        if saved.exists():
            say(warning(T("not asked from Canvas now; this is the copy saved earlier at {path}", path=saved)), "",
                saved.read_text(encoding="utf-8").rstrip())
            return OK
        say(T("The instructions of this item were never fetched. Run it again without --offline."))
        return FAILED
    answers, errors = fetch_extra(cfg, [{"name": "assignment", "keep": KEEP,
                                         "path": f"/api/v1/courses/{item['course_id']}/assignments/{item['id']}"}])
    assignment = answers.get("assignment")
    if not isinstance(assignment, dict):
        why = next((e.get("message") for e in errors if e.get("resource") == "assignment"), T("no answer"))
        say(T("Canvas would not give the details of {name} ({why}).", name=f"{item['course']} · {item['name']}", why=why))
        return FAILED
    if args.json:
        print(json.dumps(assignment, ensure_ascii=False, indent=1))
        return OK
    subs = ((payload.get("data") or {}).get(item["course_id"]) or {}).get("submissions") or []
    mine = next((s for s in subs if cc_model.sid(s.get("assignment_id")) == item["id"]), None)
    text = cc_feedback.render_details(assignment, item, mine, cfg)
    owner = f"assignment:{item['key']}"
    path = cc_store.writable_path(cfg, _saved_path(cfg, item, item["name"]), owner)   # never over the student's edits
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    cc_store.record_written(cfg, path, owner)
    mine = [T("Your own notes on this item (not from Canvas): {notes}", notes=cc_lang.join(item["own_notes"], "clauses"))] \
        if item.get("own_notes") else []
    say(text.rstrip(), *mine, "", T("Saved: {path}", path=path))
    return code
