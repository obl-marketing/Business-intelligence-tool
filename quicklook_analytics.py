"""Aggregate QuickLook activity rows into the counts the UI asks for.

Two modes:

- **API-only** (default right now): count purely from what the API returns.
  Zones come from each row's `m_zone`, dealers are grouped by `Merchant_Code`.
  No dealer-directory (CSV) cross-reference at all - every dealer the API
  reports is included, active or not. Dealer names / branch / inactive
  detection are NOT available in this mode (they only exist in the CSV).

- **Directory** (set QUICKLOOK_USE_DIRECTORY=1): join each row's Merchant_Code
  to the dealer directory so activity rolls up to dealer name / branch / zone,
  zero-activity dealers can surface, and rows are reconciled into
  mapped / unmapped / employee / unknown buckets.

Both modes run identically on live rows (quicklook_client) and mock rows
(mock_quicklook) - lists of `source_row` dicts carrying at least a
Merchant_Code and m_zone.
"""
from __future__ import annotations
import os
import re
from collections import defaultdict

import dealer_directory

GROUP_BYS = ("dealer", "branch", "zone")

# A real channel-partner/dealer/retailer code is "C" + 15 digits (e.g.
# C101129093100419). This deliberately excludes internal/test/customer codes
# like CUSTILEKART, OBLTEST6, COUSTMERYASHTILEKA, 1113367, and blanks.
_DEALER_CODE_RE = re.compile(r"^C\d{15}$", re.IGNORECASE)
# Real zones only: North-1..4, South-1..2, East-1..2, West. Excludes Employee,
# Dev Team, Coustmer, Cust, blanks, etc.
_REAL_ZONE_RE = re.compile(r"^(north|south|east|west)(-\d+)?$", re.IGNORECASE)
# Internal EMPLOYEE codes are numeric and start with "11" (e.g. 1113573). These
# are a separate population from dealers and must never be mixed into dealer/
# user answers.
_EMPLOYEE_CODE_RE = re.compile(r"^11\d+$")


def is_real_dealer_code(code: str | None) -> bool:
    return bool(_DEALER_CODE_RE.match((code or "").strip()))


def is_employee_code(code: str | None) -> bool:
    return bool(_EMPLOYEE_CODE_RE.match((code or "").strip()))


def is_real_zone(zone: str | None) -> bool:
    return bool(_REAL_ZONE_RE.match((zone or "").strip()))


def directory_enabled() -> bool:
    """Whether to cross-reference the dealer CSV. Off by default for now."""
    return (os.environ.get("QUICKLOOK_USE_DIRECTORY", "0").strip().lower()
            in ("1", "true", "yes", "on"))


def _passes(c: int, min_count: int | None, max_count: int | None) -> bool:
    if min_count is not None and c < min_count:
        return False
    if max_count is not None and c > max_count:
        return False
    return True


def _sample(row: dict) -> dict:
    """A compact version of a raw row for context (drops huge JSON blobs)."""
    out = {}
    for k, v in row.items():
        s = "" if v is None else str(v)
        if len(s) > 160:
            s = s[:157] + "..."
        out[k] = s
    return out


def summarize(type_key: str, rows: list[dict], *,
              group_by: str = "dealer",
              zone: str | None = None,
              branch: str | None = None,
              dealer_code: str | None = None,
              min_count: int | None = None,
              max_count: int | None = None,
              include_zero: bool | None = None,
              top: int = 100,
              source: str = "mock",
              truncated: bool = False,
              period: dict | None = None,
              use_directory: bool | None = None,
              dealers_only: bool = True) -> dict:
    if group_by not in GROUP_BYS:
        group_by = "dealer"
    if use_directory is None:
        use_directory = directory_enabled()
    if use_directory:
        # Directory mode already restricts to real dealers (the CSV join).
        return _summarize_with_directory(
            type_key, rows, group_by=group_by, zone=zone, branch=branch,
            dealer_code=dealer_code, min_count=min_count, max_count=max_count,
            include_zero=include_zero, top=top, source=source,
            truncated=truncated, period=period)
    return _summarize_raw(
        type_key, rows, group_by=group_by, zone=zone, branch=branch,
        dealer_code=dealer_code, min_count=min_count, max_count=max_count,
        top=top, source=source, truncated=truncated, period=period,
        dealers_only=dealers_only)


# --------------------------------------------------------------------------
# API-only mode - no CSV cross-reference
# --------------------------------------------------------------------------

def _raw_zone_matches(m_zone: str, filt: str) -> bool:
    fl = (filt or "").strip().lower()
    if fl.endswith(" zone"):
        fl = fl[: -len(" zone")].strip()
    mz = (m_zone or "").strip().lower()
    if not fl:
        return True
    grp = mz.split("-", 1)[0]                      # 'west-1' -> 'west'
    return fl == mz or fl == grp or mz.startswith(fl)


def _summarize_raw(type_key, rows, *, group_by, zone, branch, dealer_code,
                   min_count, max_count, top, source, truncated, period,
                   dealers_only: bool = True) -> dict:
    notes = []
    # Branch isn't in the API payload - it only exists in the CSV.
    if branch or group_by == "branch":
        notes.append("Branch isn't available from the API without the dealer "
                     "directory; branch grouping/filtering is off in API-only mode.")
        branch = None
        if group_by == "branch":
            group_by = "dealer"

    dealer_code = (dealer_code or "").strip().upper() or None
    per_key: dict[str, int] = defaultdict(int)
    zone_of: dict[str, str] = {}
    counted = 0
    null_code = 0
    excluded_non_dealer = 0

    for row in rows:
        code = str(row.get("Merchant_Code") or "").strip()
        mz = str(row.get("m_zone") or "").strip()
        # Default: count only real channel partners (C+15-digit code) in real
        # zones (North/South/East/West). Drops internal/test/customer accounts.
        if dealers_only and not (is_real_dealer_code(code) and is_real_zone(mz)):
            excluded_non_dealer += 1
            continue
        if zone and not _raw_zone_matches(mz, zone):
            continue
        if dealer_code and code.upper() != dealer_code:
            continue
        counted += 1
        if not code:
            null_code += 1
        if group_by == "zone":
            per_key[mz or "(no zone)"] += 1
        else:  # dealer, keyed by Merchant_Code
            key = code or "(no code)"
            per_key[key] += 1
            zone_of[key] = mz

    items = []
    for k, c in per_key.items():
        if not _passes(c, min_count, max_count):
            continue
        if group_by == "zone":
            items.append({"zone": k, "count": c})
        else:
            items.append({"dealer_code": k, "m_zone": zone_of.get(k, ""), "count": c})
    items.sort(key=lambda r: r["count"], reverse=(max_count is None))

    result = {
        "mode": "api_only",
        "type": type_key,
        "group_by": group_by,
        "period": period or {},
        "filters": {k: v for k, v in (("zone", zone), ("dealer_code", dealer_code)) if v},
        "total_rows": len(rows),
        "counted_rows": counted,
        "dealers_only": dealers_only,
        "excluded_non_dealer_rows": excluded_non_dealer,
        f"matched_{'groups' if group_by == 'zone' else 'dealers'}": len(items),
        "rows": items[:top],
        "samples": [_sample(r) for r in rows[:5]],
        "source": source,
        "truncated": truncated,
    }
    if dealers_only:
        note = (f"Counting real channel partners only: {counted} rows from dealer "
                f"codes (C+digits) in real zones (North/South/East/West). Excluded "
                f"{excluded_non_dealer} rows from internal / test / customer / "
                f"employee accounts. (Set include_non_dealers=true to include them.) ")
    else:
        note = ("Including ALL accounts (internal/test/customer not filtered out). ")
    note += ("API-only mode: no dealer-directory (CSV) cross-reference, so dealers are "
             "shown by Merchant_Code and the zone the API reports; dealer names, branch, "
             "and inactive-dealer detection need the CSV.")
    if notes:
        note += " " + " ".join(notes)
    result["note"] = note
    return result


# --------------------------------------------------------------------------
# Directory mode - CSV cross-reference (enable with QUICKLOOK_USE_DIRECTORY=1)
# --------------------------------------------------------------------------

def _classify(row: dict) -> tuple[str, dict | None]:
    code = str(row.get("Merchant_Code") or "").strip()
    if not code:
        return "unknown", None
    dealer = dealer_directory.lookup(code)
    if dealer:
        return "mapped", dealer
    if str(row.get("m_zone") or "").strip().lower() == "employee" or not code.upper().startswith("C"):
        return "employee", None
    return "unmapped", None


def _dealer_row(d: dict) -> dict:
    return {
        "dealer_name": d["dealer_name"],
        "dealer_code": d["dealer_code"],
        "branch": d["branch"],
        "zone": d["zone"],
    }


def _summarize_with_directory(type_key, rows, *, group_by, zone, branch, dealer_code,
                              min_count, max_count, include_zero, top, source,
                              truncated, period) -> dict:
    coverage = {"mapped_rows": 0, "unmapped_rows": 0, "employee_rows": 0, "unknown_rows": 0}
    per_dealer: dict[str, int] = defaultdict(int)
    dealer_code = (dealer_code or "").strip().upper() or None

    for row in rows:
        bucket, dealer = _classify(row)
        coverage[f"{bucket}_rows"] += 1
        if bucket != "mapped":
            continue
        if zone and not dealer_directory.zone_matches(dealer, zone):
            continue
        if branch and not dealer_directory.branch_matches(dealer, branch):
            continue
        if dealer_code and dealer["dealer_code"] != dealer_code:
            continue
        per_dealer[dealer["dealer_code"]] += 1

    if include_zero is None:
        include_zero = False

    result: dict = {
        "mode": "directory",
        "type": type_key,
        "group_by": group_by,
        "period": period or {},
        "filters": {k: v for k, v in
                    (("zone", zone), ("branch", branch), ("dealer_code", dealer_code)) if v},
        "total_rows": len(rows),
        "attributed_rows": coverage["mapped_rows"],
        "coverage": coverage,
        "source": source,
        "truncated": truncated,
    }

    if group_by == "dealer":
        spine = dealer_directory.all_dealers(zone=zone, branch=branch) if include_zero else None
        counts: dict[str, dict] = {}
        if spine is not None:
            for d in spine:
                if dealer_code and d["dealer_code"] != dealer_code:
                    continue
                counts[d["dealer_code"]] = {**_dealer_row(d), "count": 0}
        for code, c in per_dealer.items():
            d = dealer_directory.lookup(code)
            counts.setdefault(code, {**_dealer_row(d), "count": 0})["count"] = c
        items = [v for v in counts.values() if _passes(v["count"], min_count, max_count)]
        items.sort(key=lambda r: (r["count"], r["dealer_name"]), reverse=(max_count is None))
        result["matched_dealers"] = len(items)
        result["rows"] = items[:top]
    else:  # branch or zone
        key = "branch" if group_by == "branch" else "zone_group"
        agg: dict[str, int] = defaultdict(int)
        for code, c in per_dealer.items():
            d = dealer_directory.lookup(code)
            agg[d[key] or "(blank)"] += c
        items = [{group_by: k, "count": v} for k, v in agg.items()
                 if _passes(v, min_count, max_count)]
        items.sort(key=lambda r: r["count"], reverse=(max_count is None))
        result["matched_groups"] = len(items)
        result["rows"] = items[:top]

    result["samples"] = [_sample(r) for r in rows[:5]]

    extra = coverage["unmapped_rows"] + coverage["employee_rows"] + coverage["unknown_rows"]
    if extra:
        result["note"] = (
            f"{coverage['mapped_rows']} of {len(rows)} rows matched a dealer in the "
            f"directory. Not attributed: {coverage['unmapped_rows']} from dealer codes "
            f"outside the directory, {coverage['employee_rows']} internal/employee, "
            f"{coverage['unknown_rows']} with no dealer code. Report these as a coverage "
            "gap; they are excluded from any zone/branch/dealer breakdown above."
        )
    return result
