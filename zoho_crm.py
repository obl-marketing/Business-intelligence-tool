"""Zoho CRM — Leads & Deals (Opportunities) analytics via the native COQL API.

Counts and breakdowns for website/Meta lead performance, pre- vs post-
qualification, sliced by source / sub-source / stage and by the three date
fields (created / modified / closing). Field API names are CONFIG (env overrides
below) so nothing is hard-coded to one property — defaults match Orient Bell's
CRM (Lead_Source, Sub_Source, Stage, Created_Time, Modified_Time, Closing_Date).

Counting rule (confirmed with the CRM admin): when a lead is qualified it is
flagged **Converted** and STAYS in the Leads module (it appears under "Converted
Leads"). So the Leads module already contains every lead, converted or not:
  - TOTAL leads (e.g. from Website) = count of the Leads module (do NOT add Deals,
    that double-counts the converted ones).
  - Deals/Opportunities = the post-qualification subset.
So Leads = pre-qualification universe, Deals = post-qualification; qualification
rate = Deals / Leads over the same window/source.

Live only when Zoho is configured (see zoho_client). Everything here is guarded;
callers get a clear note when Zoho isn't connected instead of an exception.
"""
from __future__ import annotations
import os

# ---- Field / module config (env overrides; defaults = Orient Bell CRM) ----
def _cfg(key: str, default: str) -> str:
    return (os.environ.get(key) or default).strip()

def _leads_module() -> str: return _cfg("ZOHO_LEADS_MODULE", "Leads")
def _deals_module() -> str: return _cfg("ZOHO_DEALS_MODULE", "Deals")
def _source_field() -> str: return _cfg("ZOHO_SOURCE_FIELD", "Lead_Source")
def _subsource_field() -> str: return _cfg("ZOHO_SUBSOURCE_FIELD", "Sub_Source")
def _stage_field() -> str: return _cfg("ZOHO_STAGE_FIELD", "Stage")

# date_field key -> API name. Closing_Date is a DATE; the others are DATETIME.
_DATE_FIELDS = {"created": "Created_Time", "modified": "Modified_Time",
                "closing": "Closing_Date"}
_DATETIME_KEYS = {"created", "modified"}

# Fields exposable to discover_values, per module.
_DISCOVERABLE = {"source": _source_field, "sub_source": _subsource_field,
                 "stage": _stage_field}


def is_active() -> bool:
    try:
        import zoho_client
        return zoho_client.is_active()
    except Exception:
        return False


def _q(value: str) -> str:
    """Quote a COQL string value, escaping single quotes."""
    return "'" + str(value).replace("'", "\\'") + "'"


def _date_where(date_field_key: str, start: str, end: str) -> str:
    field = _DATE_FIELDS.get(date_field_key, "Created_Time")
    if date_field_key in _DATETIME_KEYS:
        # Zoho datetime needs an ISO8601 offset; CRM is IST (+05:30).
        return (f"{field} between '{start}T00:00:00+05:30' "
                f"and '{end}T23:59:59+05:30'")
    return f"{field} between '{start}' and '{end}'"


def _in_or_eq(field: str, value) -> str:
    """`field = 'x'` for one value, `field in ('a','b')` for a list."""
    if isinstance(value, (list, tuple, set)):
        vals = [v for v in value if str(v).strip()]
        if not vals:
            return ""
        if len(vals) == 1:
            return f"{field} = {_q(vals[0])}"
        return f"{field} in (" + ", ".join(_q(v) for v in vals) + ")"
    return f"{field} = {_q(value)}"


def _where(date_field_key, start, end, *, source=None, sub_source=None, stage=None) -> str:
    parts = [_date_where(date_field_key, start, end)]
    if source:
        parts.append(_in_or_eq(_source_field(), source))
    if sub_source:
        parts.append(_in_or_eq(_subsource_field(), sub_source))
    if stage:
        parts.append(_in_or_eq(_stage_field(), stage))
    return " and ".join(p for p in parts if p)


def _coql(query: str) -> dict:
    import zoho_client
    return zoho_client.post("crm/v3/coql", {"select_query": query})


def _rows(query: str) -> list[dict]:
    payload = _coql(query)
    data = payload.get("data") if isinstance(payload, dict) else None
    return [r for r in (data or []) if isinstance(r, dict)]


def _num(x) -> int:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0


def _count_value(row: dict) -> int:
    """Pull the COUNT() value from an aggregate row regardless of the key name
    Zoho uses ('count', 'COUNT(id)', ...)."""
    for k, v in row.items():
        if "count" in k.lower():
            return _num(v)
    # fall back: first numeric-looking value
    for v in row.values():
        n = _num(v)
        if n:
            return n
    return 0


def _agg_count(module: str, where: str) -> int:
    rows = _rows(f"select COUNT(id) from {module} where {where}")
    return _count_value(rows[0]) if rows else 0


def _agg_breakdown(module: str, dim_field: str, where: str, limit: int = 200) -> list[dict]:
    q = (f"select {dim_field}, COUNT(id) from {module} where {where} "
         f"group by {dim_field} order by COUNT(id) desc limit {limit}")
    out = []
    for r in _rows(q):
        label = r.get(dim_field)
        out.append({"value": label if label not in (None, "") else "(blank)",
                    "count": _count_value(r)})
    out.sort(key=lambda x: x["count"], reverse=True)
    return out


def _guard() -> dict | None:
    if not is_active():
        return {"note": "Zoho CRM isn't connected yet. Set ZOHO_CLIENT_ID / "
                        "ZOHO_CLIENT_SECRET / ZOHO_REFRESH_TOKEN (+ ZOHO_ACCOUNTS_URL "
                        "for your data centre) in the server secrets."}
    return None


# ---------------------------------------------------------------
# Public: Leads
# ---------------------------------------------------------------

def leads(start: str, end: str, date_field: str = "created",
          source=None, sub_source=None, group_by: str | None = None) -> dict:
    """Leads count (or breakdown) over a window.

    date_field: 'created' (default) or 'modified'. group_by: None for a total,
    or 'source'/'sub_source' for a breakdown. Remember: this counts ALL leads
    including converted ones (they stay in Leads), so it's the true total.
    """
    if (g := _guard()):
        return g
    where = _where(date_field, start, end, source=source, sub_source=sub_source)
    base = {"module": _leads_module(), "date_field": _DATE_FIELDS.get(date_field),
            "window": {"start": start, "end": end},
            "filters": {"source": source, "sub_source": sub_source},
            "counting_note": "ALL leads incl. converted (converted leads stay in "
                             "Leads). This is the true total; do NOT add Deals."}
    if group_by in ("source", "sub_source"):
        dim = _source_field() if group_by == "source" else _subsource_field()
        rows = _agg_breakdown(_leads_module(), dim, where)
        return {**base, "group_by": group_by, "rows": rows,
                "total": sum(r["count"] for r in rows)}
    return {**base, "total_leads": _agg_count(_leads_module(), where)}


# ---------------------------------------------------------------
# Public: Deals (Opportunities)
# ---------------------------------------------------------------

def deals(start: str, end: str, date_field: str = "closing",
          source=None, sub_source=None, stage=None,
          group_by: str | None = None) -> dict:
    """Deals/Opportunities count (or breakdown). date_field: 'closing' (default),
    'created' or 'modified'. group_by: None, 'stage', 'source' or 'sub_source'.
    Deals are the post-qualification subset of leads."""
    if (g := _guard()):
        return g
    where = _where(date_field, start, end, source=source, sub_source=sub_source, stage=stage)
    base = {"module": _deals_module(), "date_field": _DATE_FIELDS.get(date_field),
            "window": {"start": start, "end": end},
            "filters": {"source": source, "sub_source": sub_source, "stage": stage},
            "counting_note": "Deals = post-qualification (leads that became "
                             "opportunities). Qualification rate = Deals / Leads "
                             "for the same window & source."}
    if group_by in ("stage", "source", "sub_source"):
        dim = {"stage": _stage_field(), "source": _source_field(),
               "sub_source": _subsource_field()}[group_by]
        rows = _agg_breakdown(_deals_module(), dim, where)
        return {**base, "group_by": group_by, "rows": rows,
                "total": sum(r["count"] for r in rows)}
    return {**base, "total_deals": _agg_count(_deals_module(), where)}


# ---------------------------------------------------------------
# Public: discover the real values (so we don't hard-code 'Website' etc.)
# ---------------------------------------------------------------

def discover_values(module: str = "leads", field: str = "source",
                    start: str | None = None, end: str | None = None) -> dict:
    """List the distinct values (with counts) for a source / sub_source / stage
    field, so we can see EXACTLY how this CRM spells 'Website', 'Meta', etc.
    instead of guessing. Defaults to the last 90 days if no window given."""
    if (g := _guard()):
        return g
    import datetime as _dt
    if not (start and end):
        today = _dt.date.today()
        start = start or (today - _dt.timedelta(days=90)).isoformat()
        end = end or today.isoformat()
    mod = _deals_module() if module.lower().startswith("deal") else _leads_module()
    getter = _DISCOVERABLE.get(field)
    if not getter:
        return {"error": f"Unknown field '{field}'. Use source, sub_source or stage."}
    dim = getter()
    # date field: leads/deals both have Created_Time
    where = _date_where("created", start, end)
    rows = _agg_breakdown(mod, dim, where)
    return {"module": mod, "field": dim, "window": {"start": start, "end": end},
            "distinct_values": rows,
            "note": "Use these exact values when filtering by source/stage."}
