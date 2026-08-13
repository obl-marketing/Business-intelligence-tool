"""Live Meta (Facebook + Instagram) Ads client via the Marketing/Graph API.

Active when META_ACCESS_TOKEN and META_AD_ACCOUNT_ID are set (independent of
DATA_SOURCE). Returns the SAME shapes as the mock_data.meta_ads_* functions so
the agent-facing tools and schemas never change.

Conversions are account-specific. Meta returns an `actions` array per row; which
action_type counts as a "conversion" depends on the account's objective/pixel.
Set META_CONVERSION_ACTION to that type (default "purchase"; e.g. "lead",
"offsite_conversion.fb_pixel_purchase", "omni_purchase"). If it isn't present,
conversions / conversion_value / roas come back as 0.

Config (env / secrets, never committed):
    META_ACCESS_TOKEN=EAAB...            # long-lived token with ads_read
    META_AD_ACCOUNT_ID=act_1234567890    # 'act_' prefix optional
    META_API_VERSION=v21.0               # optional
    META_CONVERSION_ACTION=purchase      # optional
    META_ADS_ENABLED=1                   # optional; 0 forces demo
"""
from __future__ import annotations
import json
import os

GRAPH = "https://graph.facebook.com"


class MetaError(RuntimeError):
    pass


def _version() -> str:
    return (os.environ.get("META_API_VERSION") or "v21.0").strip()


def _account_id() -> str:
    a = (os.environ.get("META_AD_ACCOUNT_ID") or "").strip()
    if not a:
        return ""
    return a if a.startswith("act_") else f"act_{a}"


def _conv_action() -> str:
    return (os.environ.get("META_CONVERSION_ACTION") or "purchase").strip()


def is_configured() -> bool:
    return bool((os.environ.get("META_ACCESS_TOKEN") or "").strip()) and bool(_account_id())


def is_active() -> bool:
    if (os.environ.get("META_ADS_ENABLED", "1").strip().lower()) in ("0", "false", "no", "off"):
        return False
    return is_configured()


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _get(path: str, params: dict) -> dict:
    import httpx
    q = {**params, "access_token": os.environ["META_ACCESS_TOKEN"]}
    with httpx.Client(timeout=60.0) as client:
        resp = client.get(f"{GRAPH}/{_version()}/{path}", params=q)
    try:
        payload = resp.json()
    except Exception as exc:
        raise MetaError(f"Non-JSON response from Meta ({resp.status_code}).") from exc
    if isinstance(payload, dict) and payload.get("error"):
        err = payload["error"]
        raise MetaError(f"Meta API error: {err.get('message', err)}")
    resp.raise_for_status()
    return payload


def _paged(path: str, params: dict, cap: int = 5000) -> list[dict]:
    import httpx
    rows: list[dict] = []
    data = _get(path, params)
    while True:
        rows.extend(data.get("data", []) or [])
        nxt = (data.get("paging") or {}).get("next")
        if not nxt or len(rows) >= cap:
            break
        with httpx.Client(timeout=60.0) as client:
            data = client.get(nxt).json()
        if isinstance(data, dict) and data.get("error"):
            break
    return rows


def _actions_map(row: dict, key: str) -> dict:
    out: dict[str, float] = {}
    for a in (row.get(key) or []):
        out[a.get("action_type")] = _num(a.get("value"))
    return out


def _conv_and_value(row: dict) -> tuple[int, float]:
    ca = _conv_action()
    acts = _actions_map(row, "actions")
    vals = _actions_map(row, "action_values")
    conv = acts.get(ca, 0.0)
    if conv == 0.0:  # fall back to any matching action_type
        for k, v in acts.items():
            if k and (ca in k or "purchase" in k):
                conv = v
                break
    val = vals.get(ca, 0.0)
    if val == 0.0:
        for k, v in vals.items():
            if k and (ca in k or "purchase" in k):
                val = v
                break
    return int(conv), round(val, 2)


def _insights(level: str, start: str, end: str) -> list[dict]:
    fields = ["spend", "impressions", "reach", "frequency", "clicks", "ctr",
              "cpc", "cpm", "actions", "action_values", "objective"]
    if level == "campaign":
        fields = ["campaign_id", "campaign_name"] + fields
    elif level == "ad":
        fields = ["ad_id", "ad_name", "campaign_name"] + fields
    params = {
        "level": level,
        "fields": ",".join(fields),
        "time_range": json.dumps({"since": start, "until": end}),
        "limit": "200",
    }
    return _paged(f"{_account_id()}/insights", params)


def _campaign_status_map() -> dict:
    try:
        rows = _paged(f"{_account_id()}/campaigns",
                      {"fields": "id,name,effective_status", "limit": "200"})
    except Exception:
        return {}
    return {r.get("id"): ("active" if r.get("effective_status") == "ACTIVE" else "paused")
            for r in rows}


def meta_ads_campaigns(start: str, end: str) -> list[dict]:
    ins = _insights("campaign", start, end)
    statuses = _campaign_status_map()
    rows = []
    for r in ins:
        spend = _num(r.get("spend"))
        impr = int(_num(r.get("impressions")))
        reach = int(_num(r.get("reach")))
        clicks = int(_num(r.get("clicks")))
        conv, conv_value = _conv_and_value(r)
        rows.append({
            "campaign_name": r.get("campaign_name", ""),
            "objective": r.get("objective", ""),
            "status": statuses.get(r.get("campaign_id"), "unknown"),
            "spend": round(spend, 2),
            "impressions": impr,
            "reach": reach,
            "frequency": round(_num(r.get("frequency")), 2),
            "clicks": clicks,
            "conversions": conv,
            "conversion_value": conv_value,
            "ctr_pct": round(_num(r.get("ctr")), 2),      # Meta returns ctr as a %
            "cpm": round(_num(r.get("cpm")), 2),
            "cpc": round(_num(r.get("cpc")), 2),
            "cpa": round(spend / conv, 2) if conv else 0.0,
            "roas": round(conv_value / spend, 2) if spend else 0.0,
        })
    rows.sort(key=lambda x: x["spend"], reverse=True)
    return rows


def meta_ads_summary(start: str, end: str) -> dict:
    campaigns = meta_ads_campaigns(start, end)
    total_spend = sum(c["spend"] for c in campaigns)
    total_impr = sum(c["impressions"] for c in campaigns)
    total_clicks = sum(c["clicks"] for c in campaigns)
    total_conv = sum(c["conversions"] for c in campaigns)
    total_value = sum(c["conversion_value"] for c in campaigns)
    return {
        "date_range": {"start": start, "end": end},
        "spend": round(total_spend, 2),
        "impressions": total_impr,
        "clicks": total_clicks,
        "conversions": total_conv,
        "conversion_value": round(total_value, 2),
        "ctr_pct": round(total_clicks / total_impr * 100, 2) if total_impr else 0.0,
        "avg_cpc": round(total_spend / total_clicks, 2) if total_clicks else 0.0,
        "avg_cpm": round(total_spend / total_impr * 1000, 2) if total_impr else 0.0,
        "cpa": round(total_spend / total_conv, 2) if total_conv else 0.0,
        "roas": round(total_value / total_spend, 2) if total_spend else 0.0,
        "active_campaigns": sum(1 for c in campaigns if c["status"] == "active"),
    }


def meta_ads_creatives(start: str, end: str) -> list[dict]:
    """Per-ad performance. `format` is left blank in live mode (it requires an
    extra per-creative lookup); ROAS/CTR ranking still works without it."""
    ins = _insights("ad", start, end)
    rows = []
    for r in ins:
        spend = _num(r.get("spend"))
        if spend < 5:
            continue
        impr = int(_num(r.get("impressions")))
        clicks = int(_num(r.get("clicks")))
        conv, conv_value = _conv_and_value(r)
        rows.append({
            "creative_name": r.get("ad_name", ""),
            "format": "",
            "campaign": r.get("campaign_name", ""),
            "spend": round(spend, 2),
            "impressions": impr,
            "clicks": clicks,
            "ctr_pct": round(_num(r.get("ctr")), 2),
            "conversions": conv,
            "conversion_value": conv_value,
            "cpa": round(spend / conv, 2) if conv else 0.0,
            "roas": round(conv_value / spend, 2) if spend else 0.0,
        })
    rows.sort(key=lambda x: x["roas"], reverse=True)
    return rows
