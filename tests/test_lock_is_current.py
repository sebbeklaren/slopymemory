# tests/test_lock_is_current.py
import re, subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

def test_uv_lock_matches_pyproject():
    p = subprocess.run(["uv", "lock", "--check"], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr

def test_the_lock_pins_the_known_good_lines_and_the_cpu_torch():
    lock = (ROOT / "uv.lock").read_text()
    def version_of(name):
        m = re.search(rf'name = "{name}"\nversion = "([^"]+)"', lock); assert m, name; return m.group(1)
    assert version_of("transformers").startswith("5.9.")
    assert version_of("sentence-transformers").startswith("5.5.")
    assert version_of("umap-learn").startswith("0.5.")
    assert version_of("embedded-postgres").startswith("18.")
    assert version_of("mcp").startswith("1.27.")
    assert "download.pytorch.org/whl/cpu" in lock and "+cu" not in version_of("torch")
