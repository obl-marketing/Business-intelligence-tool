"""Dealer directory - maps a QuickLook Merchant_Code (the dealer / channel
partner code) to the dealer's identity and geography.

Loaded from a CSV (DEALER_DIRECTORY_CSV, default data/dealer_hierarchy.csv)
with at least these columns (extra columns are ignored, so you can drop in a
richer export without code changes):

    dealer_name, dealer_code, branch, zone

Scope note: per current requirements we only expose dealer identity + branch +
zone. Any FLS / BH / ZH / NH columns in the source file are intentionally NOT
used - there is no employee-level activity reporting.

Why this module matters: it is the *spine* for every dealer-activity question.
Activity rows from the QuickLook API only carry Merchant_Code + m_zone, so all
roll-ups (by dealer, branch, zone) and - crucially - "dealers with fewer than N
activities" (which must include dealers with ZERO activity, who never appear in
the API response) are driven by joining activity onto this full dealer list.
"""
from __future__ import annotations
import csv
import os


DEFAULT_CSV = os.path.join(os.path.dirname(__file__), "data", "dealer_hierarchy.csv")

# module-level cache, keyed by (path, mtime) so an updated file is picked up
_CACHE: dict | None = None
_CACHE_KEY: tuple | None = None


def _csv_path() -> str:
    return (os.environ.get("DEALER_DIRECTORY_CSV") or "").strip() or DEFAULT_CSV


def _norm_code(code: str | None) -> str:
    return (code or "").strip().upper()


def _zone_group(zone: str) -> str:
    """'East-2' -> 'East'; 'Enterprise' -> 'Enterprise'. Lets a filter of
    'North' match North-1..North-4 while 'East-1' still matches exactly."""
    z = (zone or "").strip()
    return z.split("-", 1)[0] if "-" in z else z


def _load() -> dict:
    """Return the cached directory, reloading if the CSV changed on disk."""
    global _CACHE, _CACHE_KEY
    path = _csv_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        _CACHE, _CACHE_KEY = {"by_code": {}, "dealers": []}, None
        return _CACHE
    key = (path, mtime)
    if _CACHE is not None and _CACHE_KEY == key:
        return _CACHE

    by_code: dict[str, dict] = {}
    dealers: list[dict] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = _norm_code(r.get("dealer_code") or r.get("Dealer Code"))
            if not code:
                continue
            zone = (r.get("zone") or r.get("Zone") or "").strip()
            rec = {
                "dealer_code": code,
                "dealer_name": (r.get("dealer_name") or r.get("Dealer Name") or "").strip(),
                "branch": (r.get("branch") or r.get("Branch") or "").strip(),
                "zone": zone,
                "zone_group": _zone_group(zone),
            }
            by_code[code] = rec
            dealers.append(rec)

    _CACHE, _CACHE_KEY = {"by_code": by_code, "dealers": dealers}, key
    return _CACHE


def is_available() -> bool:
    return bool(_load()["dealers"])


def lookup(code: str | None) -> dict | None:
    """Return the dealer record for a Merchant_Code, or None if not in the
    directory (unmapped dealer, or a non-dealer code such as an employee)."""
    return _load()["by_code"].get(_norm_code(code))


def all_dealers(zone: str | None = None, branch: str | None = None) -> list[dict]:
    """The full dealer list, optionally narrowed to a zone and/or branch.
    Used as the spine so zero-activity dealers still surface."""
    out = _load()["dealers"]
    if zone:
        out = [d for d in out if zone_matches(d, zone)]
    if branch:
        out = [d for d in out if branch_matches(d, branch)]
    return list(out)


def zone_matches(dealer: dict, filt: str) -> bool:
    """True if the dealer's zone matches the filter. Accepts the full zone
    ('East-1'), the zone group ('East' -> any East-*), and a trailing 'zone'
    word ('North zone')."""
    fl = (filt or "").strip().lower()
    if fl.endswith(" zone"):
        fl = fl[: -len(" zone")].strip()
    if not fl:
        return True
    return fl in (dealer["zone"].lower(), dealer["zone_group"].lower())


def branch_matches(dealer: dict, filt: str) -> bool:
    fl = (filt or "").strip().lower()
    if fl.endswith(" branch"):
        fl = fl[: -len(" branch")].strip()
    if not fl:
        return True
    return fl == dealer["branch"].lower()


def zones() -> list[str]:
    return sorted({d["zone"] for d in _load()["dealers"] if d["zone"]})


def zone_groups() -> list[str]:
    return sorted({d["zone_group"] for d in _load()["dealers"] if d["zone_group"]})


def branches(zone: str | None = None) -> list[str]:
    ds = all_dealers(zone=zone)
    return sorted({d["branch"] for d in ds if d["branch"]})


def stats() -> dict:
    d = _load()["dealers"]
    return {
        "dealers": len(d),
        "zones": zones(),
        "zone_groups": zone_groups(),
        "branches": len(branches()),
        "source_csv": _csv_path(),
    }
