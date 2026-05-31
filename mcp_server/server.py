"""MCP server entry point - FastMCP over streamable-HTTP with Bearer auth.

Launched via: python main.py --mcp
Endpoint: http://<host>:8765/mcp   (Bearer-protected)
Health:   http://<host>:8765/healthz   (unauthenticated)
"""
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Add project root to path so `analytics`, `export`, etc. import cleanly.
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from mcp_server import config, tools_analytics, tools_data, tools_scheduler, tools_scrape
from mcp_server.auth import BearerAuthMiddleware


def build_app():
    """Build the composed Starlette ASGI app (healthz + Bearer-wrapped MCP)."""
    mcp = FastMCP(
        "reddit-scraper",
        stateless_http=True,
        host=config.MCP_HOST,
        port=config.MCP_PORT,
    )

    tools_data.register(mcp)
    tools_scrape.register(mcp)
    tools_analytics.register(mcp)
    tools_scheduler.register(mcp)

    mcp_app = mcp.streamable_http_app()  # ASGI app exposing the /mcp endpoint

    async def healthz(request):
        return JSONResponse({"status": "ok"})

    # The mounted MCP app's lifespan (which starts the streamable-HTTP session
    # manager) is NOT run automatically by the parent app, so propagate it here.
    @asynccontextmanager
    async def lifespan(_app):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    return Starlette(
        routes=[
            Route("/healthz", healthz),
            Mount("/", app=BearerAuthMiddleware(mcp_app, token=config.MCP_AUTH_TOKEN)),
        ],
        lifespan=lifespan,
    )


# Module-level app for `uvicorn mcp_server.server:app`.
app = build_app()


def run():
    """Run the MCP server with uvicorn."""
    import uvicorn

    config.warn_if_misconfigured()
    uvicorn.run(app, host=config.MCP_HOST, port=config.MCP_PORT)


if __name__ == "__main__":
    run()
