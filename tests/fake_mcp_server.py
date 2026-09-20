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


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
