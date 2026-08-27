"""One-off probe: is `item_no` in the design-share API body, and how many
unique values come through for a given month?

Run this ON THE STARS SERVER (the QuickLook host is internal to the OBL
network and needs QUICKLOOK_API_TOKEN set).

    python quicklook_item_probe.py                # last calendar month
    python quicklook_item_probe.py 2026-07-01 2026-07-31

What it does, without touching the normal pipeline (which discards tiles[]):
  1. Pages through raw `design` records for the range, keeping the FULL record
     (source_row + tiles[]).
  2. Prints one full sample record so we can see exactly where item_no lives.
  3. Recursively finds every `item_no`-like key (item_no / item_number /
     itemno / ItemNo ...) anywhere in each record and reports:
       - total item_no values seen
       - unique item_no count
       - which JSON path(s) they were found on (e.g. tiles[].item_no)
"""
from __future__ import annotations
import calendar
import datetime as _dt
import json
import re
import sys

import quicklook_client as qc

MAX_PERPAGE = qc.MAX_PERPAGE
ITEM_KEY_RE = re.compile(r"^item[_ ]?(no|number|id|code)$", re.IGNORECASE)


def last_month_range(today: _dt.date) -> tuple[str, str]:
    first_this = today.replace(day=1)
    last_prev = first_this - _dt.timedelta(days=1)
    start = last_prev.replace(day=1)
    end = last_prev.replace(day=calendar.monthrange(last_prev.year, last_prev.month)[1])
    return start.isoformat(), end.isoformat()


def find_item_nos(obj, path="", found=None):
    """Recursively collect (json_path, value) for every item_no-like key."""
    if found is None:
        found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if ITEM_KEY_RE.match(str(k)) and not isinstance(v, (dict, list)):
                found.append((f"{path}.{k}".lstrip("."), v))
            else:
                find_item_nos(v, f"{path}.{k}".lstrip("."), found)
    elif isinstance(obj, list):
        for it in obj:
            find_item_nos(it, f"{path}[]", found)
    return found


def full_records(start_date: str, end_date: str, cap: int = 100_000):
    """Page through raw design records keeping the WHOLE record (tiles[] and all)."""
    out = []
    page = 1
    while len(out) < cap:
        payload = qc._post_page("design", start_date, end_date, page, MAX_PERPAGE)
        data = payload.get("data") or []
        out.extend(data)
        if len(data) < MAX_PERPAGE:
            break
        page += 1
    return out[:cap]


def main() -> None:
    if len(sys.argv) >= 3:
        start, end = sys.argv[1], sys.argv[2]
    else:
        start, end = last_month_range(_dt.date.today())

    if not qc.is_configured():
        print("QUICKLOOK_API_TOKEN is not set. Run this on the STARS server.")
        sys.exit(1)

    print(f"Endpoint : {qc._endpoint()}")
    print(f"Type     : design")
    print(f"Range    : {start} .. {end}\n")

    records = full_records(start, end)
    print(f"design records fetched: {len(records)}\n")
    if not records:
        print("No records returned for this range.")
        return

    # 1) Show one full record so we can see the real structure.
    print("=== sample record (full, first one) ===")
    print(json.dumps(records[0], indent=2, ensure_ascii=False, default=str)[:4000])
    print("=== end sample ===\n")

    # Show where item_no shows up in that first record specifically.
    sample_hits = find_item_nos(records[0])
    if sample_hits:
        paths = sorted({p for p, _ in sample_hits})
        print(f"item_no-like keys in the sample record, at path(s): {paths}\n")
    else:
        print("No item_no-like key found in the sample record.\n")

    # 2) Count across all records.
    all_values = []
    path_counts: dict[str, int] = {}
    records_with_item = 0
    for rec in records:
        hits = find_item_nos(rec)
        if hits:
            records_with_item += 1
        for p, v in hits:
            path_counts[p] = path_counts.get(p, 0) + 1
            if v is not None and str(v).strip() != "":
                all_values.append(str(v).strip())

    uniq = sorted(set(all_values))
    print("=== item_no summary for the range ===")
    print(f"records containing an item_no-like field : {records_with_item} / {len(records)}")
    print(f"total item_no values seen                : {len(all_values)}")
    print(f"UNIQUE item_no values                    : {len(uniq)}")
    if path_counts:
        print("found on paths (value count per path):")
        for p, c in sorted(path_counts.items(), key=lambda kv: -kv[1]):
            print(f"    {p}: {c}")
    if uniq:
        preview = uniq[:20]
        print(f"first {len(preview)} unique values: {preview}")


if __name__ == "__main__":
    main()
