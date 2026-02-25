"""S3 report fetcher (v0.1 stub - live mode not yet implemented)."""
from __future__ import annotations


def fetch_reports(namespace: str, aws_profile: str, output_dir: str) -> dict[str, str]:
    """Fetch kono-report and substantiate-report from S3.

    Uses: aws s3 cp from bucket like {namespace}.dev.aetion.com
    """
    raise NotImplementedError("Live fetching not implemented in v0.1")
