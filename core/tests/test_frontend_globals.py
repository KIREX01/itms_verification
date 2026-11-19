"""
Browser rule: classic <script> files share one global scope. A top-level `let`/`const`/`class`
whose name is also declared by another page script is a SyntaxError and the whole file fails to
load (this silently broke navigation and the ITMS tab in v1.1.x).
"""
import collections
import re
from pathlib import Path

from django.conf import settings

ROOT = Path(settings.BASE_DIR)


def _page_scripts():
    base = (ROOT / "core/templates/core/base.html").read_text(encoding="utf-8")
    return [f for f in re.findall(r"core/js/([a-z_]+\.js)", base)]


def test_no_conflicting_global_declarations_across_page_scripts():
    decl = collections.defaultdict(list)
    for name in _page_scripts():
        depth = 0
        for line in (ROOT / "core/static/core/js" / name).read_text(encoding="utf-8").splitlines():
            if depth == 0:
                for kind, ident in re.findall(r"^\s*(var|let|const|function|class)\s+([A-Za-z_$][\w$]*)", line):
                    decl[ident].append((name, kind))
            depth = max(0, depth + line.count("{") - line.count("}"))
    conflicts = {i: u for i, u in decl.items() if len(u) > 1 and {k for _, k in u} & {"let", "const", "class"}}
    assert conflicts == {}, f"Conflicting global declarations (use `var` or a single owner): {conflicts}"
