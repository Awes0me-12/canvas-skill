# Extending Canvas Coach

Read this before changing the skill. It is written for an AI agent or a person picking the
project up cold.

## How it fits together

```
Canvas  --(fetch.js, in a logged-in page)-->  payload (JSON)
        --(cc_ingest.py: check + save)----->  <data>/.data/raw/<time>.json   one per collection
        --(cc_model.py: facts)------------->  items
        --(a view module: build + render)--->  text for the agent, a page for the student
```

Core (every feature depends on these):

| File | Job |
|---|---|
| `tools/resources.json` | The list of Canvas addresses to read and which fields to keep |
| `tools/fetch.js` | Runs in the browser page: fetch, paginate, trim, checksum |
| `tools/cc_browser.py` | Drives the `agent-browser` CLI: login window, run fetch.js, close |
| `tools/cc_ingest.py` | Validates a payload (format, site, checksum), merges parts, saves |
| `tools/cc_store.py` | Settings, data folder, snapshots, `state.json` |
| `tools/cc_time.py`, `tools/cc_text.py` | Clock and date wording; HTML to text (marking hidden text), safe file names |
| `tools/cc_lang.py`, `tools/cc_lang_zh.py` | The output language: `T()` and the Chinese catalogue |
| `tools/cc_model.py` | Raw Canvas rows to plain facts: status, kind, course table |
| `tools/cc_cli.py` | The command registry, exit codes, `refresh()` and `fetch_extra()` |
| `tools/coach.py` | Entry point; built-in commands; loads every `cc_cmd_*.py` |

Features (each is a logic module plus a small command file):

| Feature | Logic | Commands in | Page |
|---|---|---|---|
| Deadline list | `cc_deadlines.py` | `coach.py` (`deadlines`) | `Deadlines.html` |
| Deadline clashes | `cc_clash.py` | shown by `deadlines`, `week`, `plan`, `brief` | block at the top of the lists |
| Study plan and class times | `cc_plan.py` | `cc_cmd_plan.py` (`plan`, `classes`) | `Study Plan.html` |
| Own ticks, deadlines, dates, notes, questions | `cc_state.py` | `cc_cmd_marks.py` (`done`, `undone`, `add`, `edit`, `confirm`, `remove`, `marks`, `ask`, `questions`, `answer`) | |
| This week | `cc_week.py` | `cc_cmd_week.py` (`week`) | `This Week.html` |
| What changed | `cc_changes.py` | `cc_cmd_week.py` (`changes`) | block at the top of the lists |
| Briefing and schedule | `cc_brief.py`, `cc_schedule.py` | `cc_cmd_brief.py` (`brief`, `schedule`) | |
| Grades | `cc_grades.py` | `cc_cmd_grades.py` (`grades`) | `Grades.html` |
| Feedback, assignment details | `cc_feedback.py` | `cc_cmd_feedback.py` (`feedback`, `show`) | saved `.md` per assignment |
| Calendar file and feed | `cc_ical.py` | `cc_cmd_calendar.py` (`export`, `feed`) | `Canvas deadlines.ics` |
| Course materials | `cc_materials.py` | `cc_cmd_materials.py` (`materials`) | saved `.md` pages, downloaded files |

Design rules worth keeping:

- **Scripts decide, the agent relays.** Dates, statuses and sums are computed in Python so they
  are exact and testable. `SKILL.md` only says when to run what.
- **Snapshots are never edited.** Each collection is a new file. Anything that compares "then and
  now" reads two snapshots.
- **Never show old data as fresh.** A failed refresh must print a `WARNING:` and keep the data time.
- **Unknown is not empty.** When a request failed, a list was cut off, or a fallback-route
  collection left things out, say "could not be read", never "nothing" and never "removed".
- **Only call work done when Canvas shows a hand-in.** A grade alone is not proof (teachers type
  zeros for missing work); see `cc_model.classify` and its tests before changing a status.
- **Facts versus estimates.** Show what Canvas states (points, group weights, its own grade) and
  label anything worked out here as an estimate with its assumptions.
- **Exit codes mean one thing each**: 0 fine, 1 failed, 2 log in, 3 set up, 4 refresh by the
  fallback route. `SKILL.md` relies on them.
- **Read-only.** Only GET requests, only to the configured Canvas host. Secrets (the calendar
  link, temporary download addresses) are never printed or put in snapshots.
- **Nothing in the background without a clear yes** from the student (`schedule install`), and no
  file downloads without `--download`.
- **Every sentence goes through `T()`**, so it can come out in the student's language. What the code
  tests (statuses, kinds, file states, reasons a course is hidden) stays an English code word beside
  the translated text; never compare translated text.
- **What the student told us wins over a guess, never over Canvas silently.** Their own date on a
  Canvas item drives the list, and the line says what Canvas shows.
- Standard library only, Python 3.10+.

## The payload (`canvas-coach/1`)

```json
{
  "format": "canvas-coach/1",
  "host": "https://canvas.school.edu",
  "collected_at": "2026-10-12T04:36:10.000Z",
  "user": {"id": "9001", "name": "..."},
  "courses": [{"id": "101", "name": "...", "course_code": "...", "term": {"end_at": "..."},
               "enrollments": [{"type": "student", "computed_current_score": 86.4}]}],
  "data": {"101": {"assignments": [...], "assignment_groups": [...], "modules": [...], "submissions": [...]}},
  "global": {"announcements": [...], "conversations": [...], "profile": {"time_zone": "..."}},
  "errors": [{"resource": "assignments", "course_id": "505", "status": 401, "message": "..."}],
  "truncated": [],
  "flags": ["hidden_text"],
  "check": {"assignments": 23, "crc": "1a2b3c4d"}
}
```

`flags` lists the checks this collection made on text it did not keep. With `"hidden_text"` there,
an assignment row carries `"hidden_text": true` when its instructions hide text from a reader, and
the absence of that field means "looked, found nothing". Without the flag (an older snapshot, or
the fallback route) nothing is known either way.

`data[course_id][name]` holds each `per_course` resource; `global[name]` holds each `global` one.
A key is absent when that request failed (see `errors`) and in snapshots saved before the
resource was added, so always handle `None`. `conversations` is only there when the `inbox`
setting is on. The private calendar link in `profile` is moved to the settings file at ingest.

`check` is a CRC32 over one line per course and one per assignment, sorted. The lines hold the
text and true/false fields that decide what is shown. Numbers are left out on purpose, because
JavaScript and Python print them differently. `fetch.js` and `cc_ingest.checksum` must build these
lines identically; `tests/test_coach.py` (CollectorTests) tells you if they drift. Modules,
submissions and the global resources are not covered by it.

## Recipe: collect something new from Canvas

1. Add an entry to `tools/resources.json`:

   ```json
   {"name": "discussions",
    "path": "/api/v1/courses/{course_id}/discussion_topics?per_page=50",
    "keep": ["id", "title", "posted_at", "html_url", "author.display_name"]}
   ```

   Placeholders: `{course_id}`; `{course_contexts}` (becomes `context_codes[]=course_1&...`, for
   `global` entries); `{date:-21}` (a date that many days from today). `keep: null` keeps every
   field; prefer a list, because descriptions can be very large. Dotted names reach into nested
   objects and lists. `"option": "inbox"` collects the entry only when that setting is on.
   `"expand": {"field": "items", "path": ".../{id}/items"}` fetches a nested list separately when
   Canvas leaves it out (it does that for long modules).
   `"inspect": {"hidden_text": "description"}` has a text field looked at once without saving it:
   `fetch.js` sets the text aside only when it shows a sign of hiding, `cc_ingest._judge_hidden_text`
   lets `cc_text.has_hidden_text` decide, keeps the yes/no under the flag's name and drops the text.
2. Add matching sample data to `tests/sample_data.py`, a route to `tests/mock_canvas.py`, and the
   resource to `make_payload` in `tests/helpers.py`.
3. Read it with `payload["data"].get(course_id, {}).get("discussions")`.
4. Run `python3 tests/run_all.py`. One test (CollectorTests.test_full_collection_matches_python)
   lists the expected resource names on purpose, so it fails until you update it; that is your
   reminder to add assertions for the new data.

A failure of one resource for one course is recorded in `errors` and never stops the rest. Each
view warns only about the resources it depends on (see `DEADLINE_ERRORS` in `cc_deadlines.py`).

## Recipe: add a command (a new feature in one file)

Create `tools/cc_cmd_<name>.py`. It is loaded automatically; nothing else needs editing.

```python
"""Command: hello (an example)."""
import cc_store
from cc_cli import REFRESH_ARGS, arg, command, no_data, refresh, say
from cc_lang import T, plural, warning


@command("hello", "Say how many courses are collected.",
         arg("--loud", action="store_true", help="shout"),
         *REFRESH_ARGS)                      # gives the command --force and --offline
def cmd_hello(args):
    cfg = cc_store.load_config()
    payload, banner, code = refresh(cfg, args.force, args.offline)   # collects when the data is stale
    if payload is None:
        return no_data(banner, code)
    if banner:
        say(warning(banner))                 # the refresh failed: this is the previous collection
    text = plural(len(payload["data"]), "{n} course", "{n} courses")
    say(text.upper() if args.loud else text)
    return code                              # pass refresh()'s code on, so 2 still means "log in"
```

Then add the two new sentences to `tools/cc_lang_zh.py` (`"{n} course": "{n}门课程"`, and the same
for `"{n} courses"`); `tests/test_lang.py` fails until they are there.

For anything bigger, put the logic in its own module shaped like `cc_deadlines.py`: a
`build_view()` that returns a plain dict, plus `render_text()` and `render_html()` (reuse
`cc_deadlines.CSS`). A broken add-on is skipped with a note; it cannot take the other commands down.

Useful building blocks:

- `cc_model.course_table(payload, cfg, at)`: which courses are shown, and why not.
- `cc_deadlines.build_view(payload, cfg, show_all=True)`: every item, decorated, including the
  student's own deadlines and ticks. Each item has a `key` (`"<course_id>:<assignment_id>"`, or
  `"m:m1"` for the student's own).
- `cc_state.find(items, words)` and `cc_state.match_course(courses, words)`: turn the student's
  words into one item or one course.
- `cc_cli.fetch_extra(cfg, [{"name": "page", "path": "/api/v1/...", "keep": [...]}])`: ask Canvas
  for something that is not in the regular collection. Returns `({name: body}, errors)`. It opens
  the browser, so it cannot run in unit tests: keep the fetcher replaceable (see how
  `cc_materials.gather` takes it as a parameter) and cover the real thing in `tests/e2e_browser.py`.
- `cc_text.html_to_text`, `cc_text.links`, `cc_text.safe_name`; `cc_time.stamp(cc_time.local(dt, cc_time.zone(cfg)))`.
- `cc_text.one_line(text)` for any Canvas text printed on one line (a title, a name, a subject), so a
  line break in it cannot imitate the script's own `WARNING:` lines; `cc_text.web_link(url)` for any
  address that becomes a link in a page.
- `cc_store.writable_path(cfg, path, owner)` then `cc_store.record_written(cfg, path, owner)` when
  saving a file for the student: it never overwrites a copy they changed or another item's file.
- `cc_store.state_lock(cfg)` around read-change-save of `state.json`, and
  `cc_store.update_config(key=value)` instead of saving a settings dict you loaded earlier: the daily
  briefing can run at the same moment as a command.
- `cc_cli.data_age(payload, cfg)` for the "Data collected ..." line every answer ends with.

## One skill, two agents

Claude Code and Codex read the same `SKILL.md` (only `name` and `description` in its header, which
both understand) and run the same scripts. Codex also reads `agents/openai.yaml` for how the skill is
shown in its list; Claude Code ignores that folder. Keep anything agent-specific to a sentence in
`SKILL.md` that names the agent, as the Codex sandbox paragraph does. `install.sh` links the folder
into `~/.claude/skills` and `~/.codex/skills` (or `$CODEX_HOME/skills`), so one copy serves both and
they share one login, one data folder and the student's marks. `tests/test_skill_files.py` checks
that both headers stay valid.

## Recipe: add or change a translation

English is written in the code; `tools/cc_lang_zh.py` maps each English sentence to Chinese.

- A sentence printed directly: `T("Nothing changed on Canvas since {since}.", since=when)`.
- A sentence kept in a constant: mark it with `N("...")` where it is defined and translate it where
  it is used, `T(CONSTANT)`. `N()` does nothing at run time; it lets the test find the sentence.
- A sentence whose English depends on a count: `plural(n, "{n} change", "{n} changes")`.
- Joining pieces: `cc_lang.join(parts)` (", " or "，") and `cc_lang.join(parts, "clauses")`.
- Dates: only through `cc_time.stamp/day/clock/relative`, which already follow the language.
- Never translated: course names, assignment titles, group names, file names, commands, `[key ...]`.

`python3 tests/lang_keys.py --missing` lists the sentences that still lack a translation.
`tests/test_lang.py` fails when one is missing, when the catalogue holds one the code no longer
uses, when the `{placeholders}` differ, or when any view rendered in Chinese still contains two
English words in a row that are not a name from Canvas, a command or a listed term.

Another language: copy `cc_lang_zh.py` to `cc_lang_<code>.py`, translate, and add the code to
`SUPPORTED` in `cc_lang.py`.

## What the student stores

`<data>/.data/state.json`:

```json
{"version": 2,
 "done": {"101:1005": "2026-10-12T19:00:00Z", "mod:101:503": "..."},
 "manual_deadlines": [{"id": "m1", "course": "HIST 101", "course_id": "12345", "title": "...", "due": "...", "note": "...",
                       "kind": "exam", "weight": 25, "points": null, "url": "", "place": "HFH 1104", "source": "syllabus",
                       "confirmed": false, "alt": [{"due": "...", "source": "TA email"}]}],
 "overrides": {"101:1014": {"due": "...", "canvas_due": null, "note": "...", "source": "...", "confirmed": true}},
 "questions": [{"id": "q1", "text": "...", "course": "", "course_id": "", "item": "", "asked": "...", "answered": null, "answer": ""}],
 "plan": {"days": {"2026-10-12": {"must": "101:1005", "title": "..."}}, "rest": {"2026-W42": "2026-10-17"}},
 "baseline": "20261012T083400.json",
 "change_log": [{"at": "...", "text": "Due date changed: ..."}]}
```

Every reader fills in defaults for fields an older file lacks (an entry without `kind` is an exam
when its title says so). `overrides` are the student's own date and note on a Canvas item;
`canvas_due` is what Canvas said when the date was set, so a later change on Canvas can be pointed
out. `plan.days` is each day's must-do (it stays the same all day, and an undone one is carried
over); `plan.rest` is the rest day chosen per calendar week. A tick on `mod:<course>:<module>` marks
a week's module reading as done.

`baseline` is the snapshot the student last looked at; `deadlines`, `week` and `changes` move it
forward, `brief` and `plan` do not. It is protected from pruning.

## How the plan is made (`cc_plan.py`)

Tasks are made from the deadline view (so ticks, own deadlines and own dates are already in), then
put on days by `_assign`, at most one must-do per day:

| Order | Task | Day | Can it move? |
|---|---|---|---|
| 1 | overdue and still open | today | no |
| 2 | an earlier day's must-do, still open | today | no |
| 3 | due today; the last review before an exam | that day | no |
| 4 | graded work | the day before it is due; two days before when its group is 10%+ or it is in a clash | to an earlier free day, not before it opens |
| 5 | preparing for work that only opens on its due day; earlier exam reviews | the day before; days 2 and 3 before the exam | no |
| 6 | the week's module | the day before the course's first class of the teaching week | no |
| | not counted in the grade; "check the outside site" | its day | listed beside the must-do |

A task that finds no free day is listed under "Also" on its own day. The rest day is the candidate
(setting `rest_days`) on which the fewest tasks are forced; ties go Saturday, Sunday, Friday.
Nothing estimates time: there is no data for it.

## The two ways data gets in

1. **Scripted browser (default).** `cc_browser.py` runs `agent-browser --session canvas-coach
   --restore`. `login` opens a visible window; the student logs in; cookies are saved encrypted
   under `~/.agent-browser/sessions/` with a key in `~/.config/canvas-coach/browser.key`.
   `collect` opens Canvas headless, starts `fetch.js`, polls `window.__cc` until it is done, reads
   the result back in slices and closes. `fetch_extra` is the same run with a one-off resource list.
2. **Agent bridge (fallback).** `fetch-script` prints `fetch.js` plus a call that returns the result
   as a list of short text pieces, for a browser tool to run in a tab the student is logged in
   to; the agent saves that list and `ingest` joins and checks it. It collects only assignments
   and groups, in slim mode (old finished work and empty fields left out), to keep the copy short.
   `show` and `materials --read` are not available on this route.

When neither works (login expired), the private calendar link (`cc_ical.backup_notes`) still
reveals new and moved deadlines; `refresh()` appends that to its warning.

Things that were tried and did not work, so do not repeat them:

- One long `agent-browser eval` awaiting the whole collection: fails after about 30 seconds.
- Reopening the browser immediately after `close`: leaves a stray browser process. `close()` waits.
- A persistent `--profile` folder: stale lock files after a crash. `--restore` alone keeps the login.
- A per-item "≈% of grade": wildly high early in a term when few items are posted. Show the
  group's weight instead.
- Telling the student's own comments apart by name: Canvas labels comments with the display name,
  which often differs from the account name. Compare `author_id` with the user id.
- Treating an all-day calendar event as midnight UTC: it lands on the day before for anyone west
  of Greenwich. It is a date, not a time.
- Snapping teaching weeks to Monday: many courses run their weeks Sunday to Saturday. Count from
  the first day the module names give.
- Passing page text on as-is: instructions can carry sentences styled to be invisible (white on
  white) that only a machine would pick up. `cc_text.html_to_text` marks them; excerpts drop them.
- Calling all white text hidden: a white label on a coloured box is ordinary formatting. The reader
  works out the text colour and the background in force for every piece of text and hides it only
  when the two are nearly the same (so near-white on white counts, and black on black too).
- Popping the element stack on any end tag: `<br />` inside a hidden span ended the hidden part, and
  the rest of the planted sentence came out as a visible instruction. Void tags and end tags with
  no start are ignored now, and old-style formatting (`<font>`, `<b>`) stays in force across a
  paragraph end, as in a browser. `tests/test_own.py` (HiddenTextTests) holds the cases.
- Opening a "[not visible ...]" marker as soon as a hidden element starts: empty hidden elements
  raised false alarms. The marker opens only when hidden words actually appear.
- Running `logout` in a trial run that only redirected the settings folder: it deleted the real
  saved login. The browser session is now tied to the settings folder (`cc_browser._session_name`),
  and the test helpers set a session of their own as well.
- Counting every open item towards a clash: four-point surveys made every week a "clash". An item
  counts when it is an exam or worth at least `clash_min_percent` of the grade.
- Testing translated words in code (`"hidden" in note`, `state == "saved"` after translating):
  it breaks in the other language. Keep a code word beside the text (`hidden_code`, `due_today`).
- Returning the collector's result from a browser script tool as an object or as one long text:
  Claude in Chrome replaces anything nested more than a few levels with "[TRUNCATED: Max depth
  exceeded]" and cuts a text at about 1,000 characters. A flat list of short pieces survives.
- Trusting a download because it arrived: an expired link answers with a login page. Compare the
  size with what Canvas reported.
- Keeping the data folder in Documents: macOS makes a background job wait on a permission prompt
  before it may read Documents, Desktop or Downloads. The default is `~/CanvasCoach`, and
  `schedule install` refuses a data folder in those places.
- A launchd job with `ProcessType` "Background": the hidden browser then takes about 25 seconds
  to start and the collection times out. The job uses "Interactive".

## Ideas not built yet

| Idea | Where it would go |
|---|---|
| Discussions and their unread replies | `resources.json` entry, a section in `cc_week.py` |
| Reading a PDF syllabus for dates | the agent reads the downloaded file and calls `coach add`; no code needed |
| Study-time estimates from past weeks | `state.json` (time per item), then `cc_plan._assign` could weigh days by hours |
| Hidden text inside PDF or Word files | a reader in `cc_materials.py` after download; the page check is HTML only |
| Class times read from the registrar | needs a source per school; today the student says them once (`classes set`) |
| Pushing straight to Google Calendar | needs sign-in to Google; the `.ics` file avoids that |
| Scheduling on Windows or Linux | `cc_schedule.py` (today: macOS launchd only) |

## Testing

```bash
python3 tests/run_all.py               # every unit test, plus fetch.js under Node if Node is installed
python3 tests/e2e_browser.py           # real hidden browser against the fake Canvas (needs agent-browser)
```

`tests/helpers.py` gives new test files a pinned clock, an isolated settings and data folder,
`make_payload()` and `run_cli()`; start a test file with `from helpers import *`.
`CANVAS_COACH_LANG=zh` in front of a command shows its output in Chinese without changing the setting.
For a trial run on a copy of real data, set both `CANVAS_COACH_HOME` and `CANVAS_COACH_ROOT` to
scratch folders (copy `config.json` and `.data` into them) and add `--offline`; a redirected
settings folder also gets its own browser session, so the real login cannot be touched.
`tests/mock_canvas.py` imitates the Canvas quirks that matter: the `while(1);` prefix, `Link`
pagination, cookie login, the two different 401 answers, a closed Files area, long modules whose
items are left out, and the calendar link. `CANVAS_COACH_NOW` pins the clock, `CANVAS_COACH_HOME`
moves the settings folder, `CANVAS_COACH_ROOT` the data folder, `CANVAS_COACH_LAUNCH_DIR` the
schedule file, and `CANVAS_COACH_SESSION` gives tests their own browser session, so tests never
touch the real settings, data, schedule or saved login.

The mock is not Canvas. When something looks wrong against the real site, save the snapshot and
add its shape as a test.
