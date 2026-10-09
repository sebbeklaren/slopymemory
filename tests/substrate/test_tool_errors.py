"""A call refused for a bad argument must tell the model why. The v2 MCP SDK hides any exception other than its
ToolError behind a bare 'Error executing tool <name>', and a model that reads that takes the store for down."""
import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from agent_memory.mcp import server
from tests_support_fakes import srv  # noqa: F401  (fixture)


async def _refusal(tool: str, args: dict) -> str:
    with pytest.raises(ToolError) as e:
        await server.mcp.call_tool(tool, args)
    assert not isinstance(e.value, UnexpectedToolError), "the reason was hidden from the model"
    return str(e.value)


async def test_a_save_with_a_concept_space_as_a_facet_says_which_keys_are_allowed(srv):
    msg = await _refusal("memory_save", {"tenant": "t", "text": "x",
                                         "facets": {"function": "input-routing", "semantic": "control-scheme"}})
    assert "facets keys must be content spaces" in msg and "semantic" in msg


async def test_a_retrieve_with_a_concept_space_as_a_facet_says_which_keys_are_allowed(srv):
    msg = await _refusal("memory_retrieve", {"tenant": "t", "query": "q", "query_facets": {"semantic": "x"}})
    assert "query_facets keys must be content spaces" in msg and "semantic" in msg


@pytest.mark.parametrize("bad", [[["bogus", "x"]], [["semantic"]], ["semantic"], [["semantic", "  "]], [["semantic", 3]]])
async def test_a_malformed_save_concept_is_refused_by_name_before_anything_is_held(srv, bad):
    """A concept the store cannot place used to crash AFTER the save was held, leaving a record that fails forever."""
    import agent_memory.durability as du
    msg = await _refusal("memory_save", {"tenant": "t", "text": "x", "save_concepts": bad})
    assert "save_concepts" in msg and "[space, label]" in msg, msg
    assert du.counts() == (0, 0), "a refused save must leave nothing held or failed"


@pytest.mark.parametrize("bad", [[["bogus", "x"]], [["semantic"]], ["semantic"], [["semantic", ""]]])
async def test_a_malformed_query_concept_is_refused_by_name(srv, bad):
    msg = await _refusal("memory_retrieve", {"tenant": "t", "query": "q", "query_concepts": bad})
    assert "query_concepts" in msg and "[space, label]" in msg, msg


@pytest.mark.parametrize("field", ["text", "thread", "scope", "facets", "save_concepts", "supersedes"])
async def test_a_nul_byte_in_any_save_string_is_refused_before_anything_is_held(srv, field):
    """Postgres refuses NUL in text; a held save carrying one would fail for good on the next drain."""
    import agent_memory.durability as du
    value = {"facets": {"function": "a\x00b"}, "save_concepts": [["semantic", "a\x00b"]]}.get(field, "a\x00b")
    args = {"tenant": "t", "text": "x", field: value}
    msg = await _refusal("memory_save", args)
    assert "NUL" in msg and field in msg, msg
    assert du.counts() == (0, 0)


@pytest.mark.parametrize("k", [0, -1])
async def test_a_retrieve_asking_for_no_results_is_refused_not_reported_as_an_outage(srv, k):
    msg = await _refusal("memory_retrieve", {"tenant": "t", "query": "q", "k": k})
    assert "k must be" in msg, msg
    assert server._state["failing"] is False


async def test_a_bad_retrieve_is_refused_even_while_the_database_is_down(srv, monkeypatch):
    from tests_support_fakes import down
    down(monkeypatch)
    msg = await _refusal("memory_retrieve", {"tenant": "t", "query": "q", "query_concepts": [["bogus", "x"]]})
    assert "query_concepts" in msg
