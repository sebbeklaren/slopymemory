"""Claude Code (2.1.280) caps every MCP tool description and server instructions at 2,048 characters and cuts what
is longer — silently, from the end. Everything slopymemory sends must fit, so no sentence is ever lost."""
from agent_memory.mcp import server
from slopymemory.launcher import INIT_DESCRIPTION
from slopymemory.usage_rules import USAGE_RULES

CAP = 2048


def test_the_server_instructions_fit_the_cap():
    assert len(USAGE_RULES) <= CAP


def test_every_tool_description_fits_the_cap():
    for name in ("memory_save", "memory_retrieve"):
        assert len(server.mcp._tool_manager.get_tool(name).description) <= CAP, name
    longest_backend_note = 400          # memory_init's description gains one sentence about the Postgres it will use
    assert len(INIT_DESCRIPTION) + longest_backend_note <= CAP
