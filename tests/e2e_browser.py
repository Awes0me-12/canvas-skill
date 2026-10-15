"""End-to-end check with a real (hidden) browser against the fake Canvas.

    python3 tests/e2e_browser.py

Needs the agent-browser CLI. Uses its own settings folder and browser session,
so it never touches your real Canvas login. Takes about a minute.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(HERE))

tmp = Path(tempfile.mkdtemp(prefix="cc-e2e-"))
os.environ.update(CANVAS_COACH_HOME=str(tmp / "home"), CANVAS_COACH_SESSION=f"cc-e2e-{os.getpid()}")
os.environ.pop("CANVAS_COACH_NOW", None)
os.environ.pop("CANVAS_COACH_ROOT", None)

import cc_browser  # noqa: E402
import cc_store  # noqa: E402
import mock_canvas  # noqa: E402


def coach(*args):
    p = subprocess.run([sys.executable, str(TOOLS / "coach.py"), *args], capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def check(label, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + label + ("" if ok else f"\n      {detail.strip()}"))
    if not ok:
        raise SystemExit(1)


def main():
    if not cc_browser.find_exe():
        print("SKIPPED: agent-browser is not installed.")
        return
    server = mock_canvas.serve(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        code, out = coach("setup", "--host", origin, "--root", str(tmp / "data folder"))
        check("setup", code == 0, out)

        code, out = coach("deadlines")
        check("not logged in is reported, not guessed", code == 2 and "not logged in" in out, out)

        cc_browser._run(["open", origin + "/login/do"])      # stands in for the student logging in
        check("login is detected", cc_browser.login_wait({"host": origin}, 10) == ("Test Student", origin))
        cc_browser.close()

        code, out = coach("collect")
        check("collect with the saved login", code == 0 and "23 assignment(s)" in out, out)
        check("problem course is named", "ORI 000" in out, out)

        code, out = coach("deadlines", "--force", "--json")
        check("deadlines --force --json", code == 0, out)
        view = json.loads(out)
        check("counts", view["counts"] == {"overdue": 3, "check_elsewhere": 0, "week_todo": 4, "week_done": 2,
                                           "later": 5, "undated": 1, "clashes": 3}, json.dumps(view["counts"]))
        hw6 = next(i for sec in view["sections"] for i in sec["items"] if i["key"] == "101:1006")
        check("hidden text in instructions is flagged, and the text itself is not kept",
              hw6["hidden_text"] is True and "giraffe" not in Path(cc_store.latest_snapshot(cc_store.load_config())[1]).read_text(),
              json.dumps(hw6["notes"]))
        code, out = coach("plan", "--offline")
        check("study plan from the same collection", code == 0 and "Must do: [overdue] CS 101 · HW 5" in out, out)
        os.environ["CANVAS_COACH_LANG"] = "zh"
        code, out = coach("deadlines", "--offline")
        del os.environ["CANVAS_COACH_LANG"]
        check("the same list in Chinese", code == 0 and "已逾期（3）" in out and "OVERDUE" not in out, out)
        check("page written", Path(view["page"]).read_text(encoding="utf-8").startswith("<!doctype html>"))
        check("only GET requests were sent", all(r.startswith("GET ") for r in server.requests))
        check("browser closed afterwards", not cc_browser.is_running())

        code, out = coach("week")
        check("week view with this week's module", code == 0 and "Module: Week 3" in out and "[page] Week 3 notes" in out, out)
        check("long module was fetched separately", "Week 4" in coach("materials", "cs", "101", "--week", "4")[1])
        code, out = coach("show", "project", "2")
        check("show: instructions and rubric from Canvas", code == 0 and "### Correctness (40 points)" in out
              and "At least 5 rooms" in out, out)
        code, out = coach("materials", "cs", "101", "--read")
        check("materials --read saves pages, lists files, downloads nothing",
              code == 0 and "Pages saved as text (1)" in out and "2 file(s) can be downloaded" in out
              and not (tmp / "data folder" / "CS 101" / "files").exists(), out)
        code, out = coach("materials", "cs", "101", "--download")
        saved = tmp / "data folder" / "CS 101" / "files" / "lecture3.pdf"
        check("materials --download fetches the files", code == 0 and saved.exists()
              and saved.read_bytes().startswith(b"%PDF-1.4 mock"), out)
        needs_login = saved.with_name("data.csv")
        check("a file that needs the login comes through the logged-in browser",
              needs_login.exists() and needs_login.read_bytes() == b"col_a,col_b\n1,2\n", out)
        code, out = coach("feed")
        check("calendar link was saved and agrees", code == 0 and "agrees with the last collection" in out, out)
        code, out = coach("grades", "--offline")
        check("grades show Canvas's own number", code == 0 and "Canvas shows: 86.4% (B)" in out, out)
        check("only GET requests were sent (still)", all(r.startswith("GET ") for r in server.requests))

        code, out = coach("logout")
        check("logout", code == 0, out)
        code, out = coach("deadlines", "--force")
        check("after logout: old list shown with a warning", code == 2 and "WARNING: Could not refresh" in out, out)
        check("after logout: the calendar link is used as a backup", "Checked the Canvas calendar link instead" in out, out)
        print("\nAll end-to-end checks passed.")
    finally:
        cc_browser.forget()
        server.shutdown()
        server.server_close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
