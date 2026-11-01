"""Every literal text key used in the code exists in data/locale/en.json (no raw keys on screen)."""
import json
import re

from app.config import PROJECT_ROOT

KEY_RE = re.compile(r"""\b(?:t|items|section)\(\s*["']([a-z_]+(?:\.[a-z0-9_]+)+)["']""")


def _lookup(data, key):
    node = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def test_all_literal_keys_exist():
    data = json.loads((PROJECT_ROOT / "data/locale/en.json").read_text(encoding="utf-8"))
    missing = set()
    for py in (PROJECT_ROOT / "app").rglob("*.py"):
        for key in KEY_RE.findall(py.read_text(encoding="utf-8")):
            if _lookup(data, key) is None:
                missing.add(f"{py.relative_to(PROJECT_ROOT)}: {key}")
    assert not missing, "missing locale keys:\n" + "\n".join(sorted(missing))
