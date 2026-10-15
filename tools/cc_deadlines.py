"""The deadline view: what is overdue, what is coming, what is done.

build_view() decides what goes where; render_text() and render_html() only draw it.
A new page (weekly plan, grades, changes) should follow the same split.
"""
import html
import zlib
from datetime import timedelta

import cc_clash
import cc_lang
import cc_model
import cc_state
import cc_time
from cc_lang import N, T, plural

DONE_DAYS = 7      # keep finished work on the page this long after its deadline
MISSED_DAYS = 14   # keep closed, never-submitted work this long
STALE_HOURS = 24

SECTIONS = (("overdue", N("Overdue")), ("week", N("Next {n} days")), ("later", N("Later")),
            ("undated", N("No due date")), ("done", N("Recently done")))
STATUS_TEXT = {
    "todo": N("not submitted"), "overdue": N("not submitted, still open"), "missed": N("missed, now closed"),
    "closed": N("closed without a hand-in"), "submitted": N("submitted"), "graded": N("graded"),
    "excused": N("excused"), "offline": N("no upload (in class or on paper)"), "ticked": N("marked done by you"),
}
MANUAL_TEXT = {"todo": N("not done yet"), "overdue": N("past its date, not marked done")}
EXAM_TEXT = {"todo": N("coming up"), "offline": N("its date has passed")}
EXTERNAL_TODO = N("not submitted yet (handed in through an outside tool)")
EXTERNAL_LATE = N("Canvas shows no hand-in; this goes through an outside tool, so check there")
KIND_WORDS = {"quiz": N("quiz"), "discussion": N("discussion"), "external tool": N("external tool"),
              "exam": N("exam"), "reading": N("reading")}
RESOURCE_WORDS = {"assignments": N("assignments"), "assignment_groups": N("assignment groups"),
                  "modules": N("modules"), "submissions": N("feedback"), "announcements": N("announcements"),
                  "conversations": N("inbox"), "profile": N("profile"), "courses": N("courses")}
DEADLINE_ERRORS = ("assignments", "assignment_groups")   # only these failures can hide a deadline
MARK = {"todo": "[ ]", "overdue": "[!]", "missed": "[x]", "closed": "[-]", "submitted": "[✓]",
        "graded": "[✓]", "excused": "[✓]", "offline": "[·]", "ticked": "[✓]"}


def _num(value):
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def resource_word(name):
    return T(RESOURCE_WORDS.get(str(name), str(name).replace("_", " ")))


def _decorate(it, at_local, tz):
    """Add the ready-to-print wording to one item."""
    due = cc_time.local(it["deadline"], tz)
    notes = []
    it["due_today"] = False
    if due:
        it["due_text"] = cc_time.stamp(due)
        it["due_day"] = cc_time.day(due)
        it["due_time"] = cc_time.clock(due)
        it["relative"] = cc_time.relative(due, at_local)
        it["due_today"] = due >= at_local and due.date() == at_local.date()
        if cc_time.midnight_note(due):
            notes.append(cc_time.midnight_note(due))
    else:
        it["due_text"] = it["due_day"] = it["due_time"] = it["relative"] = ""
    status = T(STATUS_TEXT[it["status"]])
    if it["status"] == "graded" and it["score"] is not None and it["points"]:
        status = T("graded {score}", score=f"{_num(it['score'])}/{_num(it['points'])}")
    elif it["status"] == "graded" and it["grade"]:
        status = T("graded {score}", score=it["grade"])
    if it["late"] and it["done"]:
        status += T(" (late)")
    if it["kind"] == "external tool" and not it["done"] and it["status"] != "offline":
        status = T(EXTERNAL_TODO if it["status"] == "todo" else EXTERNAL_LATE)
    if it["manual"] and it["exam"] and it["status"] in EXAM_TEXT:
        status = T(EXAM_TEXT[it["status"]])
    elif it["manual"] and it["status"] in MANUAL_TEXT:
        status = T(MANUAL_TEXT[it["status"]])
    it["status_text"] = status
    # What it is worth, as Canvas states it: points, and the weight of the whole assignment group.
    # No per-item percentage: early in a term, when few items are posted, that guess is far too high.
    worth = []
    if it["points"]:
        worth.append(T("{n} pts", n=_num(it["points"])))
    if it["manual"]:
        if it["own_weight"] is not None:
            worth.append(T("{n}% of grade", n=_num(it["own_weight"])))
    elif it["weight"] == 0:
        worth.append(T("not counted in grade"))
    elif it["course_weighted"] and it["group"] and it["group_weight"] is not None:
        n = it["group_count"]
        worth.append(T("{group}: {n}% of grade", group=it["group"], n=_num(it["group_weight"]))
                     + (plural(n, ", {n} item so far", ", {n} items so far") if n else ""))
    elif worth and not it["course_weighted"] and it["course_points"]:
        worth[0] += T(" of {n} posted so far", n=_num(it["course_points"]))
    it["worth_text"] = cc_lang.join(worth)
    if it["zero_without_handin"]:
        notes.append(T("marked 0, nothing handed in"))
    if it["redo"]:
        notes.append(T("your teacher asked you to redo this"))
    if it["not_open"]:
        notes.append(T("opens {when}", when=cc_time.stamp(cc_time.local(it["unlock"], tz))))
    elif it["locked"] and not it["done"] and it["module_locked"]:
        notes.append(T("locked until earlier module items are finished"))
    elif it["locked"] and not it["done"] and it["status"] == "todo":
        notes.append(T("locked for now"))
    if it["status"] == "overdue" and it["lock"] and it["lock"] > at_local:
        notes.append(T("closes {when}", when=cc_time.stamp(cc_time.local(it["lock"], tz))))
    if it["manual"]:
        notes.append(T("added by you"))
        if it["kind"] in ("exam", "reading"):
            notes.append(T(KIND_WORDS[it["kind"]]))
    elif it["kind"] != "assignment":
        notes.append(T(KIND_WORDS.get(it["kind"], it["kind"])))
    if it["place"]:
        notes.append(T("at {place}", place=it["place"]))
    if it["hidden_text"] and not it["done"]:
        notes.append(T("its instructions hold text that is invisible on the Canvas page; see: coach.py show {key}",
                       key=it["key"]))
    own = []                                 # what the student told us about this item, kept apart for `show`
    if it["own_due"]:
        said = cc_time.local(it["canvas_due"], tz)
        own.append(T("your own date; Canvas shows {when}", when=cc_time.stamp(said)) if said
                   else T("your own date; Canvas shows none"))
        if it["canvas_moved"]:
            own.append(T("Canvas has changed its date since you set yours: check, then coach.py confirm {key}",
                         key=it["key"]))
    if not it["confirmed"]:
        others = cc_lang.join(cc_time.stamp(cc_time.local(when, tz)) + (f" ({source})" if source else "")
                              for when, source in it["alt_dates"])
        own.append(T("date not confirmed; also given as {others}", others=others) if others else T("date not confirmed"))
    if it["source"]:
        own.append(T("source: {source}", source=it["source"]))
    if it["note"]:
        own.append(it["note"])
    it["own_notes"] = own
    notes += own
    it["notes"] = notes
    return it


def build_view(payload, cfg, at=None, days=None, show_all=False):
    """Sort every assignment into a section. Returns a plain dict (see --json)."""
    at = at or cc_time.now()
    tz = cc_time.zone(cfg)
    at_local = cc_time.local(at, tz)
    horizon = int(days or cfg.get("horizon_days") or 7)
    week_end = at_local.date() + timedelta(days=horizon)
    buckets = {key: [] for key, _ in SECTIONS}
    hidden_old = 0

    state = cc_state.load(cfg)
    canvas = cc_state.apply_overrides(cc_model.items(payload, cfg, at), state, at)
    everything = cc_state.apply_ticks(canvas + cc_state.manual_items(state, at), state)
    for it in everything:
        status = it["status"]
        # An item with no due date but an "available until" date still has a real deadline: when it closes.
        it["closes_only"] = it["due"] is None and it["lock"] is not None and not it["done"] and status != "offline"
        due = it["deadline"] = it["lock"] if it["closes_only"] else it["due"]
        if due is None:
            if show_all or status == "todo":
                buckets["undated"].append(it)
        elif due >= at:
            buckets["week" if cc_time.local(due, tz).date() <= week_end else "later"].append(it)
        elif it["done"] or status == "offline":
            sub = it["submitted_at"]
            recent = (at - due) <= timedelta(days=DONE_DAYS) or (sub and (at - sub) <= timedelta(days=DONE_DAYS))
            if show_all or (it["done"] and recent):
                buckets["done"].append(it)
        else:
            limit = MISSED_DAYS if status in ("missed", "closed") else int(cfg.get("overdue_days") or 45)
            if show_all or (at - due) <= timedelta(days=limit):
                buckets["overdue"].append(it)
            else:
                hidden_old += 1

    far = at + timedelta(days=36500)
    for key in ("overdue", "week", "later"):
        buckets[key].sort(key=lambda i: (i["deadline"], i["course"], i["name"]))
    buckets["undated"].sort(key=lambda i: (i["course"], i["name"]))
    buckets["done"].sort(key=lambda i: (i["deadline"] or far, i["course"]), reverse=True)
    for group in buckets.values():
        for it in group:
            _decorate(it, at_local, tz)
    clash_hours, clash_days = int(cfg.get("clash_hours") or 48), int(cfg.get("clash_days") or 21)
    clashes = cc_clash.find(buckets["week"] + buckets["later"], at, tz, clash_hours, clash_days,
                            float(cfg.get("clash_min_percent") if cfg.get("clash_min_percent") is not None else 1))
    cc_clash.mark(everything, clashes)

    courses = cc_model.course_table(payload, cfg, at)
    warnings = []
    for c in courses:
        if c["hidden"]:
            continue
        if not c["collected"]:
            warnings.append(T("{course} was not collected, so its deadlines are NOT in this list. Collect again.",
                              course=c["label"]))
            continue
        failed = [e for e in c["errors"] if e.get("resource") in DEADLINE_ERRORS]
        if failed:
            what = T(" and ").join(sorted({resource_word(e.get("resource")) for e in failed}))
            warnings.append(T("Could not read {what} for {course} ({why}). Its items may be missing below.",
                              what=what, course=c["label"], why=failed[0].get("message")))
    known = {c["id"] for c in courses}
    for cid in payload.get("data") or {}:
        if str(cid) not in known:
            warnings.append(T("The data holds a course ({id}) that is not in the course list, so it is not shown. "
                              "Collect again.", id=cid))
    for label in payload.get("truncated") or []:
        if str(label).split(":")[0] in DEADLINE_ERRORS + ("courses",):
            warnings.append(T("The list '{label}' was cut off before its end, so items may be missing. Collect again.",
                              label=label))
    notes, notes_hidden = [], []
    if payload.get("slim"):
        notes.append(T("Collected in slim mode: finished work older than {days} days was left out.",
                       days=payload["slim"].get("days")))
    if hidden_old:
        notes_hidden.append(T("{n} older unsubmitted item(s) are hidden. Add --all to see them.", n=hidden_old))
    for c in courses:
        if c["hidden"] and c["hidden_code"] != "ignored" and c["name"]:
            notes_hidden.append(T("{course} is hidden because {why}. To show it: coach.py config include {id}",
                                  course=c["label"], why=c["hidden"], id=c["id"]))

    collected = cc_time.parse_iso(payload.get("collected_at"))
    age = max(0.0, (at - collected).total_seconds() / 60) if collected else None
    todo_week = [i for i in buckets["week"] if not i["done"] and i["status"] != "offline"]
    elsewhere = [i for i in buckets["overdue"] if i["kind"] == "external tool"]
    return {
        "generated_at": at_local.isoformat(timespec="seconds"),
        "now_text": f"{cc_time.stamp(at_local)} {cc_time.zone_label(at_local)}",
        "collected_at": cc_time.local(collected, tz).isoformat(timespec="seconds") if collected else None,
        "collected_text": cc_time.stamp(cc_time.local(collected, tz)) if collected else T("unknown"),
        "age_minutes": None if age is None else round(age, 1),
        "age_text": T("unknown") if age is None else cc_time.age_text(age),
        "stale": age is None or age > STALE_HOURS * 60,
        "host": payload.get("host") or "",
        "user": (payload.get("user") or {}).get("name") or "",
        "horizon_days": horizon,
        "sections": [{"key": key, "title": T(title, n=horizon), "items": buckets[key]} for key, title in SECTIONS],
        "anything_collected": any(c["collected"] and not c["hidden"] for c in courses),
        "counts": {"overdue": len(buckets["overdue"]) - len(elsewhere), "check_elsewhere": len(elsewhere),
                   "week_todo": len(todo_week),
                   "week_done": len([i for i in buckets["week"] if i["done"]]),
                   "later": len(buckets["later"]), "undated": len(buckets["undated"]), "clashes": len(clashes)},
        "next": todo_week[0]["key"] if todo_week else None,
        "clashes": clashes, "clash_days": clash_days, "clash_hours": clash_hours,
        "unconfirmed": [i for i in buckets["week"] + buckets["later"] + buckets["undated"] if not i["confirmed"]],
        "courses": courses, "warnings": warnings, "notes": notes + notes_hidden, "notes_hidden": notes_hidden,
    }


def jsonable(view):
    """The view with datetimes turned into ISO text, for --json and other tools."""
    def fix(v):
        if isinstance(v, dict):
            return {k: fix(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [fix(x) for x in v]
        return v.isoformat() if hasattr(v, "isoformat") else v
    return fix(view)


def summary_line(view):
    c = view["counts"]
    parts = [T("{n} overdue", n=c["overdue"]), T("{n} to do in the next {days} days", n=c["week_todo"], days=view["horizon_days"])]
    if c["week_done"]:
        parts.append(T("{n} already handed in", n=c["week_done"]))
    if c["check_elsewhere"]:
        parts.append(T("{n} past-due on an outside site to check yourself", n=c["check_elsewhere"]))
    if c.get("clashes"):
        parts.append(plural(c["clashes"], "{n} deadline clash ahead", "{n} deadline clashes ahead"))
    return cc_lang.join(parts)


EMPTY_TEXT = N("Nothing to show: no assignments were found in your current courses.")
NOTHING_COLLECTED_TEXT = N("Nothing to show, because no course was collected. This is not an empty to-do list.")


def _when(it, section):
    """The date part of a line, worded for the section the item stands in."""
    time_rel = T("{when} ({relative})", when=it["due_time"], relative=it["relative"])
    full_rel = T("{when} ({relative})", when=it["due_text"], relative=it["relative"])
    if it["closes_only"]:   # no due date; the only deadline is the date it closes
        return {"overdue": T("closed {when}, had no due date", when=full_rel),
                "week": T("closes {when}, no due date", when=time_rel),
                "later": T("closes {when}, no due date", when=full_rel)}.get(section, T("no due date"))
    return {"overdue": T("was due {when}", when=full_rel), "week": time_rel, "later": full_rel,
            "undated": T("no due date"),
            "done": T("was due {when}", when=it["due_text"]) if it["due_text"] else T("no due date")}[section]


def _line(it, section):
    bits = [f"{it['course']} · {it['name']}", _when(it, section), it["status_text"]]
    bits += [b for b in [it["worth_text"]] + it["notes"] if b]
    return f"{MARK[it['status']]} " + " — ".join(bits)


def item_title(it):
    """'course · name', with a question mark while the student is not sure of its date."""
    return f"{it['course']} · {it['name']}" + ("" if it["confirmed"] else " (?)")


def clash_lines(view, limit=None):
    """The clash block for the top of a list: [] when there is none."""
    clashes = view["clashes"][:limit] if limit else view["clashes"]
    if not clashes:
        return []
    out = [T("CLASHES in the next {days} days ({n})", days=view["clash_days"], n=len(view["clashes"]))]
    for c in clashes:
        out.append(f"  * {c['text']}")
        out += ["      " + " — ".join(b for b in (item_title(i), i["due_text"], i["worth_text"],
                                                  T(KIND_WORDS["exam"]) if i["exam"] else "",
                                                  "" if i["confirmed"] else T("date not confirmed")) if b) for i in c["items"]]
    if limit and len(view["clashes"]) > limit:
        out.append(T("  ... and {n} more (coach.py deadlines)", n=len(view["clashes"]) - limit))
    return out


def render_text(view, banner="", keys=False, changes=()):
    """`changes` are ready-made lines (cc_changes.render_text) shown above the list."""
    out = [T("Canvas deadlines as of {now}", now=view["now_text"]), T("Summary: {summary}.", summary=summary_line(view))]
    if banner:
        out.append(cc_lang.warning(banner))
    if view["stale"]:
        out.append(cc_lang.warning(T("this data is old (collected {age}).", age=view["age_text"])))
    out += [cc_lang.warning(w) for w in view["warnings"]]
    if changes:
        out += [""] + list(changes)
    if view["clashes"]:
        out += [""] + clash_lines(view)
    for sec in view["sections"]:
        if not sec["items"]:
            continue
        out += ["", T("{title} ({n})", title=sec["title"].upper(), n=len(sec["items"]))]
        last_day = None
        for it in sec["items"]:
            if sec["key"] == "week" and it["due_day"] != last_day:
                last_day = it["due_day"]
                out.append(f"  {last_day}")
            out.append(("    " if sec["key"] == "week" else "  ") + _line(it, sec["key"])
                       + (f"  [key {it['key']}]" if keys else ""))
    if not any(sec["items"] for sec in view["sections"]):
        out += ["", T(EMPTY_TEXT if view["anything_collected"] else NOTHING_COLLECTED_TEXT)]
    if view["notes"]:
        out += [""] + [cc_lang.note(n) for n in view["notes"]]
    out += ["", T("Data collected {when} ({age}) from {host}.", when=view["collected_text"], age=view["age_text"],
                  host=view["host"])]
    return "\n".join(out)


CSS = """
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1f;--mute:#6b6b70;--line:#e4e2dd;--warn:#b3261e;--warnbg:#fdecea;
--ok:#1a7f4b;--accent:#2f5bd0;--soon:#b25e00}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1e1e21;--ink:#ececee;--mute:#9a9aa2;--line:#2e2e33;
--warn:#ff8a80;--warnbg:#3a1d1b;--ok:#5fd08f;--accent:#8fb0ff;--soon:#ffb454}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif}
main{max-width:780px;margin:0 auto;padding:28px 16px 60px}
h1{font-size:26px;margin:0 0 4px}.sub{color:var(--mute);font-size:14px;margin:0 0 18px}
.sum{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 18px}
.pill{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:6px 14px;font-size:14px}
.pill b{font-size:16px}.pill.bad b{color:var(--warn)}
.banner{background:var(--warnbg);color:var(--warn);border-radius:10px;padding:10px 14px;margin:0 0 12px;font-size:14px}
h2{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute);margin:28px 0 8px}
h2.overdue{color:var(--warn)}h3{font-size:14px;margin:14px 0 6px;color:var(--mute);font-weight:600}
ul{list-style:none;margin:0;padding:0;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
li{display:flex;gap:12px;align-items:flex-start;padding:11px 14px;border-top:1px solid var(--line);
border-left:4px solid var(--c)}li:first-child{border-top:0}
.body{flex:1;min-width:0}.name{font-weight:600;overflow-wrap:anywhere}.name a{color:inherit;text-decoration:none}
.name a:hover{text-decoration:underline;color:var(--accent)}
.meta{color:var(--mute);font-size:13px;margin-top:2px}.course{font-weight:600;color:var(--ink)}
.when{text-align:right;white-space:nowrap;font-size:14px}.when small{display:block;color:var(--mute);font-size:12px}
.st{font-weight:600}.st.overdue,.st.missed{color:var(--warn)}.st.submitted,.st.graded,.st.excused,.st.ticked{color:var(--ok)}
li.done .name{font-weight:500;color:var(--mute)}li.soon .when{color:var(--soon);font-weight:600}
li.late .when{color:var(--warn);font-weight:600}
.notes{color:var(--mute);font-size:13px;margin-top:24px}.notes p{margin:4px 0}
.changes{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:12px;
padding:10px 16px;margin:0 0 16px}.changes h2{margin:2px 0 6px;color:var(--accent)}
.changes ul{border:0;background:none;border-radius:0}.changes li{display:list-item;border:0;padding:3px 0;font-size:14px}
.changes a{color:inherit}.text{color:var(--mute);font-size:14px;margin-top:3px;white-space:pre-line}
.changes.clash{border-left-color:var(--soon)}.changes.clash h2{color:var(--soon)}
.changes small{display:block;color:var(--mute);font-size:13px;margin-left:2px}
.tag{display:inline-block;font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:var(--mute);
border:1px solid var(--line);border-radius:6px;padding:0 6px;margin-right:6px}
.tag.hot{color:var(--soon);border-color:var(--soon)}.tag.must{color:var(--card);background:var(--accent);border-color:var(--accent)}
h2.course{font-size:18px;letter-spacing:0;text-transform:none;color:var(--ink);margin:30px 0 4px}
.rest{color:var(--mute);font-size:14px;margin:6px 0 0}
footer{color:var(--mute);font-size:12px;margin-top:28px}
@media (max-width:520px){li{flex-wrap:wrap}.when{text-align:left;width:100%;padding-left:0}}
"""


def page_head(title):
    """The opening of every page: language, title, shared look."""
    return ['<!doctype html><html lang="' + T("en") + '"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width,initial-scale=1">',
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body><main>"]


def stale_alert(view):
    return T("This data is old (collected {age}). Ask for a fresh check.", age=view["age_text"])


def _hue(course_id):
    return zlib.crc32(str(course_id).encode()) % 360


def _row(it, section):
    e = html.escape
    name = e(it["name"])
    if it["url"]:
        name = f'<a href="{e(it["url"], quote=True)}" target="_blank" rel="noopener">{name}</a>'
    tags = (f'<span class="tag hot">{e(T("clash"))}</span>' if it["clash"] and not it["done"] else "") + \
           (f'<span class="tag hot">{e(T("not confirmed"))}</span>' if not it["confirmed"] else "")
    meta = [f'<span class="course">{e(it["course"])}</span>',
            f'<span class="st {it["status"]}">{e(it["status_text"])}</span>']
    meta += [e(x) for x in [it["worth_text"]] + it["notes"] if x]
    word = (T("closed ") if section == "overdue" else T("closes ")) if it["closes_only"] else ""
    if section == "week":
        when = f'{word}{e(it["due_time"])}<small>{e(it["relative"])}</small>'
    elif it["due_text"]:
        when = f'{word}{e(it["due_text"])}<small>{e(it["relative"])}</small>'
    else:
        when = f'<small>{e(T("no due date"))}</small>'
    classes = ["done"] if it["done"] else []
    if section == "overdue":
        classes.append("late")
    elif section == "week" and not it["done"] and it["due_today"]:
        classes.append("soon")
    return (f'<li class="{" ".join(classes)}" style="--c:hsl({_hue(it["course_id"])} 55% 50%)">'
            f'<div class="body"><div class="name">{tags}{name}</div><div class="meta">{" · ".join(meta)}</div></div>'
            f'<div class="when">{when}</div></li>')


def clash_html(view, limit=None):
    clashes = view["clashes"][:limit] if limit else view["clashes"]
    if not clashes:
        return ""
    e = html.escape
    rows = "".join(
        f"<li>{e(c['text'])}" + "".join(
            "<small>" + e(" — ".join(b for b in (item_title(i), i["due_text"], i["worth_text"],
                                                 "" if i["confirmed"] else T("date not confirmed")) if b))
            + "</small>" for i in c["items"]) + "</li>" for c in clashes)
    title = T("Deadline clashes in the next {days} days", days=view["clash_days"])
    return f'<div class="changes clash"><h2>{e(title)}</h2><ul>{rows}</ul></div>'


def render_html(view, banner="", changes_html=""):
    e = html.escape
    c = view["counts"]
    bold = lambda n: f"<b>{n}</b>"
    out = page_head(T("Deadlines")) + [
        f"<h1>{e(T('Deadlines'))}</h1>",
        '<p class="sub">' + e(T("As of {now} · data collected {age}", now=view["now_text"], age=view["age_text"]))
        + (" · " + e(view["user"]) if view["user"] else "") + "</p>"]
    alerts = ([banner] if banner else []) + ([stale_alert(view)] if view["stale"] else []) + view["warnings"]
    out += [f'<div class="banner">{e(a)}</div>' for a in alerts]
    out.append(changes_html)
    out.append(clash_html(view))
    out.append('<div class="sum">'
               f'<span class="pill{" bad" if c["overdue"] else ""}">{T("{n} overdue", n=bold(c["overdue"]))}</span>'
               f'<span class="pill">{T("{n} to do in {days} days", n=bold(c["week_todo"]), days=view["horizon_days"])}</span>'
               f'<span class="pill">{T("{n} already handed in", n=bold(c["week_done"]))}</span>'
               f'<span class="pill">{T("{n} later", n=bold(c["later"]))}</span>'
               + (f'<span class="pill">{T("{n} to check on an outside site", n=bold(c["check_elsewhere"]))}</span>'
                  if c["check_elsewhere"] else "") + "</div>")
    for sec in view["sections"]:
        if not sec["items"]:
            continue
        out.append(f'<h2 class="{sec["key"]}">{e(sec["title"])} · {len(sec["items"])}</h2>')
        if sec["key"] == "week":
            days = []
            for it in sec["items"]:
                if not days or days[-1][0] != it["due_day"]:
                    days.append((it["due_day"], []))
                days[-1][1].append(it)
            for label, group in days:
                out.append(f"<h3>{e(label)}</h3><ul>" + "".join(_row(i, "week") for i in group) + "</ul>")
        else:
            out.append("<ul>" + "".join(_row(i, sec["key"]) for i in sec["items"]) + "</ul>")
    if not any(sec["items"] for sec in view["sections"]):
        out.append(f"<p>{e(T(EMPTY_TEXT if view['anything_collected'] else NOTHING_COLLECTED_TEXT))}</p>")
    if view["notes"]:
        out.append('<div class="notes">' + "".join(f"<p>{e(n)}</p>" for n in view["notes"]) + "</div>")
    out.append("<footer>" + e(T("Collected {when} from {host}. Group weights are as set in Canvas; times are shown in "
                                "your timezone. Made by Canvas Coach.", when=view["collected_text"], host=view["host"]))
               + "</footer>")
    out.append("</main></body></html>")
    return "\n".join(out)
