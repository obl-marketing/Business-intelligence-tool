"""Persistent JSON storage with a GitHub backend.

Streamlit Cloud's local disk is ephemeral - it resets on every reboot. To make
the knowledge base and chat history truly persistent, this module writes them
back to a GitHub repo via the Contents API.

Activated by setting these secrets / env vars:
  GITHUB_TOKEN     - a Personal Access Token (fine-grained) with Contents:
                     Read and write on the target repo
  GITHUB_REPO      - "owner/repo", e.g. "obl-marketing/business-intelligence-tool"
  GITHUB_BRANCH    - branch to commit to, e.g. "claude/relaxed-volta-hvi2d"

If any of those is missing, this module falls back to local-file storage
(same behavior as before).
"""
from __future__ import annotations
import base64
import json
import os
import threading
from typing import Any

import httpx


_lock = threading.Lock()
_sha_cache: dict[str, str | None] = {}


def github_enabled() -> bool:
    return all(os.environ.get(k) for k in ("GITHUB_TOKEN", "GITHUB_REPO", "GITHUB_BRANCH"))


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _url(path: str) -> str:
    return f"https://api.github.com/repos/{os.environ['GITHUB_REPO']}/contents/{path}"


def _local_path(path: str) -> str:
    return path


def read_json(path: str, default: Any) -> Any:
    """Read a JSON file. Uses GitHub if configured else local disk."""
    if github_enabled():
        try:
            r = httpx.get(
                _url(path),
                params={"ref": os.environ["GITHUB_BRANCH"]},
                headers=_headers(),
                timeout=20.0,
            )
            if r.status_code == 200:
                payload = r.json()
                _sha_cache[path] = payload.get("sha")
                content = base64.b64decode(payload["content"]).decode("utf-8")
                return json.loads(content) if content else default
            if r.status_code == 404:
                _sha_cache[path] = None
                return default
            # other errors -> fall through to local
        except Exception:
            pass

    try:
        with open(_local_path(path), "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: str, data: Any, message: str | None = None) -> None:
    """Write a JSON file. Persists to GitHub if configured, also writes a local
    copy so re-reads within the same container hit the disk cache."""
    payload_text = json.dumps(data, indent=2)

    # always write a local copy for fast reads + as a fallback
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(_local_path(path), "w") as f:
        f.write(payload_text)

    if not github_enabled():
        return

    with _lock:
        # need the latest sha to update (or omit it for create)
        sha = _sha_cache.get(path)
        if sha is None and path not in _sha_cache:
            # not seen before; probe once
            try:
                probe = httpx.get(
                    _url(path),
                    params={"ref": os.environ["GITHUB_BRANCH"]},
                    headers=_headers(),
                    timeout=20.0,
                )
                if probe.status_code == 200:
                    sha = probe.json().get("sha")
                _sha_cache[path] = sha
            except Exception:
                pass

        body: dict[str, Any] = {
            "message": message or f"STARS: update {path}",
            "content": base64.b64encode(payload_text.encode("utf-8")).decode("ascii"),
            "branch": os.environ["GITHUB_BRANCH"],
        }
        if sha:
            body["sha"] = sha

        try:
            r = httpx.put(_url(path), headers=_headers(), json=body, timeout=30.0)
            if r.status_code in (200, 201):
                _sha_cache[path] = r.json().get("content", {}).get("sha")
            elif r.status_code == 409:
                # someone else committed; refetch sha and retry once
                probe = httpx.get(
                    _url(path),
                    params={"ref": os.environ["GITHUB_BRANCH"]},
                    headers=_headers(),
                    timeout=20.0,
                )
                if probe.status_code == 200:
                    body["sha"] = probe.json().get("sha")
                    httpx.put(_url(path), headers=_headers(), json=body, timeout=30.0)
        except Exception:
            # local copy is already written; don't crash the UI on a flaky push
            pass


def storage_label() -> str:
    if github_enabled():
        return f"GitHub: {os.environ['GITHUB_REPO']}@{os.environ['GITHUB_BRANCH']}"
    return "Local disk (ephemeral on Streamlit Cloud)"
