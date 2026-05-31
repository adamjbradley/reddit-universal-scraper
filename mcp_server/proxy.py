"""HTTP proxy helper - calls the existing FastAPI service for data/SQL/analytics tools."""
import httpx

from mcp_server.config import API_BASE_URL

# Shared async client reused across all tool calls.
_client = httpx.AsyncClient(base_url=API_BASE_URL, timeout=30.0)


def _clean(params: dict) -> dict:
    """Drop empty-string and None params so they don't override API defaults."""
    return {k: v for k, v in params.items() if v not in (None, "")}


def _raise_clean(e: httpx.HTTPStatusError, path: str):
    detail = ""
    try:
        detail = e.response.json().get("detail", "")
    except Exception:
        detail = e.response.text[:200]
    raise RuntimeError(f"API {e.response.status_code} on {path}: {detail}") from None


async def api_get(path: str, params: dict | None = None):
    """GET the API and return parsed JSON, raising a clean error string on failure."""
    try:
        resp = await _client.get(path, params=_clean(params or {}))
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        _raise_clean(e, path)
    except httpx.HTTPError as e:
        raise RuntimeError(f"Cannot reach API at {API_BASE_URL}{path}: {e}") from None


async def api_delete(path: str):
    """DELETE on the API and return parsed JSON, raising a clean error on failure."""
    try:
        resp = await _client.delete(path)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        _raise_clean(e, path)
    except httpx.HTTPError as e:
        raise RuntimeError(f"Cannot reach API at {API_BASE_URL}{path}: {e}") from None
