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
    # The embedding stack's lines proven to give the stores' vectors bit for bit (tests/substrate/test_embedder_vectors.py);
    # transformers 5.12 removes a function the model's pinned code calls.
    assert version_of("transformers").startswith(("5.9.", "5.10.", "5.11."))
    assert version_of("sentence-transformers").startswith(("5.5.", "6.0.", "6.1."))
    assert version_of("torch").startswith(("2.12.", "2.13.", "2.14."))
    assert version_of("umap-learn").startswith("0.5.")
    assert version_of("embedded-postgres").startswith("18.")
    assert version_of("mcp").startswith("1.27.")
    assert version_of("pgvector").startswith("0.4.")
    assert "download.pytorch.org/whl/cpu" in lock and "+cu" not in version_of("torch")
