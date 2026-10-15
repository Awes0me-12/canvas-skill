"""Run the briefing by itself every day (macOS only), using a per-user launchd job.

Nothing here runs unless the student asked for a schedule: `coach.py schedule install`.
The job is one small file in ~/Library/LaunchAgents; `coach.py schedule remove` deletes it.

How it works: macOS starts `coach.py brief --notify --scheduled` every few minutes. That command
does nothing (and prints nothing) unless today's briefing is due and has not happened yet, so the
briefing arrives at the chosen time, or soon after the Mac wakes or is switched on if it was off
then. (A plain "run at 08:00" calendar job was tried first; it never fired on the first Mac this
ran on, and it would also miss days when the Mac is asleep at that minute.)
"""
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from cc_lang import T

LABEL = "com.canvas-coach.brief"
LOG = "~/Library/Logs/canvas-coach-brief.log"
CHECK_EVERY = 600          # seconds between "is the briefing due?" checks
MAX_TRIES = 6              # failed attempts before giving up for the day


class ScheduleError(Exception):
    pass


def plist_path():
    """Where the job file lives. $CANVAS_COACH_LAUNCH_DIR moves it (tests use that)."""
    return Path(os.environ.get("CANVAS_COACH_LAUNCH_DIR") or "~/Library/LaunchAgents").expanduser() / (LABEL + ".plist")


def parse_time(text):
    """'8:00' or '08:30' -> (hour, minute)."""
    parts = (text or "").strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts) or not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
        raise ScheduleError(T("'{text}' is not a time. Use 24-hour HH:MM, for example 08:00 or 21:30.", text=text))
    return int(parts[0]), int(parts[1])


def job(coach_script, python=None):
    """The launchd job as a dict: every few minutes, run the briefing if it is due."""
    python = python or sys.executable
    tool = shutil.which("agent-browser")
    folders = [str(Path(p).parent) for p in (tool, python) if p]
    folders += ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    env = {"PATH": ":".join(dict.fromkeys(folders))}
    for name in ("CANVAS_COACH_HOME", "CANVAS_COACH_ROOT"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    return {"Label": LABEL,
            "ProgramArguments": [python, str(coach_script), "brief", "--notify", "--scheduled"],
            "StartInterval": CHECK_EVERY, "RunAtLoad": True,
            # Not "Background": macOS throttles that so hard that the hidden browser takes too long to start.
            "EnvironmentVariables": env, "ProcessType": "Interactive",
            "StandardOutPath": str(Path(LOG).expanduser()), "StandardErrorPath": str(Path(LOG).expanduser())}


def _launchctl(*args):
    if os.environ.get("CANVAS_COACH_LAUNCH_DIR"):
        # A test or trial run: write the job file only, and leave the real macOS scheduler alone.
        return subprocess.CompletedProcess(args, 0 if args[0] != "print" else 1, "", "")
    try:
        return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ScheduleError(T("Could not talk to the macOS scheduler: {why}", why=e))


def _target():
    return f"gui/{os.getuid()}"


def status():
    """{'installed': bool, 'loaded': bool, 'current': bool, 'path': str}; 'current' is False for an old-style job."""
    path = plist_path()
    info = {"installed": path.exists(), "loaded": False, "current": False, "path": str(path)}
    if info["installed"]:
        try:
            info["current"] = "--scheduled" in (plistlib.loads(path.read_bytes()).get("ProgramArguments") or [])
        except Exception:
            info["current"] = False
        if sys.platform == "darwin":
            info["loaded"] = _launchctl("print", f"{_target()}/{LABEL}").returncode == 0
    return info


def due(time_text, now_local, record):
    """Whether the daily briefing should run at this check.

    `record` is what the last runs left in state.json: {"day": "2026-10-12", "done": bool, "tries": n}.
    Due once the chosen time has passed today, until it has succeeded (or failed MAX_TRIES times).
    """
    hour, minute = parse_time(time_text or "08:00")
    record = record if isinstance(record, dict) else {}
    today = now_local.date().isoformat()
    if record.get("day") == today and (record.get("done") or int(record.get("tries") or 0) >= MAX_TRIES):
        return False
    return (now_local.hour, now_local.minute) >= (hour, minute)


def after_run(record, now_local, succeeded):
    """The record to store after an attempt."""
    today = now_local.date().isoformat()
    tries = int(record.get("tries") or 0) + 1 if isinstance(record, dict) and record.get("day") == today else 1
    return {"day": today, "done": bool(succeeded), "tries": tries}


def install(coach_script):
    """Write the job file and hand it to the macOS scheduler. Replaces an earlier schedule."""
    if sys.platform != "darwin":
        raise ScheduleError(T("The automatic schedule is only built for macOS."))
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    Path(LOG).expanduser().parent.mkdir(parents=True, exist_ok=True)
    _launchctl("bootout", f"{_target()}/{LABEL}")            # fine if it was not loaded
    path.write_bytes(plistlib.dumps(job(coach_script)))
    done = _launchctl("bootstrap", _target(), str(path))
    if done.returncode != 0:
        raise ScheduleError(T("macOS refused the schedule: {why}", why=(done.stderr or done.stdout).strip()[:300]))
    return status()


def remove():
    """Stop the schedule and delete its file. Returns True if there was one."""
    path = plist_path()
    if sys.platform == "darwin":
        _launchctl("bootout", f"{_target()}/{LABEL}")
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed
