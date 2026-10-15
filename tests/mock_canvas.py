"""A tiny fake Canvas, for testing the collector without a real school login.

    python3 tests/mock_canvas.py [port]

It imitates the parts of Canvas the collector depends on:
  - JSON bodies prefixed with `while(1);` (Canvas does this for cookie logins)
  - `Link: <...>; rel="next"` pagination, with a small page size to force paging
  - a session cookie login: GET /login/do sets it, GET /logout clears it
  - 401 {"status": "unauthenticated"} when not logged in
  - 401 {"status": "unauthorized"} for a course the student cannot read (505)
  - 403 for the course Files list (closed at many schools) while single files still work
  - modules whose `items` are left out, pages, files, feedback, announcements, inbox,
    and the private calendar feed at /feeds/calendars/<token>.ics (no login needed)
"""
import json
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sample_data  # noqa: E402

MAX_PAGE = 5
COOKIE = "canvas_session=mock-ok"


class Handler(BaseHTTPRequestHandler):
    server_version = "MockCanvas/1"
    down = False           # tests flip these to imitate an outage ...
    foreign_next = False   # ... or a pagination link that points at another site

    def log_message(self, fmt, *args):
        self.server.requests.append(self.command + " " + self.path)

    def _origin(self):
        return "http://" + self.headers.get("Host", "127.0.0.1")

    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=()):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj, headers=()):
        self._send(code, "while(1);" + json.dumps(obj), "application/json; charset=utf-8", headers)

    def _logged_in(self):
        return COOKIE in (self.headers.get("Cookie") or "")

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        path = url.path.rstrip("/") or "/"
        if path == "/login/do":
            return self._send(200, "<h1>Mock Canvas</h1><p>Logged in.</p>",
                              headers=[("Set-Cookie", COOKIE + "; Path=/; HttpOnly")])
        if path == f"/feeds/calendars/{sample_data.FEED_TOKEN}.ics":
            data = sample_data.build(datetime.now(timezone.utc), self._origin())
            return self._send(200, sample_data.feed_ics(data), "text/calendar; charset=utf-8")
        if path.startswith("/files/") and path.endswith("/download") and ("verifier" in q or self._logged_in()):
            fid = int(path.split("/")[2])       # with an access code anyone may fetch; without one, only the login
            if fid in sample_data.FILE_BYTES:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(sample_data.FILE_BYTES[fid])))
                self.end_headers()
                return self.wfile.write(sample_data.FILE_BYTES[fid])
        if path == "/logout":
            return self._send(200, "<p>Logged out.</p>",
                              headers=[("Set-Cookie", "canvas_session=; Path=/; Max-Age=0")])
        if not path.startswith("/api/"):
            if not self._logged_in():
                return self._send(200, '<h1>Mock Canvas login</h1><a href="/login/do">Log in</a>')
            return self._send(200, "<h1>Mock Canvas dashboard</h1>")
        if self.down:
            return self._send(503, "<h1>Service Unavailable</h1>")
        if not self._logged_in():
            return self._json(401, {"status": "unauthenticated",
                                    "errors": [{"message": "user authorization required"}]})

        data = sample_data.build(datetime.now(timezone.utc), self._origin())
        parts = path.split("/")[3:]  # after /api/v1
        if parts == ["users", "self"]:
            return self._json(200, data["user"])
        if parts == ["users", "self", "profile"]:
            return self._json(200, {"id": 9001, "name": "Test Student", "time_zone": "America/Los_Angeles",
                                    "primary_email": "test@example.edu",
                                    "calendar": {"ics": f"{self._origin()}/feeds/calendars/{sample_data.FEED_TOKEN}.ics"}})
        if parts == ["announcements"]:
            wanted = set(q.get("context_codes[]", []))
            if not wanted:
                return self._json(400, {"errors": [{"message": "missing context_codes"}]})
            return self._page([a for a in data["announcements"] if a["context_code"] in wanted], url, q)
        if parts == ["conversations"]:
            return self._page(data["conversations"], url, q)
        if parts == ["courses"]:
            courses = data["courses"]
            if "total_scores" in q.get("include[]", []):
                courses = [dict(c) for c in courses]
                for c in courses:
                    score = data["scores"].get(c["id"])
                    if score and c.get("enrollments"):
                        c["enrollments"] = [dict(c["enrollments"][0], computed_current_score=score[0],
                                                 computed_current_grade=score[1], computed_final_score=score[0] - 20,
                                                 computed_final_grade="C")]
            return self._page(courses, url, q)
        if len(parts) >= 3 and parts[:2] == ["courses", "505"]:
            return self._json(401, {"status": "unauthorized", "errors": [
                {"message": "user not authorized to perform that action"}]})
        if len(parts) == 3 and parts[0] == "courses" and parts[1].isdigit():
            cid = int(parts[1])
            if parts[2] == "assignments" and cid in data["assignments"]:
                items = data["assignments"][cid]
                if "submission" not in q.get("include[]", []):
                    items = [{k: v for k, v in a.items() if k != "submission"} for a in items]
                return self._page(items, url, q)
            if parts[2] == "assignment_groups" and cid in data["groups"]:
                return self._page(data["groups"][cid], url, q)
            if parts[2] == "modules" and cid in data["modules"]:
                return self._page(data["modules"][cid], url, q)
            if parts[2] == "files":
                return self._json(403, {"status": "unauthorized", "errors": [{"message": "user not authorized"}]})
            if parts[2] == "students":
                return self._json(404, {"errors": [{"message": "not found"}]})
        if len(parts) == 4 and parts[0] == "courses" and parts[1].isdigit():
            cid = int(parts[1])
            if parts[2:] == ["students", "submissions"] and cid in data["submissions"]:
                return self._page(data["submissions"][cid], url, q)
            if parts[2] == "pages" and (cid, parts[3]) in data["pages"]:
                return self._json(200, data["pages"][(cid, parts[3])])
            if parts[2] == "files" and parts[3].isdigit() and int(parts[3]) in data["files"]:
                return self._json(200, data["files"][int(parts[3])])
            if parts[2] == "assignments" and parts[3].isdigit():
                full = data["details"].get((cid, int(parts[3])))
                if full is None:
                    full = next((a for a in data["assignments"].get(cid, []) if a["id"] == int(parts[3])), None)
                if full is not None:
                    return self._json(200, full)
        if len(parts) == 5 and parts[0] == "courses" and parts[2] == "modules" and parts[4] == "items":
            items = data["long_module_items"].get(int(parts[3]))
            if items is not None:
                return self._page(items, url, q)
        if len(parts) == 2 and parts[0] == "files" and parts[1].isdigit() and int(parts[1]) in data["files"]:
            return self._json(200, data["files"][int(parts[1])])
        return self._json(404, {"errors": [{"message": "The specified resource does not exist."}]})

    def _page(self, items, url, q):
        per = max(1, min(int(q.get("per_page", ["10"])[0]), MAX_PAGE))
        page = max(1, int(q.get("page", ["1"])[0]))
        chunk = items[(page - 1) * per: page * per]
        headers = []
        if page * per < len(items):
            keep = "&".join(f"{k}={v}" for k, vs in q.items() if k not in ("page", "per_page") for v in vs)
            nxt = f"{self._origin()}{url.path}?{keep + '&' if keep else ''}page={page + 1}&per_page={per}"
            if self.foreign_next:
                nxt = "https://elsewhere.example/api/v1/next"
            cur = f"{self._origin()}{url.path}?page={page}&per_page={per}"
            headers.append(("Link", f'<{cur}>; rel="current",<{nxt}>; rel="next"'))
        return self._json(200, chunk, headers)


def serve(port=0):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.requests = []
    return server


if __name__ == "__main__":
    srv = serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
    print(f"Mock Canvas on http://127.0.0.1:{srv.server_address[1]}  (log in: /login/do)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
