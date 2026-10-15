"""The study plan: one must-do for each of the coming days, built from the deadline view.

Rules, in order of priority:
  1. Overdue work that is still open goes on today.
  2. A must-do from an earlier day that is still open is carried over to today.
  3. Graded work goes on the day before it is due; two days before when it is heavy (its group is
     10% or more of the grade) or sits in a deadline clash. When that day is taken it moves to the
     nearest earlier free day, so the load spreads out instead of piling up.
  4. An exam gets a review session on each of the three days before it.
  5. This week's module material goes on the day before the course's first class of the week.
One day a week is kept free: the lightest of the candidate days in the setting `rest_days`.

Nothing here estimates how long anything takes; there is no data for that. Every line says why
it is there, so the student can overrule it.

What is remembered (state.json, "plan"): each day's must-do, so it stays the same all day and can be
carried over, and the rest day chosen for each week.
"""
from datetime import datetime, time, timedelta

import cc_deadlines
import cc_lang
import cc_model
import cc_state
import cc_store
import cc_text
import cc_time
import cc_week
from cc_lang import N, T

HEAVY_PERCENT = 10          # a group worth this much of the grade gets an extra day
REVIEW_DAYS = 3             # review sessions before an exam
REST_PREFERENCE = ("sat", "sun", "fri", "thu", "wed", "tue", "mon")     # which candidate wins a tie
KEEP_DAYS = 21              # how long the record of past must-dos is kept
READABLE = ("Page", "File", "ExternalUrl", "ExternalTool")              # module items that are material to go through
PRIORITY = {"overdue": 1, "carried": 2, "today": 3, "review1": 3, "work": 4, "prep": 5, "review": 5, "material": 6,
            "check": 7, "optional": 8}
LABEL = {"overdue": N("overdue"), "carried": N("carried over"), "work": N("graded work"), "today": N("due today"),
         "prep": N("prepare"), "review": N("exam review"), "review1": N("exam review"), "material": N("class material"),
         "check": N("check"), "optional": N("optional")}


# ---------------------------------------------------------------- class times

def parse_days(text):
    """'tue,thu', 'TR', 'MWF' or 'mon wed' -> ['tue', 'thu'] in week order. Raises ValueError."""
    raw = str(text or "").lower().replace(",", " ").replace("/", " ").split()
    letters = {"m": "mon", "t": "tue", "w": "wed", "r": "thu", "f": "fri", "s": "sat", "u": "sun"}
    names = [name.lower() for name in cc_time.LONG_DAYS]
    days = []
    for word in raw:
        named = [cc_time.DAY_CODES[n] for n, name in enumerate(names) if len(word) >= 2 and name.startswith(word)]
        if len(named) == 1:                             # tue, tues, tuesday, th
            days += named
        elif all(ch in letters for ch in word):         # university shorthand: MWF, TR
            days += [letters[ch] for ch in word]
        else:
            raise ValueError(T("'{text}' is not a list of days. Use for example: tue,thu  or  mon wed fri", text=text))
    if not days:
        raise ValueError(T("'{text}' is not a list of days. Use for example: tue,thu  or  mon wed fri", text=text))
    return [d for d in cc_time.DAY_CODES if d in days]


def parse_clock(text):
    """'15:30', '3:30pm' or '9am' -> '15:30'. Raises ValueError."""
    raw = str(text or "").strip().lower().replace(" ", "").replace(".", "")
    half = "pm" if raw.endswith("pm") else "am" if raw.endswith("am") else ""
    body = raw[:-2] if half else raw
    hour, _, minute = body.partition(":")
    if not hour.isdigit() or (minute and not minute.isdigit()):
        raise ValueError(T("'{text}' is not a time of day. Use for example 15:30 or 3:30pm.", text=text))
    h, m = int(hour), int(minute or 0)
    if half and not 1 <= h <= 12:
        raise ValueError(T("'{text}' is not a time of day. Use for example 15:30 or 3:30pm.", text=text))
    h = (h % 12) + (12 if half == "pm" else 0) if half else h
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(T("'{text}' is not a time of day. Use for example 15:30 or 3:30pm.", text=text))
    return f"{h:02d}:{m:02d}"


def class_times(cfg):
    """{course id: [{"days": [...], "time": "HH:MM", "kind": "lecture" | "section"}]}, cleaned of anything odd."""
    out = {}
    saved = cfg.get("class_times")
    for cid, rows in saved.items() if isinstance(saved, dict) else []:
        good = []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and isinstance(row.get("days"), list) and isinstance(row.get("time"), str):
                days = [d for d in cc_time.DAY_CODES if d in row["days"]]
                clock = row["time"].split(":")
                if days and len(clock) == 2 and all(part.isdigit() for part in clock):
                    good.append({"days": days, "time": row["time"], "kind": "section" if row.get("kind") == "section" else "lecture"})
        if good:
            out[str(cid)] = good
    return out


def _at(day, clock, tz):
    """The moment `clock` ('15:30') on `day`, in the student's timezone."""
    hour, minute = (int(x) for x in clock.split(":"))
    naive = datetime.combine(day, time(hour % 24, minute % 60))
    return naive.replace(tzinfo=tz) if tz else naive.astimezone()


def meetings(times, course_id, day, tz, kind=None):
    """[(start, kind)] for one course on one day, earliest first."""
    code = cc_time.DAY_CODES[day.weekday()]
    out = [(_at(day, row["time"], tz), row["kind"]) for row in times.get(course_id, [])
           if code in row["days"] and kind in (None, row["kind"])]
    return sorted(out)


# ---------------------------------------------------------------- what could be done

def _graded(it):
    """Whether the item counts towards the grade, as far as Canvas says."""
    return it["weight"] != 0 and not (it["course_weighted"] and it["group_weight"] == 0)


def _heavy(it):
    if it["own_weight"] is not None:
        return it["own_weight"] >= HEAVY_PERCENT
    if it["course_weighted"]:
        return (it["group_weight"] or 0) >= HEAVY_PERCENT
    return (it["weight"] or 0) >= HEAVY_PERCENT


def _task(kind, day, item=None, **fields):
    task = {"kind": kind, "day": day, "earliest": day, "fixed": True, "item": item,
            "key": item["key"] if item else "", "course": item["course"] if item else "",
            "course_id": item["course_id"] if item else "", "title": item["name"] if item else "",
            "url": item["url"] if item else "", "why": "", "moved": False}
    task.update(fields)
    return task


def _item_tasks(view, at_local, tz, last_day, ticked=()):
    """Tasks that come from deadlines: overdue work, graded work, exam reviews.

    `ticked` holds the keys the student ticked: a single review or preparation session can be ticked
    off by its own key ("<item>#review2") without calling the exam itself done."""
    today = at_local.date()
    unsure = lambda it: "" if it["confirmed"] else T("; its date is not confirmed")
    by = {sec["key"]: sec["items"] for sec in view["sections"]}
    tasks = []
    late = [i for i in by["overdue"] if i["status"] == "overdue"]
    for it in sorted(late, key=lambda i: (i["kind"] == "external tool", -(i["weight"] or 0), i["deadline"])):
        if it["kind"] == "external tool":
            tasks.append(_task("check", today, it, why=T("Canvas shows no hand-in and this goes through an outside site: "
                                                         "check there that it arrived")))
        else:
            tasks.append(_task("overdue", today, it, why=T("was due {when} and can still be handed in", when=it["due_text"])))
    for it in by["week"] + by["later"]:
        due = cc_time.local(it["deadline"], tz)
        if it["done"] or it["status"] in ("missed", "closed") or due.date() > last_day + timedelta(days=REVIEW_DAYS):
            continue
        if it["exam"]:
            ahead = (due.date() - today).days
            for n in range(1, REVIEW_DAYS + 1):
                day = due.date() - timedelta(days=n)
                if today <= day <= last_day:
                    left = (due.date() - day).days
                    why = T("the exam is the next day ({when})", when=it["due_text"]) if left == 1 \
                        else T("the exam is in {n} days ({when})", n=left, when=it["due_text"])
                    if f"{it['key']}#review{n}" not in ticked:
                        tasks.append(_task("review1" if n == 1 else "review", day, it, key=f"{it['key']}#review{n}",
                                           title=T("Review for {name}", name=it["name"]), why=why + unsure(it)))
            if ahead == 0 and it["status"] != "offline":
                tasks.append(_task("today", today, it, why=T("it is today ({when})", when=it["due_time"]) + unsure(it)))
            continue
        if it["status"] == "offline":
            continue                              # nothing to hand in: a gradebook column, or work done in class
        opens = cc_time.local(it["unlock"], tz).date() if it["not_open"] and it["unlock"] else today
        earliest = max(today, opens)
        graded = _graded(it)
        heavy = graded and (_heavy(it) or it["clash"])
        natural = due.date() - timedelta(days=2 if heavy else 1)
        day = min(max(natural, earliest), due.date())
        if day > last_day:
            continue
        when = it["due_time"] if due.date() == day else it["due_text"]
        if due.date() == day:
            why = T("due the same day, {when}", when=when)
        elif due.date() - day == timedelta(days=1):
            why = T("due the next day ({when})", when=when)
        else:
            why = T("due {when}", when=when)
        if heavy and natural >= earliest and day != due.date():
            why += T("; it weighs a lot ({worth}), so it starts a day early", worth=it["worth_text"]) if _heavy(it) \
                else T("; it sits in a deadline clash, so it starts a day early")
        if it["not_open"] and opens > today:
            why += T("; it opens {when}", when=cc_time.stamp(cc_time.local(it["unlock"], tz)))
        kind = "optional" if not graded else "today" if day == due.date() and day == today else "work"
        tasks.append(_task(kind, day, it, earliest=earliest, fixed=not graded or day == earliest, why=why + unsure(it)))
        # Something that only opens on its last day cannot be started early, but it can be prepared for.
        if graded and opens >= due.date() and (heavy or it["kind"] == "quiz") and opens - timedelta(days=1) >= today \
                and f"{it['key']}#prep" not in ticked:
            tasks.append(_task("prep", opens - timedelta(days=1), it, key=f"{it['key']}#prep",
                               title=T("Prepare for {name}", name=it["name"]),
                               why=T("it opens {opens} and is due {due}", opens=cc_time.stamp(cc_time.local(it["unlock"], tz)),
                                     due=it["due_text"])))
    return tasks


def _material_tasks(payload, cfg, view, at, tz, days, done):
    """One task per course and teaching week: go through the week's module before the first class."""
    times = class_times(cfg)
    at_local = cc_time.local(at, tz)
    today, tasks, missing = at_local.date(), [], []
    number, start, _, _ = cc_week.this_week(payload, cfg, at)
    for c in view["courses"]:
        if c["hidden"] or not c["collected"]:
            continue
        if not any(row["kind"] == "lecture" for row in times.get(c["id"], [])):
            missing.append(c["label"])
            continue
        modules = cc_week.course_modules(payload, c["id"]) or []
        for week in range(2):                                    # this teaching week, and the next if it is in view
            first = start + timedelta(days=7 * week)
            lectures = [m for d in range(7) for m in meetings(times, c["id"], first + timedelta(days=d), tz, "lecture")]
            if not lectures or lectures[0][0] <= at_local:
                continue                                         # the week's first class is over: no more nudging
            lecture = lectures[0][0]
            day = max(today, lecture.date() - timedelta(days=1))
            if day not in days:
                continue
            for m in cc_week.pick_modules(modules, None if number is None else number + week, lecture.date()):
                reading = [cc_text.one_line(i.get("title")) for i in m.get("items") or [] if i.get("type") in READABLE]
                key = f"mod:{c['id']}:{cc_model.sid(m.get('id'))}"
                if not reading or key in done:
                    continue
                title = T("Go through the module {name}: {items}", name=cc_text.one_line(m.get("name")),
                          items=cc_lang.join(reading[:3]) + (T(" and {n} more", n=len(reading) - 3) if len(reading) > 3 else ""))
                tasks.append(_task("material", day, None, key=key, course=c["label"], course_id=c["id"], title=title,
                                   why=T("{course} meets {when}", course=c["label"], when=cc_time.stamp(lecture))))
    return tasks, missing


# ---------------------------------------------------------------- putting tasks on days

def _assign(tasks, days, blocked):
    """Give each day at most one must-do. Returns ({day: task}, {day: [tasks]}, how many landed on a blocked day).

    Fixed tasks take their own day when it is free. Graded work may move to an earlier free day
    (never a blocked one, never before it can be started). Whatever finds no free day is listed
    beside its day's must-do instead.
    """
    must, also, forced = {}, {d: [] for d in days}, 0
    # Within one rule, a task that has only one possible day goes first: the others can still move.
    order = sorted(tasks, key=lambda t: (PRIORITY[t["kind"]], not t["fixed"], t["day"],
                                         -((t["item"] or {}).get("weight") or 0)))
    for t in order:
        t["moved"] = False
        if t["day"] not in also:
            continue
        if t["kind"] in ("check", "optional"):
            also[t["day"]].append(t)
            continue
        span = [t["day"]] if t["fixed"] else [d for d in reversed(days) if t["earliest"] <= d <= t["day"]]
        free = next((d for d in span if d not in must and d not in blocked), None)
        if free is None:
            also[t["day"]].append(t)
            forced += t["day"] in blocked
        else:
            must[free] = t
            t["moved"] = free != t["day"]
            t["placed"] = free
    return must, also, forced


def _memory(saved):
    """The plan's record from state.json, with anything damaged left out."""
    saved = saved if isinstance(saved, dict) else {}
    days = saved.get("days") if isinstance(saved.get("days"), dict) else {}
    rest = saved.get("rest") if isinstance(saved.get("rest"), dict) else {}
    return {"days": {str(d): v for d, v in days.items() if isinstance(v, dict) and isinstance(v.get("must"), str)},
            "rest": {str(w): v for w, v in rest.items() if isinstance(v, str)}}


def _week_id(day):
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def _rest_days(cfg, tasks, days, record):
    """({rest dates}, {week id: date chosen now}). One per calendar week, taken from the setting `rest_days`."""
    wanted = [d for d in cfg.get("rest_days") or [] if d in cc_time.DAY_CODES]
    rest, chosen = set(), {}
    for week in dict.fromkeys(_week_id(d) for d in days):
        saved = (record.get("rest") or {}).get(week)
        if saved:
            rest |= {d for d in days if d.isoformat() == saved}       # "none", or a day already past, adds nothing
            continue
        candidates = [d for d in days if _week_id(d) == week and cc_time.DAY_CODES[d.weekday()] in wanted]
        if not candidates:
            continue

        def cost(day):
            must, also, forced = _assign([dict(t) for t in tasks], days, rest | {day})
            return forced, sum(len(v) for v in also.values()), REST_PREFERENCE.index(cc_time.DAY_CODES[day.weekday()])
        best = min(candidates, key=cost)
        rest.add(best)
        chosen[week] = best.isoformat()
    return rest, chosen


def _is_done(key, view, ticks):
    """Whether a remembered must-do has been finished: handed in or ticked (for module reading, ticked)."""
    if key in ticks:
        return True
    key = key.split("#")[0]
    return any(i["key"] == key and i["done"] for sec in view["sections"] for i in sec["items"])


def _line(task):
    name = f"{task['course']} · {task['title']}" if task["course"] else task["title"]
    return name + (" — " + task["why"] if task["why"] else "")


def build(payload, cfg, at=None, days=None, view=None, record=True, rest=None):
    """The plan for the coming days. `rest`: 'fri'/'sat'/..., 'none' or 'auto' to (re)set this week's rest day.

    With `record`, today's must-do and this week's rest day are remembered, which is what keeps the
    plan steady through the day and lets tomorrow know what to carry over.
    """
    at = at or cc_time.now()
    tz = cc_time.zone(cfg)
    at_local = cc_time.local(at, tz)
    today = at_local.date()
    count = int(days or cfg.get("plan_days") or 7)
    dates = [today + timedelta(days=n) for n in range(count)]
    view = view or cc_deadlines.build_view(payload, cfg, at=at)
    with cc_store.state_lock(cfg):
        state = cc_state.load(cfg)
        memory = _memory(state.get("plan"))
        this_week = _week_id(today)
        if rest == "auto":
            memory["rest"].pop(this_week, None)
        elif rest == "none":
            memory["rest"][this_week] = "none"
        elif rest in cc_time.DAY_CODES:
            monday = today - timedelta(days=today.weekday())
            memory["rest"][this_week] = (monday + timedelta(days=cc_time.DAY_CODES.index(rest))).isoformat()

        ticked = state.get("done") or {}
        tasks = _item_tasks(view, at_local, tz, dates[-1], ticked)
        materials, no_times = _material_tasks(payload, cfg, view, at, tz, dates, ticked)
        tasks += materials
        by_key = {t["key"]: t for t in tasks}

        # Rule 2: what was the must-do on an earlier day and is still open comes first today.
        carried = None
        for iso in sorted((d for d in memory["days"] if d < today.isoformat()), reverse=True)[:7]:
            key = memory["days"][iso]["must"]
            if key in by_key and by_key[key]["kind"] not in ("overdue",):
                carried = by_key[key]
                try:
                    was = datetime.strptime(iso, "%Y-%m-%d").date()
                except ValueError:
                    continue
                carried.update(kind="carried", day=today, earliest=today, fixed=True,
                               why=T("carried over from {day}", day=cc_time.day(at_local.replace(
                                   year=was.year, month=was.month, day=was.day))) + (T("; ") + carried["why"] if carried["why"] else ""))
                break
        # Today's must-do does not change during the day: what was chosen this morning stays on top.
        pinned = (memory["days"].get(today.isoformat()) or {}).get("must")
        done_today = None
        if pinned and pinned in by_key:
            by_key[pinned].update(day=today, earliest=today, fixed=True)
        elif pinned and _is_done(pinned, view, state.get("done") or {}):
            done_today = (memory["days"][today.isoformat()] or {}).get("title") or pinned
        elif pinned:
            pinned = None                    # it is neither open nor done any more (removed, or its class has passed)
            memory["days"].pop(today.isoformat(), None)

        rest_days, chosen = _rest_days(cfg, tasks, dates, memory)
        must, also, _ = _assign(tasks, dates, rest_days)
        if pinned and pinned in by_key and must.get(today) is not by_key[pinned]:      # keep the morning's choice on top
            bumped = must.get(today)
            must[today] = by_key[pinned]
            also[today] = ([bumped] if bumped else []) + [t for t in also[today] if t is not by_key[pinned]]

        if record:
            if today in must and not pinned:
                memory["days"][today.isoformat()] = {"must": must[today]["key"], "title": _line(must[today]).split(" — ")[0]}
            if this_week in chosen:
                memory["rest"][this_week] = chosen[this_week]
            oldest = (today - timedelta(days=KEEP_DAYS)).isoformat()
            memory["days"] = {d: v for d, v in memory["days"].items() if d >= oldest}
            memory["rest"] = dict(sorted(memory["rest"].items())[-8:])
            if state.get("plan") != memory:
                state["plan"] = memory
                cc_state.save(cfg, state)

    times = class_times(cfg)
    labels = {c["id"]: c["label"] for c in view["courses"]}
    due_on = {}
    for sec in view["sections"]:
        if sec["key"] in ("week", "later"):
            for it in sec["items"]:
                due_on.setdefault(cc_time.local(it["deadline"], tz).date(), []).append(it)
    rows = []
    for d in dates:
        shown = at_local.replace(year=d.year, month=d.month, day=d.day)
        classes = sorted((start, kind, labels.get(cid, cid)) for cid in times for start, kind in meetings(times, cid, d, tz)
                         if cid in labels)
        rows.append({
            "date": d.isoformat(), "label": cc_time.day(shown), "today": d == today, "rest": d in rest_days,
            "classes": [T("{course} section {time}", course=name, time=cc_time.clock(start)) if kind == "section"
                        else T("{course} {time}", course=name, time=cc_time.clock(start)) for start, kind, name in classes],
            "must": must.get(d), "also": also[d], "due": due_on.get(d, []),
            "must_done": done_today if d == today else None})
        if must.get(d) and must[d]["moved"]:              # say which day it was meant for
            was = must[d]["day"]
            must[d]["moved_from"] = cc_time.day(at_local.replace(year=was.year, month=was.month, day=was.day))
    # A free day is a chance to get ahead: point at the next piece of work that is not on the plan yet.
    planned = {t["key"].split("#")[0] for t in tasks}
    ahead = next((i for sec in view["sections"] if sec["key"] in ("week", "later") for i in sec["items"]
                  if not i["done"] and i["status"] == "todo" and i["key"] not in planned and not i["not_open"]
                  and not i["exam"] and _graded(i)), None)
    notes = []
    undated = [i for sec in view["sections"] if sec["key"] == "undated" for i in sec["items"]
               if not i["done"] and i["status"] == "todo" and _graded(i) and i["points"]]
    if undated:
        names = cc_lang.join((cc_deadlines.item_title(i) for i in undated[:4]), "clauses")
        notes.append(T("{n} open item(s) have no date on Canvas, so they are not on this plan: {names}{more}. "
                       "If you know a date: coach.py edit <key> --due \"2026-10-20 17:00\"", n=len(undated), names=names,
                       more=T(" and {n} more", n=len(undated) - 4) if len(undated) > 4 else ""))
    if no_times:
        notes.append(T("No class times are saved for {courses}, so their module reading is not planned. Add them with: "
                       "coach.py classes set <course> <days> <time>", courses=cc_lang.join(no_times)))
    if not (cfg.get("rest_days") or []) and not memory["rest"].get(_week_id(today)):
        notes.append(T("No rest day is set. To keep one day a week free: coach.py config set rest_days fri,sat,sun"))
    return {"days": rows, "title": T("Study plan, {first} to {last}", first=rows[0]["label"], last=rows[-1]["label"]),
            "now_text": view["now_text"], "collected_text": view["collected_text"], "age_text": view["age_text"],
            "stale": view["stale"], "host": view["host"], "warnings": view["warnings"], "notes": notes,
            "clashes": view["clashes"], "clash_days": view["clash_days"], "ahead": ahead,
            "rest": [r["label"] for r in rows if r["rest"]], "rest_options": list(cfg.get("rest_days") or [])}


def todays_must(payload, cfg, view=None, at=None):
    """Today's must-do for the briefing: {"key", "short", "text", "why"}, or None when the day is free."""
    plan = build(payload, cfg, at=at, view=view)
    task = plan["days"][0]["must"]
    if not task:
        return None
    name = f"{task['title']} ({task['course']})" if task["course"] else task["title"]
    return {"key": task["key"].split("#")[0], "short": name, "text": _line(task).split(" — ")[0], "why": task["why"]}


# ---------------------------------------------------------------- drawing

def _tag(task):
    return T(LABEL[task["kind"]])


def render_text(plan, banner="", keys=False):
    out = [T("{title}, as of {now}", title=plan["title"], now=plan["now_text"])]
    if banner:
        out.append(cc_lang.warning(banner))
    if plan["stale"]:
        out.append(cc_lang.warning(T("this data is old (collected {age}).", age=plan["age_text"])))
    out += [cc_lang.warning(w) for w in plan["warnings"]]
    if plan["clashes"]:
        out += [""] + cc_deadlines.clash_lines(plan, limit=2)
    key = lambda t: f"  [key {t['key']}]" if keys else ""
    for d in plan["days"]:
        head = (T("TODAY, {day}", day=d["label"]) if d["today"] else d["label"]) + (T(" — rest day") if d["rest"] else "")
        out += ["", head]
        if d["classes"]:
            out.append(T("  Classes: {list}", list=cc_lang.join(d["classes"])))
        if d["due"]:
            out.append(T("  Due this day: {list}", list=cc_lang.join(
                (("✓ " if i["done"] else "") + cc_lang.paren(cc_deadlines.item_title(i), i["due_time"]) for i in d["due"]),
                "clauses")))
        if d["must_done"]:
            out.append(T("  Must-do done: {task}", task=d["must_done"]))
        if d["must"]:
            label = T("  Next:    {task}") if d["must_done"] else T("  Must do: {task}")
            out.append(label.format(task=f"[{_tag(d['must'])}] {_line(d['must'])}") + key(d["must"]))
            if d["must"]["moved"]:
                out.append(T("           (brought forward from {day}, which already has a must-do)", day=d["must"]["moved_from"]))
        elif not d["rest"] and not d["must_done"]:
            out.append(T("  Must do: nothing; a free day."))
        for t in d["also"]:
            out.append(T("  Also:    {task}").format(task=f"[{_tag(t)}] {_line(t)}") + key(t)
                       + (T("  (cannot wait for another day)") if d["rest"] and t["kind"] not in ("optional", "check") else ""))
    if plan["ahead"]:
        i = plan["ahead"]
        out += ["", T("To get ahead on a free day: {item}", item=cc_lang.paren(f"{i['course']} · {i['name']}", i["due_text"]))]
    if plan["rest"]:
        out += ["", T("Rest day: {day}, the lightest of your rest-day choices. To pick another: coach.py plan --rest fri|sat|sun|none",
                      day=cc_lang.join(plan["rest"]))]
    out += ["", T("Tick a task off with: coach.py done <key or words>   (keys: coach.py plan --keys)")]
    if plan["notes"]:
        out += [""] + [cc_lang.note(n) for n in plan["notes"]]
    out += ["", T("Data collected {when} ({age}) from {host}.", when=plan["collected_text"], age=plan["age_text"],
                  host=plan["host"])]
    return "\n".join(out)


def _card(task, must, note=""):
    e = cc_text.escape
    name = e(task["title"])
    if cc_text.web_link(task["url"]):
        name = f'<a href="{e(cc_text.web_link(task["url"]))}" target="_blank" rel="noopener">{name}</a>'
    tag = f'<span class="tag{" must" if must else ""}">{e(T("must do") if must else _tag(task))}</span>'
    kind = f'<span class="tag">{e(_tag(task))}</span>' if must else ""
    meta = " · ".join(x for x in (f'<span class="course">{e(task["course"])}</span>' if task["course"] else "",
                                  e(task["why"]), e(note)) if x)
    return (f'<li style="--c:hsl({cc_deadlines._hue(task["course_id"])} 55% 50%)"><div class="body">'
            f'<div class="name">{tag}{kind}{name}</div><div class="meta">{meta}</div></div></li>')


def render_html(plan, banner=""):
    e = cc_text.escape
    out = cc_deadlines.page_head(T("Study Plan")) + [
        f"<h1>{e(plan['title'])}</h1>",
        '<p class="sub">' + e(T("As of {now} · data collected {age}", now=plan["now_text"], age=plan["age_text"])) + "</p>"]
    alerts = ([banner] if banner else []) + ([cc_deadlines.stale_alert(plan)] if plan["stale"] else []) + plan["warnings"]
    out += [f'<div class="banner">{e(a)}</div>' for a in alerts]
    out.append(cc_deadlines.clash_html(plan, limit=2))
    for d in plan["days"]:
        head = (T("Today, {day}", day=d["label"]) if d["today"] else d["label"]) + (T(" — rest day") if d["rest"] else "")
        out.append(f'<h2 class="course">{e(head)}</h2>')
        facts = ([T("Classes: {list}", list=cc_lang.join(d["classes"]))] if d["classes"] else []) + \
                ([T("Due this day: {list}", list=cc_lang.join((cc_lang.paren(cc_deadlines.item_title(i), i["due_time"])
                                                               for i in d["due"]), "clauses"))] if d["due"] else []) + \
                ([T("Must-do done: {task}", task=d["must_done"])] if d["must_done"] else [])
        out += [f'<p class="rest">{e(f)}</p>' for f in facts]
        cards = ([_card(d["must"], not d["must_done"],
                        T("brought forward from {day}, which already has a must-do", day=d["must"]["moved_from"])
                        if d["must"]["moved"] else "")]
                 if d["must"] else []) + [_card(t, False) for t in d["also"]]
        if cards:
            out.append("<ul>" + "".join(cards) + "</ul>")
        elif not d["rest"] and not d["must_done"]:
            out.append(f'<p class="rest">{e(T("No must-do: a free day."))}</p>')
    if plan["ahead"]:
        i = plan["ahead"]
        out.append('<p class="rest">' + e(T("To get ahead on a free day: {item}",
                                            item=cc_lang.paren(f"{i['course']} · {i['name']}", i["due_text"]))) + "</p>")
    if plan["notes"]:
        out.append('<div class="notes">' + "".join(f"<p>{e(n)}</p>" for n in plan["notes"]) + "</div>")
    out.append("<footer>" + e(T("Collected {when} from {host}. One must-do a day; the rest is optional. Made by Canvas Coach.",
                                when=plan["collected_text"], host=plan["host"])) + "</footer>")
    out.append("</main></body></html>")
    return "\n".join(out)
