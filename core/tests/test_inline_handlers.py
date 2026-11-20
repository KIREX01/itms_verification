"""Every inline on*="..." handler built in the front-end scripts must be valid JavaScript.

A handler built inside a template literal can't nest quotes: `\'` there becomes a bare quote, which broke the
"Photo Missing" image fallback ("SyntaxError: unexpected token: identifier"). Needs Node.js; skipped without it.
"""
import glob
import shutil
import subprocess
from pathlib import Path

import pytest
from django.conf import settings

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node.js not installed")
def test_inline_handlers_compile():
    base = Path(settings.BASE_DIR)
    files = sorted(glob.glob(str(base / "core" / "static" / "core" / "js" / "*.js")))
    proc = subprocess.run([NODE, str(base / "core" / "tests" / "js" / "check_handlers.js"), *files],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
