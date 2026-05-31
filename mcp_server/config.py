"""Configuration for the MCP server, loaded from environment variables."""
import os
import sys

# Static bearer token required from MCP clients. If empty, the auth middleware
# rejects ALL requests (fail closed).
MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN", "")

# Base URL of the existing FastAPI service that data/SQL/analytics tools proxy to.
# Inside docker-compose, services reach each other by name (http://api:8000).
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")

# Where the MCP server itself listens.
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "8765"))


def warn_if_misconfigured():
    """Log loudly at startup if the auth token is missing."""
    if not MCP_AUTH_TOKEN:
        print(
            "⚠️  MCP_AUTH_TOKEN is not set - the MCP server will reject ALL requests. "
            "Set it in your .env to enable access.",
            file=sys.stderr,
        )
