"""Concord API fetcher — fetch process log via REST API."""
from __future__ import annotations

import subprocess
import urllib.request


CONCORD_BASE_URL = "https://concord.dev.aetion.com"


def _get_keychain_token() -> str:
    """Retrieve the Concord token from the macOS keychain."""
    result = subprocess.run(
        ["security", "find-generic-password", "-s", "concord-token", "-w"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Could not retrieve Concord token from keychain. "
            "Store it with: security add-generic-password -s concord-token -a concord -w <token>"
        )
    return result.stdout.strip()


def fetch_log(
    process_id: str,
    token: str | None = None,
    base_url: str = CONCORD_BASE_URL,
) -> str:
    """Fetch Concord process log via API.

    Uses: GET /api/v1/process/{id}/log
    Auth: Concord API token (from argument or macOS keychain).
    Returns the raw log text.
    """
    if not token:
        token = _get_keychain_token()

    url = f"{base_url}/api/v1/process/{process_id}/log"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "text/plain",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")
