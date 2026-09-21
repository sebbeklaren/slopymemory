"""The installer's Python half: pure functions over the lock, the cache, the disk and the model cache. No network
in this file: the one function that asks a server for a size is stubbed."""
import json
from slopymemory import install_steps as s

LOCK = '''
[[package]]
name = "torch"
version = "2.12.0+cpu"
wheels = [
  { url = "https://download.pytorch.org/whl/cpu/torch-2.12.0%2Bcpu-cp313-cp313-manylinux_2_28_x86_64.whl", size = 200000000 },
  { url = "https://download.pytorch.org/whl/cpu/torch-2.12.0%2Bcpu-cp313-cp313-macosx_11_0_arm64.whl", size = 70000000 },
]
[[package]]
name = "tomli-w"
version = "1.2.0"
wheels = [ { url = "https://files.pythonhosted.org/x/tomli_w-1.2.0-py3-none-any.whl", size = 6000 } ]
'''
TAGS = ("manylinux_2_28_x86_64", "py3-none-any")


def test_download_size_is_read_from_the_lock_for_this_platform(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text(LOCK)
    total, per = s.download_size(lock, platform_tags=TAGS, cached=set())
    assert total == 200006000 and per["torch"] == 200000000
    total2, _ = s.download_size(lock, platform_tags=TAGS, cached={"torch"})
    assert total2 == 6000


def test_free_space_gate_refuses_below_4gb(monkeypatch, tmp_path):
    monkeypatch.setattr(s, "free_bytes", lambda p: 3 * 1024**3)
    ok, msg = s.check_free_space(tmp_path)
    assert not ok and "4 GB" in msg and "SETUP.md#space" in msg
    monkeypatch.setattr(s, "free_bytes", lambda p: 5 * 1024**3)
    ok, msg = s.check_free_space(tmp_path)
    assert ok and "5" in msg


def test_free_bytes_walks_up_to_an_existing_ancestor(tmp_path):
    assert s.free_bytes(tmp_path / "not" / "yet" / "there") == s.free_bytes(tmp_path) > 0


def test_python_check_names_the_uv_way_to_get_one(monkeypatch):
    monkeypatch.setattr(s, "find_python", lambda: None)
    ok, msg = s.check_python()
    assert not ok and "uv python install 3.13" in msg and "SETUP.md#python" in msg
    monkeypatch.setattr(s, "find_python", lambda: "/x/python3.13")
    ok, msg = s.check_python()
    assert ok and "/x/python3.13" in msg


def test_steps_are_announced_and_idempotent(tmp_path, capsys):
    done = []
    s.run_step("venv", "create the venv", lambda: done.append(1), already=lambda: len(done) > 0)
    s.run_step("venv", "create the venv", lambda: done.append(1), already=lambda: len(done) > 0)
    out = capsys.readouterr().out
    assert done == [1] and "1/6" not in out and "venv: create the venv" in out and "already done" in out


# --- beyond the four: what the real lock and the real cache look like ---

def test_a_wheel_without_a_size_is_reported_as_unknown_not_guessed(tmp_path):
    """The PyTorch index publishes no sizes: its wheels have a url and a hash only. Such a wheel is counted in
    `per` as None and left out of the total — never a made-up number. A size the caller found out (a HEAD
    request, outside this pure function) is taken through `sizes`."""
    lock = tmp_path / "uv.lock"
    lock.write_text(LOCK.replace(", size = 200000000", "").replace(", size = 70000000", ""))
    total, per = s.download_size(lock, platform_tags=TAGS, cached=set())
    assert total == 6000 and per["torch"] is None
    total, per = s.download_size(lock, platform_tags=TAGS, cached=set(), sizes={"torch": 192272034})
    assert total == 6000 + 192272034 and per["torch"] == 192272034


def test_only_the_root_packages_closure_counts_dev_deps_do_not(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text(LOCK + '''
[[package]]
name = "pytest"
version = "8.0.0"
wheels = [ { url = "https://files.pythonhosted.org/x/pytest-8.0.0-py3-none-any.whl", size = 999 } ]
[[package]]
name = "slopymemory"
version = "0.2.0"
source = { editable = "." }
dependencies = [ { name = "tomli-w" }, { name = "torch", version = "2.12.0+cpu", marker = "sys_platform != 'darwin'" } ]
[package.optional-dependencies]
dev = [ { name = "pytest" } ]
''')
    wheels = s.locked_wheels(lock, TAGS, root="slopymemory")
    assert sorted(w.name for w in wheels) == ["tomli-w", "torch"]
    total, per = s.download_size(lock, platform_tags=TAGS, cached=set(), root="slopymemory")
    assert total == 200006000 and "pytest" not in per


def test_a_dependency_for_another_platform_is_left_out(tmp_path, monkeypatch):
    lock = tmp_path / "uv.lock"
    lock.write_text(LOCK + '''
[[package]]
name = "pywin32"
version = "300"
wheels = [ { url = "https://files.pythonhosted.org/x/pywin32-300-py3-none-any.whl", size = 5 } ]
[[package]]
name = "slopymemory"
version = "0.2.0"
source = { editable = "." }
dependencies = [ { name = "tomli-w" }, { name = "torch" }, { name = "pywin32", marker = "sys_platform == 'win32'" } ]
''')
    monkeypatch.setattr(s.sys, "platform", "linux")
    assert sorted(w.name for w in s.locked_wheels(lock, TAGS, root="slopymemory")) == ["tomli-w", "torch"]
    monkeypatch.setattr(s.sys, "platform", "win32")
    assert "pywin32" in [w.name for w in s.locked_wheels(lock, TAGS, root="slopymemory")]


def test_platform_tags_name_this_interpreter_and_machine_most_specific_first():
    tags = s.platform_tags()
    v = f"cp{s.sys.version_info[0]}{s.sys.version_info[1]}"
    assert tags[-1] == "py3-none-any" and tags[0].startswith(f"{v}-{v}-")
    if s.sys.platform.startswith("linux"):
        assert f"manylinux_2_{s._glibc_minor()}_" in tags[0] and any("manylinux2014_" in t for t in tags)


def test_the_wheel_uv_would_pick_is_the_one_counted(tmp_path):
    """Several wheels fit; the most specific tag (the earliest in `platform_tags`) decides, whatever the lock's order."""
    lock = tmp_path / "uv.lock"
    lock.write_text('''
[[package]]
name = "cryptography"
version = "50.0.1"
wheels = [
  { url = "https://files.pythonhosted.org/x/cryptography-50.0.1-cp39-abi3-manylinux_2_28_x86_64.whl", size = 1 },
  { url = "https://files.pythonhosted.org/x/cryptography-50.0.1-cp311-abi3-manylinux_2_34_x86_64.whl", size = 2 },
  { url = "https://files.pythonhosted.org/x/cryptography-50.0.1-py3-none-any.whl", size = 3 },
]
''')
    tags = ("cp313-cp313-manylinux_2_34_x86_64", "cp311-abi3-manylinux_2_34_x86_64", "cp39-abi3-manylinux_2_34_x86_64",
            "cp311-abi3-manylinux_2_28_x86_64", "cp39-abi3-manylinux_2_28_x86_64", "py3-none-any")
    (w,) = s.locked_wheels(lock, tags)
    assert w.size == 2


def test_cached_wheels_reads_uvs_cache_layout_and_says_when_it_cannot(tmp_path):
    """uv keeps an unpacked wheel at <cache>/wheels-v*/<index>/<name>/<version>-<tags>, a link into archive-v*.
    A wheel found there is not downloaded again. No such layout → None: the caller says 'less if cached'."""
    lock = tmp_path / "uv.lock"; lock.write_text(LOCK)
    wheels = s.locked_wheels(lock, TAGS)
    cache = tmp_path / "uv-cache"
    assert s.cached_wheels(cache, wheels) is None                      # no cache dir at all
    assert s.cached_wheels(None, wheels) is None                       # `uv cache dir` did not answer
    archive = cache / "archive-v0" / "abc"; archive.mkdir(parents=True)
    entry = cache / "wheels-v5" / "index" / "e1d1" / "torch"; entry.mkdir(parents=True)     # another index: one level deeper
    (entry / "2.12.0+cpu-cp313-cp313-manylinux_2_28_x86_64").symlink_to(archive)
    (entry / "2.12.0+cpu-cp313-cp313-manylinux_2_28_x86_64.http").write_bytes(b"...torch-2.12.0+cpu-cp313-cp313-manylinux_2_28_x86_64.whl...")
    (entry / "2.12.0+cpu-cp313-cp313-macosx_11_0_arm64.msgpack").write_bytes(b"torch-2.12.0+cpu-cp313-cp313-macosx_11_0_arm64.whl")  # metadata only: never fetched
    assert s.cached_wheels(cache, wheels) == {"torch"}
    pypi = cache / "wheels-v5" / "pypi" / "tomli-w"; pypi.mkdir(parents=True)
    (pypi / "1.2.0-527811ebca4593a1").symlink_to(tmp_path / "gone")           # uv hashes a long tag string; a dangling link is not a cached wheel
    (pypi / "1.2.0-527811ebca4593a1.http").write_bytes(b"tomli_w-1.2.0-py3-none-any.whl")
    assert s.cached_wheels(cache, wheels) == {"torch"}
    (tmp_path / "gone").mkdir()
    assert s.cached_wheels(cache, wheels) == {"torch", "tomli-w"}


def test_preflight_prints_the_size_before_asking_and_refuses_a_no(tmp_path, monkeypatch, capsys):
    lock = tmp_path / "uv.lock"; lock.write_text(LOCK.replace(", size = 200000000", "").replace(", size = 70000000", ""))
    monkeypatch.setattr(s, "free_bytes", lambda p: 10 * 1024**3)
    monkeypatch.setattr(s, "uv_cache_dir", lambda: None)
    monkeypatch.setattr(s, "remote_size", lambda url: 192272034)     # the HEAD request, stubbed
    answers = iter(["n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": (print(prompt, end=""), next(answers))[1])
    rc = s.preflight(tmp_path / "venv", yes=False, lock=lock)
    out = capsys.readouterr().out
    assert rc == 1 and "183 MB" in out and "less if cached" in out and out.index("183 MB") < out.index("continue")
    rc = s.preflight(tmp_path / "venv", yes=True, lock=lock)
    assert rc == 0


def test_preflight_gives_both_numbers_when_the_cache_is_readable(tmp_path, monkeypatch, capsys):
    """The cache-subtracted size is a floor (uv refetches a cached wheel it cannot verify), the full size the ceiling."""
    lock = tmp_path / "uv.lock"; lock.write_text(LOCK)
    monkeypatch.setattr(s, "free_bytes", lambda p: 10 * 1024**3)
    monkeypatch.setattr(s, "cached_wheels", lambda cache, wheels: {"torch"})
    assert s.preflight(tmp_path / "venv", yes=True, lock=lock, cache=tmp_path) == 0
    out = capsys.readouterr().out
    assert "about 0 MB for 1 package(s)" in out and "1 found there" in out and "191 MB without the cache" in out


def test_preflight_says_which_sizes_it_could_not_find_out(tmp_path, monkeypatch, capsys):
    lock = tmp_path / "uv.lock"; lock.write_text(LOCK.replace(", size = 200000000", "").replace(", size = 70000000", ""))
    monkeypatch.setattr(s, "free_bytes", lambda p: 10 * 1024**3)
    monkeypatch.setattr(s, "uv_cache_dir", lambda: None)
    monkeypatch.setattr(s, "remote_size", lambda url: None)
    assert s.preflight(tmp_path / "venv", yes=True, lock=lock) == 0
    out = capsys.readouterr().out
    assert "unknown size" in out and "torch" in out and "0 MB" in out


def test_preflight_refuses_without_space_and_names_the_anchor(tmp_path, monkeypatch, capsys):
    lock = tmp_path / "uv.lock"; lock.write_text(LOCK)
    monkeypatch.setattr(s, "free_bytes", lambda p: 1 * 1024**3)
    assert s.preflight(tmp_path / "venv", yes=True, lock=lock) == 1
    assert "SETUP.md#space" in capsys.readouterr().out


def test_cached_revision_lists_the_snapshots_in_the_hub_cache(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    monkeypatch.setattr(s, "hub_cache", lambda: hub)
    assert s.cached_revisions() == []
    snaps = hub / "models--nomic-ai--nomic-embed-text-v1.5" / "snapshots"
    (snaps / "aaaa").mkdir(parents=True); (snaps / "bbbb").mkdir()
    assert s.cached_revisions() == ["aaaa", "bbbb"]


def test_remote_code_repos_are_read_from_the_snapshots_config(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"auto_map": {
        "AutoConfig": "nomic-ai/nomic-bert-2048--configuration_hf_nomic_bert.NomicBertConfig",
        "AutoModel": "nomic-ai/nomic-bert-2048--modeling_hf_nomic_bert.NomicBertModel"}}))
    assert s.remote_code_repos(tmp_path) == ["nomic-ai/nomic-bert-2048"]
    (tmp_path / "config.json").write_text(json.dumps({"auto_map": {"AutoModel": "modeling_local.Model"}}))
    assert s.remote_code_repos(tmp_path) == []                     # code inside the repo itself: nothing more to fetch
    (tmp_path / "config.json").unlink()
    assert s.remote_code_repos(tmp_path) == []


def test_download_model_skips_when_the_pin_is_cached(monkeypatch, capsys, tmp_path):
    calls = []
    def fake_snapshot_download(repo_id, revision=None, local_files_only=False, **kw):
        calls.append((repo_id, revision, local_files_only))
        if local_files_only:
            return str(tmp_path)
        raise AssertionError("must not download when the pin is cached")
    monkeypatch.setattr(s, "snapshot_download", lambda: fake_snapshot_download)
    path = s.download_model("e9b6")
    assert path == str(tmp_path) and calls[0] == (s.MODEL, "e9b6", True)
    assert "already" in capsys.readouterr().out


def test_download_model_says_the_size_once_then_fetches(monkeypatch, capsys, tmp_path):
    calls = []
    def fake_snapshot_download(repo_id, revision=None, local_files_only=False, **kw):
        calls.append((repo_id, revision, local_files_only))
        if local_files_only:
            raise FileNotFoundError("not cached")
        (tmp_path / "config.json").write_text(json.dumps({"auto_map": {"AutoModel": "nomic-ai/nomic-bert-2048--m.M"}}))
        return str(tmp_path)
    monkeypatch.setattr(s, "snapshot_download", lambda: fake_snapshot_download)
    path = s.download_model("e9b6")
    out = capsys.readouterr().out
    assert path == str(tmp_path) and "this happens once (about 0.5 GB)" in out
    assert (s.MODEL, "e9b6", False) in calls
    assert any(c[0] == "nomic-ai/nomic-bert-2048" and c[2] is False for c in calls)   # the remote code the config names


def test_an_extra_pulls_the_packages_optional_group(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text(LOCK + '''
[[package]]
name = "psycopg"
version = "3.3.6"
dependencies = [ { name = "tomli-w" } ]
wheels = [ { url = "https://files.pythonhosted.org/x/psycopg-3.3.6-py3-none-any.whl", size = 10 } ]
[package.optional-dependencies]
binary = [ { name = "psycopg-binary", marker = "implementation_name != 'pypy'" } ]
[[package]]
name = "psycopg-binary"
version = "3.3.6"
wheels = [ { url = "https://files.pythonhosted.org/x/psycopg_binary-3.3.6-cp313-cp313-manylinux_2_28_x86_64.whl", size = 20 } ]
[[package]]
name = "slopymemory"
version = "0.2.0"
source = { editable = "." }
dependencies = [ { name = "psycopg", extra = ["binary"] } ]
''')
    assert sorted(w.name for w in s.locked_wheels(lock, TAGS, root="slopymemory")) == ["psycopg", "psycopg-binary", "tomli-w"]


def test_platform_tags_accept_abi3_wheels_built_for_an_older_cpython():
    tags = s.platform_tags()
    v = s.sys.version_info
    assert any(t.startswith("cp39-abi3-") for t in tags) and any(t.startswith(f"cp3{v[1]}-abi3-") for t in tags)
    assert not any(t.startswith(f"cp3{v[1] + 1}-") for t in tags)
