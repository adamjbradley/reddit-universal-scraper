"""Reddit official API (OAuth2) token management.

Register an app at https://www.reddit.com/prefs/apps:
  - "script" app  -> set REDDIT_USERNAME + REDDIT_PASSWORD (password grant; can
                     access your account and is allowed higher rate limits)
  - "web app"     -> leave username/password blank (client_credentials grant;
                     read-only access to public data)

Tokens are cached in-process and refreshed automatically shortly before expiry.
"""
import time
import requests

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"

_token = None
_expires_at = 0.0


def _fetch(client_id, client_secret, username, password, user_agent):
    """Request a fresh access token from Reddit. Returns (token, expires_in)."""
    auth = requests.auth.HTTPBasicAuth(client_id, client_secret)
    if username and password:
        data = {"grant_type": "password", "username": username, "password": password}
    else:
        data = {"grant_type": "client_credentials"}

    resp = requests.post(
        TOKEN_URL, auth=auth, data=data,
        headers={"User-Agent": user_agent}, timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if "access_token" not in payload:
        raise RuntimeError(f"Reddit OAuth token error: {payload}")
    return payload["access_token"], int(payload.get("expires_in", 3600))


def get_token(client_id, client_secret, username, password, user_agent, force=False):
    """Return a valid bearer token, fetching/refreshing as needed (cached)."""
    global _token, _expires_at
    now = time.time()
    # Refresh 60s before expiry to avoid mid-request expiration.
    if force or not _token or now >= _expires_at - 60:
        _token, expires_in = _fetch(client_id, client_secret, username, password, user_agent)
        _expires_at = now + expires_in
    return _token
