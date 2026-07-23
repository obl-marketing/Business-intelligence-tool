"""Per-user password overrides, so users can change their own password.

The roster of WHO can log in stays in APP_USERS (admin-controlled). This
module stores each user's *current* password as a salted hash in a
users.json file next to the data folder - so a user's self-set password
survives restarts and code updates, and the plaintext seed password in
APP_USERS is only used until they change it.

Passwords are stored hashed (PBKDF2-HMAC-SHA256), never plaintext, and
never sent to GitHub - this file lives only on the server.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import secrets

_ITERATIONS = 200_000


def _users_file() -> str:
    # Store beside the chats data (persistent, outside the app code folder).
    chats_dir = os.environ.get("CHATS_DIR", "chats")
    if os.path.isabs(chats_dir):
        root = os.path.dirname(os.path.abspath(chats_dir))
    else:
        root = "."
    return os.path.join(root, "users.json")


def _load() -> dict:
    try:
        with open(_users_file()) as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    path = _users_file()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _hash(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), _ITERATIONS
    ).hex()


def has_override(email: str) -> bool:
    return email.strip().lower() in _load()


def set_password(email: str, new_password: str) -> None:
    email = email.strip().lower()
    salt = secrets.token_hex(16)
    data = _load()
    data[email] = {"salt": salt, "hash": _hash(new_password, salt)}
    _save(data)


def verify(email: str, password: str, seed_password: str | None = None) -> bool:
    """True if the password matches. Uses the user's self-set password if they
    have one, otherwise the seed password from APP_USERS."""
    email = email.strip().lower()
    rec = _load().get(email)
    if rec:
        return hmac.compare_digest(rec.get("hash", ""), _hash(password, rec.get("salt", "")))
    if seed_password is not None:
        return hmac.compare_digest(password, seed_password)
    return False
