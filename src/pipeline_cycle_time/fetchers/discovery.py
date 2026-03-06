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
class PipelineMetadata:
    namespace: str = ""
    webapp_pod: str = ""
    dispatcher_pod: str = ""
    s3_bucket: str = ""
    s3_folder: str = ""
    grafana_base_url: str = ""
    start_epoch_ns: int = 0
    end_epoch_ns: int = 0
    children: list[str] = field(default_factory=list)
    report_locations: list[ReportLocation] = field(default_factory=list)

    @property
    def start_epoch_s(self) -> float:
        return self.start_epoch_ns / 1e9

    @property
    def end_epoch_s(self) -> float:
        return self.end_epoch_ns / 1e9


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

    # Extract pod names from Grafana monitoring URLs in the log
    # webapp pod: pod=webapp-XXXXX
    webapp_pod_re = re.compile(r'pod(?:%3D|=)\\?"?(webapp-[a-z0-9-]+)')
    dispatcher_pod_re = re.compile(r'pod(?:%3D|=)\\?"?(dispatcher-batch-job-[a-z0-9-]+)')

    # Also check URL-encoded versions
    for line in log_text.splitlines():
        decoded = unquote(line)
        if not meta.webapp_pod:
            wm = re.search(r'pod[=\\"]+(webapp-[a-z0-9-]+)', decoded)
            if wm:
                meta.webapp_pod = wm.group(1)
        if not meta.dispatcher_pod:
            dm = re.search(r'pod[=\\"]+(dispatcher-batch-job-[a-z0-9-]+)', decoded)
            if dm:
                meta.dispatcher_pod = dm.group(1)
        if not meta.grafana_base_url:
            gm = re.search(r'(https://monitoring\.[^/|>]+)', decoded)
            if gm:
                meta.grafana_base_url = gm.group(1)

    return meta


def discover_report_locations(
    child_id: str,
    child_log: str,
) -> list[ReportLocation]:
    """Parse a child process log to find S3 report upload locations.

    Child test-runner processes log lines like:
        s3 cp ... s3://bucket/folder/kono-report/data/...
        Uploading allure report to s3://bucket/folder/kono-report/
        report uploaded to s3://bucket/folder/substantiate-report/data/

    We look for any s3:// URI containing 'kono-report' or 'substantiate-report'.
    """
    results = []
    seen = set()

    s3_uri_re = re.compile(r'(s3://[^\s"\']+(?:kono-report|substantiate-report)[^\s"\']*)')

    for line in child_log.splitlines():
        for m in s3_uri_re.finditer(line):
            uri = m.group(1).rstrip("/")
            # Normalize to the report root (strip /data/... suffix)
            for report_name in ("kono-report", "substantiate-report"):
                if report_name in uri:
                    # Truncate at report_name to get the base, then add /data/
                    idx = uri.index(report_name)
                    base_uri = uri[:idx + len(report_name)]
                    data_uri = base_uri + "/data/"
                    if report_name not in seen:
                        seen.add(report_name)
                        results.append(ReportLocation(
                            report_name=report_name,
                            s3_uri=data_uri,
                            child_id=child_id,
                        ))
    return results
