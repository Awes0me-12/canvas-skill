"""Find every sentence the code can print: the text handed to T(), N() or plural() in tools/*.py.

Used by test_lang.py to check that each one has a translation. Run it directly to list them:
    python3 tests/lang_keys.py            # every sentence, grouped by file
    python3 tests/lang_keys.py --missing  # only those that cc_lang_zh.py does not translate yet
"""
import ast
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def _name(func):
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""


def keys_in(path):
    """The sentences in one file, in the order they appear."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        name = _name(node.func)
        wanted = node.args[:1] if name in ("T", "N") else node.args[1:3] if name == "plural" else []
        for arg in wanted:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.append((arg.lineno, arg.value))
    return [text for _, text in sorted(found)]


def all_keys():
    """{file name: [sentences]} for every tool file except the translation tables themselves."""
    return {p.name: keys_in(p) for p in sorted(TOOLS.glob("*.py")) if not p.name.startswith("cc_lang_")}


if __name__ == "__main__":
    sys.path.insert(0, str(TOOLS))
    table = {}
    if "--missing" in sys.argv:
        import cc_lang_zh
        table = cc_lang_zh.TABLE
    seen = set()
    for name, keys in all_keys().items():
        fresh = [k for k in dict.fromkeys(keys) if k not in seen and k not in table]
        seen.update(fresh)
        if fresh:
            print(f"# {name}")
            for k in fresh:
                print(repr(k))
