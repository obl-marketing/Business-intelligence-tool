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


def _static_token() -> str:
    """A directly-supplied access token (ZOHO_ACCESS_TOKEN), for quick testing
    without the refresh-token dance. Zoho access tokens expire in ~1 hour, so this
    is a temporary bridge - set ZOHO_REFRESH_TOKEN for a permanent connection."""
    return (os.environ.get("ZOHO_ACCESS_TOKEN") or "").strip()


def is_configured() -> bool:
    # Either a directly-supplied (temporary) access token, OR the refresh-token trio.
    if _static_token():
        return True
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
    if not (os.environ.get("ZOHO_REFRESH_TOKEN") or "").strip():
        raise ZohoError(
            "No ZOHO_REFRESH_TOKEN is set. You're on a temporary ZOHO_ACCESS_TOKEN "
            "which has now expired - paste a fresh access token, or set a refresh "
            "token for a permanent connection.")
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


def _has_refresh_trio() -> bool:
    return all(bool((os.environ.get(k) or "").strip())
               for k in ("ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN"))


def access_token() -> str:
    """Return a valid access token. Prefer the durable refresh-token flow when it's
    configured (so setting both tokens is safe); fall back to a directly-supplied
    ZOHO_ACCESS_TOKEN if there's no refresh trio, or if a refresh attempt fails."""
    if _has_refresh_trio():
        if _cache["access_token"] and time.time() < _cache["expires_at"] - 60:
            return _cache["access_token"]
        try:
            return _refresh()
        except ZohoError:
            if _static_token():
                return _static_token()
            raise
    if _static_token():
        return _static_token()
    raise ZohoError("Zoho not configured: set ZOHO_REFRESH_TOKEN (+ client id/secret) "
                    "or ZOHO_ACCESS_TOKEN.")


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
    if resp.status_code == 204:  # Zoho returns 204 for "no records match"
        return {"data": [], "info": {"more_records": False}}
    if resp.status_code >= 400:
        raise ZohoError(f"Zoho API {resp.status_code} on {path}: {resp.text[:300]}")
    return resp.json()


def post(path: str, json_body: dict) -> dict:
    """Authenticated POST against the Zoho API domain (used by COQL:
    path 'crm/v3/coql', body {'select_query': '...'}). Refreshes once on 401.
    A 204 (no rows) is returned as an empty payload rather than an error."""
    import httpx
    url = f"{_api_domain()}/{path.lstrip('/')}"

    def _call(tok: str):
        with httpx.Client(timeout=60.0) as client:
            return client.post(url, json=json_body,
                               headers={"Authorization": f"Zoho-oauthtoken {tok}"})

    resp = _call(access_token())
    if resp.status_code == 401:
        resp = _call(_refresh())
    if resp.status_code == 204:
        return {"data": [], "info": {"more_records": False}}
    if resp.status_code >= 400:
        raise ZohoError(f"Zoho API {resp.status_code} on {path}: {resp.text[:300]}")
    return resp.json()


def selftest() -> dict:
    """Attempt a token refresh and report status WITHOUT exposing the token.
    Run on the server after setting the secrets to validate the refresh flow:
        python -c "import zoho_client, json; print(json.dumps(zoho_client.selftest()))"
    """
    if not is_configured():
        return {"ok": False, "error": "Set ZOHO_ACCESS_TOKEN (quick test) OR "
                                      "ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET / "
                                      "ZOHO_REFRESH_TOKEN (permanent)."}
    ver = (os.environ.get("ZOHO_API_VERSION") or "v8").strip().lstrip("/")

    # 1) Obtain an access token (proves the token/refresh flow itself works).
    try:
        tok = access_token()
    except Exception as exc:
        return {"ok": False, "error": f"Could not obtain an access token: {exc}",
                "accounts_url": _accounts_url()}
    mode = "refresh_token" if _has_refresh_trio() else "static_access_token"

    import httpx

    # 2) Probe a plain RECORDS read (needs ZohoCRM.modules.leads.READ).
    def _records_probe() -> dict:
        try:
            with httpx.Client(timeout=30.0) as client:
                r = client.get(f"{_api_domain()}/crm/{ver}/Leads",
                               params={"fields": "id", "per_page": 1},
                               headers={"Authorization": f"Zoho-oauthtoken {tok}"})
            return {"records_ok": r.status_code < 400 or r.status_code == 204,
                    "status": r.status_code, "body": (r.text or "")[:300]}
        except Exception as exc:
            return {"records_ok": False, "error": str(exc)}

    # 3) Probe COQL (needs the separate ZohoCRM.coql.READ scope).
    def _coql_probe() -> dict:
        try:
            q = ("select Lead_Source from Leads where Created_Time > "
                 "'2000-01-01T00:00:00+05:30' limit 1")
            with httpx.Client(timeout=30.0) as client:
                r = client.post(f"{_api_domain()}/crm/{ver}/coql",
                                json={"select_query": q},
                                headers={"Authorization": f"Zoho-oauthtoken {tok}"})
            return {"coql_ok": r.status_code < 400 or r.status_code == 204,
                    "status": r.status_code, "body": (r.text or "")[:300]}
        except Exception as exc:
            return {"coql_ok": False, "error": str(exc)}

    records = _records_probe()
    coql = _coql_probe()
    result = {"ok": True, "mode": mode, "api_domain": _api_domain(),
              "api_version": ver, "records": records, "coql": coql}
    if records.get("records_ok") and not coql.get("coql_ok"):
        result["diagnosis"] = ("Records read works but COQL is blocked -> the token "
                               "lacks ZohoCRM.coql.READ. Either add that scope, or "
                               "STARS can use the records API instead (no COQL).")
    elif not records.get("records_ok"):
        result["diagnosis"] = ("Even a plain records read failed -> the token has no "
                               "CRM read scope. Regenerate it with "
                               "ZohoCRM.modules.leads.READ and ZohoCRM.modules.deals.READ "
                               "(add ZohoCRM.coql.READ too).")
    return result
