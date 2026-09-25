"""The installer's verbs (`install-postgres`, `install-model`, `register --detected`) and `uninstall`, against a
temp home, a fake Postgres and fake harnesses. Nothing here touches the real home, a real harness or the network."""
import io, sys
from pathlib import Path
import pytest
from slopymemory import cli, harnesses, install_steps, provision, server as srv
from slopymemory.harnesses import Harness
from slopymemory.store import Store


class FakePg:
    def __init__(self): self.created = []; self.dropped = []; self.asked = []; self.running = False; self.vector = True; self.stops = 0
    def database_exists(self, n): return n in self.created and n not in self.dropped
    def database_size(self, n): return None
    def create_database(self, n): self.created.append(n)
    def drop_database(self, n): self.dropped.append(n)
    def vector_installed(self, n): return self.vector
    def has_pgvector(self): return True
    def describe(self): return "fake Postgres"
    def can_provision(self): return True
    def is_running(self): return self.running
    def stop(self): self.stops += 1; self.running = False; return True


@pytest.fixture
def fake_pg(monkeypatch):
    pg = FakePg()
    monkeypatch.setattr(cli, "postgres", lambda backend="system": (pg.asked.append(backend), pg)[1])
    monkeypatch.setattr(cli.embedded_pg, "available", lambda: True)
    monkeypatch.setattr(srv, "stop", lambda st: False)
    return pg


def run(argv, stdin=""):
    out = io.StringIO(); err = io.StringIO()
    old = sys.stdin, sys.stdout, sys.stderr; sys.stdin, sys.stdout, sys.stderr = io.StringIO(stdin), out, err
    try:
        code = cli.main(argv)
    finally:
        sys.stdin, sys.stdout, sys.stderr = old
    return code, out.getvalue() + err.getvalue()


# --- install-postgres -------------------------------------------------------------------------------------------

def test_install_postgres_offers_the_system_one_and_verifies_with_a_scratch_database(tmp_home, fake_pg):
    code, out = run(["install-postgres"], stdin="y\ny\n")
    assert code == 0 and "fake Postgres" in out and "use the system Postgres" in out
    assert len(fake_pg.created) == 1 and fake_pg.created[0].startswith("slopymem_install_check_") and fake_pg.dropped == fake_pg.created
    assert "system Postgres verified" in out and "scratch database" in out


def test_install_postgres_falls_back_to_embedded_when_the_system_one_cannot(tmp_home, fake_pg, monkeypatch):
    def cannot(): raise provision.InitRefused("psql not found — see SETUP.md#postgres")
    monkeypatch.setattr(fake_pg, "can_provision", cannot)
    code, out = run(["install-postgres", "--yes"])
    assert code == 0 and "psql not found" in out and "embedded" in out and fake_pg.asked[-1] == "embedded"
    assert fake_pg.stops == 1                    # the cluster was down before the check: it is left down after it


def test_install_postgres_honours_the_request_and_refuses_a_backend_that_cannot(tmp_home, fake_pg, monkeypatch):
    code, out = run(["install-postgres", "--postgres", "embedded", "--yes"])
    assert code == 0 and "embedded Postgres verified" in out and fake_pg.asked[-1] == "embedded"
    monkeypatch.setattr(cli.embedded_pg, "available", lambda: False)
    code, out = run(["install-postgres", "--postgres", "embedded", "--yes"])
    assert code == 1 and "SETUP.md#postgres" in out


def test_install_postgres_fails_when_pgvector_did_not_install_and_says_the_anchor(tmp_home, fake_pg):
    fake_pg.vector = False
    code, out = run(["install-postgres", "--yes"])
    assert code == 1 and "pgvector" in out and "SETUP.md#postgres" in out and fake_pg.dropped == fake_pg.created


def test_install_postgres_reports_the_refusal_even_when_the_stop_after_it_fails_too(tmp_home, fake_pg, monkeypatch):
    """Seen: a SLOPYMEM_HOME too deep for a unix socket — create_database refused, then the check's own cleanup
    stop() raised (no data dir to ask pg_ctl about) and the traceback buried the refusal that mattered. Both are
    said, in order, and the exit is a refusal, not a crash."""
    def refuse(n): raise provision.InitRefused("the embedded Postgres socket path is 130 bytes; unix sockets allow 107 — set SLOPYMEM_HOME to a shorter path — see SETUP.md#postgres")
    def stop_fails(): raise provision.InitRefused("pg_ctl status: directory does not exist — see SETUP.md#postgres")
    monkeypatch.setattr(fake_pg, "create_database", refuse)
    monkeypatch.setattr(fake_pg, "stop", stop_fails)
    code, out = run(["install-postgres", "--postgres", "embedded", "--yes"])
    assert code == 1 and "Traceback" not in out
    assert "set SLOPYMEM_HOME to a shorter path" in out and "was not stopped" in out and "directory does not exist" in out
    assert out.index("shorter path") < out.index("was not stopped")


def test_install_postgres_drops_the_scratch_database_and_stops_the_cluster_even_when_the_check_fails(tmp_home, fake_pg, monkeypatch):
    def boom(n): raise provision.InitRefused("embedded Postgres: out of disk — see SETUP.md#postgres")
    monkeypatch.setattr(fake_pg, "vector_installed", boom)
    code, out = run(["install-postgres", "--postgres", "embedded", "--yes"])
    assert code == 1 and "out of disk" in out and fake_pg.dropped == fake_pg.created and len(fake_pg.created) == 1
    assert fake_pg.stops == 1                    # down before, down after — on the failure path too


def test_install_postgres_says_no_when_told_no(tmp_home, fake_pg):
    code, out = run(["install-postgres"], stdin="y\nn\n")
    assert code == 1 and fake_pg.created == []


# --- install-model ------------------------------------------------------------------------------------------------

def test_install_model_asks_with_the_size_once_downloads_the_pin_and_names_the_anchor_on_failure(tmp_home, monkeypatch, tmp_path):
    from agent_memory.config import settings
    calls = []
    def fake_download(rev, model=install_steps.MODEL, code_revision=None, ask=None):
        if install_steps.weights_complete(model, rev) is None and ask is not None and not ask("this happens once (about 0.5 GB)"):
            return None                                      # asked only when the weights are to be fetched, as the real one does
        calls.append((rev, code_revision)); return "/hub/snap"
    monkeypatch.setattr(install_steps, "weights_complete", lambda model, rev: None)
    monkeypatch.setattr(install_steps, "download_model", fake_download)
    code, out = run(["install-model"], stdin="n\n")
    assert code == 1 and calls == [] and out.count("0.5 GB") == 1 and "SETUP.md#model" in out
    code, out = run(["install-model", "--yes"])
    assert code == 0 and calls == [(settings.embed_revision, settings.embed_code_revision)] and "/hub/snap" in out
    monkeypatch.setattr(install_steps, "weights_complete", lambda model, rev: tmp_path)
    code, out = run(["install-model"])                       # complete: said so, and download_model still runs (the code repo)
    assert code == 0 and "already" in out and len(calls) == 2
    monkeypatch.setattr(install_steps, "weights_complete", lambda model, rev: None)
    def boom(rev, model=install_steps.MODEL, code_revision=None, ask=None): raise OSError("no network")
    monkeypatch.setattr(install_steps, "download_model", boom)
    code, out = run(["install-model", "--yes"])
    assert code == 1 and "no network" in out and "SETUP.md#model" in out


def test_install_model_fails_loud_through_the_real_download_when_the_loader_still_refuses_after_the_fetch(tmp_home, monkeypatch, tmp_path):
    """Files present or freshly fetched must not read as "model ready" while the embedder's own offline resolution
    still refuses the snapshot — that was a doctor FAIL with no way out. The real `download_model` (not a fake)
    raises; `cmd_install_model` must turn that into a non-zero exit naming the anchor, same as any other download
    failure."""
    import json
    from agent_memory.config import settings
    hub = tmp_path / "hub"
    monkeypatch.setattr(install_steps, "hub_cache", lambda: hub)
    w = hub / f"models--{install_steps.MODEL.replace('/', '--')}" / "snapshots" / settings.embed_revision
    for name in install_steps.WEIGHT_FILES:
        (w / name).parent.mkdir(parents=True, exist_ok=True); (w / name).write_text("x")
    auto_map = {"AutoModel": "nomic-ai/nomic-bert-2048--modeling_hf_nomic_bert.NomicBertModel"}
    (w / "config.json").write_text(json.dumps({"auto_map": auto_map}))
    c = hub / "models--nomic-ai--nomic-bert-2048" / "snapshots" / settings.embed_code_revision
    c.mkdir(parents=True); (c / "modeling_hf_nomic_bert.py").write_text("# code")

    def fake(repo_id, revision=None, local_files_only=False, allow_patterns=None, ignore_patterns=None, **kw):
        return str(w) if repo_id == install_steps.MODEL else str(c)     # the fetch "succeeds": the files were already there
    monkeypatch.setattr(install_steps, "snapshot_download", lambda: fake)
    monkeypatch.setattr(install_steps, "weights_size", lambda model, revision, ignore=None: None)
    monkeypatch.setattr(install_steps, "loader_resolves",
                        lambda repo, revision, what: "IncompleteSnapshotError: not in the cache" if what == "weights" else None)
    code, out = run(["install-model", "--yes"])
    assert code == 1 and "IncompleteSnapshotError" in out and "SETUP.md#model" in out


# --- register --detected -------------------------------------------------------------------------------------------

def _fake_harnesses(tmp_path, monkeypatch, ran):
    def runner(cmd, **kw): ran.append(list(cmd))
    monkeypatch.setattr(harnesses.subprocess, "run", runner)
    instr = tmp_path / "instructions.md"
    hs = [Harness(id="a", name="A", detect=lambda: True, registered=lambda lp: False, registered_as=lambda lp: None,
                  register_cmd=lambda lp: ["a-add", lp], unregister_cmd=lambda name: ["a-remove", name],
                  config_hint="a hint", instructions_file=lambda: instr),
          Harness(id="b", name="B", detect=lambda: True, registered=lambda lp: True, registered_as=lambda lp: "mem2",
                  register_cmd=lambda lp: ["b-add", lp], unregister_cmd=lambda name: ["b-remove", name],
                  config_hint="b hint", instructions_file=None),
          Harness(id="c", name="C", detect=lambda: False, registered=lambda lp: True, registered_as=lambda lp: "memory",
                  register_cmd=lambda lp: ["c-add", lp], unregister_cmd=lambda name: ["c-remove", name],
                  config_hint="c hint", instructions_file=None)]
    monkeypatch.setattr(harnesses, "KNOWN", hs)
    return hs, instr


def test_register_detected_registers_only_what_is_there_and_says_so(tmp_home, tmp_path, monkeypatch):
    ran = []
    hs, instr = _fake_harnesses(tmp_path, monkeypatch, ran)
    code, out = run(["register", "--detected", "--yes"])
    assert code == 0 and ran == [["a-add", harnesses.launcher_path()]]            # b already registered, c not detected
    assert "A:" in out and "B: already registered" in out and "C" not in out
    assert instr.read_text().count(harnesses.OFFER_LINE) == 1
    monkeypatch.setattr(harnesses, "KNOWN", [hs[2]])
    code, out = run(["register", "--detected", "--yes"])
    assert code == 0 and "no known harness detected" in out and "slopymem register" in out


# --- uninstall -----------------------------------------------------------------------------------------------------

def _installed_home(tmp_home, tmp_path, monkeypatch, fake_pg, stores=("one", "two")):
    venv = tmp_home / "venv" / "bin"; venv.mkdir(parents=True); (venv / "python").write_text("")
    (tmp_home / "logs").mkdir(); (tmp_home / "logs" / "one.log").write_text("log")
    (tmp_home / "pg").mkdir(); (tmp_home / "pg" / "PG_VERSION").write_text("18")
    for i, name in enumerate(stores):
        repo = tmp_path / name; repo.mkdir(); monkeypatch.chdir(repo)
        assert run(["init", "--postgres", "embedded" if i else "system", "--yes"])[0] == 0
    return tmp_home


def test_uninstall_refuses_yes_without_data(tmp_home, tmp_path, monkeypatch, fake_pg):
    _installed_home(tmp_home, tmp_path, monkeypatch, fake_pg)
    code, out = run(["uninstall", "--yes"])
    assert code == 2 and "--yes" in out and "--data" in out and (tmp_home / "venv").exists()


def test_uninstall_lists_first_asks_twice_and_keeps_the_data(tmp_home, tmp_path, monkeypatch, fake_pg):
    _installed_home(tmp_home, tmp_path, monkeypatch, fake_pg)
    ran = []
    hs, instr = _fake_harnesses(tmp_path, monkeypatch, ran)
    instr.write_text(f"# mine\nkeep this\n\n# slopymemory\n{harnesses.OFFER_LINE}\n")
    fake_pg.running = True                                                        # the embedded cluster is up, as it usually is
    code, out = run(["uninstall"], stdin="y\nn\n")
    assert code == 1 and (tmp_home / "venv").exists() and ran == [] and fake_pg.stops == 0
    head = out[:out.index("remove these?")]                                       # the list comes BEFORE the first question
    assert str(tmp_home / "venv") in head and "B" in head and str(instr) in head and str(tmp_home / "logs") in head
    assert "KEPT" in head and "one, two" in head and "--data" in head
    assert "stop the embedded Postgres" in head and "kept" in head                # its binaries live in the venv that goes
    code, out = run(["uninstall"], stdin="y\ny\n")
    assert code == 0
    assert fake_pg.stops == 1 and out.index("embedded Postgres stopped") < out.index(f"removed {tmp_home / 'venv'}")
    assert not (tmp_home / "venv").exists() and not (tmp_home / "logs").exists() and not (tmp_home / "registry.toml").exists()
    assert Store.exists("one") and Store.exists("two") and (tmp_home / "pg" / "PG_VERSION").exists()
    assert fake_pg.dropped == []
    assert ran == [["b-remove", "mem2"]]                                         # registered and detected: B only, by the name OUR entry has
    assert instr.read_text() == "# mine\nkeep this\n"                            # the line and its heading, nothing else


def test_uninstall_data_asks_a_third_time_by_name_and_drops_through_each_backend(tmp_home, tmp_path, monkeypatch, fake_pg):
    _installed_home(tmp_home, tmp_path, monkeypatch, fake_pg)
    _fake_harnesses(tmp_path, monkeypatch, [])
    stopped = []
    monkeypatch.setattr(srv, "stop", lambda st: (stopped.append(st.name), True)[1])
    fake_pg.asked.clear()
    code, out = run(["uninstall", "--data"], stdin="y\ny\nn\n")
    assert code == 1 and Store.exists("one") and fake_pg.dropped == [] and (tmp_home / "venv").exists()
    third = out[out.rindex("really?"):]
    assert "one" in third and "two" in third
    code, out = run(["uninstall", "--data"], stdin="y\ny\ny\n")
    assert code == 0
    assert sorted(fake_pg.dropped) == ["one_memory", "two_memory"] and sorted(stopped) == ["one", "two"]
    assert "system" in fake_pg.asked and "embedded" in fake_pg.asked                # each store's OWN backend
    assert not (tmp_home / "stores").exists() and not (tmp_home / "pg").exists() and not (tmp_home / "venv").exists()
    assert not tmp_home.exists()                                                     # nothing left: the home goes too


def test_uninstall_data_with_yes_still_asks_the_third_question(tmp_home, tmp_path, monkeypatch, fake_pg):
    _installed_home(tmp_home, tmp_path, monkeypatch, fake_pg)
    _fake_harnesses(tmp_path, monkeypatch, [])
    code, out = run(["uninstall", "--data", "--yes"], stdin="n\n")
    assert code == 1 and Store.exists("one") and "remove these?" not in out and "one" in out
    code, out = run(["uninstall", "--data", "--yes"], stdin="y\n")
    assert code == 0 and sorted(fake_pg.dropped) == ["one_memory", "two_memory"]


def test_uninstall_stops_at_an_embedded_postgres_that_did_not_stop_and_keeps_the_venv(tmp_home, tmp_path, monkeypatch, fake_pg):
    """pg_ctl lives in the venv: removing it under a postmaster that did not stop leaves a cluster nobody can stop
    cleanly. The uninstall ends there — the venv, the registrations, the logs and the registry stay — and the message
    names the exact pg_ctl line, the kill fallback, and says to run uninstall again."""
    _installed_home(tmp_home, tmp_path, monkeypatch, fake_pg)
    ran = []
    _fake_harnesses(tmp_path, monkeypatch, ran)
    def wedged(): raise provision.InitRefused("pg_ctl stop: server does not shut down — see SETUP.md#postgres")
    monkeypatch.setattr(fake_pg, "stop", wedged)
    code, out = run(["uninstall"], stdin="y\ny\n")
    assert code == 1 and "did not stop" in out and "server does not shut down" in out
    assert (tmp_home / "venv").exists() and (tmp_home / "logs").exists() and (tmp_home / "registry.toml").exists()
    assert ran == []                                                              # the registrations were not touched
    assert "pg_ctl -D " + str(tmp_home / "pg") in out and "-m fast stop" in out
    assert "postmaster.pid" in out and "uninstall" in out.split("did not stop")[1]
    assert "uninstalled" not in out.split("did not stop")[1].replace("uninstall again", "")


def test_uninstall_reports_a_failing_unregister_and_goes_on(tmp_home, tmp_path, monkeypatch, fake_pg):
    _installed_home(tmp_home, tmp_path, monkeypatch, fake_pg)
    hs, instr = _fake_harnesses(tmp_path, monkeypatch, [])
    import subprocess
    def boom(cmd, **kw): raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(harnesses.subprocess, "run", boom)
    code, out = run(["uninstall"], stdin="y\ny\n")
    assert code == 1 and "B" in out and "SETUP.md#harnesses" in out
    assert not (tmp_home / "venv").exists()                                          # the rest was still removed


def test_uninstall_on_a_home_that_is_not_there_says_so(tmp_home, fake_pg, monkeypatch, tmp_path):
    hs, _ = _fake_harnesses(tmp_path, monkeypatch, [])
    monkeypatch.setattr(harnesses, "KNOWN", [hs[0], hs[2]])                     # nothing registered anywhere
    code, out = run(["uninstall"], stdin="y\ny\n")
    assert code == 0 and "nothing to uninstall" in out and "remove these?" not in out
    monkeypatch.setattr(harnesses, "KNOWN", hs)                                  # a registration alone IS something to remove
    code, out = run(["uninstall"], stdin="y\ny\n")
    assert code == 0 and "B: b-remove mem2" in out


def test_remove_offer_line_takes_the_line_and_its_heading_only(tmp_path):
    f = tmp_path / "CLAUDE.md"
    f.write_text(f"# mine\nkeep\n\n# slopymemory\n{harnesses.OFFER_LINE}\n\n# after\nalso keep\n")
    assert harnesses.remove_offer_line(f) is True
    assert f.read_text() == "# mine\nkeep\n\n# after\nalso keep\n"
    assert harnesses.remove_offer_line(f) is False                                  # absent: untouched, says so
    f.write_text("just text\nIf the memory tools show only an older line\n")       # a stale line goes too
    assert harnesses.remove_offer_line(f) is True and f.read_text() == "just text\n"
    link = tmp_path / "link.md"; target = tmp_path / "target.md"
    target.write_text(f"a\n# slopymemory\n{harnesses.OFFER_LINE}\n"); link.symlink_to(target)
    assert harnesses.remove_offer_line(link) is True and link.is_symlink() and target.read_text() == "a\n"


def test_known_harnesses_have_an_unregister_where_they_have_a_register():
    from slopymemory.harnesses import claude_code, codex, pi
    assert claude_code.HARNESS.unregister_cmd("mem2") == ["claude", "mcp", "remove", "--scope", "user", "mem2"]
    assert codex.HARNESS.unregister_cmd("ours") == ["codex", "mcp", "remove", "ours"]
    assert pi.HARNESS.unregister_cmd is None and all(h.registered_as is not None for h in (claude_code.HARNESS, codex.HARNESS, pi.HARNESS))


# --- harness-memory ----------------------------------------------------------------------------------------------

def test_harness_memory_off_says_the_consequence_and_asks(tmp_home, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("n\n"))
    code, out = run(["harness-memory", "off", "--harness", "claude-code"])
    assert code == 1 and "EVERY project" in out and "no memory at all" in out
    assert not (tmp_path / ".claude" / "settings.json").exists()           # declined: nothing written


def test_harness_memory_off_then_on_round_trip(tmp_home, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    assert run(["harness-memory", "off", "--harness", "claude-code", "--yes"])[0] == 0
    code, out = run(["harness-memory", "status"])
    assert code == 0 and "off (slopymemory)" in out
    assert run(["harness-memory", "on", "--harness", "claude-code", "--yes"])[0] == 0
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_harness_memory_conflict_is_not_answered_by_yes(tmp_home, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    run(["harness-memory", "off", "--harness", "claude-code", "--yes"])
    (tmp_path / ".claude" / "settings.json").write_text('{"autoMemoryEnabled": true}')
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("k\n"))
    code, out = run(["harness-memory", "on", "--harness", "claude-code", "--yes"])
    assert code == 0 and "changed after slopymemory" in out
    assert (tmp_path / ".claude" / "settings.json").read_text() == '{"autoMemoryEnabled": true}'


def test_uninstall_restores_harness_memory_first(tmp_home, tmp_path, monkeypatch, fake_pg):
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    _installed_home(tmp_home, tmp_path, monkeypatch, fake_pg)
    run(["harness-memory", "off", "--harness", "claude-code", "--yes"])
    code, out = run(["uninstall", "--yes", "--data"], stdin="y\n")
    assert "Claude Code file memory restored" in out
    # "dropped database" is printed only by an actual removal step (never by the restore message
    # itself, which cannot contain it) — a real ordering violation moves this index before it.
    assert "dropped database" in out
    assert out.index("Claude Code file memory restored") < out.index("dropped database")
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_uninstall_preview_lists_the_harness_memory_restore(tmp_home, tmp_path, monkeypatch, fake_pg):
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    _installed_home(tmp_home, tmp_path, monkeypatch, fake_pg)
    run(["harness-memory", "off", "--harness", "claude-code", "--yes"])
    code, out = run(["uninstall"], stdin="n\n")
    assert code == 1
    head = out[:out.index("remove these?")]
    assert "harness memory switched off by slopymemory: restored first" in head


def test_uninstall_survives_an_unreadable_settings_file_and_leaves_it_untouched(tmp_home, tmp_path, monkeypatch, fake_pg):
    """A user who never touched harness-memory but has a hand-broken ~/.claude/settings.json must still be
    able to uninstall: harness-memory state is said as unreadable and left alone, not a blocker."""
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    broken = "{not json"
    (tmp_path / ".claude" / "settings.json").write_text(broken)
    _installed_home(tmp_home, tmp_path, monkeypatch, fake_pg)
    code, out = run(["uninstall"], stdin="y\ny\n")
    assert code == 0
    assert "harness memory state unreadable" in out
    assert "SETUP.md#harness-memory" in out and "left as it is" in out
    assert (tmp_path / ".claude" / "settings.json").read_text() == broken


def test_uninstall_conflict_question_during_uninstall_names_the_harness_and_restores_on_r(tmp_home, tmp_path, monkeypatch, fake_pg):
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    _installed_home(tmp_home, tmp_path, monkeypatch, fake_pg)
    run(["harness-memory", "off", "--harness", "claude-code", "--yes"])
    (tmp_path / ".claude" / "settings.json").write_text('{"autoMemoryEnabled": true}')     # changed since
    code, out = run(["uninstall", "--yes", "--data"], stdin="r\ny\n")
    assert code == 0
    assert "claude-code: it changed after slopymemory set it" in out
    assert "Claude Code file memory restored" in out


def test_harness_memory_status_on_unreadable_file_fails_cleanly_no_traceback(tmp_home, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{not json")
    code, out = run(["harness-memory", "status"])
    assert code == 1
    assert "SETUP.md#harness-memory" in out
    assert "Traceback" not in out


class _Detected:
    def __init__(self, id):
        self.id = id


def test_harness_memory_bare_off_and_on_say_none_detected(tmp_home, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    monkeypatch.setattr(harnesses, "detected", lambda: [])
    code, out = run(["harness-memory", "off"])
    assert code == 0 and "no harness with a switch detected (Claude Code, Codex)" in out
    assert "EVERY project" not in out                     # no warning/question when there is nothing to switch
    code, out = run(["harness-memory", "on"])
    assert code == 0 and "no harness with a switch detected (Claude Code, Codex)" in out


def test_harness_memory_bare_on_reports_nothing_to_restore_but_explicit_harness_still_refuses(tmp_home, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    monkeypatch.setattr(harnesses, "detected", lambda: [_Detected("claude-code")])
    code, out = run(["harness-memory", "on"])
    assert code == 0 and "claude-code: nothing to restore" in out
    code, out = run(["harness-memory", "on", "--harness", "claude-code"])
    assert code == 1 and "no record" in out


def test_harness_memory_conflict_question_names_the_harness(tmp_home, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    run(["harness-memory", "off", "--harness", "claude-code", "--yes"])
    (tmp_path / ".claude" / "settings.json").write_text('{"autoMemoryEnabled": true}')
    code, out = run(["harness-memory", "on", "--harness", "claude-code", "--yes"], stdin="k\n")
    assert "claude-code: it changed after slopymemory set it" in out


def test_uninstall_data_declined_after_restore_says_it_was_restored(tmp_home, tmp_path, monkeypatch, fake_pg):
    monkeypatch.setenv("HOME", str(tmp_path)); (tmp_path / ".claude").mkdir()
    _installed_home(tmp_home, tmp_path, monkeypatch, fake_pg)
    run(["harness-memory", "off", "--harness", "claude-code", "--yes"])
    code, out = run(["uninstall", "--data"], stdin="y\ny\nn\n")
    assert code == 1
    assert "restored" in out
    assert "slopymem: nothing removed" not in out          # the bare message would be false: something WAS restored
    assert not (tmp_path / ".claude" / "settings.json").exists()     # the restore itself did happen
    assert fake_pg.dropped == []
