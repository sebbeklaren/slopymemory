"""The agent decides WHAT to remember, never WHERE: a store is one project's memory, chosen by the folder the
launcher runs in. A `tenant` argument made agents invent a space name on every call (default, sebastian, slopy,
testfolder002, …) although the store ignored it. The tools must not ask for one, and a client that still sends
one must not be refused for it."""
import asyncio, json, os, subprocess, sys

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from agent_memory.mcp import server
from tests_support_fakes import srv  # noqa: F401  (fixture)

_LIST = ("import asyncio, json; from agent_memory.mcp import server; "
         "print(json.dumps({t.name: {'d': t.description, 's': t.input_schema if hasattr(t, 'input_schema') "
         "else t.inputSchema} for t in asyncio.run(server.mcp.list_tools())}))")


@pytest.fixture(scope="module", params=["concept_primary", None])
def tools(request):
    env = {k: v for k, v in os.environ.items() if k != "AM_M3_RETRIEVAL_MODE"}
    if request.param:
        env["AM_M3_RETRIEVAL_MODE"] = request.param
    out = subprocess.run([sys.executable, "-c", _LIST], env=env, capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("tool", ["memory_save", "memory_retrieve"])
def test_no_tool_asks_the_agent_where_a_memory_goes(tools, tool):
    schema = tools[tool]["s"]
    assert "tenant" not in schema.get("properties", {}), schema
    assert "tenant" not in schema.get("required", []), schema
    assert "tenant" not in tools[tool]["d"], tools[tool]["d"]


async def test_an_old_client_that_still_sends_tenant_is_not_refused_for_it(srv):
    # k=0 is refused by the server's own check, which runs only after the arguments were accepted.
    with pytest.raises(ToolError) as e:
        await server.mcp.call_tool("memory_retrieve", {"tenant": "anything", "query": "q", "k": 0})
    assert "tenant" not in str(e.value) and "k" in str(e.value), str(e.value)
