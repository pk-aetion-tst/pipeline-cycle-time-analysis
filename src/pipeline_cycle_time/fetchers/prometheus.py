"""Prometheus metrics fetcher via Grafana API proxy (v0.1 stub)."""
from __future__ import annotations


def fetch_metric(
    grafana_cookie: str,
    query: str,
    start_s: float,
    end_s: float,
    step: str = "30s",
) -> dict:
    """Fetch metrics from Prometheus via Grafana API proxy.

    Uses: GET /api/datasources/proxy/uid/metrics/api/v1/query_range
    """
    raise NotImplementedError("Live fetching not implemented in v0.1")
