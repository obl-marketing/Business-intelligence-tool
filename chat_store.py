"""Persistent multi-conversation chat history (ChatGPT/Gemini style).

Stores all conversations in a single JSON file. Each conversation has:
  id, title, created_at, updated_at, messages[]

`messages` is the same shape used in st.session_state["messages"] -
a list of {"role", "content", "display_blocks"} entries, including
chart specs so re-rendering stays rich.

Streamlit Cloud's disk is ephemeral - export periodically or commit
the JSON to the repo for true permanence.
"""
from __future__ import annotations
import base64
import datetime as _dt
import json
import os
import re
import uuid

import persistent_store

CHATS_DIR = os.environ.get("CHATS_DIR", "chats")
CHATS_FILE = os.path.join(CHATS_DIR, "conversations.json")


def _now() -> str:
    return _dt.datetime.utcnow().isoformat(timespec="microseconds") + "Z"


def _ensure_dir() -> None:
    os.makedirs(CHATS_DIR, exist_ok=True)


def _serialize_blocks(blocks: list[dict]) -> list[dict]:
    """Make display_blocks JSON-safe. Chart specs and tool outputs are already
    plain dicts/strings; this is mostly a defensive deep-clone."""
    out = []
    for b in blocks:
        out.append({k: v for k, v in b.items()})
    return out


def load_all() -> list[dict]:
    data = persistent_store.read_json(CHATS_FILE, default=[])
    return data if isinstance(data, list) else []


def _save_all(chats: list[dict]) -> None:
    _ensure_dir()
    persistent_store.write_json(CHATS_FILE, chats,
                                message=f"STARS: chats ({len(chats)} conversations)")


def list_chats() -> list[dict]:
    """Metadata only, newest-first, for the sidebar list."""
    out = []
    for c in load_all():
        out.append({
            "id": c["id"],
            "title": c.get("title") or "Untitled chat",
            "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at") or c.get("created_at"),
            "message_count": len(c.get("messages", [])),
        })
    out.sort(key=lambda c: c["updated_at"] or "", reverse=True)
    return out


def load_chat(chat_id: str) -> dict | None:
    for c in load_all():
        if c["id"] == chat_id:
            return c
    return None


def new_chat() -> str:
    chat_id = uuid.uuid4().hex[:12]
    chats = load_all()
    chats.append({
        "id": chat_id,
        "title": "New chat",
        "created_at": _now(),
        "updated_at": _now(),
        "messages": [],
    })
    _save_all(chats)
    return chat_id


def save_chat(chat_id: str, messages: list[dict], title: str | None = None) -> None:
    chats = load_all()
    found = False
    for c in chats:
        if c["id"] == chat_id:
            c["messages"] = [
                {
                    "role": m["role"],
                    "content": m["content"],
                    "display_blocks": _serialize_blocks(m.get("display_blocks", [])),
                }
                for m in messages
            ]
            c["updated_at"] = _now()
            if title is not None:
                c["title"] = title
            elif (c.get("title") in (None, "", "New chat")) and messages:
                c["title"] = auto_title(messages)
            found = True
            break
    if not found:
        chats.append({
            "id": chat_id,
            "title": title or (auto_title(messages) if messages else "New chat"),
            "created_at": _now(),
            "updated_at": _now(),
            "messages": [
                {"role": m["role"], "content": m["content"],
                 "display_blocks": _serialize_blocks(m.get("display_blocks", []))}
                for m in messages
            ],
        })
    _save_all(chats)


def rename_chat(chat_id: str, title: str) -> None:
    chats = load_all()
    for c in chats:
        if c["id"] == chat_id:
            c["title"] = title.strip() or "Untitled chat"
            c["updated_at"] = _now()
    _save_all(chats)


def delete_chat(chat_id: str) -> None:
    chats = [c for c in load_all() if c["id"] != chat_id]
    _save_all(chats)


def clear_all() -> None:
    _save_all([])


def export_json() -> str:
    return json.dumps(load_all(), indent=2)


def import_json(raw: str, merge: bool = True) -> int:
    incoming = json.loads(raw)
    if not isinstance(incoming, list):
        raise ValueError("Expected a JSON list of conversations.")
    existing = load_all() if merge else []
    existing_ids = {c["id"] for c in existing}
    added = 0
    for c in incoming:
        if "id" not in c:
            c["id"] = uuid.uuid4().hex[:12]
        if c["id"] in existing_ids:
            continue
        existing.append(c)
        existing_ids.add(c["id"])
        added += 1
    _save_all(existing)
    return added


def auto_title(messages: list[dict]) -> str:
    """Pick a short title from the first user message."""
    for m in messages:
        if m["role"] == "user":
            txt = re.sub(r"\s+", " ", (m.get("content") or "")).strip()
            return (txt[:60] + "…") if len(txt) > 60 else (txt or "New chat")
    return "New chat"
