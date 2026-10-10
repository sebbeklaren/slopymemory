"""The launcher fingerprints the files an agent names, inside the project only; the hash leaves, the bytes never do."""
import hashlib
from types import SimpleNamespace as T

import pytest

from slopymemory import anchors


def _h(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def test_sha256_of_a_file_under_the_root(tmp_path):
    (tmp_path / "src").mkdir(); (tmp_path / "src" / "a.py").write_bytes(b"x = 1\n")
    assert anchors.sha256_of(tmp_path, "src/a.py") == _h(b"x = 1\n")
    assert anchors.sha256_of(tmp_path, "./src//a.py") == _h(b"x = 1\n")


def test_sha256_streams_a_large_file(tmp_path, monkeypatch):
    data = b"z" * (3 * 1024 * 1024 + 7)
    (tmp_path / "big.bin").write_bytes(data)
    reads = []
    real_open = open

    class Spy:
        def __init__(self, f): self.f = f
        def __enter__(self): return self
        def __exit__(self, *a): return self.f.__exit__(*a)
        def read(self, n=-1):
            reads.append(n)
            return self.f.read(n)

    monkeypatch.setattr("builtins.open", lambda *a, **k: Spy(real_open(*a, **k)))
    assert anchors.sha256_of(tmp_path, "big.bin") == _h(data)
    assert reads and all(n > 0 for n in reads), "read whole instead of in chunks"


@pytest.mark.parametrize("rel", ["missing.py", "dir", "../outside.py", "/etc/hostname", "link_out", ""])
def test_not_a_readable_file_under_the_root_is_none(tmp_path, rel):
    root = tmp_path / "proj"; root.mkdir(); (root / "dir").mkdir()
    (tmp_path / "outside.py").write_text("not yours")
    (root / "link_out").symlink_to(tmp_path / "outside.py")
    assert anchors.sha256_of(root, rel) is None


def test_fill_only_touches_entries_without_a_sha256_key(tmp_path):
    (tmp_path / "a.py").write_text("a")
    args = {"tenant": "t", "text": "x", "files": [{"path": "a.py"}, {"path": "b.py", "sha256": "f" * 64},
                                                  {"path": "c.py", "sha256": None}, "junk"]}
    got = anchors.fill("memory_save", args, tmp_path)
    assert got["files"][0] == {"path": "a.py", "sha256": _h(b"a")}
    assert got["files"][1:] == args["files"][1:]
    assert args["files"][0] == {"path": "a.py"}, "the caller's dict is not changed"


def test_fill_on_save_refuses_an_unreadable_file_by_name(tmp_path):
    with pytest.raises(anchors.Unreadable) as e:
        anchors.fill("memory_save", {"files": [{"path": "nope.py"}]}, tmp_path)
    assert "nope.py" in str(e.value)


def test_fill_on_retrieve_sends_an_unreadable_file_as_gone(tmp_path):
    got = anchors.fill("memory_retrieve", {"files": [{"path": "nope.py"}]}, tmp_path)
    assert got["files"] == [{"path": "nope.py", "sha256": None}]


def test_needs_and_other_tools():
    assert anchors.needs("memory_save", {"files": [{"path": "a"}]})
    assert not anchors.needs("memory_save", {"files": []}) and not anchors.needs("memory_save", {})
    assert not anchors.needs("memory_ping", {"files": [{"path": "a"}]})


def test_server_takes_files_reads_the_schemas():
    new = [T(name=n, input_schema={"properties": {"files": {}}}) for n in anchors.TOOLS]
    old = [T(name=n, input_schema={"properties": {"text": {}}}) for n in anchors.TOOLS]
    assert anchors.server_takes_files(new) and not anchors.server_takes_files(old)


def test_fill_cleans_a_path_the_way_the_server_does_before_hashing(tmp_path):
    """A stray space or ./ must not make an existing file look gone: the launcher hashes and forwards the same path
    the server will store."""
    (tmp_path / "src").mkdir(); (tmp_path / "src" / "a.py").write_text("a")
    got = anchors.fill("memory_retrieve", {"files": [{"path": " ./src//a.py "}]}, tmp_path)
    assert got["files"] == [{"path": "src/a.py", "sha256": _h(b"a")}]
    saved = anchors.fill("memory_save", {"files": [{"path": "src/a.py "}]}, tmp_path)
    assert saved["files"] == [{"path": "src/a.py", "sha256": _h(b"a")}]


def test_fill_passes_a_path_the_server_would_refuse_unchanged(tmp_path):
    entry = {"path": "src\\a.py"}
    assert anchors.fill("memory_save", {"files": [entry]}, tmp_path)["files"] == [entry]


def test_fill_passes_a_path_with_a_nul_unchanged_for_the_server_to_refuse(tmp_path):
    entry = {"path": "a\x00b.py"}
    assert anchors.fill("memory_save", {"files": [entry]}, tmp_path)["files"] == [entry]
    assert anchors.sha256_of(tmp_path, "a\x00b.py") is None


def test_the_unreadable_message_names_the_file_not_the_project_root(tmp_path):
    with pytest.raises(anchors.Unreadable) as e:
        anchors.fill("memory_save", {"files": [{"path": "nope.py"}]}, tmp_path)
    assert "nope.py" in str(e.value) and str(tmp_path) not in str(e.value)
