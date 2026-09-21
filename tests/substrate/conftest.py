"""Substrate tests: a real Postgres with pgvector, database from TEST_DATABASE_URL (default a local
`slopymem_substrate_test`). Refuses any database whose name does not end in `_test` — these tests TRUNCATE."""
import os, pytest, psycopg
from pgvector.psycopg import register_vector
# Before the substrate's config is imported: its settings.test_database_url reads the same variable, and the
# roundtrip test starts a server subprocess on it — so the fixture and the server must agree on ONE _test database.
os.environ.setdefault("TEST_DATABASE_URL", "postgresql:///slopymem_substrate_test")
from agent_memory.config import settings
from agent_memory.store import db
from agent_memory.spaces import store as m3

def _url():
    url = os.environ.get("TEST_DATABASE_URL", "postgresql:///slopymem_substrate_test")
    name = url.rsplit("/", 1)[-1].split("?")[0]
    if not name.endswith("_test"):
        raise SystemExit(f"refusing to run substrate tests against {name!r}: the database name must end in _test")
    return url

# The roundtrip test starts the server in fixed projector mode, whose warmup fits the semantic projector on a
# background corpus read from AM_M3_BACKGROUND_CORPUS_PATH — a repo-relative data file where these tests come
# from, absent here. Any dozen-plus distinct sentences serve a one-memory roundtrip; the buffer path likewise
# goes to the run's temp dir so the subprocess never writes into this checkout.
_BACKGROUND = """a function that returns early on invalid input is easier to read
the parser rejects a config file whose required keys are missing
retries with exponential backoff smooth over a flaky network
a cache invalidated on write never serves a stale record
the scheduler runs the nightly job after the last backup finishes
unit tests pin behavior so a refactor cannot drift silently
logging the request id ties a failure to the call that caused it
a migration adds the column with a default before the code reads it
the queue drops to zero when consumers outnumber producers
timeouts on outbound calls keep one slow service from stalling the rest
the linter flags an unused import before review does
a feature flag lets the new path ship dark and turn on later
the database index makes the lookup by owner cheap
a health check answers only when the dependencies are reachable
the build fails loud when a dependency's checksum changes
pagination keeps the list endpoint bounded as the table grows
"""

@pytest.fixture(scope="session", autouse=True)
def _subprocess_env(tmp_path_factory):
    d = tmp_path_factory.mktemp("substrate")
    (d / "background_corpus.txt").write_text(_BACKGROUND)
    os.environ.setdefault("AM_M3_BACKGROUND_CORPUS_PATH", str(d / "background_corpus.txt"))
    os.environ.setdefault("AM_M3_BUFFER_PATH", str(d / "m3_buffer.jsonl"))

@pytest.fixture(scope="session")
def _db_ok():
    try:
        c = psycopg.connect(_url(), autocommit=True)
    except Exception as e:
        msg = f"no test database: {e} — createdb slopymem_substrate_test && psql -d slopymem_substrate_test -c 'create extension vector'"
        # The export sets SLOPYMEM_REQUIRE_DB=1: for it, no database is a FAILED gate, never a skipped one. Anyone
        # else running these tests without a database gets the skip and the hint.
        if os.environ.get("SLOPYMEM_REQUIRE_DB") == "1":
            pytest.fail(msg)
        pytest.skip(msg)
    with c: db.apply_schema(c); m3.apply_schema(c)
    yield

@pytest.fixture
def conn(_db_ok):
    c = psycopg.connect(_url(), autocommit=True); register_vector(c)
    db.apply_schema(c); m3.apply_schema(c); m3.seed_spaces(c)   # idempotent; a test may have dropped the tables
    with c.cursor() as cur:
        cur.execute("TRUNCATE m3_facet, m3_memory, m3_memory_supersession, m3_positive_link, m3_negative_link, m3_synapse, m3_node CASCADE")
    yield c
    c.close()
