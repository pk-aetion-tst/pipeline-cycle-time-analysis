"""Concord API fetcher."""
from __future__ import annotations

import subprocess
from urllib import error, request

DEFAULT_CONCORD_URL = "https://concord.dev.aetion.com"


def get_token_from_keychain(service: str = "concord-token") -> str:
    """Read the Concord token from macOS keychain."""
    cmd = ["security", "find-generic-password", "-w", "-s", service]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("macOS 'security' CLI not available; provide Concord token manually") from exc
    except subprocess.CalledProcessError as exc:
        msg = exc.stderr.strip() or exc.stdout.strip() or "unknown keychain error"
        raise RuntimeError(f"Unable to read Concord token from keychain service '{service}': {msg}") from exc

    token = result.stdout.strip()
    if not token:
        raise RuntimeError(f"Concord token for keychain service '{service}' is empty")
    return token


def fetch_log(
    process_id: str,
    token: str,
    concord_base_url: str = DEFAULT_CONCORD_URL,
    timeout_s: int = 60,
) -> str:
    """Fetch Concord process log via API.

    Uses: GET /api/v1/process/{id}/log.
    Auth: Bearer token.
    """
    base = concord_base_url.rstrip("/")
    url = f"{base}/api/v1/process/{process_id}/log"
    req = request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="GET")

    try:
        with request.urlopen(req, timeout=timeout_s) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        if exc.code in (401, 403):
            raise RuntimeError(
                "Concord authentication failed (401/403). Check keychain token 'concord-token'."
            ) from exc
        raise RuntimeError(f"Concord API returned HTTP {exc.code} for process {process_id}") from exc
    except error.URLError as exc:
        raise RuntimeError(
            f"Unable to reach Concord API at {base}. Check network connectivity and URL."
        ) from exc

    if not payload.strip():
        raise RuntimeError(f"Concord API returned empty log for process {process_id}")
    return payload
