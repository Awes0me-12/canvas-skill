# Canvas Coach

A personal agent skill that reads your Canvas and answers study questions from it. It works in
**Claude Code** and in **Codex**. It is read-only: it never posts, messages or submits anything.

It can tell you:

- what is overdue, due in the next 7 days and later, and whether you have handed it in
- what to do each day this week: one must-do per day with the reason, and one rest day
- when deadlines from different courses fall within 48 hours of each other
- what this week's modules hold and what your teachers announced
- what changed on Canvas since you last looked (new work, moved dates, new grades)
- where you stand in each course, and what a target grade would take
- what your markers wrote, and exactly what an assignment asks for (instructions and rubric)
- what is in the course materials, saved as text you and your assistant can read
- whether an assignment's instructions hide text a reader cannot see (it is marked, never followed)

It also keeps what you tell it that Canvas does not know: exams and deadlines from a syllabus, your
own date for work Canvas shows without one, notes, and questions you want to ask a TA.

Everything can be shown in English or in Chinese (course and assignment names stay as they are).

## Installing

You need a Mac with Python 3.10 or newer, and `agent-browser` (it opens the window where you log
in to Canvas). These are **Terminal** commands; paste one at a time.

1. Get the files (put this repository's address after `clone`):

```bash
git clone https://github.com/<you>/canvas-coach.git ~/canvas-coach
```

2. Make it a skill in both apps. This only creates two links pointing at that folder, so Claude
   Code and Codex share one copy, one login and one data folder:

```bash
bash ~/canvas-coach/install.sh
```

3. If the script says agent-browser is missing:

```bash
npm i -g agent-browser && agent-browser install
```

4. Restart Claude Code or Codex.

To update later, run `git -C ~/canvas-coach pull`; the links keep pointing at the new version.

## Using it

Ask in plain words. In Claude Code you can also start with `/canvas-coach`, in Codex with
`$canvas-coach`. For example:

- "What's due this week?" / "What's on this week?" / "这周有什么要交的？"
- "What should I do today?" / "Plan my week, Saturday off."
- "Did anything change on Canvas?"
- "What do I need on the rest of Statistics to finish above 90?"
- "Show me what Essay 2 asks for and check my draft against the rubric."
- "I finished the attendance one, tick it off." / "Add the history midterm on Oct 29 from the syllabus."
- "Put my deadlines in my calendar."

The first time, it asks for your school's Canvas address and opens a browser window where **you**
log in. After that it reuses that login quietly until your school expires it.

**In Codex:** Codex runs commands in a sandbox that cannot reach the internet or your home folder,
so it will ask your approval to run this skill's commands outside the sandbox. That is expected:
the skill needs to read Canvas, save to `~/CanvasCoach` and open the login window. Approve it.

It writes pages you can open yourself in `CanvasCoach` in your home folder: `Deadlines.html`,
`Study Plan.html`, `This Week.html`, `Grades.html`, a calendar file `Canvas deadlines.ics`, and one
folder per course for saved materials and assignment instructions.

To change the language, tell your assistant, or run the first line below in Terminal. The second
shows one command in Chinese without changing the setting.

```bash
python3 ~/canvas-coach/tools/coach.py config set language zh
```

```bash
CANVAS_COACH_LANG=zh python3 ~/canvas-coach/tools/coach.py plan
```

## Running it by hand (optional)

These are **Terminal** commands, not Python code. Open the Terminal app and paste one line at a time.
`~/canvas-coach` is the folder you cloned in step 1.

```bash
python3 ~/canvas-coach/tools/coach.py status
```

```bash
python3 ~/canvas-coach/tools/coach.py deadlines --open
```

Add `--help` after `coach.py` to see every command.

## Where things are

| What | Where |
|---|---|
| The skill (this folder) | linked from `~/.claude/skills/canvas-coach` and `~/.codex/skills/canvas-coach` |
| Your settings | `~/.config/canvas-coach/config.json` |
| The pages, calendar file and course folders | `~/CanvasCoach/` |
| Collected data (one file per collection), and what you told it (ticks, own deadlines, questions, the plan's record) | `~/CanvasCoach/.data/` |
| Saved Canvas login (encrypted) | `~/.agent-browser/sessions/canvas-coach-*` |
| The daily briefing job, only if you switched it on | `~/Library/LaunchAgents/com.canvas-coach.brief.plist` |

Nothing personal is in this folder or in the repository: your settings, login and Canvas data live
in the places above.

## Privacy

- Everything stays on this computer. There is no server.
- Your password is never seen or stored by the skill. You type it into a real browser window.
- What is stored is the login cookie that browser receives, encrypted, so you do not have to log
  in every time. `coach.py logout` deletes it.
- Your private Canvas calendar link is kept in the settings file and used only as a backup when
  the login has expired. It is never printed.
- The Canvas inbox is not read unless you switch it on (`coach.py config set inbox on`), and then
  only the subject and first line of each conversation.
- Files are downloaded only when you say so. Nothing runs in the background unless you ask for the
  daily briefing.
- Assignment instructions are looked at once for hidden text while collecting; only a yes or no is
  kept. The full text is saved only when you ask to see an assignment.
- Whatever your AI assistant reads passes through that AI service, as with anything else you ask it.

## Removing it

These are **Terminal** commands. Do them in this order.

1. Switch off the daily briefing (harmless if you never switched it on), then delete the saved login:

```bash
python3 ~/canvas-coach/tools/coach.py schedule remove
```

```bash
python3 ~/canvas-coach/tools/coach.py logout
```

2. Delete the links `~/.claude/skills/canvas-coach` and `~/.codex/skills/canvas-coach`, and this folder.
3. Delete `~/.config/canvas-coach` and `~/CanvasCoach`.

## Tests

A **Terminal** command. It uses a fake Canvas, so it needs no login and touches none of your data.

```bash
python3 ~/canvas-coach/tests/run_all.py
```

## Changing it

`references/extending.md` explains how the code is laid out, how to collect more from Canvas by
editing one list, how to add a command by dropping in one file, how the plan is made, how one skill
serves both agents, and how to add or change a translation.
