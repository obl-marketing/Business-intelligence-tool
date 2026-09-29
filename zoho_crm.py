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
import functools
import json
import os

# ---- Field / module config (env overrides; defaults = Orient Bell CRM) ----
def _cfg(key: str, default: str) -> str:
    return (os.environ.get(key) or default).strip()


@functools.lru_cache(maxsize=1)
def _catalog() -> dict:
    """The Deals/Opportunity field dictionary (label → api_name → meaning) built
    from the OBL field export. Lets STARS understand ALL ~100+ columns without the
    metadata scope. api_names flagged confirmed:false are derived from the label."""
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "data", "zoho_deals_fields.json")
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"fields": []}


@functools.lru_cache(maxsize=1)
def _label_map() -> dict:
    """Lower-cased human label → api_name, from the catalog."""
    return {f["label"].strip().lower(): f["api_name"]
            for f in _catalog().get("fields", []) if f.get("label") and f.get("api_name")}

def _leads_module() -> str: return _cfg("ZOHO_LEADS_MODULE", "Leads")
def _deals_module() -> str: return _cfg("ZOHO_DEALS_MODULE", "Deals")
def _source_field() -> str: return _cfg("ZOHO_SOURCE_FIELD", "Lead_Source")
def _subsource_field() -> str: return _cfg("ZOHO_SUBSOURCE_FIELD", "Sub_Source")
# Deals module spells it 'Sub_source' (confirmed via COQL probe); Leads uses 'Sub_Source'.
def _deals_subsource_field() -> str: return _cfg("ZOHO_DEALS_SUBSOURCE_FIELD", "Sub_source")
def _stage_field() -> str: return _cfg("ZOHO_STAGE_FIELD", "Stage")
def _owner_field() -> str: return _cfg("ZOHO_OWNER_FIELD", "Owner")
# Deals/Opportunity extras (API-name best guesses from the OBL field dictionary;
# override via env if discover_zoho_fields shows a different api_name).
def _salesperson_field() -> str: return _cfg("ZOHO_SALESPERSON_FIELD", "Sales_Person_Email_ID")
def _status_field() -> str: return _cfg("ZOHO_STATUS_FIELD", "Stage_Category")  # New/Active/Closed
def _dealer_field() -> str: return _cfg("ZOHO_DEALER_FIELD", "Assigned_CP_Name")
def _zone_field() -> str: return _cfg("ZOHO_ZONE_FIELD", "Zone")
def _branch_field() -> str: return _cfg("ZOHO_BRANCH_FIELD", "Branch_Area")

# date_field key -> API name. Closing_Date is a DATE; the others are DATETIME.
_DATE_FIELDS = {"created": "Created_Time", "modified": "Modified_Time",
                "closing": "Closing_Date"}
_DATETIME_KEYS = {"created", "modified"}

# Fields exposable to discover_values, per module.
_DISCOVERABLE = {"source": _source_field, "sub_source": _subsource_field,
                 "stage": _stage_field}


def _dim_field(group_by: str) -> str:
    """Resolve a group_by key to a field API name. Friendly keys map to their
    configured field; anything else is treated as a RAW field API name (e.g.
    a salesperson-email field discovered via discover_zoho_fields)."""
    known = {"source": _source_field(), "sub_source": _subsource_field(),
             "stage": _stage_field(), "owner": _owner_field(),
             "salesperson": _salesperson_field(), "status": _status_field(),
             "dealer": _dealer_field(), "zone": _zone_field(),
             "branch": _branch_field()}
    if group_by in known:
        return known[group_by]
    # A human label from the field catalog (e.g. 'Days Difference') → its api_name.
    return _label_map().get(str(group_by).strip().lower(), group_by)


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


def _where(date_field_key, start, end, *, source=None, sub_source=None, stage=None,
           sub_source_field=None) -> str:
    parts = [_date_where(date_field_key, start, end)]
    if source:
        parts.append(_in_or_eq(_source_field(), source))
    if sub_source:
        parts.append(_in_or_eq(sub_source_field or _subsource_field(), sub_source))
    if stage:
        parts.append(_in_or_eq(_stage_field(), stage))
    return " and ".join(p for p in parts if p)


def _api_version() -> str:
    # Zoho COQL endpoint version; v8 is current (override via ZOHO_API_VERSION).
    return (os.environ.get("ZOHO_API_VERSION") or "v8").strip().lstrip("/")


def _coql(query: str) -> dict:
    import zoho_client
    return zoho_client.post(f"crm/{_api_version()}/coql", {"select_query": query})


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
    # NB: Zoho COQL rejects the same aggregate in SELECT and ORDER BY
    # ("duplicate aggregate function"), so we sort in Python below instead.
    q = (f"select {dim_field}, COUNT(id) from {module} where {where} "
         f"group by {dim_field} limit {limit}")
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
# Measures: COUNT (default) and SUM of a numeric-ish field (Amount / Won_Amount /
# Volume). COQL SUM works on Currency fields; Volume is stored as text, so SUM
# there falls back to fetching the values and summing in Python.
# ---------------------------------------------------------------

_MEASURE_FIELDS = {"volume": "Volume_In_Sq_Mtr", "amount": "Amount",
                   "won_amount": "Won_Amount", "won amount": "Won_Amount",
                   "value": "Amount", "pipeline": "Amount"}


def _measure_field(key: str) -> str:
    k = (key or "").strip().lower()
    if k in _MEASURE_FIELDS:
        return _MEASURE_FIELDS[k]
    return _label_map().get(k, key)  # human label → api_name, else assume raw


def _numf(x) -> float:
    try:
        return float(str(x).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _agg_num(row: dict) -> float:
    for k, v in row.items():
        if "count" in k.lower() or "sum" in k.lower():
            return _numf(v)
    for v in row.values():
        n = _numf(v)
        if n:
            return n
    return 0.0


def _fetch_values(module: str, field: str, where: str, dims: list[str] | None = None,
                  cap: int = 60000) -> list[dict]:
    """Paginate COQL selecting `field` (+ optional dims) and return raw row dicts.
    Used to sum a text-stored numeric field (e.g. Volume) in Python."""
    sel = ", ".join((dims or []) + [field])
    out: list[dict] = []
    offset, page = 0, 2000
    while offset < cap:
        try:
            rows = _rows(f"select {sel} from {module} where {where} "
                         f"limit {offset}, {page}")
        except Exception:
            if offset == 0:  # offset pagination unsupported → grab first page only
                rows = _rows(f"select {sel} from {module} where {where} limit {page}")
                out += rows
            break
        if not rows:
            break
        out += rows
        if len(rows) < page:
            break
        offset += page
    return out


def _measure_total(module: str, where: str, metric: str, sum_field: str | None):
    if metric == "sum":
        try:
            rows = _rows(f"select SUM({sum_field}) from {module} where {where}")
            return round(_agg_num(rows[0]), 2) if rows else 0.0
        except Exception:
            key = sum_field.split(".")[-1]
            vals = _fetch_values(module, sum_field, where)
            return round(sum(_numf(r.get(key)) for r in vals), 2)
    return _agg_count(module, where)


def _measure_breakdown(module: str, dim: str, where: str, metric: str,
                       sum_field: str | None, limit: int = 200) -> list[dict]:
    if metric != "sum":
        return _agg_breakdown(module, dim, where, limit)
    try:
        rows = _rows(f"select {dim}, SUM({sum_field}) from {module} where {where} "
                     f"group by {dim} limit {limit}")
        dkey = dim.split(".")[-1]
        out = [{"value": (r.get(dkey) or "(blank)"), "value_sum": round(_agg_num(r), 2)}
               for r in rows]
    except Exception:  # SUM not allowed on this field → group-sum in Python
        dkey, fkey = dim.split(".")[-1], sum_field.split(".")[-1]
        agg: dict[str, float] = {}
        for r in _fetch_values(module, sum_field, where, dims=[dim]):
            k = r.get(dkey) or "(blank)"
            agg[k] = agg.get(k, 0.0) + _numf(r.get(fkey))
        out = [{"value": k, "value_sum": round(v, 2)} for k, v in agg.items()]
    out.sort(key=lambda x: x["value_sum"], reverse=True)
    return out


def _months(start: str, end: str) -> list[tuple]:
    """(label 'YYYY-MM', month_start, month_end) clipped to [start, end]."""
    import datetime as _dt
    d0 = _dt.date.fromisoformat(start); d1 = _dt.date.fromisoformat(end)
    out = []; y, m = d0.year, d0.month
    while (y, m) <= (d1.year, d1.month):
        ms = _dt.date(y, m, 1)
        nm = _dt.date(y + (m // 12), (m % 12) + 1, 1)
        me = nm - _dt.timedelta(days=1)
        out.append((ms.strftime("%Y-%m"), max(ms, d0).isoformat(), min(me, d1).isoformat()))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _run(module, start, end, *, date_field, sub_source_field, source, sub_source,
         stage, filters, group_by, metric, sum_field, base) -> dict:
    """Shared query engine: COUNT/SUM, arbitrary equality filters, and month or
    field group-by. Rebuilds the WHERE per month for time series."""
    metric = "sum" if (metric or "count").lower() == "sum" else "count"
    sum_api = _measure_field(sum_field) if metric == "sum" else None
    if metric == "sum" and not sum_api:
        return {**base, "error": "metric='sum' needs sum_field (e.g. volume, amount, won_amount)."}

    parts = []
    if source:
        parts.append(_in_or_eq(_source_field(), source))
    if sub_source:
        parts.append(_in_or_eq(sub_source_field, sub_source))
    if stage:
        parts.append(_in_or_eq(_stage_field(), stage))
    for k, v in (filters or {}).items():
        if v in (None, ""):
            continue
        fld = sub_source_field if k == "sub_source" else _dim_field(k)
        parts.append(_in_or_eq(fld, v))
    parts = [p for p in parts if p]

    def _where_for(ds, de):
        return " and ".join([_date_where(date_field, ds, de)] + parts)

    base = {**base, "metric": metric, "sum_field": sum_api,
            "applied_filters": {k: v for k, v in
                                (("source", source), ("sub_source", sub_source),
                                 ("stage", stage), *(filters or {}).items()) if v}}

    if group_by == "month":
        rows = [{"month": lbl, "value": _measure_total(module, _where_for(ms, me), metric, sum_api)}
                for lbl, ms, me in _months(start, end)]
        return {**base, "group_by": "month",
                "rows": rows, "total": round(sum(r["value"] for r in rows), 2)}

    where = _where_for(start, end)
    if group_by:
        dim = sub_source_field if group_by == "sub_source" else _dim_field(group_by)
        rows = _measure_breakdown(module, dim, where, metric, sum_api)
        vkey = "value_sum" if metric == "sum" else "count"
        return {**base, "group_by": group_by, "group_by_field": dim, "rows": rows,
                "total": round(sum(r.get(vkey, 0) for r in rows), 2)}
    return {**base, "total": _measure_total(module, where, metric, sum_api)}


# ---------------------------------------------------------------
# Public: Leads
# ---------------------------------------------------------------

def leads(start: str, end: str, date_field: str = "created",
          source=None, sub_source=None, group_by: str | None = None,
          metric: str = "count", sum_field: str | None = None,
          filters: dict | None = None) -> dict:
    """Leads count/sum (or breakdown) over a window.

    date_field: 'created' (default) or 'modified'. metric: 'count' (default) or
    'sum' (needs sum_field). group_by: None, 'month', 'source', 'sub_source', or
    any field/friendly-key. filters: equality filters on any field, e.g.
    {'zone':'North-1','salesperson':'x@y.com'}. Counts ALL leads incl. converted
    (they stay in Leads) - the true total; never add Deals."""
    if (g := _guard()):
        return g
    base = {"module": _leads_module(), "date_field": _DATE_FIELDS.get(date_field),
            "window": {"start": start, "end": end},
            "counting_note": "ALL leads incl. converted (converted leads stay in "
                             "Leads). This is the true total; do NOT add Deals."}
    return _run(_leads_module(), start, end, date_field=date_field,
                sub_source_field=_subsource_field(), source=source,
                sub_source=sub_source, stage=None, filters=filters,
                group_by=group_by, metric=metric, sum_field=sum_field, base=base)


# ---------------------------------------------------------------
# Public: Deals (Opportunities)
# ---------------------------------------------------------------

def deals(start: str, end: str, date_field: str = "closing",
          source=None, sub_source=None, stage=None,
          group_by: str | None = None, metric: str = "count",
          sum_field: str | None = None, filters: dict | None = None) -> dict:
    """Deals/Opportunities count/sum (or breakdown). date_field: 'closing'
    (default), 'created' or 'modified'. metric: 'count' (default) or 'sum' (needs
    sum_field, e.g. 'volume'/'amount'/'won_amount'). group_by: None, 'month',
    'stage', 'salesperson', 'status', 'dealer', 'zone', 'branch', or any field.
    filters: equality filters on any field, e.g. {'zone':'North-1'}. Deals =
    post-qualification subset of leads."""
    if (g := _guard()):
        return g
    base = {"module": _deals_module(), "date_field": _DATE_FIELDS.get(date_field),
            "window": {"start": start, "end": end},
            "counting_note": "Deals = post-qualification. Qualification rate = "
                             "Deals / Leads for the same window & source."}
    return _run(_deals_module(), start, end, date_field=date_field,
                sub_source_field=_deals_subsource_field(), source=source,
                sub_source=sub_source, stage=stage, filters=filters,
                group_by=group_by, metric=metric, sum_field=sum_field, base=base)


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


def discover_fields(module: str = "deals") -> dict:
    """List the REAL field API names of a module (Leads or Deals) via Zoho's
    fields metadata, so we can find the exact salesperson-email field, a status
    field, etc. instead of guessing - then pass that api_name as group_by to
    query_zoho_leads / query_zoho_deals. Falls back gracefully if the metadata
    scope isn't granted."""
    if (g := _guard()):
        return g
    import zoho_client
    mod = _deals_module() if module.lower().startswith("deal") else _leads_module()
    try:
        payload = zoho_client.get(f"crm/{_api_version()}/settings/fields",
                                  params={"module": mod})
    except Exception as exc:
        # Metadata scope missing → serve the baked field catalog (all ~100+ Deals
        # columns with meanings + api_names), so STARS understands every column
        # without any extra Zoho scope. (Leads has no catalog file → probe a few.)
        cat_fields = _catalog().get("fields", []) if mod == _deals_module() else []
        if cat_fields:
            low = lambda s: (s or "").lower()
            likely_sp = [f for f in cat_fields if any(
                k in low(f["label"]) + low(f["api_name"])
                for k in ("sales person", "salesperson", "owner", "assign", "agent", "fls"))]
            likely_status = [f for f in cat_fields if any(
                k in low(f["label"]) + low(f["api_name"])
                for k in ("stage", "status", "state"))]
            return {"module": mod, "method": "baked_catalog",
                    "field_count": len(cat_fields),
                    "likely_salesperson_fields": likely_sp,
                    "likely_status_fields": likely_status,
                    "fields": cat_fields,
                    "note": "Full Deals field catalog (label → api_name → meaning). "
                            "Pass any api_name (or its human label) as group_by / a "
                            "filters key / sum_field. api_names with confirmed:false are "
                            "derived from the label — if a query errors 'invalid column', "
                            "tell me and I'll correct that one."}
        # No catalog (e.g. Leads) → probe a few common candidates via COQL.
        def _field_works(cand: str) -> bool:
            try:
                _coql(f"select {cand} from {mod} where Created_Time > "
                      f"'2000-01-01T00:00:00+05:30' limit 1")
                return True
            except Exception:
                return False
        candidates = {
            "salesperson_email": ["Sales_Person_Email_ID", "Sales_Person_Email"],
            "status_new_active_closed": ["Stage_Category"],
            "source": ["Lead_Source"], "sub_source": ["Sub_Source", "Sub_source"],
            "stage": ["Stage"], "owner": ["Owner"],
        }
        resolved = {logical: next((c for c in cands if _field_works(c)), None)
                    for logical, cands in candidates.items()}
        return {"module": mod, "method": "coql_probe", "ok": True,
                "resolved_fields": resolved,
                "metadata_scope_skipped": "Zoho's field-metadata scope isn't granted, "
                    "so field names were resolved by live COQL probing instead. This is "
                    "EXPECTED and NOT an error — the resolved_fields below are valid and "
                    "usable. Do NOT tell the user Zoho is unauthorized or ask them to "
                    "re-authorize because of this.",
                "note": "Use any resolved api_name as group_by / a filters key. If one "
                        "came back null, tell me which and I'll probe alternatives — but "
                        "proceed with the query using the ones that resolved."}
    fields = payload.get("fields") or []
    all_fields = [{"api_name": f.get("api_name"), "label": f.get("field_label"),
                   "type": f.get("data_type")} for f in fields if f.get("api_name")]
    low = lambda s: (s or "").lower()
    likely_salesperson = [f for f in all_fields if any(
        k in low(f["api_name"]) + low(f["label"])
        for k in ("owner", "sales", "assign", "agent", "rep", "fls", "email"))]
    likely_status = [f for f in all_fields if any(
        k in low(f["api_name"]) + low(f["label"])
        for k in ("stage", "status", "state"))]
    return {"module": mod, "field_count": len(all_fields),
            "likely_salesperson_fields": likely_salesperson,
            "likely_status_fields": likely_status,
            "all_fields": all_fields,
            "note": "Pick the api_name of the salesperson-email field and pass it as "
                    "group_by to query_zoho_deals (e.g. group_by='Sales_Person_Email') "
                    "to see deals per rep. Same for a status field."}


def field_usage(module: str = "deals", fields: list | None = None,
                start: str | None = None, end: str | None = None,
                limit: int = 30) -> dict:
    """How POPULATED each field is (fill-rate = records with a non-empty value ÷
    total) over a window. Use this to prioritise fields that are actually used per
    lead/deal and avoid mostly-empty ones. If `fields` is omitted, checks the
    in-use catalog fields. Defaults to the last 90 days."""
    if (g := _guard()):
        return g
    import datetime as _dt
    if not (start and end):
        today = _dt.date.today()
        start = start or (today - _dt.timedelta(days=90)).isoformat()
        end = end or today.isoformat()
    mod = _deals_module() if module.lower().startswith("deal") else _leads_module()
    date_where = _date_where("created", start, end)

    def _resolve(f):
        api = _dim_field(f)              # friendly key / label → api_name
        return _measure_field(api) if api == f else api  # measure aliases too
    if fields:
        apis = [_resolve(f) for f in fields][:limit]
    else:
        apis = [f["api_name"] for f in _catalog().get("fields", [])
                if f.get("in_use") and f.get("api_name")][:limit]

    total = _agg_count(mod, date_where)
    rows = []
    for api in apis:
        try:
            n = _agg_count(mod, f"{date_where} and {api} is not null")
        except Exception:
            n = None  # field not COQL-filterable / bad api_name
        rows.append({"api_name": api, "populated": n,
                     "fill_pct": (round(n / total * 100, 1) if (n is not None and total) else None)})
    # populated first; unknown/empty last
    rows.sort(key=lambda r: (r["fill_pct"] is not None, r["fill_pct"] or 0), reverse=True)
    return {"module": mod, "window": {"start": start, "end": end},
            "total_records": total, "fields": rows,
            "note": "fill_pct = % of records where the field has a value. Prefer fields "
                    "with high fill_pct for filtering/grouping; near-0% fields are "
                    "effectively unused. fill_pct=null means the api_name isn't "
                    "COQL-filterable (may be wrong spelling)."}
