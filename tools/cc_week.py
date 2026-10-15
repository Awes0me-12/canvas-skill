"""The "this week" view: per course, what the current week's module holds, what is due,
and what was announced. Built on the deadline view, so both always agree."""
import re
from collections import Counter
from datetime import date, timedelta

import cc_deadlines
import cc_lang
import cc_model
import cc_state
import cc_text
import cc_time
from cc_lang import N, T

WEEK = re.compile(r"\bw(?:ee)?k\.?\s*(\d{1,2})\b", re.I)
RANGE = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})\s*[-–—]\s*(?:(\d{1,2})\s*/\s*)?(\d{1,2})")
ANNOUNCE_DAYS = 14
INBOX_DAYS = 14
ITEM_WORD = {"Page": N("page"), "File": N("file"), "Assignment": N("assignment"), "Quiz": N("quiz"),
             "Discussion": N("discussion"), "ExternalUrl": N("link"), "ExternalTool": N("tool")}


def item_word(kind):
    return T(ITEM_WORD[kind]) if kind in ITEM_WORD else ""


def _near(month, day, today):
    """The date month/day falls on in the year that puts it closest to today."""
    best = None
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if best is None or abs((d - today).days) < abs((best - today).days):
            best = d
    return best


def name_range(name, today):
    """(first day, last day) from a module name like 'Week 3 (10/12 - 10/18)' or '10/12-18', or None."""
    m = RANGE.search(name or "")
    if not m:
        return None
    m1, d1, m2, d2 = (int(x) if x else None for x in m.groups())
    start = _near(m1, d1, today)
    end = _near(m2 or m1, d2, today) if start else None
    if not start or not end or end < start or (end - start).days > 31:
        return None
    return start, end


def name_week(name):
    m = WEEK.search(name or "")
    return int(m.group(1)) if m else None


def _monday(d):
    return d - timedelta(days=d.weekday())


def course_modules(payload, course_id):
    """The collected modules of a course (each with its items), or None when they were not collected."""
    data = (payload.get("data") or {}).get(course_id)
    rows = data.get("modules") if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else None


def week_one(payload, cfg, courses, today):
    """(first day of teaching week 1, how we know). The date is None when nothing tells us.

    The first day is whatever the course uses (Monday, Sunday, ...), read from dated module names,
    so "Week 3 (10/11 - 10/17)" and "Week 3 (10/12 - 10/18)" both work.
    """
    saved = (cfg.get("week1_start") or "").strip()
    if saved:
        d = cc_time.parse_iso(saved + "T00:00:00Z")
        if d:
            return d.date(), T("from your settings")
    votes = Counter()
    for c in courses:
        for m in course_modules(payload, c["id"]) or []:
            n, span = name_week(m.get("name")), name_range(m.get("name"), today)
            if n is not None and span:
                votes[span[0] - timedelta(days=7 * (n - 1))] += 1
    if votes:
        return votes.most_common(1)[0][0], T("worked out from the dates in module names")
    return None, ""


def _item(i, tz, at_local):
    details = i.get("content_details") or {}
    due = cc_time.local(cc_time.parse_iso(details.get("due_at")), tz)
    return {"type": i.get("type") or "", "word": item_word(i.get("type")),
            "title": cc_text.one_line(i.get("title")), "indent": int(i.get("indent") or 0),
            "url": cc_text.web_link(i.get("external_url")) or cc_text.web_link(i.get("html_url")),
            "due_text": T("due {when}", when=cc_time.stamp(due)) if due else "",
            "locked": bool(details.get("locked_for_user"))}


def this_week(payload, cfg, at=None):
    """(week number or None, first day, last day, how we know) for the teaching week containing `at`."""
    at = at or cc_time.now()
    today = cc_time.local(at, cc_time.zone(cfg)).date()
    courses = [c for c in cc_model.course_table(payload, cfg, at) if not c["hidden"] and c["collected"]]
    first, source = week_one(payload, cfg, courses, today)
    if first is None:
        return None, _monday(today), _monday(today) + timedelta(days=6), source
    number = (today - first).days // 7 + 1
    start = first + timedelta(days=7 * (number - 1))
    return number, start, start + timedelta(days=6), source


def pick_modules(modules, number, day):
    """The modules for the week containing `day`.

    A module whose name carries dates is taken when those dates contain the day. Only when no
    module does are modules matched by their week number (`number` is that week's number).
    """
    dated = []
    for m in modules:
        span = name_range(m.get("name"), day)
        if span and span[0] <= day <= span[1]:
            dated.append(m)
    if dated:
        return dated
    return [m for m in modules if number is not None and name_week(m.get("name")) == number
            and not name_range(m.get("name"), day)]


def build_view(payload, cfg, at=None):
    at = at or cc_time.now()
    tz = cc_time.zone(cfg)
    at_local = cc_time.local(at, tz)
    today = at_local.date()
    deadlines = cc_deadlines.build_view(payload, cfg, at=at)
    courses = [c for c in deadlines["courses"] if not c["hidden"] and c["collected"]]
    number, start, end, source = this_week(payload, cfg, at)
    as_day = lambda d: cc_time.day(at_local.replace(year=d.year, month=d.month, day=d.day))
    label = T("{first} to {last}", first=as_day(start), last=as_day(end))
    warnings, notes = list(deadlines["warnings"]), []

    due = {}
    for sec in deadlines["sections"]:
        if sec["key"] in ("overdue", "week"):
            for it in sec["items"]:
                due.setdefault(it["course_id"], []).append((sec["key"], it))
    news = {}
    announcements = (payload.get("global") or {}).get("announcements")
    if not isinstance(announcements, list):
        why = next((e.get("message") for e in payload.get("errors") or [] if e.get("resource") == "announcements"), "")
        warnings.append(T("Announcements could not be read ({why}), so new ones may be missing below.",
                          why=why or T("they are not in this collection")))
    for a in announcements if isinstance(announcements, list) else []:
        posted = cc_time.parse_iso(a.get("posted_at"))
        if posted and at - posted <= timedelta(days=ANNOUNCE_DAYS):
            news.setdefault(str(a.get("context_code") or "").replace("course_", ""), []).append({
                "title": cc_text.one_line(a.get("title")), "who": cc_text.one_line(a.get("user_name")),
                "when": cc_time.stamp(cc_time.local(posted, tz)), "posted": posted,
                "text": cc_text.excerpt(a.get("message"), 400), "url": cc_text.web_link(a.get("html_url")),
                # The excerpt leaves out text that is invisible on the page; the flag says that some was there.
                "hidden_text": cc_text.has_hidden_text(a.get("message")),
                "unread": a.get("read_state") == "unread"})

    rows = []
    for c in courses:
        modules = course_modules(payload, c["id"])
        chosen, fallback = [], False
        if modules:
            chosen = pick_modules(modules, number, today)
            if not chosen:
                open_ones = [m for m in modules if m.get("state") != "locked" and (m.get("items") or m.get("items_count"))]
                if open_ones:   # no module is labelled for this week: show the newest one instead, and say so
                    chosen, fallback = [max(open_ones, key=lambda m: m.get("position") or 0)], True
        data = (payload.get("data") or {}).get(c["id"]) or {}
        rows.append({
            "id": c["id"], "label": c["label"], "name": c["name"], "own": False,
            "modules": [{"name": cc_text.one_line(m.get("name")), "fallback": fallback,
                         "items": [_item(i, tz, at_local) for i in m.get("items") or []],
                         "unlisted": max(0, int(m.get("items_count") or 0) - len(m.get("items") or []))}
                        for m in chosen],
            "no_modules": modules is not None and not modules, "modules_unread": modules is None,
            "due": due.get(c["id"], []),
            # A course whose assignments could not be read has unknown deadlines, not none.
            "due_unread": not isinstance(data.get("assignments"), list)
            or any(e.get("resource") in cc_deadlines.DEADLINE_ERRORS for e in c["errors"]),
            "announcements": sorted(news.get(c["id"], []), key=lambda a: a["posted"], reverse=True),
        })
        if modules is None and not any(e.get("resource") == "modules" for e in c["errors"]):
            notes.append(T("Modules for {course} are not in this collection.", course=c["label"]))
        for e in c["errors"]:
            if e.get("resource") == "modules":
                notes.append(T("Could not read the modules of {course} ({why}).", course=c["label"], why=e.get("message")))
    shown = {c["id"] for c in courses}
    own = [pair for cid, pairs in due.items() if cid not in shown for pair in pairs]
    if own:        # the student's own deadlines that belong to no course on the list must not vanish
        own.sort(key=lambda pair: (pair[0] != "overdue", pair[1]["deadline"]))
        rows.append({"id": "own", "label": T("Your own deadlines"), "name": "", "own": True, "modules": [],
                     "no_modules": False, "modules_unread": False, "due": own, "due_unread": False, "announcements": []})

    inbox = None
    conversations = (payload.get("global") or {}).get("conversations") if cfg.get("inbox") else None
    if isinstance(conversations, list):
        me = cc_model.sid((payload.get("user") or {}).get("id"))
        inbox = []
        for m in conversations:
            when = cc_time.parse_iso(m.get("last_message_at"))
            if when and at - when <= timedelta(days=INBOX_DAYS):
                others = [cc_text.one_line(p.get("name")) for p in m.get("participants") or [] if cc_model.sid(p.get("id")) != me]
                inbox.append({"subject": cc_text.one_line(m.get("subject")) or T("(no subject)"),
                              "when": cc_time.stamp(cc_time.local(when, tz)), "sent": when,
                              "who": ", ".join(o for o in others if o) or T("someone"),
                              "course": cc_text.one_line(m.get("context_name")), "text": cc_text.excerpt(m.get("last_message"), 200),
                              "unread": m.get("workflow_state") == "unread"})
        inbox.sort(key=lambda m: m["sent"], reverse=True)

    if number is None:
        notes.append(T("Which teaching week it is could not be worked out. If you know the first day of week 1, run: "
                       "coach.py config set week1_start YYYY-MM-DD"))
    questions = [dict(q, asked_text=cc_time.day(cc_time.local(cc_time.parse_iso(q.get("asked")), tz))
                      if cc_time.parse_iso(q.get("asked")) else "") for q in cc_state.questions(cc_state.load(cfg))]
    return {"week": {"number": number, "label": label, "source": source,
                     "title": T("{title} ({label})", label=label,
                                title=T("Week {n}", n=number) if number is not None else T("This week"))},
            "now_text": deadlines["now_text"], "collected_text": deadlines["collected_text"],
            "age_text": deadlines["age_text"], "stale": deadlines["stale"], "host": deadlines["host"],
            "horizon_days": deadlines["horizon_days"],
            "clashes": deadlines["clashes"], "clash_days": deadlines["clash_days"],
            "summary": cc_deadlines.summary_line(deadlines), "courses": rows, "inbox": inbox, "questions": questions,
            "warnings": warnings, "notes": notes + deadlines["notes_hidden"]}


def _due_section(section):
    """Rows here are read later and out of context, so every one carries its full date."""
    return "overdue" if section == "overdue" else "later"


def question_line(q):
    where = q.get("course") or ""
    return f"[{q['id']}] " + (f"{where}: " if where else "") + q["text"] + \
        (" " + T("(noted {day})", day=q["asked_text"]) if q.get("asked_text") else "")


def render_text(view, banner="", changes=()):
    out = [T("{title}, as of {now}", title=view["week"]["title"], now=view["now_text"]),
           T("Deadlines: {summary}.", summary=view["summary"])]
    if view["week"]["source"]:
        out.append(T("(The week number is {source}.)", source=view["week"]["source"]))
    if banner:
        out.append(cc_lang.warning(banner))
    if view["stale"]:
        out.append(cc_lang.warning(T("this data is old (collected {age}).", age=view["age_text"])))
    out += [cc_lang.warning(w) for w in view["warnings"]]
    if changes:
        out += [""] + list(changes)
    if view["clashes"]:
        out += [""] + cc_deadlines.clash_lines(view, limit=3)
    for c in view["courses"]:
        out += ["", f"== {c['label']}" + (f" ({c['name']})" if c["name"] and c["name"] != c["label"] else "")]
        if c["own"]:
            out += ["  " + cc_deadlines._line(it, _due_section(section)) for section, it in c["due"]]
            continue
        for m in c["modules"]:
            out.append(T("  Module: {name}", name=m["name"])
                       + (T("  (no module is labelled for this week; this is the newest one)") if m["fallback"] else ""))
            for i in m["items"]:
                pad = "    " + "  " * min(i["indent"], 3)
                if i["type"] == "SubHeader":
                    out.append(f"{pad}{i['title']}:")
                else:
                    bits = [f"{pad}- " + (f"[{i['word']}] " if i["word"] else "") + i["title"]]
                    bits += [b for b in (i["due_text"], T("locked") if i["locked"] else "", i["url"]) if b]
                    out.append(" — ".join(bits))
            if m["unlisted"]:
                out.append(T("    ({n} more item(s) not listed; open the module in Canvas)", n=m["unlisted"]))
            if not m["items"] and not m["unlisted"]:
                out.append(T("    (empty)"))
        if c["no_modules"]:
            out.append(T("  Modules: none in this course."))
        elif not c["modules"] and not c["modules_unread"]:
            out.append(T("  Modules: nothing for this week."))
        if c["due_unread"]:
            out.append(T("  Due: could not be read this time (see the warning above); this is not an empty list."))
        if c["due"]:
            out.append(T("  Due:"))
            out += ["    " + cc_deadlines._line(it, _due_section(section)) for section, it in c["due"]]
        elif not c["due_unread"]:
            out.append(T("  Due: nothing overdue or due in the next {days} days on Canvas.", days=view["horizon_days"]))
        for a in c["announcements"]:
            out.append(T("  Announcement ({when}, {who}): {title}", when=a["when"], who=a["who"], title=a["title"])
                       + (T(" [unread]") if a["unread"] else ""))
            out.append(f"    {a['text']}")
            if a.get("hidden_text"):
                out.append("    " + T("(This announcement also holds text that is invisible on the Canvas page; it is left out here.)"))
    if view["inbox"] is not None:
        out += ["", T("== Inbox (last {days} days)", days=INBOX_DAYS)]
        out += [f"  {T('[unread] ') if m['unread'] else ''}"
                + T("{when} from {who}: {subject}", when=m["when"], who=m["who"], subject=m["subject"]) + f" — {m['text']}"
                for m in view["inbox"]] or [T("  Nothing new.")]
    if view.get("questions"):
        out += ["", T("== Questions you still want to ask ({n})", n=len(view["questions"]))]
        out += ["  " + question_line(q) for q in view["questions"]]
    if view["notes"]:
        out += [""] + [cc_lang.note(n) for n in view["notes"]]
    out += ["", T("Data collected {when} ({age}) from {host}.", when=view["collected_text"], age=view["age_text"],
                  host=view["host"])]
    return "\n".join(out)


def render_html(view, banner="", changes_html=""):
    e = cc_text.escape
    link = lambda text, url: (f'<a href="{e(cc_text.web_link(url))}" target="_blank" rel="noopener">{e(text)}</a>'
                              if cc_text.web_link(url) else e(text))
    out = cc_deadlines.page_head(T("This Week")) + [
        f"<h1>{e(view['week']['title'])}</h1>",
        '<p class="sub">' + e(T("As of {now} · data collected {age}", now=view["now_text"], age=view["age_text"]))
        + f' · {e(view["summary"])}</p>']
    alerts = ([banner] if banner else []) + ([cc_deadlines.stale_alert(view)] if view["stale"] else []) + view["warnings"]
    out += [f'<div class="banner">{e(a)}</div>' for a in alerts]
    out.append(changes_html)
    out.append(cc_deadlines.clash_html(view, limit=3))
    for c in view["courses"]:
        hue = cc_deadlines._hue(c["id"])
        out.append(f'<h2 class="course" style="border-left:4px solid hsl({hue} 55% 50%);padding-left:10px">{e(c["label"])}</h2>')
        if c["due_unread"]:
            out.append(f'<div class="banner">{e(T("The deadlines of this course could not be read this time."))}</div>')
        if c["due"]:
            out.append(f"<h3>{e(T('Due'))}</h3><ul>" + "".join(cc_deadlines._row(it, _due_section(section))
                                                              for section, it in c["due"]) + "</ul>")
        for m in c["modules"]:
            out.append(f"<h3>{e(m['name'])}{e(T(' (newest module; none is labelled for this week)')) if m['fallback'] else ''}</h3><ul>")
            for i in m["items"]:
                if i["type"] == "SubHeader":
                    out.append(f'<li style="--c:transparent"><div class="body"><div class="meta"><b>{e(i["title"])}</b></div></div></li>')
                else:
                    tag = f'<span class="tag">{e(i["word"])}</span>' if i["word"] else ""
                    extra = " · ".join(x for x in (i["due_text"], T("locked") if i["locked"] else "") if x)
                    out.append(f'<li style="--c:hsl({hue} 55% 50%)"><div class="body"><div class="name">{tag}{link(i["title"], i["url"])}</div>'
                               + (f'<div class="meta">{e(extra)}</div>' if extra else "") + "</div></li>")
            if m["unlisted"]:
                more = T("{n} more item(s): open the module in Canvas", n=m["unlisted"])
                out.append(f'<li style="--c:transparent"><div class="body"><div class="meta">{e(more)}</div></div></li>')
            out.append("</ul>")
        if c["announcements"]:
            unread, unseen = e(T(" · unread")), e(T(" · also holds text that is invisible on the Canvas page"))
            out.append(f"<h3>{e(T('Announcements'))}</h3><ul>" + "".join(
                f'<li style="--c:hsl({hue} 55% 50%)"><div class="body"><div class="name">{link(a["title"], a["url"])}</div>'
                f'<div class="meta">{e(a["who"])} · {e(a["when"])}{unread if a["unread"] else ""}'
                f'{unseen if a.get("hidden_text") else ""}</div>'
                f'<div class="text">{e(a["text"])}</div></div></li>' for a in c["announcements"]) + "</ul>")
        if not c["due"] and not c["modules"] and not c["announcements"] and not c["due_unread"]:
            out.append(f'<p class="sub">{e(T("Nothing on Canvas for this week."))}</p>')
    if view["inbox"]:
        unread = e(T(" · unread"))
        out.append(f'<h2 class="course">{e(T("Inbox"))}</h2><ul>' + "".join(
            f'<li style="--c:var(--line)"><div class="body"><div class="name">{e(m["subject"])}</div>'
            f'<div class="meta">{e(m["who"])} · {e(m["when"])}{unread if m["unread"] else ""}</div>'
            f'<div class="text">{e(m["text"])}</div></div></li>' for m in view["inbox"]) + "</ul>")
    if view.get("questions"):
        out.append(f'<h2 class="course">{e(T("Questions to ask"))}</h2><ul>' + "".join(
            f'<li style="--c:var(--line)"><div class="body"><div class="name">{e(q["text"])}</div>'
            f'<div class="meta">{e(" · ".join(x for x in (q["id"], q.get("course"), q.get("asked_text")) if x))}</div></div></li>'
            for q in view["questions"]) + "</ul>")
    if view["notes"]:
        out.append('<div class="notes">' + "".join(f"<p>{e(n)}</p>" for n in view["notes"]) + "</div>")
    out.append("<footer>" + e(T("Collected {when} from {host}. Made by Canvas Coach.", when=view["collected_text"],
                                host=view["host"])) + "</footer>")
    out.append("</main></body></html>")
    return "\n".join(out)
