"""Auto-discovery of live-mode metadata from a Concord log."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+\d{4})\s+\[\w+\s*\]")
NS_RE = re.compile(r'\b(?:NAMESPACE:\s*|namespace:\s*")([a-z0-9-]+)"?', re.IGNORECASE)
BUCKET_RE = re.compile(r's3Bucket:\s*"([^"]+)"', re.IGNORECASE)
POD_IN_URL_RE = re.compile(r"pod%3D%5C%22([a-z0-9-]+)%5C%22", re.IGNORECASE)


@dataclass(frozen=True)
class DiscoveryResult:
    namespace: str
    webapp_pod: str
    dispatcher_pod: str
    s3_bucket: str
    start_ts: datetime
    end_ts: datetime

    @property
    def start_ns(self) -> int:
        return int(self.start_ts.timestamp() * 1_000_000_000)

    @property
    def end_ns(self) -> int:
        return int(self.end_ts.timestamp() * 1_000_000_000)

    @property
    def start_s(self) -> float:
        return self.start_ts.timestamp()

    @property
    def end_s(self) -> float:
        return self.end_ts.timestamp()


def _parse_ts(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f%z")


def discover_from_log(log_text: str) -> DiscoveryResult:
    """Extract namespace, pods, bucket, and time range from Concord log text."""
    timestamps: list[datetime] = []
    namespace = ""
    webapp_pod = ""
    dispatcher_pod = ""
    s3_bucket = ""

    for line in log_text.splitlines():
        if m := TS_RE.match(line):
            timestamps.append(_parse_ts(m.group(1)))

        if not namespace:
            if m := NS_RE.search(line):
                namespace = m.group(1)

        if m := BUCKET_RE.search(line):
            bucket = m.group(1)
            if "*" not in bucket:
                s3_bucket = bucket

        if not webapp_pod and "|webapp>" in line:
            if m := POD_IN_URL_RE.search(line):
                webapp_pod = m.group(1)

        if not dispatcher_pod and "|dispatcher>" in line:
            if m := POD_IN_URL_RE.search(line):
                dispatcher_pod = m.group(1)

    if not timestamps:
        raise RuntimeError("No timestamps found in Concord log; cannot infer time window")
    if not namespace:
        raise RuntimeError("Unable to discover namespace from Concord log")
    if not webapp_pod:
        raise RuntimeError("Unable to discover webapp pod from Concord log")
    if not dispatcher_pod:
        raise RuntimeError("Unable to discover dispatcher pod from Concord log")
    if not s3_bucket:
        s3_bucket = f"{namespace}.dev.aetion.com"

    timestamps.sort()
    return DiscoveryResult(
        namespace=namespace,
        webapp_pod=webapp_pod,
        dispatcher_pod=dispatcher_pod,
        s3_bucket=s3_bucket,
        start_ts=timestamps[0],
        end_ts=timestamps[-1],
    )
