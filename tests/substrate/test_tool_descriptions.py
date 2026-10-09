"""A coding store (concept-primary, no facet projectors) must not ask for facets: a model that was told to send them
sent a concept space as a facet key and was refused. A design store keeps asking."""
import json, os, subprocess, sys
import pytest

_LIST = ("import asyncio, json; from agent_memory.mcp import server; "
         "print(json.dumps({t.name: t.description for t in asyncio.run(server.mcp.list_tools())}))")


def _descriptions(mode: str | None) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "AM_M3_RETRIEVAL_MODE"}
    if mode:
        env["AM_M3_RETRIEVAL_MODE"] = mode
    out = subprocess.run([sys.executable, "-c", _LIST], env=env, capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_coding_store_never_asks_for_facets():
    d = _descriptions("concept_primary")
    assert "`facets`" not in d["memory_save"] and "query_facets" not in d["memory_retrieve"], d
    assert "save_concepts" in d["memory_save"] and "query_concepts" in d["memory_retrieve"]


def test_a_design_store_still_asks_for_facets():
    d = _descriptions(None)
    assert "`facets`" in d["memory_save"] and "query_facets" in d["memory_retrieve"]
