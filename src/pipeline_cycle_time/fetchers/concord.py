"""Concord API fetcher (v0.1 stub - live mode not yet implemented)."""
from __future__ import annotations


def fetch_log(process_id: str, token: str) -> str:
    """Fetch Concord process log via API.

    Uses: GET /api/v1/process/{id}/log
    Auth: Concord token from keychain
    """
    raise NotImplementedError("Live fetching not implemented in v0.1")
