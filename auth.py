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


def _admins() -> set[str]:
    """Admin emails. From APP_ADMINS if set, else the first APP_USERS entry
    (which is your admin@... login)."""
    raw = _secret("APP_ADMINS")
    admins = {e.strip().lower() for e in raw.split(",") if e.strip()}
    if admins:
        return admins
    users = _users()
    return {next(iter(users))} if users else set()


def is_admin(email: str | None = None) -> bool:
    return (email or current_user()).strip().lower() in _admins()


def require_login() -> None:
    users = _users()
    shared = _shared_password()

    # No auth configured -> open app.
    if not users and not shared:
        st.session_state.setdefault("user_email", "local")
        return

    # Multi-user mode
    if users:
        user_store.ensure_bootstrapped(users)  # seed roster from APP_USERS once
        if st.session_state.get("_authed"):
            return
        st.markdown("## 🔒 STARS")
        st.caption("Sign in with your work email.")
        with st.form("login"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary")
        if submitted:
            key = (email or "").strip().lower()
            # roster is now the user_store (seeded from APP_USERS + admin-added)
            if user_store.exists(key) and user_store.verify(key, password, seed_password=users.get(key)):
                st.session_state["_authed"] = True
                st.session_state["user_email"] = key
                st.rerun()
            else:
                st.error("Incorrect email or password.")
        st.stop()

    if st.session_state.get("_authed"):
        return
    st.markdown("## 🔒 STARS")

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


def manage_users_ui() -> None:
    """Admin-only: add or remove logins from inside the app."""
    if not st.session_state.get("_authed"):
        return
    if not _users():
        return  # not in multi-user mode
    if not is_admin():
        return
    admins = _admins()
    with st.expander("👥 Manage users (admin)"):
        st.caption("Add a login and share the email + password with the person. "
                   "They can change their own password after signing in.")
        with st.form("add_user", clear_on_submit=True):
            new_email = st.text_input("New user's email")
            new_pw = st.text_input("Set a password for them", type="password")
            add = st.form_submit_button("Add / update user", type="primary")
        if add:
            e = (new_email or "").strip().lower()
            if "@" not in e or "." not in e:
                st.error("Please enter a valid email address.")
            elif len(new_pw) < 4:
                st.error("Password must be at least 4 characters.")
            else:
                user_store.add_user(e, new_pw)
                st.success(f"✅ {e} can now sign in with the password you set.")

        roster = user_store.roster()
        if roster:
            st.markdown("**Current users**")
            for email in roster:
                c1, c2 = st.columns([4, 1])
                tag = " *(admin)*" if email in admins else ""
                me_tag = " *(you)*" if email == current_user() else ""
                c1.markdown(f"- {email}{tag}{me_tag}")
                # can't remove yourself or another admin
                if email != current_user() and email not in admins:
                    if c2.button("Remove", key=f"rm_user_{email}"):
                        user_store.remove_user(email)
                        st.rerun()
