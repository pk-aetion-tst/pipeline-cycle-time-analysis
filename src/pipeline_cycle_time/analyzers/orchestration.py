"""Parse Concord orchestration logs to extract phase timeline and findings."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


TIMESTAMP_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+\d{4})\s+\[(\w+)\s*\]\s+(.*)"
)


@dataclass
class Phase:
    name: str
    start: datetime
    end: datetime

    @property
    def duration_s(self) -> float:
        return (self.end - self.start).total_seconds()


@dataclass
class ResumeOverhead:
    """Overhead from one suspend/resume cycle."""
    resume_time: datetime
    repo_export_s: float
    dep_resolution_s: float

    @property
    def total_s(self) -> float:
        return self.repo_export_s + self.dep_resolution_s


@dataclass
class OrchestrationResult:
    phases: list[Phase] = field(default_factory=list)
    children: list[str] = field(default_factory=list)
    suspend_times: list[datetime] = field(default_factory=list)
    resume_overheads: list[ResumeOverhead] = field(default_factory=list)
    total_start: datetime | None = None
    total_end: datetime | None = None

    @property
    def total_duration_s(self) -> float:
        if self.total_start and self.total_end:
            return (self.total_end - self.total_start).total_seconds()
        return 0.0

    @property
    def active_duration_s(self) -> float:
        return sum(p.duration_s for p in self.phases if p.name != "Suspended")

    @property
    def suspended_duration_s(self) -> float:
        suspended = [p for p in self.phases if p.name == "Suspended"]
        return sum(p.duration_s for p in suspended)

    @property
    def idle_pct(self) -> float:
        total = self.total_duration_s
        if total == 0:
            return 0.0
        return (self.suspended_duration_s / total) * 100

    @property
    def total_resume_overhead_s(self) -> float:
        return sum(r.total_s for r in self.resume_overheads)

    @property
    def resume_count(self) -> int:
        return len(self.resume_overheads)


def _parse_ts(s: str) -> datetime:
    # Format: 2026-02-24T18:21:04.907+0000
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f%z")


def analyze(log_path: str) -> OrchestrationResult:
    with open(log_path) as f:
        lines = f.readlines()

    result = OrchestrationResult()
    timestamps: list[tuple[datetime, str, str]] = []

    for line in lines:
        m = TIMESTAMP_RE.match(line.strip())
        if m:
            ts = _parse_ts(m.group(1))
            level = m.group(2)
            msg = m.group(3)
            timestamps.append((ts, level, msg))

    if not timestamps:
        return result

    # Sort by timestamp
    timestamps.sort(key=lambda x: x[0])
    result.total_start = timestamps[0][0]
    result.total_end = timestamps[-1][0]

    # Extract children
    child_re = re.compile(r"Started a process: <concord:instanceId>([^<]+)</concord:instanceId>")
    for ts, level, msg in timestamps:
        m = child_re.search(msg)
        if m:
            result.children.append(m.group(1))

    # Extract phases by key markers
    phase_markers = []
    for ts, level, msg in timestamps:
        if "Storing policy" in msg and not phase_markers:
            phase_markers.append(("Init", ts))
        elif "Exporting the repository data" in msg and len(phase_markers) <= 1:
            phase_markers.append(("Bootstrap", ts))
        elif "Assuming role" in msg:
            phase_markers.append(("AWS", ts))
        elif "Connecting to" in msg and "mica" in msg.lower():
            phase_markers.append(("Config/Mica", ts))
        elif "helm" in msg.lower() and ("install" in msg.lower() or "upgrade" in msg.lower() or "deployed" in msg.lower()):
            if not any(p[0] == "Helm" for p in phase_markers):
                phase_markers.append(("Helm", ts))

    # Find suspend/resume points
    # A resume cycle starts at "Storing policy" (after a SUSPENDED) and ends at RUNNING.
    # The full overhead includes: policy storage, agent acquisition, repo export,
    # state download, dependency resolution.
    suspend_re = re.compile(r"Process status: SUSPENDED")
    resume_running_re = re.compile(r"Process status: RUNNING")
    resume_export_re = re.compile(r"Repository data export took (\d+)ms")

    seen_first_running = False
    in_resume = False
    current_resume_start = None
    current_export_duration = 0.0

    for i, (ts, level, msg) in enumerate(timestamps):
        if not seen_first_running and resume_running_re.search(msg):
            seen_first_running = True
            continue
        if suspend_re.search(msg):
            result.suspend_times.append(ts)
            in_resume = False
        elif seen_first_running and "Storing policy" in msg and not in_resume:
            # Start of a resume cycle (first activity after suspend)
            in_resume = True
            current_resume_start = ts
            current_export_duration = 0.0
        elif in_resume:
            m = resume_export_re.search(msg)
            if m:
                current_export_duration = int(m.group(1)) / 1000.0
            elif resume_running_re.search(msg):
                total_overhead = (ts - current_resume_start).total_seconds()
                result.resume_overheads.append(ResumeOverhead(
                    resume_time=current_resume_start,
                    repo_export_s=current_export_duration,
                    dep_resolution_s=total_overhead - current_export_duration,
                ))
                in_resume = False
                current_export_duration = 0.0
                current_dep_start = None

    # Build phase timeline from markers
    # Find key phase boundary timestamps more precisely
    init_start = None
    bootstrap_start = None
    aws_start = None
    mica_start = None
    helm_start = None
    helm_end = None
    first_suspend = None

    for ts, level, msg in timestamps:
        if init_start is None and "Storing policy" in msg:
            init_start = ts
        if bootstrap_start is None and "Exporting the repository data" in msg:
            bootstrap_start = ts
        if aws_start is None and "Assuming role" in msg:
            aws_start = ts
        if mica_start is None and "Connecting to" in msg and "mica" in msg.lower():
            mica_start = ts
        if "Helm install/upgrade" in msg or ("deployed" in msg.lower() and "STATUS" in msg):
            helm_end = ts
        if first_suspend is None and "Process status: SUSPENDED" in msg:
            first_suspend = ts

    # For helm start, find when helm command begins (after mica config)
    for ts, level, msg in timestamps:
        if helm_start is None and mica_start and ts > mica_start and ("helm" in msg.lower() or "Helm" in msg):
            helm_start = ts
            break

    # Build phases
    if init_start and bootstrap_start:
        result.phases.append(Phase("Init", init_start, bootstrap_start))
    if bootstrap_start and aws_start:
        result.phases.append(Phase("Bootstrap", bootstrap_start, aws_start))
    if aws_start and mica_start:
        result.phases.append(Phase("AWS", aws_start, mica_start))
    if mica_start:
        helm_s = helm_start or mica_start
        if helm_s != mica_start:
            result.phases.append(Phase("Config/Mica", mica_start, helm_s))
    if helm_end and first_suspend:
        if helm_start:
            result.phases.append(Phase("Helm", helm_start, helm_end))
    if first_suspend and result.total_end:
        result.phases.append(Phase("Suspended", first_suspend, result.total_end))

    return result
