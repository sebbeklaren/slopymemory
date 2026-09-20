import os
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake-memory", host="127.0.0.1", port=int(os.environ["AM_MCP_PORT"]))


@mcp.tool()
def memory_ping(text: str) -> str:
    """Echo, prefixed — proves a call went through the bridge."""
    return f"pong:{text}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
