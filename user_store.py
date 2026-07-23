"""User accounts store - the live roster + per-user passwords.

Kept in users.json next to the data folder (persistent, chmod 600, never
pushed to GitHub, passwords stored as salted PBKDF2 hashes).

Bootstrapping: on first run the roster is seeded from the APP_USERS env
list. After that, admins add/remove users in-app and users.json is the
source of truth - so you don't need to edit the server to add people.

File shape:
  {"bootstrapped": true, "users": {email: {"salt": hex, "hash": hex}}}
The older flat shape {email: {...}} is migrated automatically.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import secrets

_ITERATIONS = 200_000


def _users_file() -> str:
    chats_dir = os.environ.get("CHATS_DIR", "chats")
    root = os.path.dirname(os.path.abspath(chats_dir)) if os.path.isabs(chats_dir) else "."
    return os.path.join(root, "users.json")


def _load() -> dict:
    try:
        with open(_users_file()) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"bootstrapped": False, "users": {}}
    if not isinstance(data, dict):
        return {"bootstrapped": False, "users": {}}
    # migrate the old flat {email: {salt,hash}} form
    if "users" not in data:
        return {"bootstrapped": False, "users": data}
    data.setdefault("users", {})
    return data


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


def _record(password: str) -> dict:
    salt = secrets.token_hex(16)
    return {"salt": salt, "hash": _hash(password, salt)}


def ensure_bootstrapped(seed: dict[str, str]) -> None:
    """Seed the roster once from APP_USERS (email -> plaintext seed password).
    Existing users (e.g. someone who already changed their password) are kept."""
    data = _load()
    if data.get("bootstrapped"):
        return
    users = data["users"]
    for email, pw in seed.items():
        email = email.strip().lower()
        if email and email not in users:
            users[email] = _record(pw)
    data["bootstrapped"] = True
    _save(data)


def roster() -> list[str]:
    return sorted(_load()["users"].keys())


def exists(email: str) -> bool:
    return email.strip().lower() in _load()["users"]


def verify(email: str, password: str, seed_password: str | None = None) -> bool:
    email = email.strip().lower()
    rec = _load()["users"].get(email)
    if rec:
        return hmac.compare_digest(rec.get("hash", ""), _hash(password, rec.get("salt", "")))
    # pre-bootstrap fallback to the seed password
    if seed_password is not None:
        return hmac.compare_digest(password, seed_password)
    return False


def set_password(email: str, new_password: str) -> None:
    data = _load()
    data["users"][email.strip().lower()] = _record(new_password)
    _save(data)


# admin operations -------------------------------------------------

def add_user(email: str, password: str) -> None:
    set_password(email, password)


def remove_user(email: str) -> bool:
    data = _load()
    email = email.strip().lower()
    if email in data["users"]:
        del data["users"][email]
        _save(data)
        return True
    return False
