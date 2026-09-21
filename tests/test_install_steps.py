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
    assert s.cached_wheels(cache, wheels) == set()                     # a cache with no wheels yet: the first run, nothing cached
    assert s.cached_wheels(None, wheels) is None                       # `uv cache dir` did not answer: unknown, not "nothing"
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


def test_preflight_on_a_fresh_machine_says_nothing_is_cached_yet_not_an_error(tmp_path, monkeypatch, capsys):
    lock = tmp_path / "uv.lock"; lock.write_text(LOCK)
    monkeypatch.setattr(s, "free_bytes", lambda p: 10 * 1024**3)
    assert s.preflight(tmp_path / "venv", yes=True, lock=lock, cache=tmp_path / "empty-cache") == 0
    out = capsys.readouterr().out
    assert "about 191 MB for 2 package(s) (nothing in uv's cache yet)" in out and "could not" not in out


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


def test_preflight_refuses_without_ss_before_any_download_and_names_iproute2(tmp_path, monkeypatch, capsys):
    """Seen on a slim container: everything installed, then `slopymem stop` and the doctor's servers check had no
    `ss` to find the process on a store's port. The preflight says so before a byte is fetched."""
    lock = tmp_path / "uv.lock"; lock.write_text(LOCK)
    monkeypatch.setattr(s, "free_bytes", lambda p: 10 * 1024**3)
    monkeypatch.setattr(s.shutil, "which", lambda name: None if name == "ss" else f"/usr/bin/{name}")
    assert s.preflight(tmp_path / "venv", yes=True, lock=lock, cache=tmp_path / "empty-cache") == 1
    out = capsys.readouterr().out
    assert "ss" in out and "iproute2" in out and "SETUP.md#servers" in out and "download:" not in out


def test_check_tools_passes_when_ss_is_there(monkeypatch):
    monkeypatch.setattr(s.shutil, "which", lambda name: f"/usr/bin/{name}")
    ok, msg = s.check_tools()
    assert ok and "ss" in msg


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


# --- download_model: the same directory-and-files view the doctor has, for both repositories ---

CODE_REPO = "nomic-ai/nomic-bert-2048"
AUTO_MAP = {"AutoConfig": f"{CODE_REPO}--configuration_hf_nomic_bert.NomicBertConfig",
            "AutoModel": f"{CODE_REPO}--modeling_hf_nomic_bert.NomicBertModel"}
CODE_FILES = ("configuration_hf_nomic_bert.py", "modeling_hf_nomic_bert.py")


def _scratch_hub(tmp_path, monkeypatch, weights_files=None, code_files=None, weights_rev="e9b6", code_rev="7710"):
    """A scratch hub cache (no refs/) holding the weights snapshot with `weights_files` (None = absent) and the code
    snapshot with `code_files` (None = absent); the weights' config.json always names the code repository."""
    hub = tmp_path / "hub"
    monkeypatch.setattr(s, "hub_cache", lambda: hub)
    w = hub / "models--nomic-ai--nomic-embed-text-v1.5" / "snapshots" / weights_rev
    c = hub / f"models--{CODE_REPO.replace('/', '--')}" / "snapshots" / code_rev
    if weights_files is not None:
        for name in weights_files:
            (w / name).parent.mkdir(parents=True, exist_ok=True); (w / name).write_text("x")
        (w / "config.json").write_text(json.dumps({"auto_map": AUTO_MAP}))
    if code_files is not None:
        c.mkdir(parents=True, exist_ok=True)
        for name in code_files:
            (c / name).write_text("# code")
    return hub, w, c


def _spy_hub(monkeypatch, w, c):
    """A fake snapshot_download that records its calls and writes the files a real fetch would leave."""
    calls = []
    def fake(repo_id, revision=None, local_files_only=False, allow_patterns=None, ignore_patterns=None, **kw):
        calls.append({"repo": repo_id, "revision": revision, "local": local_files_only, "allow": allow_patterns, "ignore": ignore_patterns})
        if local_files_only:
            raise FileNotFoundError("the probe must not be used any more")
        if repo_id == s.MODEL:
            for name in s.WEIGHT_FILES:
                (w / name).parent.mkdir(parents=True, exist_ok=True); (w / name).write_text("x")
            (w / "config.json").write_text(json.dumps({"auto_map": AUTO_MAP}))
            return str(w)
        c.mkdir(parents=True, exist_ok=True)
        for name in CODE_FILES:
            (c / name).write_text("# code")
        return str(c)
    monkeypatch.setattr(s, "snapshot_download", lambda: fake)
    monkeypatch.setattr(s, "weights_size", lambda model, revision, ignore=s.WEIGHTS_IGNORE: None)
    return calls


def test_download_model_fetches_nothing_when_both_snapshots_are_complete(monkeypatch, capsys, tmp_path):
    hub, w, c = _scratch_hub(tmp_path, monkeypatch, weights_files=s.WEIGHT_FILES, code_files=CODE_FILES)
    calls = _spy_hub(monkeypatch, w, c)
    assert s.download_model("e9b6", code_revision="7710") == str(w)
    assert calls == [] and capsys.readouterr().out == ""                  # nothing fetched, nothing said: the caller reports


def test_download_model_says_the_size_once_then_fetches_the_weights_without_the_exports(monkeypatch, capsys, tmp_path):
    """The weights fetch skips onnx/ and openvino/ (1.7 GB of exported formats nothing here reads): the 0.5 GB the note
    promises is what is fetched. Then the code repository at ITS pin."""
    hub, w, c = _scratch_hub(tmp_path, monkeypatch)                        # both absent: a fresh machine
    calls = _spy_hub(monkeypatch, w, c)
    assert s.download_model("e9b6", code_revision="7710") == str(w)
    out = capsys.readouterr().out
    assert "this happens once (about 0.5 GB)" in out
    weights = [k for k in calls if k["repo"] == s.MODEL]
    assert weights == [{"repo": s.MODEL, "revision": "e9b6", "local": False, "allow": None, "ignore": list(s.WEIGHTS_IGNORE)}]
    assert "onnx/*" in s.WEIGHTS_IGNORE and "openvino/*" in s.WEIGHTS_IGNORE
    assert {"repo": CODE_REPO, "revision": "7710", "local": False, "allow": ["*.py"], "ignore": None} in calls
    assert "fetching the model's code" in out and "7710" in out


def test_download_model_prints_the_real_size_when_the_hub_lists_it(monkeypatch, capsys, tmp_path):
    hub, w, c = _scratch_hub(tmp_path, monkeypatch)
    _spy_hub(monkeypatch, w, c)
    monkeypatch.setattr(s, "weights_size", lambda model, revision, ignore=s.WEIGHTS_IGNORE: 547959597)
    s.download_model("e9b6", code_revision="7710")
    assert "this happens once (about 0.5 GB)" in capsys.readouterr().out    # 523 MiB, from the file list, reads the same


def test_weights_size_sums_the_hubs_file_list_minus_the_ignored_and_is_none_when_it_cannot(monkeypatch):
    class Sib:
        def __init__(self, name, size): self.rfilename, self.size = name, size
    class Info:
        siblings = [Sib("model.safetensors", 500), Sib("onnx/model.onnx", 5000), Sib("openvino/x.bin", 700), Sib("config.json", 3), Sib("README.md", None)]
    class Api:
        def model_info(self, model, revision=None, files_metadata=False, timeout=None):
            assert files_metadata and revision == "e9b6"
            return Info()
    monkeypatch.setattr(s, "hf_api", lambda: Api())
    assert s.weights_size(s.MODEL, "e9b6") == 503
    class Offline:
        def model_info(self, *a, **kw): raise OSError("offline")
    monkeypatch.setattr(s, "hf_api", lambda: Offline())
    assert s.weights_size(s.MODEL, "e9b6") is None
    assert s.size_note(None) == "this happens once (about 0.5 GB)" and s.size_note(547959597) == "this happens once (about 0.5 GB)"
    assert s.size_note(2 * 2**30) == "this happens once (about 2.0 GB)"


def test_download_model_refetches_a_weights_snapshot_cut_short(monkeypatch, capsys, tmp_path):
    """A snapshot directory that exists without its files (an interrupted download) is fetched again — the hub fills in
    what is missing — instead of being reported as cached."""
    hub, w, c = _scratch_hub(tmp_path, monkeypatch, weights_files=("config.json", "tokenizer.json"), code_files=CODE_FILES)
    calls = _spy_hub(monkeypatch, w, c)
    assert s.download_model("e9b6", code_revision="7710") == str(w)
    assert [k["repo"] for k in calls] == [s.MODEL] and "0.5 GB" in capsys.readouterr().out
    assert not s.missing_files(w, s.WEIGHT_FILES)


def test_download_model_refetches_a_code_snapshot_missing_a_module_file(monkeypatch, capsys, tmp_path):
    """The doctor's 'code snapshot incomplete: missing modeling_hf_nomic_bert.py — run slopymem install-model' must not
    be a dead end: the directory being there is not enough, the files the config names must be."""
    hub, w, c = _scratch_hub(tmp_path, monkeypatch, weights_files=s.WEIGHT_FILES, code_files=("configuration_hf_nomic_bert.py",))
    calls = _spy_hub(monkeypatch, w, c)
    assert s.download_model("e9b6", code_revision="7710") == str(w)
    assert [k["repo"] for k in calls] == [CODE_REPO] and calls[0]["revision"] == "7710" and calls[0]["allow"] == ["*.py"]
    out = capsys.readouterr().out
    assert "fetching the model's code" in out and "modeling_hf_nomic_bert.py" in out and "0.5 GB" not in out
    assert (c / "modeling_hf_nomic_bert.py").exists()


def test_download_model_without_a_code_pin_falls_back_to_the_hubs_probe(monkeypatch, capsys, tmp_path):
    hub, w, c = _scratch_hub(tmp_path, monkeypatch, weights_files=s.WEIGHT_FILES, code_files=CODE_FILES)
    calls = []
    def fake(repo_id, revision=None, local_files_only=False, allow_patterns=None, ignore_patterns=None, **kw):
        calls.append((repo_id, revision, local_files_only))
        if local_files_only:
            return str(c)
        raise AssertionError("cached by the hub's own test: no fetch")
    monkeypatch.setattr(s, "snapshot_download", lambda: fake)
    assert s.download_model("e9b6") == str(w)
    assert calls == [(CODE_REPO, None, True)]


def test_local_snapshot_and_its_files_are_read_from_the_directory_alone(tmp_path, monkeypatch):
    """No refs/, no network: the directory by commit hash, and the files the embedder reads inside it."""
    hub = tmp_path / "hub"
    monkeypatch.setattr(s, "hub_cache", lambda: hub)
    assert s.local_snapshot("nomic-ai/x", "abc") is None
    d = hub / "models--nomic-ai--x" / "snapshots" / "abc"; d.mkdir(parents=True)
    assert s.local_snapshot("nomic-ai/x", "abc") == d
    assert s.missing_files(d, s.WEIGHT_FILES) == list(s.WEIGHT_FILES)
    (d / "config.json").write_text(json.dumps({"auto_map": {"AutoConfig": "nomic-ai/code--configuration_x.XConfig",
                                                             "AutoModel": "nomic-ai/code--modeling_x.XModel"}}))
    assert s.code_files(d) == ["configuration_x.py", "modeling_x.py"]
    assert "config.json" not in s.missing_files(d, s.WEIGHT_FILES)
