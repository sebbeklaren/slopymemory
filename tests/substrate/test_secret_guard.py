"""Secrets never enter a store. `find_secret` names what a text looks like it carries (its KIND and a
plain-language WHY); `memory_save` REFUSES such a text — never redacts, never stores — and logs the
refusal by kind, never the text. Every fixture here is a SHAPED fake (a prefix plus a run of one repeated
character, or a run assembled by hand), never a value that was ever a real credential anywhere."""
import dataclasses
import json

import pytest

from agent_memory.guard import KINDS, Secret, find_secret

# A JWT is three base64url segments: here a header {"alg":"HS256"}, a payload {"sub":"1234567890"} and a
# shaped signature run — the structure is what the pattern reads, the signature bytes are not.
_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "s" * 43
# A 32-character mixed-case alphanumeric run with every character distinct: 5.0 bits/char.
_HIGH_ENTROPY = "q9Zx7Lp2Vb8Nc4Mw1Kd6Fh3Gj5Rt0YuE"


@pytest.mark.parametrize("text,kind", [
    ("the key is sk-" + "A" * 40, "api_key_openai"),
    ("token ghp_" + "b" * 36, "github_token"),
    ("fine-grained github_pat_" + "c" * 60 + " for the deploy", "github_token"),
    ("AKIAABCDEFGHIJKLMNOP is the access key", "aws_access_key"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIE...", "private_key_block"),
    ("-----BEGIN PRIVATE KEY-----\nMIIE...", "private_key_block"),
    (_JWT, "jwt"),
    ("postgresql://app:hunter2secret@db.internal/prod", "connection_string_with_password"),
    ("xoxb-" + "1234567890-abcdefghijklmnop", "slack_token"),
    ("session secret: " + _HIGH_ENTROPY, "high_entropy_token"),
])
def test_recognisable_secrets_are_found_with_their_kind(text, kind):
    s = find_secret(text)
    assert s is not None and s.kind == kind
    assert isinstance(s, Secret) and s.why           # the message needs the plain-language half


@pytest.mark.parametrize("text", [
    "the wifi password is sunflower42",                       # prose: a known limit, documented — not a false positive to fix
    "memory id 1f2e3d4c5b6a79880123456789abcdef",              # a hex id (the shape of this store's own memory ids)
    "supersedes 1f2e3d4c-5b6a-7988-0123-456789abcdef",         # a UUID
    "commit 9f1805b3c2d1e0f4a5b6c7d8e9f0a1b2c3d4e5f6",           # a sha
    "sha256 " + "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",   # a 64-hex digest
    "https://example.com/docs/setup#postgres and nothing else",
    "postgresql:///slopymem_memory",                            # a connection string WITHOUT a password
    "ssh://git@example.com/org/repo.git has no password either",
    "the retrieval returned 12 results in 0.22 s (handshake), cold start 11.1 s",
    "test_memory_save_refuses_and_logs_the_kind_never_the_text went red first",   # a long identifier
    "the file src/agent_memory/spaces/live_store_state.py holds the LiveStore",  # a long path
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA is forty of one letter",           # long but zero entropy
    # Paths, dated file names and env-var names are what a dev journal is MADE of, and the long ones reach
    # 4.0-4.6 bits per character: many distinct letters, digits from the date, separators. They are runs of
    # words, numbers and hex groups joined by / - _ , which is the shape a random token never takes.
    "written up in docs/notes/1999-08-19-plan-for-the-retrieval-change.md",
    "set AM_M3_RETRIEVAL_MODE_CONCEPT_PRIMARY_STEEPNESS=4.0 before the run",
    "see ~/repo/docs/Reviews/1999-09-03-gauge-of-the-window/README.md",
    "the branch feature/scan-store-and-secrets-check-v2 is ready",
    "runs/timing_test/9f1805b3/summary.md holds the record",
    "model someorg/Model3-27B-AWQ-BF16-INT4 served at 46 tok/s",
    "export AM_M3_RETRIEVAL_MODE=concept_primary_with_word_depth first",   # an env assignment: = joins name and value
    "memory/feedback_pre2000_papers_not_load_bearing.md says why",        # a year suffix on a word
    "RecognisableSecretsAreFoundWithTheirKindAndWhy is a long type name",   # letters only: a word, whatever its case
])
def test_ordinary_text_passes(text):
    assert find_secret(text) is None


@pytest.mark.parametrize("token", [
    "q9Zx7Lp2Vb8N-c4Mw1Kd6Fh3Gj5Rt0YuE",            # a base64url-style run with a separator inside
    "q9Zx7Lp2Vb8Nc4Mw1Kd6Fh3Gj5Rt0Yu+E/=",           # standard base64 alphabet
    "z8k2m9q4w7e1r5t3y6u0i2o4p8a1s5d7f9g3h6j",      # lowercase letters and digits interleaved, 40 chars
    "q9Zx7Lp2Vb8Nc4Mw1Kd6Fh3Gj5Rt0YuE-abc-def",     # two separators, but the first segment is no word
])
def test_random_looking_runs_are_still_caught_beside_the_identifier_exclusion(token):
    s = find_secret("the token is " + token)
    assert s is not None and s.kind == "high_entropy_token"


@pytest.mark.parametrize("text", [
    "branch feature/task-scoped-review-gate-for-secrets is up",          # …sk- inside a kebab identifier
    "wrote docs/disk-usage-and-free-space-check.md this morning",
    "the risk-adjusted-return-calculation-notes are in the tree",
    "desk-side-notes-from-the-meeting.md",
])
def test_a_word_ending_in_sk_is_not_an_openai_key(text):
    assert find_secret(text) is None


@pytest.mark.parametrize("text", [
    "the key sk-" + "A" * 40 + " leaked",                # mid-sentence, after a space
    "OPENAI_API_KEY=sk-" + "A" * 40,                     # after =
    'key: "sk-' + "A" * 40 + '"',                         # quoted
    "sk-proj-" + "B" * 40,                                # the project-key shape
])
def test_a_real_sk_shape_still_fires_wherever_it_sits(text):
    s = find_secret(text)
    assert s is not None and s.kind == "api_key_openai"


# Structured tokens of the form prefix_v1_<long hex>: every segment is a word, a number or a hex group, so the
# joined-shape exclusion would hide them — a long hex segment rescues a run only when the run is a PATH.
_HEX64 = "0123456789abcdef" * 4


@pytest.mark.parametrize("text", [
    "token dop_v1_" + _HEX64,                                             # a DigitalOcean-style personal access token
    "xapp-1-A0123456789-0123456789012-" + _HEX64,                         # a Slack app-level token shape
])
def test_a_long_hex_segment_does_not_rescue_a_joined_token(text):
    s = find_secret(text)
    assert s is not None and s.kind == "high_entropy_token"


@pytest.mark.parametrize("text", [
    "runs/snapshots/" + "9f1805b3c2d1e0f4a5b6c7d8e9f0a1b2c3d4e5f6" + "/config.json was read",   # a 40-hex path segment
    "see runs/timing_test/" + _HEX64 + "/summary.md",                                            # a 64-hex path segment
])
def test_a_long_hex_segment_inside_a_path_still_passes(text):
    assert find_secret(text) is None


def test_the_entropy_boundary_is_inclusive():
    from agent_memory.guard import ENTROPY_BITS_PER_CHAR, shannon_bits_per_char
    exactly_four = "ab12CD34ef56GH78" * 2       # 16 distinct characters, each twice: exactly 4.0 bits per character
    assert shannon_bits_per_char(exactly_four) == ENTROPY_BITS_PER_CHAR == 4.0
    s = find_secret("token " + exactly_four)
    assert s is not None and s.kind == "high_entropy_token"
    below = exactly_four + "a"                   # one more of an existing character: 3.99 bits, 33 chars
    assert shannon_bits_per_char(below) < 4.0 and find_secret("token " + below) is None


def test_the_kinds_are_the_eight_named_ones_in_specific_to_generic_order():
    assert [k.kind for k in KINDS] == ["api_key_openai", "github_token", "aws_access_key", "private_key_block",
                                       "jwt", "slack_token", "connection_string_with_password", "high_entropy_token"]


def test_a_specific_kind_wins_over_the_generic_entropy_kind():
    # a real-shaped GitHub token is ALSO a 40-character high-entropy run; the specific name is the useful one
    s = find_secret("ghp_" + "aB3dE5fG7hJ9kL1mN2pQ4rS6tU8vW0xYzA1bC3d")
    assert s is not None and s.kind == "github_token"


def test_empty_and_none_text_pass():
    assert find_secret("") is None
    assert find_secret(None) is None


def test_memory_save_refuses_and_logs_the_kind_never_the_text(conn, tmp_path, monkeypatch):
    from agent_memory.mcp import server
    monkeypatch.setattr(server, "settings", dataclasses.replace(
        server.settings, mcp_invocation_log=str(tmp_path / "log.jsonl"),
        database_url=server.settings.test_database_url))
    connects = []
    real_connect = server._connect
    monkeypatch.setattr(server, "_connect", lambda: (connects.append(1), real_connect())[1])
    out = server.memory_save(tenant="t", text="deploy key ghp_" + "c" * 36)
    assert out["status"] == "refused" and out["reason"] == "secret" and out["kind"] == "github_token"
    assert "store the secret elsewhere" in out["message"] and "GitHub token" in out["message"]
    assert "memory_id" not in out                        # nothing was stored, so there is nothing to point at
    assert connects == []                                # the refusal never opened a store connection
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM m3_memory")
        assert cur.fetchone()[0] == 0                    # and the store holds nothing
    log = (tmp_path / "log.jsonl").read_text()
    assert '"refused": "secret"' in log and "ghp_" not in log and "deploy key" not in log
    rec = json.loads(log.strip().splitlines()[-1])
    assert rec["tool"] == "memory_save" and rec["refused"] == "secret" and rec["kind"] == "github_token"
    # the exact key set: any new field on a refusal record goes red here — none of the agent's values belong in it
    assert set(rec) == {"tool", "tenant", "session_key", "refused", "kind", "field", "status", "t"}
    assert rec["field"] == "text" and out["field"] == "text"


def _fake_store_path(monkeypatch, server):
    """A save that reaches the store: the store and the connection are fakes, so only the guard's verdict matters."""
    calls = []
    monkeypatch.setattr(server, "_connect", lambda: type("C", (), {"close": lambda self: None})())
    monkeypatch.setattr(server.handlers, "memory_save", lambda *a, **k: (calls.append(a), {"status": "saved", "memory_id": "m1"})[1])
    return calls


@pytest.mark.parametrize("field,kwargs,kind", [
    ("thread", {"thread": "ghp_" + "d" * 36}, "github_token in thread"),
    ("scope", {"scope": "postgresql://app:hunter2secret@db/prod"}, "connection_string_with_password in scope"),
    ("facet", {"facets": {"function": "the key is sk-" + "E" * 40}}, "api_key_openai in facet"),
    ("concept", {"save_concepts": [["function", "ok label"], ["semantic", "xoxb-" + "1234567890-abcdefghijklmnop"]]}, "slack_token in concept"),
])
def test_every_agent_supplied_field_is_guarded_and_the_kind_names_the_field(tmp_path, monkeypatch, field, kwargs, kind):
    from agent_memory.mcp import server
    monkeypatch.setattr(server, "settings", dataclasses.replace(server.settings, mcp_invocation_log=str(tmp_path / "log.jsonl")))
    calls = _fake_store_path(monkeypatch, server)
    out = server.memory_save(tenant="t", text="a perfectly ordinary decision", **kwargs)
    assert out["status"] == "refused" and out["kind"] == kind and out["field"] == field
    assert f"the {field} contains what looks like" in out["message"] or f"the {field} label contains" in out["message"]
    assert calls == []                                   # the store was never reached
    log = (tmp_path / "log.jsonl").read_text()
    for value in ("ghp_", "hunter2", "sk-", "xoxb", "ok label", "ordinary decision"):
        assert value not in log                          # none of the agent's values, from any field
    rec = json.loads(log.strip().splitlines()[-1])
    assert rec["kind"] == kind and rec["field"] == field


def test_a_clean_save_with_every_field_passes_the_guard(tmp_path, monkeypatch):
    from agent_memory.mcp import server
    monkeypatch.setattr(server, "settings", dataclasses.replace(server.settings, mcp_invocation_log=str(tmp_path / "log.jsonl")))
    calls = _fake_store_path(monkeypatch, server)
    out = server.memory_save(tenant="t", text="the decision was to keep the port", scope="ops/ports", thread="ports",
                             facets={"function": "which port the server binds"},
                             save_concepts=[["function", "port binding"], ["semantic", "server start"]])
    assert out["status"] == "saved" and len(calls) == 1
    rec = json.loads((tmp_path / "log.jsonl").read_text().strip().splitlines()[-1])
    assert "refused" not in rec and rec["text"] == "the decision was to keep the port"


def test_memory_save_description_tells_the_agent_never_to_save_secrets():
    from agent_memory.mcp import server
    tool = server.mcp._tool_manager.get_tool("memory_save")
    assert "Never save passwords, keys or tokens" in tool.description
    assert "refused" in tool.description
