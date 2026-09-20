#!/usr/bin/env python3
from pathlib import Path
from slopymemory.checks import render_setup
root = Path(__file__).resolve().parent.parent
(root / "SETUP.md").write_text(render_setup((root / "docs" / "SETUP-head.md").read_text()))
print("SETUP.md written")
