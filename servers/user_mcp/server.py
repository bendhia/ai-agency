# servers/user_mcp/server.py
import asyncio
from typing import Optional, List
from mcp.server.fastmcp import FastMCP

# Name your MCP server (what clients will see)
mcp = FastMCP("user-mcp")

@mcp.tool()
async def ask_user(
    question: str,
    options: Optional[List[str]] = None,
    required: bool = True
) -> str:
    """
    Ask the human a clarifying question and return their answer.
    If options are provided, show them; otherwise accept free text.
    """
    print("\n=== USER CLARIFICATION NEEDED ===")
    print(question)
    if options:
        for i, opt in enumerate(options, 1):
            print(f"  {i}. {opt}")
    print("(Type your answer and press Enter)\n> ", end="", flush=True)

    loop = asyncio.get_running_loop()
    # read from stdin without blocking the event loop
    answer = await loop.run_in_executor(None, input)
    answer = (answer or "").strip()
    if required and not answer:
        return "[no answer provided]"
    return answer

if __name__ == "__main__":
    # Run an MCP stdio server — clients connect over stdio
    mcp.run()   # STDIO is the default transport

