"""Zoho CRM Opportunities ("ChatbotDeals") — fetch + aggregate.

Pulls opportunity records from the custom Zoho endpoint and answers dealer/FLS
sales questions: Closed Won counts, branch/zone/FLS/CP breakdowns, and FLS
lead-hygiene (which reps have incomplete records).

The record already carries stage, amount, volume, zone, branch_Area,
sales_Person (FLS), and assigned_CP (dealer) — so no CSV cross-reference is
needed. Live rows come from zoho_client; mock rows from mock_zoho, both the
same shape.

Endpoint path is configurable via ZOHO_OPPORTUNITIES_PATH (default
'api/OBL_Zoho/ChatbotDeals') so a spelling/version change needs no redeploy.
"""
from __future__ import annotations
import os
from collections import defaultdict

DEFAULT_PATH = "api/OBL_Zoho/ChatbotDeals"

# group_by value -> (record field, human label)
_DIMS = {
    "branch": ("branch_Area", "branch"),
    "zone": ("zone", "zone"),
    "fls": ("sales_Person_Name", "fls"),
    "sales_person": ("sales_Person_Name", "fls"),
    "cp": ("assigned_CP_Name", "cp"),
    "dealer": ("assigned_CP_Name", "cp"),
    "stage": ("stage", "stage"),
}
DATE_FIELDS = {"closing": "closing_Date", "created": "created_Time",
               "allocated": "cP_Allocated_Date"}


def _path() -> str:
    return (os.environ.get("ZOHO_OPPORTUNITIES_PATH") or DEFAULT_PATH).strip()


def fetch(params: dict | None = None) -> list[dict]:
    """Fetch opportunity records live from Zoho. Returns the `response` list."""
    import zoho_client
    payload = zoho_client.get(_path(), params=params)
    if isinstance(payload, dict):
        data = payload.get("response") or payload.get("data") or []
    elif isinstance(payload, list):
        data = payload
    else:
        data = []
    return [r for r in data if isinstance(r, dict)]


def _day(rec: dict, date_field: str) -> str:
    return str(rec.get(date_field) or "")[:10]  # 'YYYY-MM-DD' prefix


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _is_incomplete(rec: dict) -> list[str]:
    """Reasons a record looks poorly-updated (missing key fields)."""
    missing = []
    if not _num(rec.get("amount")):
        missing.append("amount")
    if not rec.get("category") and not rec.get("category_Details_from_APP"):
        missing.append("category")
    if not rec.get("sizes_Shortlisted"):
        missing.append("sizes")
    return missing


def summarize(records: list[dict], *, stage: str | None = None,
              date_field: str = "closing", start_date: str | None = None,
              end_date: str | None = None, group_by: str = "branch",
              top: int = 100, source: str = "mock") -> dict:
    df = DATE_FIELDS.get(date_field, "closing_Date")
    stage_l = (stage or "").strip().lower()

    kept = []
    for rec in records:
        if stage_l and str(rec.get("stage") or "").strip().lower() != stage_l:
            continue
        d = _day(rec, df)
        if start_date and d and d < start_date:
            continue
        if end_date and d and d > end_date:
            continue
        kept.append(rec)

    dim_field, dim_label = _DIMS.get(group_by, ("branch_Area", "branch"))
    agg: dict[str, dict] = defaultdict(lambda: {"count": 0, "amount": 0.0, "volume": 0.0})
    for rec in kept:
        key = str(rec.get(dim_field) or "(blank)")
        a = agg[key]
        a["count"] += 1
        a["amount"] += _num(rec.get("amount"))
        a["volume"] += _num(rec.get("volume_In_Sq_Mtr"))

    rows = [{dim_label: k, "count": v["count"],
             "amount": round(v["amount"], 2), "volume_sq_mtr": round(v["volume"], 2)}
            for k, v in agg.items()]
    rows.sort(key=lambda r: r["count"], reverse=True)

    return {
        "type": "opportunities",
        "filters": {k: v for k, v in
                    (("stage", stage), ("date_field", df),
                     ("start_date", start_date), ("end_date", end_date)) if v},
        "group_by": dim_label,
        "total_deals": len(kept),
        "total_amount": round(sum(_num(r.get("amount")) for r in kept), 2),
        "total_volume_sq_mtr": round(sum(_num(r.get("volume_In_Sq_Mtr")) for r in kept), 2),
        "rows": rows[:top],
        "records_scanned": len(records),
        "source": source,
        "samples": [{k: rec.get(k) for k in
                     ("deal_Name", "stage", "amount", "branch_Area", "zone",
                      "sales_Person_Name", "assigned_CP_Name", "closing_Date")}
                    for rec in kept[:5]],
    }


def fls_hygiene(records: list[dict], *, stage: str | None = None,
                date_field: str = "closing", start_date: str | None = None,
                end_date: str | None = None, top: int = 100,
                source: str = "mock") -> dict:
    """Per-FLS lead-quality: total deals and how many are incomplete (missing
    amount / category / sizes). Ranks the poorest updaters first. NOTE: this is
    the 'incomplete data' definition; 'stale / not touched in N days' would need
    a last-modified field, which this endpoint doesn't return."""
    df = DATE_FIELDS.get(date_field, "closing_Date")
    stage_l = (stage or "").strip().lower()

    per_fls: dict[str, dict] = defaultdict(lambda: {"total": 0, "incomplete": 0,
                                                    "missing": defaultdict(int),
                                                    "emp_id": ""})
    for rec in records:
        if stage_l and str(rec.get("stage") or "").strip().lower() != stage_l:
            continue
        d = _day(rec, df)
        if start_date and d and d < start_date:
            continue
        if end_date and d and d > end_date:
            continue
        fls = str(rec.get("sales_Person_Name") or "(unassigned)")
        e = per_fls[fls]
        e["total"] += 1
        e["emp_id"] = rec.get("sales_Person_Emp_ID") or e["emp_id"]
        miss = _is_incomplete(rec)
        if miss:
            e["incomplete"] += 1
            for m in miss:
                e["missing"][m] += 1

    rows = []
    for fls, e in per_fls.items():
        pct = round(e["incomplete"] / e["total"] * 100, 1) if e["total"] else 0.0
        rows.append({
            "fls": fls, "emp_id": e["emp_id"], "deals": e["total"],
            "incomplete": e["incomplete"], "incomplete_pct": pct,
            "missing_breakdown": dict(e["missing"]),
        })
    # poorest updaters first: highest incomplete %, then most deals
    rows.sort(key=lambda r: (r["incomplete_pct"], r["deals"]), reverse=True)

    return {
        "type": "fls_lead_hygiene",
        "definition": "incomplete = record missing amount, category, or sizes",
        "filters": {k: v for k, v in
                    (("stage", stage), ("start_date", start_date),
                     ("end_date", end_date)) if v},
        "fls_count": len(rows),
        "rows": rows[:top],
        "source": source,
    }
