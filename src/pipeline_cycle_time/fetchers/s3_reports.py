"""S3 report fetcher — download Allure reports from S3."""
from __future__ import annotations

import subprocess
from pathlib import Path


# Fallback bucket when report locations are not discovered from child logs.
REPORTS_BUCKET = "reports.dev.aetion.com"


def fetch_report(
    s3_uri: str,
    report_name: str,
    aws_profile: str,
    output_dir: str,
) -> str:
    """Download a single report from a discovered S3 URI.

    Args:
        s3_uri: Full s3:// URI to the report data directory.
        report_name: Local directory name (e.g. "kono-report").
        aws_profile: AWS CLI profile name.
        output_dir: Local parent directory to save into.

    Returns:
        Local path to the downloaded report directory.
    """
    local_dir = Path(output_dir) / report_name / "data"
    local_dir.mkdir(parents=True, exist_ok=True)

    # Ensure trailing slash for recursive copy
    src = s3_uri.rstrip("/") + "/"

    proc = subprocess.run(
        ["aws", "s3", "cp", "--recursive", "--profile", aws_profile,
         src, str(local_dir)],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Failed to download {report_name} from {src}: {proc.stderr}"
        )
    return str(Path(output_dir) / report_name)


def fetch_reports(
    s3_folder: str,
    aws_profile: str,
    output_dir: str,
    reports_bucket: str = REPORTS_BUCKET,
) -> dict[str, str]:
    """Fetch kono-report and substantiate-report using a fallback bucket/folder.

    This is the legacy path used when report locations are not discovered
    from child process logs.
    """
    result = {}
    for report_name in ("kono-report", "substantiate-report"):
        s3_uri = f"s3://{reports_bucket}/{s3_folder}/{report_name}/data/"
        result[report_name] = fetch_report(
            s3_uri, report_name, aws_profile, output_dir,
        )
    return result
