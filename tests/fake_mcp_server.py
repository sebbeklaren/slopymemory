import os
import sys
from pathlib import Path
from mcp.server.fastmcp import Context, FastMCP

# FAKE_MCP_ONCE=<marker path>: the first start runs normally and leaves the marker; every later start
# exits at once with code 3 — the shape of a store server whose database has gone away between runs.
if marker := os.environ.get("FAKE_MCP_ONCE"):
    if Path(marker).exists():
        print("FATAL: no such database")
        sys.exit(3)
    Path(marker).touch()

mcp = FastMCP("fake-memory", host="127.0.0.1", port=int(os.environ["AM_MCP_PORT"]))


@mcp.tool()
def memory_ping(text: str) -> str:
    """Echo, prefixed — proves a call went through the bridge."""
    return f"pong:{text}"


@mcp.tool()
def memory_session(ctx: Context) -> str:
    """Names the server process and the MCP session this call arrived on — two calls that name the same
    session went through one connection; a changed name is a reconnect."""
    return f"{os.getpid()}:{id(ctx.session)}"


# FAKE_MCP_MEMORY=1: a canned store for the end-to-end client (scripts/e2e_stdio.py) — the shapes the real store's
# tools answer with, and nothing behind them: `buffered` with a count until the twelfth save, `saved` from then on;
# a retrieve says `warming up` before the twelfth save and afterwards returns the saved texts, in save order.
# FAKE_MCP_RETRIEVE=warming keeps the retrieve at `warming up` regardless — the shape the client must refuse.
WARM_AT = 12
_saved: list[str] = []

if os.environ.get("FAKE_MCP_MEMORY"):
    @mcp.tool()
    def memory_save(tenant: str, text: str, scope: str | None = None, thread: str | None = None,
                    save_concepts: list | None = None) -> dict:
        """Canned: buffered until the twelfth save, then saved."""
        _saved.append(text)
        n = len(_saved)
        if n < WARM_AT:
            return {"status": "buffered", "warming": True, "count": n, "need": WARM_AT}
        return {"status": "saved", "memory_id": f"m{n}", "warmed": n == WARM_AT}

    @mcp.tool()
    def memory_retrieve(tenant: str, query: str, k: int = 5, query_concepts: list | None = None) -> dict:
        """Canned: warming up before the twelfth save; then the saved texts in save order."""
        if len(_saved) < WARM_AT or os.environ.get("FAKE_MCP_RETRIEVE") == "warming":
            return {"status": "warming up", "results": []}
        return {"status": "ok", "results": [{"memory_id": f"m{i}", "score": round(1 / i, 3), "text": t}
                                            for i, t in enumerate(_saved[:k], start=1)]}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
