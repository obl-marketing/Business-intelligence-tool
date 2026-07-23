"""Persistent, per-user chat history.

Each user's conversations live in their own file: chats/<user>.json, so
one person's chats are never visible to another. Every public function
takes a `user` (the logged-in email) to scope storage.

Training/knowledge data is NOT here - that stays shared across everyone
(see knowledge_base.py).
"""
from __future__ import annotations
import datetime as _dt
import json
import os
import re
import uuid

import persistent_store

CHATS_DIR = os.environ.get("CHATS_DIR", "chats")


def _now() -> str:
    return _dt.datetime.utcnow().isoformat(timespec="microseconds") + "Z"


def _safe(user: str) -> str:
    """Turn an email into a safe filename fragment."""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (user or "anon").strip().lower()).strip("_")
    return s or "anon"


def _file(user: str) -> str:
    return os.path.join(CHATS_DIR, f"{_safe(user)}.json")


def _ensure_dir() -> None:
    os.makedirs(CHATS_DIR, exist_ok=True)


def _serialize_blocks(blocks: list[dict]) -> list[dict]:
    return [dict(b) for b in blocks]


def load_all(user: str) -> list[dict]:
    data = persistent_store.read_json(_file(user), default=[])
    return data if isinstance(data, list) else []


def _save_all(user: str, chats: list[dict]) -> None:
    _ensure_dir()
    persistent_store.write_json(
        _file(user), chats,
        message=f"STARS: chats for {_safe(user)} ({len(chats)})",
    )


def list_chats(user: str) -> list[dict]:
    out = []
    for c in load_all(user):
        out.append({
            "id": c["id"],
            "title": c.get("title") or "Untitled chat",
            "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at") or c.get("created_at"),
            "message_count": len(c.get("messages", [])),
        })
    out.sort(key=lambda c: c["updated_at"] or "", reverse=True)
    return out


def load_chat(chat_id: str, user: str) -> dict | None:
    for c in load_all(user):
        if c["id"] == chat_id:
            return c
    return None


def new_chat(user: str) -> str:
    chat_id = uuid.uuid4().hex[:12]
    chats = load_all(user)
    chats.append({
        "id": chat_id, "title": "New chat",
        "created_at": _now(), "updated_at": _now(), "messages": [],
    })
    _save_all(user, chats)
    return chat_id


def save_chat(chat_id: str, messages: list[dict], user: str, title: str | None = None) -> None:
    chats = load_all(user)
    found = False
    for c in chats:
        if c["id"] == chat_id:
            c["messages"] = [
                {"role": m["role"], "content": m["content"],
                 "display_blocks": _serialize_blocks(m.get("display_blocks", []))}
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
            "created_at": _now(), "updated_at": _now(),
            "messages": [
                {"role": m["role"], "content": m["content"],
                 "display_blocks": _serialize_blocks(m.get("display_blocks", []))}
                for m in messages
            ],
        })
    _save_all(user, chats)


def rename_chat(chat_id: str, title: str, user: str) -> None:
    chats = load_all(user)
    for c in chats:
        if c["id"] == chat_id:
            c["title"] = title.strip() or "Untitled chat"
            c["updated_at"] = _now()
    _save_all(user, chats)


def delete_chat(chat_id: str, user: str) -> None:
    chats = [c for c in load_all(user) if c["id"] != chat_id]
    _save_all(user, chats)


def auto_title(messages: list[dict]) -> str:
    for m in messages:
        if m["role"] == "user":
            txt = re.sub(r"\s+", " ", (m.get("content") or "")).strip()
            return (txt[:60] + "…") if len(txt) > 60 else (txt or "New chat")
    return "New chat"
