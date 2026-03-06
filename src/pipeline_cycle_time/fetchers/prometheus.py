"""Prometheus metrics fetcher via Grafana API proxy."""
from __future__ import annotations

import json
import urllib.request
import urllib.parse


GRAFANA_BASE_URL = "https://monitoring.dev.aetion.com"
PROM_PROXY_PATH = "/api/datasources/proxy/uid/metrics/api/v1/query_range"

# Standard metric queries for pipeline analysis.
# Each tuple: (query_template, filename, description)
# Templates use {namespace} and {pod} placeholders.
METRIC_QUERIES = [
    ('rate(container_cpu_usage_seconds_total{{namespace="{namespace}", pod="{pod}"}}[1m])',
     "webapp-cpu.json", "Webapp CPU"),
    ('container_memory_working_set_bytes{{namespace="{namespace}", pod="{pod}"}}',
     "webapp-memory.json", "Webapp Memory"),
    ('hikaricp_connections_active{{namespace="{namespace}", pod="{pod}"}}',
     "hikaricp_connections_active.json", "HikariCP Active"),
    ('hikaricp_connections_pending{{namespace="{namespace}", pod="{pod}"}}',
     "hikaricp_connections_pending.json", "HikariCP Pending"),
    ('jetty_threads_busy{{namespace="{namespace}", pod="{pod}"}}',
     "jetty_threads_busy.json", "Jetty Threads"),
    ('jvm_memory_used_bytes{{namespace="{namespace}", pod="{pod}", area="heap"}}',
     "jvm_memory_used_bytes.json", "JVM Heap"),
    ('jvm_threads_current{{namespace="{namespace}", pod="{pod}"}}',
     "jvm_threads_current.json", "JVM Threads"),
    ('rate(jvm_gc_collection_seconds_sum{{namespace="{namespace}", pod="{pod}"}}[1m])',
     "rate_jvm_gc.json", "GC Rate"),
]

DISPATCHER_QUERIES = [
    ('rate(container_cpu_usage_seconds_total{{namespace="{namespace}", pod="{pod}"}}[1m])',
     "dispatcher-cpu.json", "Dispatcher CPU"),
    ('container_memory_working_set_bytes{{namespace="{namespace}", pod="{pod}"}}',
     "dispatcher-memory.json", "Dispatcher Memory"),
]


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


def fetch_metric(
    *,
    query: str,
    start_s: float,
    end_s: float,
    step: str = "30s",
    grafana_cookie: str | None = None,
    grafana_token: str | None = None,
    grafana_base_url: str = GRAFANA_BASE_URL,
) -> dict:
    """Fetch metrics from Prometheus via Grafana API proxy.

    Args:
        query: PromQL query string.
        start_s: Start time in seconds since epoch.
        end_s: End time in seconds since epoch.
        step: Query resolution step (default "30s").
        grafana_cookie: Grafana session cookie value.
        grafana_token: Grafana service-account token (preferred).
        grafana_base_url: Override Grafana base URL.

    Returns:
        Prometheus JSON response dict with {status, data: {resultType, result}}.
    """
    headers = _grafana_headers(grafana_cookie, grafana_token)

    params = urllib.parse.urlencode({
        "query": query,
        "start": str(start_s),
        "end": str(end_s),
        "step": step,
    })

    url = f"{grafana_base_url}{PROM_PROXY_PATH}?{params}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())
