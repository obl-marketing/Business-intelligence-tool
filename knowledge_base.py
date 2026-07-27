"""Self-learning knowledge base.

The user feeds the tool ground-truth context about their business -
form definitions, PLP/category page behavior, chatbot flows, event
definitions, and freeform learnings - optionally with screenshots.
Everything here is injected into the agent as authoritative context.

Storage: a single JSON file with images stored inline as base64, so the
whole knowledge base is one portable file you can export, back up, or
commit to the repo for permanence (Streamlit Cloud's disk is ephemeral
and resets on reboot - export or commit to keep entries).
"""
from __future__ import annotations
import base64
import datetime as _dt
import json
import os
import uuid
from typing import Any

import persistent_store

KB_DIR = os.environ.get("KB_DIR", "knowledge")
KB_FILE = os.path.join(KB_DIR, "entries.json")

CATEGORIES = [
    "Forms",
    "Product Listing Pages (PLP)",
    "Category Pages",
    "Chatbot",
    "Event Definitions",
    "General Learning",
]

# Cap how many screenshots we attach to the model per request (token budget)
MAX_IMAGES_IN_CONTEXT = 8


def _ensure_dir() -> None:
    os.makedirs(KB_DIR, exist_ok=True)


def load_entries() -> list[dict[str, Any]]:
    return persistent_store.read_json(KB_FILE, default=[])


def _save(entries: list[dict]) -> None:
    _ensure_dir()
    persistent_store.write_json(KB_FILE, entries,
                                message=f"STARS: knowledge ({len(entries)} entries)")


def add_entry(
    category: str,
    title: str,
    description: str,
    image_bytes: bytes | None = None,
    image_mime: str | None = None,
    datasets: list[dict] | None = None,
) -> dict:
    """Add a knowledge entry.

    `datasets` (optional) attaches the FULL contents of an uploaded
    spreadsheet/CSV as real data - each item is
    {"name": str, "csv": str, "rows": int, "cols": int}. Unlike the old
    text-extraction path, this is NOT truncated: the whole table is stored
    and later loaded into the pandas analysis engine, so every row is
    analysable for all users in all future chats.
    """
    entries = load_entries()
    entry = {
        "id": uuid.uuid4().hex[:12],
        "category": category,
        "title": title.strip(),
        "description": description.strip(),
        "created_at": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "image_b64": base64.b64encode(image_bytes).decode() if image_bytes else None,
        "image_mime": image_mime if image_bytes else None,
        "datasets": datasets or [],
    }
    entries.append(entry)
    _save(entries)
    return entry


def training_dataset_signature() -> tuple:
    """A cheap fingerprint of all attached datasets, so the UI can cache the
    parsed DataFrames and only re-parse when something actually changes."""
    sig = []
    for e in load_entries():
        for d in e.get("datasets", []) or []:
            sig.append((e["id"], d.get("name", ""), len(d.get("csv", ""))))
    return tuple(sig)


def training_dataframes() -> list[dict]:
    """Parse every attached Training dataset into a pandas DataFrame.

    Returns a list of {"label", "df", "rows", "cols"} - the `label` includes
    the entry title so the model knows what each dataset represents. These are
    fed to the analyze_data engine alongside any in-chat uploads.
    """
    import io
    import pandas as pd

    out = []
    for e in load_entries():
        for d in e.get("datasets", []) or []:
            csv = d.get("csv")
            if not csv:
                continue
            try:
                df = pd.read_csv(io.StringIO(csv))
            except Exception:
                continue
            label = f"{e['title']} — {d.get('name', 'data')} (Training)"
            out.append({"label": label, "df": df,
                        "rows": int(df.shape[0]), "cols": int(df.shape[1])})
    return out


def delete_entry(entry_id: str) -> None:
    entries = [e for e in load_entries() if e["id"] != entry_id]
    _save(entries)


def clear_all() -> None:
    _save([])


def export_json() -> str:
    return json.dumps(load_entries(), indent=2)


def import_json(raw: str, merge: bool = True) -> int:
    """Import entries from a JSON string. Returns number imported."""
    incoming = json.loads(raw)
    if not isinstance(incoming, list):
        raise ValueError("Expected a JSON list of entries.")
    existing = load_entries() if merge else []
    existing_ids = {e["id"] for e in existing}
    added = 0
    for e in incoming:
        if "id" not in e:
            e["id"] = uuid.uuid4().hex[:12]
        if e["id"] in existing_ids:
            continue
        existing.append(e)
        existing_ids.add(e["id"])
        added += 1
    _save(existing)
    return added


def count() -> int:
    return len(load_entries())


def knowledge_prompt() -> str:
    """Assemble the text knowledge for injection into the system prompt."""
    entries = load_entries()
    if not entries:
        return ""

    by_cat: dict[str, list[dict]] = {}
    for e in entries:
        by_cat.setdefault(e["category"], []).append(e)

    lines = [
        "",
        "# VERIFIED BUSINESS CONTEXT (ground truth - provided by the user)",
        "",
        "The user has trained you with the following facts about their website and "
        "business. Treat these as AUTHORITATIVE - they override your assumptions. When "
        "they are relevant to a question, use them; when an entry has a screenshot, the "
        "user has also given you the image (see attached reference images).",
        "",
    ]
    for cat in CATEGORIES:
        items = by_cat.get(cat)
        if not items:
            continue
        lines.append(f"## {cat}")
        for e in items:
            has_img = " [screenshot attached]" if e.get("image_b64") else ""
            ds = e.get("datasets") or []
            has_data = (
                " [dataset attached: " +
                ", ".join(f"{d.get('name','data')} ({d.get('rows',0):,} rows)" for d in ds) +
                " — loaded for analyze_data]"
            ) if ds else ""
            lines.append(f"- **{e['title']}**{has_img}{has_data}: {e['description']}")
        lines.append("")

    if any(e.get("datasets") for e in entries):
        lines.append(
            "Some trained entries have full spreadsheet/CSV data attached. That data is "
            "loaded as pandas DataFrames (see the dataset summary in the user's message) - "
            "use the `analyze_data` tool to compute over it exactly, never estimate."
        )
        lines.append("")
    return "\n".join(lines)


def images_for_context() -> list[dict]:
    """Return up to MAX_IMAGES_IN_CONTEXT screenshots for the model.

    Each item: {"title", "category", "mime", "bytes"}.
    """
    out = []
    for e in load_entries():
        if e.get("image_b64"):
            out.append({
                "title": e["title"],
                "category": e["category"],
                "mime": e.get("image_mime") or "image/png",
                "bytes": base64.b64decode(e["image_b64"]),
            })
        if len(out) >= MAX_IMAGES_IN_CONTEXT:
            break
    return out
