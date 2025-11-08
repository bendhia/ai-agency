FROM python:3.11-slim

WORKDIR /app

# Install deps for the MCP server
COPY servers/user_mcp/requirements.txt /app/servers/user_mcp/requirements.txt
RUN pip install --no-cache-dir -r /app/servers/user_mcp/requirements.txt

# Copy the MCP server code
COPY servers/user_mcp/server.py /app/servers/user_mcp/server.py

# Run the MCP server over stdio (waits for an MCP client like Claude Desktop)
CMD ["python", "/app/servers/user_mcp/server.py"]
