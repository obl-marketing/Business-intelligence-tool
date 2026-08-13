"""Zoho API client with OAuth2 refresh-token handling.

You generate a long-lived **refresh token** out-of-band (via Swagger / Zoho's
console). STARS stores it and trades it for short-lived **access tokens**
(~1 hour) at Zoho's token endpoint, caches each one, and auto-refreshes when it
expires. Zoho API calls use the header `Authorization: Zoho-oauthtoken <token>`
(note: NOT `Bearer`).

This module is the auth foundation; product-specific query tools get layered on
top once the endpoints are known.

Config (env / secrets, never committed):
    ZOHO_CLIENT_ID=...
    ZOHO_CLIENT_SECRET=...
    ZOHO_REFRESH_TOKEN=...
    ZOHO_ACCOUNTS_URL=https://accounts.zoho.in   # region-specific (.in / .com / .eu ...)
    ZOHO_API_DOMAIN=https://www.zohoapis.in       # optional; the token response also carries it
    ZOHO_ENABLED=1                                # optional; 0 forces off
"""
from __future__ import annotations
import os
import time

DEFAULT_ACCOUNTS_URL = "https://accounts.zoho.in"
DEFAULT_API_DOMAIN = "https://www.zohoapis.in"

# in-memory cache: access tokens are short-lived, so we reuse until ~expiry
_cache: dict = {"access_token": None, "expires_at": 0.0, "api_domain": None}


class ZohoError(RuntimeError):
    pass


def is_configured() -> bool:
    return all(bool((os.environ.get(k) or "").strip())
               for k in ("ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN"))


def is_active() -> bool:
    if (os.environ.get("ZOHO_ENABLED", "1").strip().lower()) in ("0", "false", "no", "off"):
        return False
    return is_configured()


def _accounts_url() -> str:
    return (os.environ.get("ZOHO_ACCOUNTS_URL") or DEFAULT_ACCOUNTS_URL).rstrip("/")


def _api_domain() -> str:
    return ((os.environ.get("ZOHO_API_DOMAIN") or _cache.get("api_domain")
             or DEFAULT_API_DOMAIN)).rstrip("/")


def _refresh() -> str:
    """Exchange the refresh token for a new access token; cache it."""
    import httpx
    params = {
        "grant_type": "refresh_token",
        "client_id": os.environ["ZOHO_CLIENT_ID"].strip(),
        "client_secret": os.environ["ZOHO_CLIENT_SECRET"].strip(),
        "refresh_token": os.environ["ZOHO_REFRESH_TOKEN"].strip(),
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{_accounts_url()}/oauth/v2/token", data=params)
        payload = resp.json()
    except Exception as exc:
        raise ZohoError(f"Token refresh request failed: {type(exc).__name__}: {exc}") from exc

    # Zoho returns errors as {"error": "invalid_client" | "invalid_code" | ...}
    if not isinstance(payload, dict) or payload.get("error"):
        raise ZohoError(f"Zoho token refresh error: {payload.get('error', payload) if isinstance(payload, dict) else payload}")
    token = payload.get("access_token")
    if not token:
        raise ZohoError(f"No access_token in Zoho response: {str(payload)[:200]}")

    expires_in = payload.get("expires_in", 3600)
    try:
        expires_in = float(expires_in)
    except (TypeError, ValueError):
        expires_in = 3600.0
    if expires_in > 100000:  # some responses report ms; normalise to seconds
        expires_in /= 1000.0

    _cache["access_token"] = token
    _cache["expires_at"] = time.time() + expires_in
    if payload.get("api_domain"):
        _cache["api_domain"] = payload["api_domain"].rstrip("/")
    return token


def access_token() -> str:
    """Return a valid access token, refreshing ~60s before expiry."""
    if _cache["access_token"] and time.time() < _cache["expires_at"] - 60:
        return _cache["access_token"]
    return _refresh()


def get(path: str, params: dict | None = None) -> dict:
    """Authenticated GET against the Zoho API domain. `path` is the part after
    the domain, e.g. 'crm/v3/Leads'. Refreshes once on a 401 and retries."""
    import httpx
    url = f"{_api_domain()}/{path.lstrip('/')}"

    def _call(tok: str):
        with httpx.Client(timeout=60.0) as client:
            return client.get(url, params=params or {},
                              headers={"Authorization": f"Zoho-oauthtoken {tok}"})

    resp = _call(access_token())
    if resp.status_code == 401:  # token rejected -> force refresh once
        resp = _call(_refresh())
    if resp.status_code >= 400:
        raise ZohoError(f"Zoho API {resp.status_code} on {path}: {resp.text[:300]}")
    return resp.json()


def selftest() -> dict:
    """Attempt a token refresh and report status WITHOUT exposing the token.
    Run on the server after setting the secrets to validate the refresh flow:
        python -c "import zoho_client, json; print(json.dumps(zoho_client.selftest()))"
    """
    if not is_configured():
        return {"ok": False, "error": "ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET / "
                                      "ZOHO_REFRESH_TOKEN not all set."}
    try:
        _refresh()
        return {
            "ok": True,
            "accounts_url": _accounts_url(),
            "api_domain": _api_domain(),
            "expires_in_seconds": round(_cache["expires_at"] - time.time()),
            "token_length": len(_cache["access_token"] or ""),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "accounts_url": _accounts_url()}
