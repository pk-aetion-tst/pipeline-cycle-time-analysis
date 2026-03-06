"""S3 report fetcher — download Allure reports from S3."""
from __future__ import annotations

import subprocess
from pathlib import Path


# Reports are stored at s3://reports.dev.aetion.com/{s3_folder}/
# with subdirectories kono-report/ and substantiate-report/
REPORTS_BUCKET = "reports.dev.aetion.com"


def fetch_reports(
    s3_folder: str,
    aws_profile: str,
    output_dir: str,
    reports_bucket: str = REPORTS_BUCKET,
) -> dict[str, str]:
    """Fetch kono-report and substantiate-report from S3.

    Downloads the Allure report data directories needed for analysis.

    Args:
        s3_folder: S3 folder prefix (e.g. "aep-dev").
        aws_profile: AWS CLI profile name.
        output_dir: Local directory to save reports into.
        reports_bucket: Override the S3 bucket name.

    Returns:
        Dict mapping report name to local path, e.g.
        {"kono-report": "/tmp/.../kono-report", "substantiate-report": "/tmp/.../substantiate-report"}
    """
    out = Path(output_dir)
    result = {}

    for report_name in ("kono-report", "substantiate-report"):
        s3_prefix = f"s3://{reports_bucket}/{s3_folder}/{report_name}/data/"
        local_dir = out / report_name / "data"
        local_dir.mkdir(parents=True, exist_ok=True)

        proc = subprocess.run(
            [
                "aws", "s3", "cp", "--recursive",
                "--profile", aws_profile,
                s3_prefix, str(local_dir),
            ],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to download {report_name} from {s3_prefix}: {proc.stderr}"
            )
        result[report_name] = str(out / report_name)

    return result
