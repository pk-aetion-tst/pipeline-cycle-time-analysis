"""Parse Prometheus JSON metrics data."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MetricSummary:
    name: str
    min_val: float = 0.0
    max_val: float = 0.0
    avg_val: float = 0.0
    unit: str = ""


@dataclass
class MetricsResult:
    cpu: MetricSummary | None = None
    memory: MetricSummary | None = None
    hikaricp_active: MetricSummary | None = None
    hikaricp_pending: MetricSummary | None = None
    jetty_threads: MetricSummary | None = None
    jvm_heap: MetricSummary | None = None
    jvm_threads: MetricSummary | None = None
    gc: MetricSummary | None = None
    dispatcher_cpu: MetricSummary | None = None
    dispatcher_memory: MetricSummary | None = None

    @property
    def has_headroom(self) -> bool:
        """Application has massive resource headroom."""
        if self.cpu and self.hikaricp_pending:
            return self.cpu.avg_val < 1.0 and self.hikaricp_pending.max_val == 0
        return False


def _summarize(path: str, name: str, unit: str = "") -> MetricSummary | None:
    """Load a Prometheus JSON file and summarize the first result."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    if data.get("status") != "success":
        return None

    results = data.get("data", {}).get("result", [])
    if not results:
        return None

    # Use the first result that has the 'container' label matching 'webapp'
    # or the first result with actual data
    target = results[0]
    for r in results:
        container = r.get("metric", {}).get("container", "")
        if container in ("webapp", "dispatcher"):
            target = r
            break

    values = target.get("values", [])
    nums = []
    for _, v in values:
        try:
            n = float(v)
            if not math.isnan(n):
                nums.append(n)
        except (ValueError, TypeError):
            continue

    if not nums:
        return None

    return MetricSummary(
        name=name,
        min_val=min(nums),
        max_val=max(nums),
        avg_val=sum(nums) / len(nums),
        unit=unit,
    )


def analyze(metrics_dir: str) -> MetricsResult:
    """Analyze all metric files in the directory."""
    d = Path(metrics_dir)
    result = MetricsResult()

    result.cpu = _summarize(str(d / "webapp-cpu.json"), "CPU", "cores")
    result.memory = _summarize(str(d / "webapp-memory.json"), "Memory", "bytes")
    result.dispatcher_cpu = _summarize(str(d / "dispatcher-cpu.json"), "Dispatcher CPU", "cores")
    result.dispatcher_memory = _summarize(str(d / "dispatcher-memory.json"), "Dispatcher Memory", "bytes")

    # HikariCP
    hikari_active = list(d.glob("hikaricp_connections_active*.json"))
    if hikari_active:
        result.hikaricp_active = _summarize(str(hikari_active[0]), "HikariCP Active", "connections")

    hikari_pending = list(d.glob("hikaricp_connections_pending*.json"))
    if hikari_pending:
        result.hikaricp_pending = _summarize(str(hikari_pending[0]), "HikariCP Pending", "connections")

    jetty = list(d.glob("jetty_threads_busy*.json"))
    if jetty:
        result.jetty_threads = _summarize(str(jetty[0]), "Jetty Threads", "threads")

    jvm_heap = list(d.glob("jvm_memory_used_bytes*.json"))
    if jvm_heap:
        result.jvm_heap = _summarize(str(jvm_heap[0]), "JVM Heap", "bytes")

    jvm_threads = list(d.glob("jvm_threads_current*.json"))
    if jvm_threads:
        result.jvm_threads = _summarize(str(jvm_threads[0]), "JVM Threads", "threads")

    gc = list(d.glob("rate_jvm_gc*.json"))
    if gc:
        result.gc = _summarize(str(gc[0]), "GC Rate", "s/s")

    return result
