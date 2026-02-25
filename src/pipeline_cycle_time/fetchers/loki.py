"""Loki log fetcher via Grafana API proxy."""
from __future__ import annotations

import json
from urllib import error, parse, request

DEFAULT_GRAFANA_URL = "https://monitoring.dev.aetion.com"


def _cookie_header(cookie: str) -> str:
    return cookie if "=" in cookie else f"grafana_session={cookie}"


def fetch_logs(
    grafana_cookie: str,
    query: str,
    start_ns: int,
    end_ns: int,
    limit: int = 5000,
    direction: str = "backward",
    grafana_base_url: str = DEFAULT_GRAFANA_URL,
    timeout_s: int = 60,
) -> dict:
    """Fetch logs from Loki via Grafana API proxy.

    Uses: GET /api/datasources/proxy/uid/loki_logs/loki/api/v1/query_range
    """
    base = grafana_base_url.rstrip("/")
    endpoint = f"{base}/api/datasources/proxy/uid/loki_logs/loki/api/v1/query_range"
    params = {
        "query": query,
        "start": str(start_ns),
        "end": str(end_ns),
        "limit": str(limit),
        "direction": direction,
    }
    url = f"{endpoint}?{parse.urlencode(params)}"
    req = request.Request(
        url,
        headers={"Cookie": _cookie_header(grafana_cookie), "Accept": "application/json"},
        method="GET",
    )

    try:
        with request.urlopen(req, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        if exc.code in (401, 403):
            raise RuntimeError("Grafana authentication failed (401/403). Check --grafana-cookie.") from exc
        raise RuntimeError(f"Loki query failed with HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Unable to reach Grafana at {base}") from exc
