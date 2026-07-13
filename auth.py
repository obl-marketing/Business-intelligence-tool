"""Shared-password gate for STARS.

Activated only when APP_PASSWORD is set (env var or Streamlit secret).
If it's not set, the app is open (so local dev and the current cloud
deploy aren't locked out until you deliberately turn it on).

Usage in app.py, right after st.set_page_config():
    import auth
    auth.require_login()   # halts here until the correct password is entered
"""
from __future__ import annotations
import hmac
import os

import streamlit as st


def _configured_password() -> str:
    # Streamlit secret takes priority, then env var.
    try:
        if "APP_PASSWORD" in st.secrets:
            return str(st.secrets["APP_PASSWORD"])
    except Exception:
        pass
    return os.environ.get("APP_PASSWORD", "")


def require_login() -> None:
    password = _configured_password()

    # No password configured -> app is open (opt-in security).
    if not password:
        return

    if st.session_state.get("_authed"):
        return

    # Login screen
    st.markdown("## 🔒 STARS")
    st.caption("Enter the team password to continue.")
    with st.form("login"):
        entered = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        # constant-time compare to avoid timing leaks
        if hmac.compare_digest(entered, password):
            st.session_state["_authed"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()  # nothing below renders until authed


def logout_button() -> None:
    """Optional: render a logout control in the sidebar."""
    if st.session_state.get("_authed") and _configured_password():
        if st.button("Log out", use_container_width=True):
            st.session_state["_authed"] = False
            st.rerun()
