"""Command: grades (where you stand per course, and what a target would take)."""
import json

import cc_grades
import cc_model
import cc_state
import cc_store
import cc_time
from cc_cli import (NEED_LOGIN, REFRESH_ARGS, arg, command, login_hint, no_data, no_such_course, open_file,
                    refresh, say, write_page)
from cc_lang import T

PAGE_NAME = "Grades.html"


@command("grades", "Where you stand in each course, by assignment group, and what a target grade would take.",
         arg("course", nargs="*", help="a few words of one course's name (default: all current courses)"),
         arg("--target", type=float, metavar="PERCENT", help="the final percentage you are aiming for, e.g. 90"),
         *REFRESH_ARGS,
         arg("--json", action="store_true", help="print machine-readable JSON instead of text"),
         arg("--open", action="store_true", help="open the page in your browser afterwards"))
def cmd_grades(args):
    cfg = cc_store.load_config()
    if args.target is not None and not 0 < args.target <= 200:
        raise ValueError(T("--target must be a percentage, for example 90."))
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
    view = cc_grades.build_view(payload, cfg, course_id=course_id, target=args.target)
    page = write_page(cfg, PAGE_NAME, cc_grades.render_html(view, banner))
    if args.json:
        print(json.dumps(dict(view, banner=banner, page=str(page)), ensure_ascii=False, indent=1))
    else:
        say(cc_grades.render_text(view, banner), T("Page: {path}", path=page))
        if code == NEED_LOGIN:
            say(login_hint())
    if args.open:
        open_file(page)
    return code
