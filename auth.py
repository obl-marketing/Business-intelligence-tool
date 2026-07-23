"""Login for STARS.

Two modes, chosen automatically by which secret is set:

1. Multi-user (recommended): set APP_USERS to a comma-separated list of
   email:password pairs, e.g.
     APP_USERS=admin@orientbell.com:secret,sneha.sengar@orientbell.com:sneha@1234
   Each person logs in with their email + password. Chats are private
   per email; training/knowledge data is shared across everyone.

2. Single shared password (back-compat): set APP_PASSWORD only. Everyone
   uses one password and shares one chat history.

If neither is set, the app is open (no login).

Passwords with a ':' or ',' aren't supported by the APP_USERS format;
the passwords in use here don't contain those characters.
"""
from __future__ import annotations
import hmac
import os

import streamlit as st

import user_store


def _secret(key: str) -> str:
    try:
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    return os.environ.get(key, "")


def _users() -> dict[str, str]:
    """email(lowercased) -> password. Empty dict means 'no login configured'."""
    users: dict[str, str] = {}
    raw = _secret("APP_USERS")
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        email, pw = pair.split(":", 1)
        email = email.strip().lower()
        if email:
            users[email] = pw
    return users


def _shared_password() -> str:
    return _secret("APP_PASSWORD")


def require_login() -> None:
    users = _users()
    shared = _shared_password()

    # No auth configured -> open app.
    if not users and not shared:
        st.session_state.setdefault("user_email", "local")
        return

    if st.session_state.get("_authed"):
        return

    st.markdown("## 🔒 STARS")

    # Multi-user mode
    if users:
        st.caption("Sign in with your work email.")
        with st.form("login"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary")
        if submitted:
            key = (email or "").strip().lower()
            # key must be in the roster (APP_USERS); the current password may be
            # the seed OR a self-set override stored in user_store.
            if key in users and user_store.verify(key, password, seed_password=users[key]):
                st.session_state["_authed"] = True
                st.session_state["user_email"] = key
                st.rerun()
            else:
                st.error("Incorrect email or password.")
        st.stop()

    # Single shared-password mode
    st.caption("Enter the team password to continue.")
    with st.form("login"):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        if hmac.compare_digest(password, shared):
            st.session_state["_authed"] = True
            st.session_state["user_email"] = "team"
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()


def current_user() -> str:
    return st.session_state.get("user_email", "local")


def logout_button() -> None:
    if not st.session_state.get("_authed"):
        return
    st.caption(f"Signed in as **{current_user()}**")
    if st.button("Log out", use_container_width=True):
        for k in ("_authed", "user_email", "messages", "active_chat_id"):
            st.session_state.pop(k, None)
        st.rerun()


def password_change_ui() -> None:
    """Render a 'change my password' expander in the sidebar. Only meaningful
    in multi-user mode (each user has their own login)."""
    if not st.session_state.get("_authed"):
        return
    users = _users()
    if not users:
        return  # shared-password / open mode has no per-user password
    me = current_user()
    with st.expander("🔑 Change my password"):
        with st.form("pw_change", clear_on_submit=True):
            old = st.text_input("Current password", type="password")
            new1 = st.text_input("New password", type="password")
            new2 = st.text_input("Confirm new password", type="password")
            submitted = st.form_submit_button("Update password", type="primary")
        if submitted:
            if not user_store.verify(me, old, seed_password=users.get(me)):
                st.error("Current password is incorrect.")
            elif len(new1) < 4:
                st.error("New password must be at least 4 characters.")
            elif new1 != new2:
                st.error("The two new passwords don't match.")
            else:
                user_store.set_password(me, new1)
                st.success("Password updated. Use your new password next time you sign in.")
