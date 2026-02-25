"""S3 report fetcher."""
from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path


REPORT_RE = re.compile(r"(.*/)?(kono-report|substantiate-report)/data/timeline\.json$")


def _run_aws(cmd: list[str]) -> str:
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("AWS CLI is not installed or not available in PATH") from exc
    except subprocess.CalledProcessError as exc:
        rendered = " ".join(shlex.quote(part) for part in cmd)
        msg = exc.stderr.strip() or exc.stdout.strip() or "unknown aws cli error"
        raise RuntimeError(f"AWS CLI command failed: {rendered}\n{msg}") from exc
    return result.stdout


def _discover_report_roots(bucket: str, aws_profile: str) -> dict[str, str]:
    cmd = ["aws", "--profile", aws_profile, "s3", "ls", f"s3://{bucket}/", "--recursive"]
    listing = _run_aws(cmd)

    latest: dict[str, tuple[str, str]] = {}
    for line in listing.splitlines():
        # Format: YYYY-MM-DD HH:MM:SS   SIZE KEY
        parts = line.split(maxsplit=3)
        if len(parts) != 4:
            continue
        ts = f"{parts[0]} {parts[1]}"
        key = parts[3]
        m = REPORT_RE.search(key)
        if not m:
            continue
        report_name = m.group(2)
        root = key.rsplit("/data/timeline.json", 1)[0]
        prev = latest.get(report_name)
        if prev is None or ts > prev[0]:
            latest[report_name] = (ts, root)

    missing = [name for name in ("kono-report", "substantiate-report") if name not in latest]
    if missing:
        raise RuntimeError(
            f"Unable to find report data in s3://{bucket}/ for: {', '.join(missing)}"
        )
    return {name: latest[name][1] for name in latest}


def fetch_reports(bucket: str, aws_profile: str, output_dir: str) -> dict[str, str]:
    """Fetch kono-report and substantiate-report from S3."""
    roots = _discover_report_roots(bucket, aws_profile)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    downloads: dict[str, str] = {}
    for report_name, report_root in roots.items():
        src = f"s3://{bucket}/{report_root}/data"
        dest = out / report_name / "data"
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "aws", "--profile", aws_profile, "s3", "cp",
            src, str(dest), "--recursive",
        ]
        _run_aws(cmd)
        downloads[report_name] = str(dest)

    return downloads
