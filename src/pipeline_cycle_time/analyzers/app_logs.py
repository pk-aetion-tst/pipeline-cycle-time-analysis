"""Parse Loki JSON log data (webapp + dispatcher)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class LogWarning:
    category: str
    count: int
    description: str
    worst_case: str = ""


@dataclass
class DispatcherTimeline:
    job_id: str = ""
    pod_name: str = ""
    first_log_ns: int = 0
    last_log_ns: int = 0
    compute_start_ns: int = 0
    compute_end_ns: int = 0

    @property
    def total_duration_s(self) -> float:
        if self.first_log_ns and self.last_log_ns:
            return (self.last_log_ns - self.first_log_ns) / 1e9
        return 0.0

    @property
    def compute_duration_s(self) -> float:
        if self.compute_start_ns and self.compute_end_ns:
            return (self.compute_end_ns - self.compute_start_ns) / 1e9
        return 0.0


@dataclass
class AppLogsResult:
    warnings: list[LogWarning] = field(default_factory=list)
    error_count: int = 0
    validation_error_count: int = 0
    total_log_entries: int = 0
    dispatcher: DispatcherTimeline = field(default_factory=DispatcherTimeline)


def analyze_webapp_logs(log_path: str) -> AppLogsResult:
    """Analyze webapp Loki JSON logs."""
    with open(log_path) as f:
        data = json.load(f)

    result = AppLogsResult()

    if data.get("status") != "success":
        return result

    streams = data.get("data", {}).get("result", [])
    all_entries: list[tuple[int, str]] = []
    for stream in streams:
        for ts_str, line in stream.get("values", []):
            all_entries.append((int(ts_str), line))

    result.total_log_entries = len(all_entries)

    # Count categories
    effect_consumer_count = 0
    effect_consumer_worst = ""
    hibernate_dialect = 0
    missing_dataset_attrs = 0
    error_count = 0
    validation_errors = 0

    effect_time_re = re.compile(r"(\d+\.\d+)s")

    for ts, line in all_entries:
        if "WARN" in line:
            if "EffectConsumer" in line:
                effect_consumer_count += 1
                m = effect_time_re.search(line)
                if m:
                    t = m.group(0)
                    if not effect_consumer_worst or float(m.group(1)) > float(effect_consumer_worst.rstrip("s")):
                        effect_consumer_worst = t
            if "dialect" in line.lower() and ("mariadb" in line.lower() or "hibernate" in line.lower() or "mysql" in line.lower()):
                hibernate_dialect += 1
            if "missing" in line.lower() and ("dataset" in line.lower() or "attribute" in line.lower()):
                missing_dataset_attrs += 1
        if "ERROR" in line:
            error_count += 1
            if "valid" in line.lower():
                validation_errors += 1

    result.error_count = error_count
    result.validation_error_count = validation_errors

    if effect_consumer_count > 0:
        result.warnings.append(LogWarning(
            category="EffectConsumer queue blocking",
            count=effect_consumer_count,
            description="Messages that 'should have been an actuator' blocked the EffectConsumer queue",
            worst_case=effect_consumer_worst,
        ))
    if hibernate_dialect > 0:
        result.warnings.append(LogWarning(
            category="Hibernate dialect mismatch",
            count=hibernate_dialect,
            description="MariaDB dialect configured but database is MySQL 8.0",
        ))
    if missing_dataset_attrs > 0:
        result.warnings.append(LogWarning(
            category="Missing dataset attribute mappings",
            count=missing_dataset_attrs,
            description="Dataset attribute mapping warnings",
        ))

    return result


def analyze_dispatcher_logs(log_path: str) -> DispatcherTimeline:
    """Analyze dispatcher Loki JSON logs for pod startup and compute timeline."""
    with open(log_path) as f:
        data = json.load(f)

    timeline = DispatcherTimeline()

    if data.get("status") != "success":
        return timeline

    streams = data.get("data", {}).get("result", [])
    all_entries: list[tuple[int, str, str]] = []  # (ts_ns, line, stream_type)
    for stream in streams:
        stream_type = stream.get("stream", {}).get("stream", "unknown")
        pod_name = stream.get("stream", {}).get("pod", "")
        if pod_name:
            timeline.pod_name = pod_name
        for ts_str, line in stream.get("values", []):
            all_entries.append((int(ts_str), line, stream_type))

    if not all_entries:
        return timeline

    all_entries.sort(key=lambda x: x[0])
    timeline.first_log_ns = all_entries[0][0]
    timeline.last_log_ns = all_entries[-1][0]

    # Extract job ID
    for ts, line, stype in all_entries:
        if "Running job" in line:
            m = re.search(r"Running job (\d+)", line)
            if m:
                timeline.job_id = m.group(1)
                timeline.compute_start_ns = ts
            break

    # Find compute end (last meaningful stdout before S3 upload)
    for ts, line, stype in reversed(all_entries):
        if stype == "stdout" and "Finished" in line and "S3" in line:
            timeline.compute_end_ns = ts
            break
    if not timeline.compute_end_ns:
        stdout_entries = [(ts, line) for ts, line, st in all_entries if st == "stdout"]
        if stdout_entries:
            timeline.compute_end_ns = stdout_entries[-1][0]

    return timeline
