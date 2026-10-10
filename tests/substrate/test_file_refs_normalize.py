"""File anchors are checked at the boundary: a malformed entry is refused by name with what to fix, and a path is
normalised so a save and a later retrieve of the same file always meet."""
import pytest
from agent_memory.spaces.file_refs import FileRefError, MAX_FILES, normalize

H = "a" * 64


def test_none_is_none_and_a_clean_save_ref_is_kept():
    assert normalize(None, arg="files", save=True) is None
    assert normalize([{"path": "src/a.py", "sha256": H, "line": 3}], arg="files", save=True) == [
        {"path": "src/a.py", "sha256": H, "line_start": 3, "line_end": 3}]


def test_paths_are_normalised_so_save_and_retrieve_meet():
    got = normalize([{"path": " ./src//a.py ", "sha256": H}], arg="files", save=True)
    assert got[0]["path"] == "src/a.py"
    assert normalize([{"path": "src/./a.py"}], arg="files", save=False) == [{"path": "src/a.py"}]


def test_an_upper_case_hash_is_stored_lower_case():
    assert normalize([{"path": "a", "sha256": H.upper()}], arg="files", save=True)[0]["sha256"] == H


def test_lines_and_no_line():
    r = normalize([{"path": "a", "sha256": H, "lines": [2, 9]}], arg="files", save=True)[0]
    assert (r["line_start"], r["line_end"]) == (2, 9)
    r = normalize([{"path": "a", "sha256": H}], arg="files", save=True)[0]
    assert (r["line_start"], r["line_end"]) == (None, None)


def test_identical_refs_collapse_and_two_lines_on_one_file_stay_two():
    got = normalize([{"path": "a", "sha256": H, "line": 1}, {"path": "./a", "sha256": H, "line": 1},
                     {"path": "a", "sha256": H, "line": 5}], arg="files", save=True)
    assert [r["line_start"] for r in got] == [1, 5]


def test_retrieve_keeps_null_and_absent_apart():
    got = normalize([{"path": "a", "sha256": None}, {"path": "b"}, {"path": "c", "sha256": H}],
                    arg="files", save=False)
    assert got == [{"path": "a", "sha256": None}, {"path": "b"}, {"path": "c", "sha256": H}]


@pytest.mark.parametrize("files, needle", [
    ("src/a.py", "must be a list"),
    ([{"path": "a"}] * (MAX_FILES + 1), f"at most {MAX_FILES}"),
    (["src/a.py"], "must be an object"),
    ([{"path": "a", "sha256": H, "size": 3}], "unknown keys"),
    ([{"path": "", "sha256": H}], "non-empty"),
    ([{"path": "/etc/passwd", "sha256": H}], "relative to the project root"),
    ([{"path": "C:/x.py", "sha256": H}], "relative to the project root"),
    ([{"path": "src\\a.py", "sha256": H}], "/ separators"),
    ([{"path": "../a.py", "sha256": H}], "no '..'"),
    ([{"path": "src/", "sha256": H}], "not a folder"),
    ([{"path": ".", "sha256": H}], "must name a file"),
    ([{"path": "a"}], "sha256 is required"),
    ([{"path": "a", "sha256": None}], "64 hex"),
    ([{"path": "a", "sha256": "abc"}], "64 hex"),
    ([{"path": "a", "sha256": H, "line": 0}], "line must be a whole number"),
    ([{"path": "a", "sha256": H, "line": True}], "line must be a whole number"),
    ([{"path": "a", "sha256": H, "lines": [5, 2]}], "lines must be [start, end]"),
    ([{"path": "a", "sha256": H, "line": 1, "lines": [1, 2]}], "not both"),
])
def test_a_malformed_save_entry_is_refused_by_name(files, needle):
    with pytest.raises(FileRefError) as e:
        normalize(files, arg="files", save=True)
    assert needle in str(e.value) and "files" in str(e.value), str(e.value)


@pytest.mark.parametrize("files, needle", [
    ([{"path": "a", "line": 3}], "unknown keys"),
    ([{"path": "a", "sha256": "xyz"}], "or null"),
])
def test_a_malformed_retrieve_entry_is_refused_by_name(files, needle):
    with pytest.raises(FileRefError) as e:
        normalize(files, arg="files", save=False)
    assert needle in str(e.value)


def test_one_path_with_two_different_current_hashes_is_refused():
    with pytest.raises(FileRefError) as e:
        normalize([{"path": "a.py", "sha256": H}, {"path": "./a.py", "sha256": None}], arg="files", save=False)
    assert "a.py" in str(e.value) and "twice" in str(e.value)
    assert normalize([{"path": "a.py", "sha256": H}, {"path": "./a.py", "sha256": H}], arg="files", save=False) == [
        {"path": "a.py", "sha256": H}]


def test_a_bare_entry_never_conflicts_whatever_its_order():
    for files in ([{"path": "a.py"}, {"path": "a.py", "sha256": H}], [{"path": "a.py", "sha256": H}, {"path": "a.py"}]):
        assert normalize(files, arg="files", save=False)


def test_a_save_naming_one_anchor_twice_with_different_hashes_is_refused():
    with pytest.raises(FileRefError) as e:
        normalize([{"path": "a.py", "sha256": H, "line": 2}, {"path": "a.py", "sha256": "b" * 64, "line": 2}],
                  arg="files", save=True)
    assert "twice" in str(e.value)
    assert len(normalize([{"path": "a.py", "sha256": H, "line": 2}, {"path": "a.py", "sha256": "b" * 64, "line": 3}],
                         arg="files", save=True)) == 2
