"""Deterministic mock QuickLook activity, used when no live token is set.

Produces rows in the same shape as the live API's `source_row` objects, keyed
to REAL dealer codes from the directory so the dealer join, the roll-ups, and
the "dealers with < N activities" logic all demonstrably work. A few unmapped /
employee / null-code rows are mixed in so the reconciliation buckets are
exercised too.

Row volume and per-dealer spread are intentionally uneven: some dealers are
heavy users, many are light (0-4), so threshold questions return real answers.
"""
from __future__ import annotations
import datetime as _dt
import hashlib
import random

import dealer_directory
import quicklook_client

# Mock window ends "today" and spans a few months back so "June" and
# "last month" style questions land on data.
_TODAY = _dt.date.today()
MOCK_END = _TODAY
MOCK_START = _TODAY - _dt.timedelta(days=135)

# Roughly how active each type is (rows per active dealer over the window).
_INTENSITY = {
    "sessions": 9.0,
    "design": 4.0,
    "catalogue": 3.0,
    "quotation": 2.2,
    "voice_prompts": 3.5,
}

_TRANSCRIPTS = [
    "ALTANI MARBLE GREY GLOSSY 6 by 4 FLOORING",
    "wooden strip tile 2 by 2 matt finish",
    "bathroom wall tile blue 300 by 450",
    "vitrified tile double charge 600 by 600",
    "kitchen backsplash subway white",
]


def _seed(*parts) -> random.Random:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return random.Random(int(h[:12], 16))


def _dates_in_range(rng: random.Random, start: _dt.date, end: _dt.date, n: int) -> list[_dt.date]:
    span = (end - start).days
    if span < 0 or n <= 0:
        return []
    return [start + _dt.timedelta(days=rng.randint(0, span)) for _ in range(n)]


def _date_str(type_key: str, d: _dt.date, rng: random.Random) -> str:
    # design/sessions/voice carry a time component in the real API
    if quicklook_client.TYPES[type_key]["date_field"] in ("request_time", "date", "created_at"):
        return f"{d.isoformat()} {rng.randint(0,23):02d}:{rng.randint(0,59):02d}:{rng.randint(0,59):02d}"
    return d.isoformat()


def _row(type_key: str, dealer: dict | None, code: str, zone: str, d: _dt.date,
         rng: random.Random) -> dict:
    df = quicklook_client.TYPES[type_key]["date_field"]
    row = {"Merchant_Code": code, "m_zone": zone, df: _date_str(type_key, d, rng)}
    if type_key == "sessions":
        row["Session_id"] = str(rng.randint(400000, 500000))
        row["Userid"] = str(rng.randint(10000, 999999))
        row["version"] = f"{rng.randint(7,8)}.{rng.randint(100,300)}"
    elif type_key == "voice_prompts":
        t = rng.choice(_TRANSCRIPTS)
        row["transcript"] = t
        row["db_results"] = str(rng.randint(0, 90))
        row["user_id"] = str(rng.randint(10000, 999999))
        row["user_action"] = rng.choice(["", "ai_mockup_opened", ""])
    elif type_key == "quotation":
        row["amount"] = str(rng.randint(200, 8000))
        row["customer_name"] = f"Customer {rng.randint(1,9999)}"
    else:  # design / catalogue
        row["user_id"] = str(rng.randint(10000, 999999))
    return row


def rows(type_key: str, start_date: _dt.date, end_date: _dt.date) -> list[dict]:
    """Return mock source_rows of `type_key` whose date falls in [start, end]."""
    if type_key not in quicklook_client.TYPES:
        return []
    dealers = dealer_directory.all_dealers()
    if not dealers:
        return []

    intensity = _INTENSITY.get(type_key, 3.0)
    out: list[dict] = []
    # Use a stable subset (~40%) as "active" so many dealers stay at 0-4.
    for dealer in dealers:
        rng = _seed(type_key, dealer["dealer_code"])
        activity_level = rng.random()
        if activity_level < 0.35:
            n = 0  # dormant dealer
        elif activity_level < 0.70:
            n = rng.randint(0, 4)  # light user (drives "< 5" answers)
        else:
            n = max(1, int(rng.gauss(intensity, intensity * 0.6)))
        for d in _dates_in_range(rng, MOCK_START, MOCK_END, n):
            out.append(_row(type_key, dealer, dealer["dealer_code"], dealer["zone"], d, rng))

    # A few non-directory rows to exercise reconciliation buckets.
    rng = _seed(type_key, "extras")
    for i in range(6):  # unmapped C-codes (dealers not in our partial list)
        code = "C999" + "".join(str(rng.randint(0, 9)) for _ in range(13))
        for d in _dates_in_range(rng, MOCK_START, MOCK_END, rng.randint(1, 3)):
            out.append(_row(type_key, None, code, rng.choice(["North-3", "South-2", "West-1"]), d, rng))
    for i in range(4):  # employee / internal
        for d in _dates_in_range(rng, MOCK_START, MOCK_END, rng.randint(1, 2)):
            out.append(_row(type_key, None, str(rng.randint(1113000, 1114000)), "Employee", d, rng))
    for i in range(3):  # null Merchant_Code
        for d in _dates_in_range(rng, MOCK_START, MOCK_END, 1):
            r = _row(type_key, None, "", "", d, rng)
            r["Merchant_Code"] = None
            out.append(r)

    # date-range filter (the live API does this server-side); compare on the
    # YYYY-MM-DD prefix of the type's date field.
    lo, hi = start_date.isoformat(), end_date.isoformat()
    df = quicklook_client.TYPES[type_key]["date_field"]
    return [r for r in out if lo <= str(r.get(df, ""))[:10] <= hi]
