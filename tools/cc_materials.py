"""Course materials, reached through modules: pages (saved as text) and files (downloaded on request).

Many schools close the course "Files" area, so the way in is the module list that is already
in the snapshot. Pages and file details are fetched on demand; a file is only downloaded when
asked for, into the student's data folder. The download address Canvas hands out is temporary
and is never printed or stored.
"""
import os
import re
import urllib.request
from datetime import timedelta
from urllib.parse import urlparse

import cc_store
import cc_text
import cc_time
import cc_week
from cc_lang import N, T, warning

# What can be said about one file. These exact words are also what the code tests, so they stay
# English inside the result; state_words() turns them into the output language.
STATES = (N("saved"), N("already there"), N("too big"), N("not available"), N("can be downloaded"),
          N("already downloaded"), N("locked"), N("needs the login"))
REASONS = (N("not a secure address"), N("what arrived is not the file Canvas described"),
           N("this file needs the Canvas login to download"), N("the browser could not download it"))


def state_words(state):
    """A file's state in the output language: 'failed (<why>)' keeps its shape, with the reason translated."""
    if state.startswith("failed (") and state.endswith(")"):
        return T("failed ({why})", why=T(state[8:-1]))
    return T(state)

MAX_BYTES = 100 * 1024 * 1024
FILE_LINK = re.compile(r"/files/(\d+)")
PAGE_KEEP = ["title", "body", "updated_at", "url"]
FILE_KEEP = ["id", "display_name", "filename", "size", "content-type", "locked_for_user", "url"]


def choose_modules(payload, cfg, course_id, week=None, words=None, everything=False, at=None):
    """(modules, sentence saying which were chosen). Empty list when the course has none collected."""
    modules = cc_week.course_modules(payload, course_id)
    if not modules:
        return [], (T("This course has no modules on Canvas.") if modules == [] else
                    T("The modules of this course were not collected. Collect again."))
    if everything:
        return list(modules), T("All {n} modules.", n=len(modules))
    if words:
        whole = [re.compile(r"(?<!\w)" + re.escape(w.lower()) + r"(?!\w)") for w in words]   # "4" must not match "10/04"
        hits = [m for m in modules if all(p.search(str(m.get("name") or "").lower()) for p in whole)]
        return hits, (T("Modules matching '{words}'.", words=" ".join(words)) if hits else
                      T("No module name matches. Modules: {names}",
                        names="; ".join(str(m.get("name")).strip() for m in modules)))
    at = at or cc_time.now()
    number, _, _, _ = cc_week.this_week(payload, cfg, at)
    day = cc_time.local(at, cc_time.zone(cfg)).date()
    if week is not None:
        if number is None:
            return [m for m in modules if cc_week.name_week(m.get("name")) == week], T("Modules named week {n}.", n=week)
        day, number = day + timedelta(days=7 * (week - number)), week
    hits = cc_week.pick_modules(modules, number, day)
    label = T("week {n}", n=number) if number is not None else T("this week")
    if hits:
        return hits, T("The module(s) for {week}.", week=label)
    if week is None:
        open_ones = [m for m in modules if m.get("state") != "locked" and (m.get("items") or m.get("items_count"))]
        if open_ones:
            return [max(open_ones, key=lambda m: m.get("position") or 0)], \
                T("No module is labelled for {week}; this is the newest one.", week=label)
    return [], T("No module is labelled for {week}. Modules: {names}", week=label,
                 names="; ".join(str(m.get("name")).strip() for m in modules))


def listing(modules):
    """The chosen modules and their items as text lines (from the snapshot, no network)."""
    out = []
    for m in modules:
        out.append(T("Module: {name}", name=str(m.get("name") or "").strip()))
        for i in m.get("items") or []:
            pad = "  " + "  " * min(int(i.get("indent") or 0), 3)
            title = str(i.get("title") or "").strip()
            if i.get("type") == "SubHeader":
                out.append(f"{pad}{title}:")
                continue
            word = cc_week.item_word(i.get("type"))
            link = i.get("external_url") or i.get("html_url") or ""
            out.append(f"{pad}- " + (f"[{word}] " if word else "") + title + (f" — {link}" if link else ""))
        missing = int(m.get("items_count") or 0) - len(m.get("items") or [])
        if missing > 0:
            out.append(T("  ({n} more item(s) not listed; open the module in Canvas)", n=missing))
    return out


def api_path(url, host):
    """The '/api/v1/...' part of a Canvas API address, or None when it is not on the student's Canvas."""
    url = str(url or "")
    return url[len(host):] if host and url.startswith(host + "/api/v1/") else None


def page_requests(modules, host):
    """([requests for fetch_extra], {request name: (module name, item)}) for every Page item."""
    requests, index = [], {}
    for m in modules:
        for i in m.get("items") or []:
            path = api_path(i.get("url"), host) if i.get("type") == "Page" else None
            if path:
                name = f"page{len(requests)}"
                requests.append({"name": name, "path": path, "keep": PAGE_KEEP})
                index[name] = (str(m.get("name") or "").strip(), i)
    return requests, index


def page_markdown(course, module, item, page, cfg):
    """A page's text with a short header saying where it came from."""
    updated = cc_time.parse_iso(page.get("updated_at"))
    head = [f"# {page.get('title') or item.get('title')}", "", T("- Course: {course}", course=course),
            T("- Module: {name}", name=module)]
    if item.get("html_url"):
        head.append(T("- On Canvas: {url}", url=item["html_url"]))
    if updated:
        head.append(T("- Last changed: {when}", when=cc_time.stamp(cc_time.local(updated, cc_time.zone(cfg)))))
    note = f"\n\n{cc_text.hidden_note()}" if cc_text.has_hidden_text(page.get("body")) else ""
    return "\n".join(head) + "\n\n" + (cc_text.html_to_text(page.get("body")) or T("(This page is empty.)")) + note + "\n"


def linked_files(body, host):
    """[(file id, link text)] for links in a page that point at files on the student's Canvas."""
    out = []
    for text, href in cc_text.links(body):
        m = FILE_LINK.search(urlparse(href).path) if href.startswith(host + "/") else None
        if m and m.group(1) not in [f for f, _ in out]:
            out.append((m.group(1), text))
    return out


def size_words(size):
    if not isinstance(size, (int, float)):
        return T("size unknown")
    return f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{max(1, round(size / 1024))} KB"


def download(url, dest, expected=None, limit=MAX_BYTES):
    """Fetch one file to `dest`. Returns 'saved', 'already there', 'too big' or 'failed (<why>)'."""
    parts = urlparse(url or "")
    local = parts.hostname in ("127.0.0.1", "localhost", "::1")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        return "failed (not a secure address)"
    if isinstance(expected, (int, float)) and expected > limit:
        return "too big"
    if dest.exists() and isinstance(expected, (int, float)) and dest.stat().st_size == expected:
        return "already there"
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "canvas-coach"})
        with urllib.request.urlopen(request, timeout=60) as answer, open(partial, "wb") as out:
            total = 0
            while True:
                chunk = answer.read(262144)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise OverflowError
                out.write(chunk)
        if isinstance(expected, (int, float)) and total != expected:
            # An expired link answers with a login page: never keep that under the file's name.
            partial.unlink(missing_ok=True)
            return "failed (what arrived is not the file Canvas described)"
        os.replace(partial, dest)
        return "saved"
    except OverflowError:
        partial.unlink(missing_ok=True)
        return "too big"
    except Exception as e:
        partial.unlink(missing_ok=True)
        return f"failed ({type(e).__name__})"


def needs_login(url, host):
    """Whether a download address only works with the student's login (no access code in it)."""
    return str(url or "").startswith(host + "/") and "verifier=" not in url and "token=" not in url


def looks_like_the_file(path, expected):
    """A login page saved under the file's name is the classic failure: check the size, or at least the start."""
    if isinstance(expected, (int, float)):
        return path.stat().st_size == expected
    with open(path, "rb") as f:
        start = f.read(200).lstrip().lower()
    return path.suffix.lower() in (".html", ".htm") or not start.startswith((b"<!doctype html", b"<html"))


def gather(payload, cfg, course, modules, fetcher, get_files=False, downloader=download, browser=None):
    """Read the pages of the chosen modules, list their files, and download the files when asked.

    `fetcher` is cc_cli.fetch_extra and `browser` is cc_cli.fetch_files (or stand-ins with the same
    shape). A file whose address works without a login is downloaded directly; one that needs the
    login is fetched by the logged-in browser, all such files in one go.
    Returns {"pages": [...], "files": [...], "problems": [...]}; download addresses are not in it.
    """
    host = cfg["host"]
    base = cc_store.root(cfg) / cc_text.safe_name(course["label"])
    result = {"pages": [], "files": [], "problems": []}
    wanted = {}          # file id -> a name to show if Canvas will not tell us one
    for m in modules:
        for i in m.get("items") or []:
            if i.get("type") == "File" and i.get("content_id") is not None:
                wanted.setdefault(str(i["content_id"]), (i.get("content_details") or {}).get("display_name") or i.get("title"))

    requests, index = page_requests(modules, host)
    if requests:
        answers, errors = fetcher(cfg, requests)
        failed = {e.get("resource"): e.get("message") for e in errors}
        for name, (module, item) in index.items():
            page = answers.get(name)
            if not isinstance(page, dict):
                result["problems"].append(T("Could not read the page '{title}' ({why}).", title=item.get("title"),
                                            why=failed.get(name, T("no answer"))))
                continue
            owner = f"page:{course['id']}:{item.get('page_url') or item.get('id')}"
            path = cc_store.writable_path(cfg, base / "materials" / cc_text.safe_name(module) / (
                cc_text.safe_name(page.get("title") or item.get("title")) + ".md"), owner)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(page_markdown(course["label"], module, item, page, cfg), encoding="utf-8")
            cc_store.record_written(cfg, path, owner)
            links = [(t, u) for t, u in cc_text.links(page.get("body")) if u and not u.startswith("#")]
            for fid, text in linked_files(page.get("body"), host):
                wanted.setdefault(fid, text)
            result["pages"].append({"title": page.get("title") or item.get("title"), "module": module,
                                    "path": str(path), "links": links})

    through_browser = []     # (row, address, final path, owner)
    if wanted:
        requests = [{"name": f"file{fid}", "path": f"/api/v1/courses/{course['id']}/files/{fid}", "keep": FILE_KEEP}
                    for fid in wanted]
        answers, errors = fetcher(cfg, requests)
        failed = {e.get("resource"): e.get("message") for e in errors}
        for fid, hint in wanted.items():
            info = answers.get(f"file{fid}")
            if not isinstance(info, dict):
                result["files"].append({"name": str(hint or f"file {fid}"), "size": None, "state": "not available",
                                        "detail": failed.get(f"file{fid}", T("no answer")), "path": ""})
                continue
            name = cc_text.safe_name(info.get("display_name") or info.get("filename") or hint or f"file-{fid}")
            owner = f"file:{fid}"
            # Two Canvas files can share a name, and the student may have edited their copy: writable_path
            # then gives a different name instead of overwriting.
            dest = cc_store.writable_path(cfg, base / "files" / name, owner)
            row = {"name": dest.name, "size": info.get("size"), "state": "can be downloaded", "detail": "", "path": ""}
            if dest.name != name:
                row["detail"] = T("'{name}' already holds something else, so this one gets its own name", name=name)
            have = dest.exists() and isinstance(info.get("size"), (int, float)) and dest.stat().st_size == info["size"]
            if have:
                row["state"], row["path"] = "already downloaded", str(dest)
            elif info.get("locked_for_user") or not info.get("url"):
                row["state"] = "locked"
            elif get_files:
                row["state"] = "needs the login" if needs_login(info["url"], host) else downloader(info["url"], dest, info.get("size"))
                if row["state"] == "saved":
                    cc_store.record_written(cfg, dest, owner)
                    row["path"] = str(dest)
                elif browser and str(info["url"]).startswith(host + "/") and row["state"] != "too big" \
                        and not (isinstance(info.get("size"), (int, float)) and info["size"] > MAX_BYTES):
                    through_browser.append((row, info["url"], dest, owner, info.get("size")))
                elif row["state"] == "needs the login":
                    row["state"] = "failed (this file needs the Canvas login to download)"
            result["files"].append(row)
    if through_browser:
        partials = [dest.with_name(dest.name + ".part") for _, _, dest, _, _ in through_browser]
        arrived = browser(cfg, [(address, partial) for (_, address, _, _, _), partial in zip(through_browser, partials)])
        for (row, _, dest, owner, size), partial, ok in zip(through_browser, partials, arrived):
            if ok and partial.exists() and looks_like_the_file(partial, size):
                os.replace(partial, dest)
                cc_store.record_written(cfg, dest, owner)
                row["state"], row["path"] = "saved", str(dest)
            else:
                partial.unlink(missing_ok=True)
                row["state"] = "failed (what arrived is not the file Canvas described)" if ok else "failed (the browser could not download it)"
    return result


def render_gathered(result, downloaded):
    out = []
    if result["pages"]:
        out.append(T("Pages saved as text ({n}):", n=len(result["pages"])))
        for p in result["pages"]:
            out.append(f"  {p['title']} -> {p['path']}")
            out += [T("      link: {text} ({url})", text=t, url=u) for t, u in p["links"][:12]]
    if result["files"]:
        out.append(T("Files ({n}):", n=len(result["files"])))
        for f in result["files"]:
            out.append(f"  {f['name']}, {size_words(f['size'])}: {state_words(f['state'])}"
                       + (f" -> {f['path']}" if f["path"] else "") + (f" ({f['detail']})" if f["detail"] else ""))
        ready = sum(1 for f in result["files"] if f["state"] == "can be downloaded")
        kept = [f for f in result["files"] if f["state"] == "already downloaded"]
        if kept and not downloaded:
            out.append(T("{n} of them are already on this computer (paths above).", n=len(kept)))
        if ready and not downloaded:
            out.append(T("{n} file(s) can be downloaded. Ask the student first, then add --download.", n=ready))
    out += [warning(p) for p in result["problems"]]
    if not result["pages"] and not result["files"] and not result["problems"]:
        out.append(T("These modules hold no pages or files to read."))
    return out
