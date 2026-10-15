"""Command: materials (what a course's modules hold; read the pages; download files on request)."""
import json

import cc_materials
import cc_model
import cc_state
import cc_store
import cc_time
from cc_cli import (FAILED, OK, REFRESH_ARGS, arg, command, data_age, fetch_extra, fetch_files, no_data,
                    no_such_course, refresh, say)
from cc_lang import T, warning


@command("materials", "A course's module contents. --read saves the pages as text; --download also fetches the files.",
         arg("course", nargs="+", help="a few words of the course's name"),
         arg("--week", type=int, metavar="N", help="teaching week to look at (default: this week)"),
         arg("--module", nargs="+", metavar="WORD", help="pick modules by words in their name"),
         arg("--all", action="store_true", help="every module of the course"),
         arg("--read", action="store_true", help="fetch the pages now and save them as text files"),
         arg("--download", action="store_true", help="also download the files (ask the student first)"),
         *REFRESH_ARGS,
         arg("--json", action="store_true", help="print machine-readable JSON instead of text"))
def cmd_materials(args):
    cfg = cc_store.load_config()
    payload, banner, code = refresh(cfg, args.force, args.offline)
    if payload is None:
        return no_data(banner, code)
    table = cc_model.course_table(payload, cfg, cc_time.now())
    course = cc_state.match_course(table, " ".join(args.course))
    if course is None:
        return no_such_course(" ".join(args.course), table)
    modules, chosen = cc_materials.choose_modules(payload, cfg, course["id"], week=args.week, words=args.module,
                                                  everything=args.all)
    listing = cc_materials.listing(modules)
    lines = ([warning(banner)] if banner else []) + [T("Materials for {course}. {chosen}", course=course["label"],
                                                       chosen=chosen)] + listing
    result = None
    if modules and (args.read or args.download) and args.offline:
        lines += ["", T("Reading pages and files needs Canvas: run it again without --offline.")]
    elif modules and (args.read or args.download):
        result = cc_materials.gather(payload, cfg, course, modules, fetch_extra, get_files=args.download,
                                     browser=fetch_files)
        lines += [""] + cc_materials.render_gathered(result, args.download)
    elif modules:
        lines += ["", T("Add --read to fetch the pages as text (and list the files they link to).")]
    lines += ["", T("Module list collected {when}.", when=data_age(payload, cfg))]
    if args.json:
        print(json.dumps({"course": course["label"], "chosen": chosen, "banner": banner,
                          "modules": [str(m.get("name")).strip() for m in modules], "items": listing,
                          "result": result}, ensure_ascii=False, indent=1))
    else:
        say(*lines)
    if not modules:
        return code or FAILED
    return code or OK
