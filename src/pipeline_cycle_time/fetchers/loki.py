"""Loki log fetcher via Grafana API proxy."""
from __future__ import annotations

import json
import urllib.request
import urllib.parse


GRAFANA_BASE_URL = "https://monitoring.dev.aetion.com"
LOKI_PROXY_PATH = "/api/datasources/proxy/uid/loki_logs/loki/api/v1/query_range"


def _grafana_headers(
    grafana_cookie: str | None = None,
    grafana_token: str | None = None,
) -> dict[str, str]:
    """Build auth headers — token preferred over cookie."""
    if grafana_token:
        return {"Authorization": f"Bearer {grafana_token}"}
    if grafana_cookie:
        return {"Cookie": f"grafana_session={grafana_cookie}"}
    raise ValueError("Either grafana_cookie or grafana_token is required")


def fetch_logs(
    *,
    namespace: str,
    pod_selector: str,
    start_ns: int,
    end_ns: int,
    grafana_cookie: str | None = None,
    grafana_token: str | None = None,
    grafana_base_url: str = GRAFANA_BASE_URL,
    limit: int = 5000,
) -> dict:
    """Fetch logs from Loki via Grafana API proxy.

    Args:
        namespace: Kubernetes namespace (e.g. "aep").
        pod_selector: Pod name or regex (e.g. "webapp-5f94..." or
            "dispatcher-batch-job-236757.*").
        start_ns: Start time in nanoseconds since epoch.
        end_ns: End time in nanoseconds since epoch.
        grafana_cookie: Grafana session cookie value.
        grafana_token: Grafana service-account token (preferred).
        grafana_base_url: Override Grafana base URL.
        limit: Max log lines to fetch.

    Returns:
        Loki JSON response dict with {status, data: {resultType, result}}.
    """
    headers = _grafana_headers(grafana_cookie, grafana_token)

    # Use regex match (~=) if selector contains wildcard chars
    op = "=~" if any(c in pod_selector for c in ".*+?|[]()") else "="
    expr = f'{{namespace="{namespace}", pod{op}"{pod_selector}"}}'

    params = urllib.parse.urlencode({
        "query": expr,
        "start": str(start_ns),
        "end": str(end_ns),
        "limit": str(limit),
        "direction": "backward",
    })

    url = f"{grafana_base_url}{LOKI_PROXY_PATH}?{params}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())
