"""Auto-discover pipeline metadata from a Concord process log."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import unquote


TIMESTAMP_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+\d{4})"
)


@dataclass
class ReportLocation:
    """S3 location for a test report discovered from a child process log."""
    report_name: str  # "kono-report" or "substantiate-report"
    s3_uri: str       # full s3:// URI to the report data directory
    child_id: str     # Concord process ID of the child that produced it


@dataclass
class DeployedComponent:
    """A component discovered from Grafana monitoring URLs in the Concord log."""
    label: str   # human label from the URL, e.g. "webapp", "dispatcher", "fe-aep"
    pod: str     # full pod name, e.g. "webapp-5f9486b946-wlwbf"


@dataclass
class PipelineMetadata:
    namespace: str = ""
    s3_bucket: str = ""
    s3_folder: str = ""
    grafana_base_url: str = ""
    start_epoch_ns: int = 0
    end_epoch_ns: int = 0
    children: list[str] = field(default_factory=list)
    report_locations: list[ReportLocation] = field(default_factory=list)
    deployed_components: list[DeployedComponent] = field(default_factory=list)

    @property
    def start_epoch_s(self) -> float:
        return self.start_epoch_ns / 1e9

    @property
    def end_epoch_s(self) -> float:
        return self.end_epoch_ns / 1e9

    @property
    def webapp_pod(self) -> str:
        for c in self.deployed_components:
            if c.label == "webapp":
                return c.pod
        return ""

    @property
    def dispatcher_pod(self) -> str:
        for c in self.deployed_components:
            if c.label == "dispatcher":
                return c.pod
        return ""


def _parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f%z")


def discover(log_text: str) -> PipelineMetadata:
    """Extract pipeline metadata from a Concord log."""
    meta = PipelineMetadata()

    # Extract timestamps for time window
    first_ts = None
    last_ts = None
    for line in log_text.splitlines():
        m = TIMESTAMP_RE.match(line.strip())
        if m:
            ts = _parse_ts(m.group(1))
            if first_ts is None:
                first_ts = ts
            last_ts = ts

    if first_ts:
        meta.start_epoch_ns = int(first_ts.timestamp() * 1e9)
    if last_ts:
        meta.end_epoch_ns = int(last_ts.timestamp() * 1e9)

    # Extract namespace from config block (e.g. '    namespace: "aep"')
    ns_re = re.compile(r'^\s+namespace:\s*"([^"]+)"', re.MULTILINE)
    m = ns_re.search(log_text)
    if m:
        meta.namespace = m.group(1)

    # Extract s3Bucket and s3Folder
    bucket_re = re.compile(r's3Bucket:\s*"([^"]+)"')
    m = bucket_re.search(log_text)
    if m:
        meta.s3_bucket = m.group(1)

    folder_re = re.compile(r's3Folder:\s*"([^"]+)"')
    m = folder_re.search(log_text)
    if m:
        meta.s3_folder = m.group(1)

    # Extract child process IDs
    child_re = re.compile(
        r"Started a process: <concord:instanceId>([^<]+)</concord:instanceId>"
    )
    for line in log_text.splitlines():
        cm = child_re.search(line)
        if cm:
            meta.children.append(cm.group(1))

    # Extract deployed components and Grafana base URL from monitoring URLs.
    # Dashboard URLs:  <https://monitoring.../d/...?var-pod=<pod>&from=...|<label>>
    # Both end with |<label>> — we extract pod and label from dashboard URLs.
    component_re = re.compile(
        r'var-pod=([a-z0-9][a-z0-9._-]+)'  # pod name after var-pod=
        r'[^|]*\|'                           # skip until pipe
        r'([^>]+)'                           # label (until closing >)
    )
    seen_labels: set[str] = set()
    for line in log_text.splitlines():
        decoded = unquote(line)
        if not meta.grafana_base_url:
            gm = re.search(r'(https://monitoring\.[^/|>]+)', decoded)
            if gm:
                meta.grafana_base_url = gm.group(1)
        for cm in component_re.finditer(decoded):
            pod, label = cm.group(1), cm.group(2).strip()
            if label not in seen_labels:
                seen_labels.add(label)
                meta.deployed_components.append(DeployedComponent(label=label, pod=pod))

    return meta


# Map S3 path keywords to canonical report names used by analyzers
_REPORT_KEYWORDS = {
    "kono-report": "kono-report",
    "substantiate-report": "substantiate-report",
    "kono-integration": "kono-report",
    "substantiate-integration": "substantiate-report",
}

_REPORT_PATTERN = "|".join(re.escape(k) for k in _REPORT_KEYWORDS)


def discover_report_locations(
    child_id: str,
    child_log: str,
) -> list[ReportLocation]:
    """Parse a child process log to find S3 report upload locations.

    Matches both legacy paths (kono-report/, substantiate-report/) and
    current paths (kono-integration/report-<date>/, substantiate-integration/...).
    Prefers URIs containing /data/ (the actual report data directory).
    """
    results = []
    seen: dict[str, str] = {}  # canonical_name -> best data_uri

    s3_uri_re = re.compile(
        r'(s3://[^\s"\']+(?:' + _REPORT_PATTERN + r')[^\s"\']*)'
    )

    for line in child_log.splitlines():
        for m in s3_uri_re.finditer(line):
            uri = m.group(1).rstrip("/")
            for keyword, canonical in _REPORT_KEYWORDS.items():
                if keyword not in uri:
                    continue
                idx = uri.index(keyword)
                after_keyword = uri[idx + len(keyword):]
                # For timestamped paths like kono-integration/report-<date>/data/...
                report_dir_match = re.match(r'/report-[^/]+', after_keyword)
                if report_dir_match:
                    base_uri = uri[:idx + len(keyword) + report_dir_match.end()]
                else:
                    base_uri = uri[:idx + len(keyword)]
                data_uri = base_uri + "/data/"
                # Prefer URIs that reference /data/ (actual report files)
                prev = seen.get(canonical)
                if prev is None or "/data/" in uri:
                    seen[canonical] = data_uri
                break

    for canonical, data_uri in seen.items():
        results.append(ReportLocation(
            report_name=canonical,
            s3_uri=data_uri,
            child_id=child_id,
        ))
    return results
