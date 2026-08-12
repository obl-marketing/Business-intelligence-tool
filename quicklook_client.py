"""Live QuickLook GetUsageStats API client - dealer usage activity.

This is a standalone data source, independent of DATA_SOURCE / GA4. It is
active whenever QUICKLOOK_API_TOKEN is set (and QUICKLOOK_ENABLED is not turned
off), so it can run alongside a live or mock GA4 connection.

Endpoint (form-data POST, Bearer auth):
    POST {QUICKLOOK_BASE_URL}/QAPIv2/Customers/GetUsageStats

The endpoint returns *raw rows only* (no aggregates) and paginates via
page/perpage (max 500). We page through the full result set for the date range
and hand the raw rows to quicklook_analytics, which joins them to the dealer
directory and computes the counts the UI actually asks for.

Note: the host is internal (quicklook.orientbell.com, plain HTTP). It is
reachable from the STARS server inside the OBL network, not from a machine
outside it.
"""
from __future__ import annotations
import os
import time

# HTTPS by default: the API also serves 443, and some networks block outbound
# port 80. Override with QUICKLOOK_BASE_URL if needed.
DEFAULT_BASE_URL = "https://quicklook.orientbell.com"
ENDPOINT_PATH = "/QAPIv2/Customers/GetUsageStats"
MAX_PERPAGE = 500
# Hard ceiling so a huge/misdated query can never pull unbounded data.
DEFAULT_ROW_CAP = 100_000

# Friendly type key -> API contract for that type.
# `api` is the exact string the endpoint expects (note voicePromts is spelled
# that way in the API - the typo is intentional and must be sent verbatim).
TYPES: dict[str, dict] = {
    "design":        {"api": "design",      "date_field": "request_time", "has_tiles": True},
    "catalogue":     {"api": "catalogue",   "date_field": "created_date",  "has_tiles": True},
    "quotation":     {"api": "quotation",   "date_field": "date",          "has_tiles": True},
    "sessions":      {"api": "sessions",    "date_field": "date",          "has_tiles": False},
    "voice_prompts": {"api": "voicePromts", "date_field": "created_at",    "has_tiles": False},
}


class QuickLookError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool((os.environ.get("QUICKLOOK_API_TOKEN") or "").strip())


def is_active() -> bool:
    if (os.environ.get("QUICKLOOK_ENABLED", "1").strip().lower()) in ("0", "false", "no", "off"):
        return False
    return is_configured()


def _base_url() -> str:
    return (os.environ.get("QUICKLOOK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _endpoint() -> str:
    return _base_url() + ENDPOINT_PATH


def _token() -> str:
    tok = (os.environ.get("QUICKLOOK_API_TOKEN") or "").strip()
    if not tok:
        raise QuickLookError("QUICKLOOK_API_TOKEN is not set.")
    return tok


def _row_cap() -> int:
    try:
        return int(os.environ.get("QUICKLOOK_ROW_CAP", DEFAULT_ROW_CAP))
    except ValueError:
        return DEFAULT_ROW_CAP


def _post_page(api_type: str, start_date: str, end_date: str, page: int, perpage: int) -> dict:
    """POST one page. Retries transient network errors with backoff."""
    import httpx

    form = {
        "type": api_type,
        "start_date": start_date,
        "end_date": end_date,
        "page": str(page),
        "perpage": str(perpage),
    }
    headers = {"Authorization": f"Bearer {_token()}"}
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(_endpoint(), data=form, headers=headers)
            if resp.status_code == 401:
                raise QuickLookError("Unauthorized (401): the QuickLook bearer token is missing or invalid.")
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload, dict):
                raise QuickLookError(f"Unexpected response (not a JSON object): {str(payload)[:200]}")
            if payload.get("status") is False:
                raise QuickLookError(payload.get("message") or "QuickLook API returned status:false.")
            return payload
        except QuickLookError:
            raise
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_exc = exc
            time.sleep(2 * (attempt + 1))  # 2s, 4s
        except Exception as exc:  # HTTP errors, JSON decode, etc.
            raise QuickLookError(f"{type(exc).__name__}: {exc}") from exc
    raise QuickLookError(f"Network error reaching QuickLook after retries: {last_exc}")


def fetch_rows(type_key: str, start_date: str, end_date: str,
               max_rows: int | None = None) -> dict:
    """Page through the full result set for a type + date range.

    Returns {"rows": [source_row dict, ...], "pages": int, "truncated": bool}.
    Only the source_row is kept (the join key Merchant_Code and fields live
    there); the tiles[] array is not needed for counting.
    """
    if type_key not in TYPES:
        raise QuickLookError(f"Unknown type '{type_key}'. Valid: {', '.join(TYPES)}")
    api_type = TYPES[type_key]["api"]
    cap = max_rows or _row_cap()

    rows: list[dict] = []
    page = 1
    truncated = False
    while True:
        payload = _post_page(api_type, start_date, end_date, page, MAX_PERPAGE)
        data = payload.get("data") or []
        for item in data:
            src = item.get("source_row") if isinstance(item, dict) else None
            rows.append(src if isinstance(src, dict) else (item if isinstance(item, dict) else {}))
        if len(rows) >= cap:
            rows = rows[:cap]
            truncated = True
            break
        if len(data) < MAX_PERPAGE:  # last (short) page
            break
        page += 1

    return {"rows": rows, "pages": page, "truncated": truncated}


def status() -> dict:
    return {
        "configured": is_configured(),
        "active": is_active(),
        "base_url": _base_url(),
        "types": list(TYPES),
    }
