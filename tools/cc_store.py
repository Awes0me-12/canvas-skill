"""Where things live: settings, the data folder, and saved snapshots.

Settings:  ~/.config/canvas-coach/config.json   (override with $CANVAS_COACH_HOME)
Data:      <root>/Deadlines.html                the page for humans
           <root>/.data/raw/<stamp>.json        one file per collection, never edited
           <root>/.data/state.json              your own marks, deadlines, questions and plan record
"""
import contextlib
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import cc_lang
import cc_time
from cc_lang import T

APP = "canvas-coach"
FORMAT = "canvas-coach/1"
KEEP_SNAPSHOTS = 60

DEFAULTS = {
    "version": 1,
    "host": "",                    # https://canvas.yourschool.edu
    "root": "~/CanvasCoach",            # not in Documents: macOS blocks background jobs from reading there
    "timezone": "auto",            # "auto" = this computer's timezone, or e.g. "America/Los_Angeles"
    "ignore_courses": [],          # course ids never shown or collected
    "include_courses": [],         # course ids shown even if their term looks finished
    "course_names": {},            # course id -> your own short label
    "fresh_minutes": 10,           # reuse a collection younger than this
    "horizon_days": 7,             # how far "Next 7 days" reaches
    "overdue_days": 45,            # hide unsubmitted work older than this
    "collect_timeout": 180,
    "collector": "browser",        # "browser" = scripted window; "bridge" = an AI agent copies from a browser tab
    "inbox": False,                # also read the Canvas inbox (subject and first line of each conversation)
    "calendar_feed": "",           # the private Canvas calendar link; saved automatically, used as a backup
    "week1_start": "",             # first day of teaching week 1 (YYYY-MM-DD); worked out from module names when empty
    "brief_time": "08:00",         # when the daily briefing is due, if the student switched the schedule on
    "language": "en",              # "en" or "zh": the language of everything the skill prints and writes
    "class_times": {},             # course id -> [{"days": ["tue", "thu"], "time": "15:30", "kind": "lecture"}]
    "rest_days": [],               # days of which one per week is kept free in the study plan, e.g. ["fri", "sat", "sun"]
    "clash_hours": 48,             # deadlines from two or more courses this close together count as a clash
    "clash_days": 21,              # how far ahead clashes are looked for
    "clash_min_percent": 1,        # an item counts towards a clash when it is worth at least this share of the grade
    "plan_days": 7,                # how many days the study plan covers
}
BOOL_KEYS = ("inbox",)
SECRET_KEYS = ("calendar_feed",)
LIST_KEYS = ("ignore_courses", "include_courses", "rest_days")
INT_KEYS = {"fresh_minutes": (1, 1440), "horizon_days": (1, 366), "overdue_days": (1, 3660),
            "collect_timeout": (20, 900), "clash_hours": (1, 336), "clash_days": (1, 120), "plan_days": (1, 28),
            "clash_min_percent": (0, 100)}
CHOICES = {"collector": ("browser", "bridge"), "language": cc_lang.SUPPORTED}


class ConfigError(Exception):
    """Not set up yet (exit code 3)."""


class DataError(Exception):
    """A settings or data file cannot be read (exit code 1)."""


def home():
    return Path(os.environ.get("CANVAS_COACH_HOME") or "~/.config/" + APP).expanduser()


def config_path():
    return home() / "config.json"


def write_json(path, obj, private=False):
    """Write atomically, so a crash never leaves a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
            f.write("\n")
        if private:
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        raise DataError(T("Cannot read {path}: {why}", path=path, why=e))


def load_config():
    cfg = dict(DEFAULTS)
    saved = read_json(config_path(), {})
    if not isinstance(saved, dict):
        raise DataError(T("{path} is not a JSON object.", path=config_path()))
    cfg.update(saved)
    cc_lang.use(cfg.get("language"))     # every command loads the settings first, so this sets the output language
    return cfg


def save_config(cfg):
    home().mkdir(parents=True, exist_ok=True)
    os.chmod(home(), 0o700)
    write_json(config_path(), cfg, private=True)


def update_config(**changes):
    """Change a few settings on disk without overwriting what another command saved meanwhile."""
    fresh = load_config()
    fresh.update(changes)
    save_config(fresh)


def normalize_host(text):
    """Accept any Canvas page address and keep just https://host."""
    text = (text or "").strip()
    if not text:
        raise ConfigError(T("Empty address."))
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        text = "https://" + text
    u = urlparse(text)
    if not u.hostname:
        raise ConfigError(T("'{text}' does not look like a web address.", text=text))
    local = u.hostname in ("localhost", "127.0.0.1", "::1")
    if u.scheme != "https" and not (local and u.scheme == "http"):
        raise ConfigError(T("The Canvas address must start with https://"))
    return f"{u.scheme}://{u.netloc}".lower()


def require_host(cfg):
    if not cfg.get("host"):
        raise ConfigError(T("Not set up yet. Run: coach.py setup --host https://<your school's Canvas address>"))
    return cfg["host"]


def root(cfg):
    """The data folder. $CANVAS_COACH_ROOT overrides it (tests use that)."""
    return Path(os.environ.get("CANVAS_COACH_ROOT") or cfg.get("root") or DEFAULTS["root"]).expanduser()


def data_dir(cfg):
    return root(cfg) / ".data"


def raw_dir(cfg):
    return data_dir(cfg) / "raw"


def ensure_dirs(cfg):
    raw_dir(cfg).mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(root(cfg), 0o700)        # course data and message excerpts: for this user only
    state = data_dir(cfg) / "state.json"
    if not state.exists():
        write_json(state, {"version": 1, "done": {}, "manual_deadlines": [], "notes": {}})


@contextlib.contextmanager
def state_lock(cfg):
    """Hold this from reading state.json to saving it, so two commands at once cannot lose each other's change."""
    ensure_dirs(cfg)
    with open(data_dir(cfg) / "state.lock", "w") as handle:
        try:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        except (ImportError, OSError):      # no file locking here (Windows): carry on unlocked
            pass
        yield


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _written(cfg):
    index = read_json(data_dir(cfg) / "written.json", {})
    return index if isinstance(index, dict) else {}


def is_ours(cfg, path, owner):
    """True when `path` holds exactly what we saved there earlier for this owner (a Canvas file or page id)."""
    rec = _written(cfg).get(str(path))
    return bool(rec and rec.get("owner") == owner and Path(path).exists() and rec.get("sha") == file_hash(path))


def writable_path(cfg, path, owner):
    """Where content from `owner` can be saved without destroying anything.

    `path` itself when it is free or still holds our own untouched earlier copy for the same owner.
    Otherwise (the student edited it, or another Canvas item has the same name) a sibling name that
    carries the owner's id, so nothing is ever overwritten blindly.
    """
    path = Path(path)
    if not path.exists() or is_ours(cfg, path, owner):
        return path
    tag = re.sub(r"[^A-Za-z0-9_-]+", "-", str(owner)).strip("-")[-40:] or "copy"
    n = 1
    while True:
        other = path.with_name(f"{path.stem} [{tag}{'' if n == 1 else ' ' + str(n)}]{path.suffix}")
        if not other.exists() or is_ours(cfg, other, owner):
            return other
        n += 1


def record_written(cfg, path, owner):
    """Remember what we saved at `path`, so a later run can tell our copy from the student's changes."""
    ensure_dirs(cfg)
    index = _written(cfg)
    index[str(path)] = {"owner": owner, "sha": file_hash(path)}
    write_json(data_dir(cfg) / "written.json", index)


def load_state(cfg):
    return read_json(data_dir(cfg) / "state.json", {"version": 1}) or {"version": 1}


def save_state(cfg, state):
    write_json(data_dir(cfg) / "state.json", state)


def list_snapshots(cfg):
    """Snapshot files, oldest first. Names sort by collection time."""
    d = raw_dir(cfg)
    return sorted(p for p in d.glob("*.json") if not p.name.startswith(".")) if d.is_dir() else []


def save_snapshot(cfg, payload):
    ensure_dirs(cfg)
    stamp = re.sub(r"[^0-9T]", "", str(payload.get("collected_at", ""))[:19]) or "unknown"
    path = raw_dir(cfg) / f"{stamp}.json"
    n = 1
    while path.exists():  # "_" sorts after ".", so a second file in the same second still sorts later
        n += 1
        path = raw_dir(cfg) / f"{stamp}_{n:02d}.json"
    write_json(path, payload)
    baseline = (load_state(cfg) or {}).get("baseline")     # change detection still needs this one
    for old in list_snapshots(cfg)[:-KEEP_SNAPSHOTS]:
        if old.name != baseline:
            old.unlink()
    return path


def _readable(cfg):
    """(payload, path) pairs, newest first, skipping files that are damaged or stamped in the future."""
    limit = cc_time.now()
    for path in reversed(list_snapshots(cfg)):
        try:
            payload = read_json(path)
        except DataError:
            continue
        when = cc_time.parse_iso(payload.get("collected_at")) if isinstance(payload, dict) else None
        if when is not None and (when - limit).total_seconds() <= 300:
            yield payload, path


def latest_snapshot(cfg):
    """(payload, path) for the newest usable collection, or (None, None)."""
    return next(_readable(cfg), (None, None))


def forget_resource(cfg, name):
    """Delete one `global` resource from every saved snapshot (when the student withdraws it). Returns how many."""
    cleaned = 0
    for path in list_snapshots(cfg):
        try:
            payload = read_json(path)
        except DataError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("global"), dict) and name in payload["global"]:
            del payload["global"][name]
            write_json(path, payload)
            cleaned += 1
    return cleaned


def previous_snapshot(cfg):
    """The collection before the newest one (for change detection later)."""
    found = list(zip(range(2), _readable(cfg)))
    return found[1][1] if len(found) == 2 else (None, None)
