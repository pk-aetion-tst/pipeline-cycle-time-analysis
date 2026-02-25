"""Loki log fetcher via Grafana API proxy (v0.1 stub)."""
from __future__ import annotations


def fetch_logs(
    grafana_cookie: str,
    namespace: str,
    pod_selector: str,
    start_ns: int,
    end_ns: int,
) -> dict:
    """Fetch logs from Loki via Grafana API proxy.

    Uses: GET /api/datasources/proxy/uid/loki_logs/loki/api/v1/query_range
    """
    raise NotImplementedError("Live fetching not implemented in v0.1")
