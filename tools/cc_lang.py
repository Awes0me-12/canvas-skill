"""Which language the output is in, and the one function that translates: T().

English is written in the code. Another language lives in cc_lang_<code>.py as a dictionary
from the English sentence to its translation (see cc_lang_zh.py). A sentence that is missing
from the dictionary comes out in English, and tests/test_lang.py fails until it is added.

    T("Nothing changed on Canvas since {since}.", since=when)

What is never translated: course names, assignment titles, group names, file names, commands.
"""
import importlib
import os

SUPPORTED = ("en", "zh")
ALIASES = {"zh-cn": "zh", "zh-hans": "zh", "zh_cn": "zh", "cn": "zh", "chinese": "zh", "中文": "zh",
           "english": "en", "en-us": "en", "en_us": "en"}
_code, _table = "en", {}


def use(code):
    """Switch the output language. $CANVAS_COACH_LANG overrides the setting (tests and trial runs
    use that). An unknown language means English. Returns the code now in use."""
    global _code, _table
    code = str(os.environ.get("CANVAS_COACH_LANG") or code or "en").strip().lower()
    code = ALIASES.get(code, code)
    table = {}
    if code != "en":
        try:
            table = importlib.import_module("cc_lang_" + code).TABLE
        except Exception:
            code = "en"
    _code, _table = code, table
    return code


def lang():
    return _code


def T(text, /, **values):
    """The sentence in the current language, with its {placeholders} filled in."""
    out = _table.get(text, text)
    return out.format(**values) if values else out


def N(text):
    """Marks a sentence that sits in a constant and is translated later, where it is used: T(CONSTANT)."""
    return text


def plural(n, one, many, /, **values):
    """T() for a sentence whose English form depends on the count `n` (passed to it as {n})."""
    return T(one if n == 1 else many, n=n, **values)


def join(parts, kind="list"):
    """Join short pieces the way the language does: 'a, b' in English, 'a、b' or 'a，b' in Chinese."""
    return T(N(", ") if kind == "list" else N("; ")).join(str(p) for p in parts)


def paren(text, aside):
    """'text (aside)', with the brackets of the language."""
    return T("{text} ({aside})", text=text, aside=aside)


def warning(text):
    return T("WARNING: {text}", text=text)


def note(text):
    return T("Note: {text}", text=text)
