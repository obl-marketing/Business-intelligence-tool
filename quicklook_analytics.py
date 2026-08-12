"""Join QuickLook activity rows to the dealer directory and aggregate.

Works identically on live rows (from quicklook_client) and mock rows
(from mock_quicklook) - both are lists of `source_row` dicts that carry at
least a Merchant_Code. This is where all counting happens, since the API only
returns raw rows.

Reconciliation is explicit: every row lands in exactly one bucket -
  mapped   : Merchant_Code is a dealer in the directory
  unmapped : has a C-dealer-code but it's not in our directory (partial list)
  employee : a non-dealer / internal code (e.g. m_zone == "Employee")
  unknown  : Merchant_Code is null (the API couldn't map the user)
so a total always reconciles to mapped + unmapped + employee + unknown.
"""
from __future__ import annotations
from collections import defaultdict

import dealer_directory

GROUP_BYS = ("dealer", "branch", "zone")


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
              period: dict | None = None) -> dict:
    if group_by not in GROUP_BYS:
        group_by = "dealer"

    # Bucket + per-dealer counts (only mapped rows can be attributed).
    coverage = {"mapped_rows": 0, "unmapped_rows": 0, "employee_rows": 0, "unknown_rows": 0}
    per_dealer: dict[str, int] = defaultdict(int)
    dealer_code = (dealer_code or "").strip().upper() or None

    for row in rows:
        bucket, dealer = _classify(row)
        coverage[f"{bucket}_rows"] += 1
        if bucket != "mapped":
            continue
        # apply filters (they only make sense against mapped dealers)
        if zone and not dealer_directory.zone_matches(dealer, zone):
            continue
        if branch and not dealer_directory.branch_matches(dealer, branch):
            continue
        if dealer_code and dealer["dealer_code"] != dealer_code:
            continue
        per_dealer[dealer["dealer_code"]] += 1

    # Whether to include dealers with ZERO activity. Off by default: "fewer
    # than 5 sessions" means light-but-active dealers (1-4), and a dealer who
    # did nothing is a separate "inactive / never used" group. Turn this on
    # explicitly (include_zero=True) to fold in or target zero-activity dealers
    # - e.g. include_zero=True + max_count=0 lists dealers who did nothing.
    if include_zero is None:
        include_zero = False

    def _passes(c: int) -> bool:
        if min_count is not None and c < min_count:
            return False
        if max_count is not None and c > max_count:
            return False
        return True

    result: dict = {
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
        items = [v for v in counts.values() if _passes(v["count"])]
        # low-side questions (max_count) read best ascending; else biggest first
        items.sort(key=lambda r: (r["count"], r["dealer_name"]),
                   reverse=(max_count is None))
        result["matched_dealers"] = len(items)
        result["rows"] = items[:top]

    else:  # branch or zone
        key = "branch" if group_by == "branch" else "zone_group"
        agg: dict[str, int] = defaultdict(int)
        for code, c in per_dealer.items():
            d = dealer_directory.lookup(code)
            agg[d[key] or "(blank)"] += c
        items = [{group_by: k, "count": v} for k, v in agg.items() if _passes(v)]
        items.sort(key=lambda r: r["count"], reverse=(max_count is None))
        result["matched_groups"] = len(items)
        result["rows"] = items[:top]

    # a few raw rows for context (transcripts, dates, etc.)
    result["samples"] = [_sample(r) for r in rows[:5]]

    # honest coverage note so counts always reconcile
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


def _dealer_row(d: dict) -> dict:
    return {
        "dealer_name": d["dealer_name"],
        "dealer_code": d["dealer_code"],
        "branch": d["branch"],
        "zone": d["zone"],
    }
