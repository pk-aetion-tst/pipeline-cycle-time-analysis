"""S3 report fetcher."""
from __future__ import annotations

import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path


REPORT_RE = re.compile(r"(.*/)?(kono-report|substantiate-report)/data/timeline\.json$")
TIMELINE_RE = re.compile(r".*/data/timeline\.json$")
S3_URL_RE = re.compile(r"s3://([^/\s]+)/([^\s]+)")


def _logical_name_from_text(text: str) -> str:
    lower = text.lower()
    if "substantiate" in lower:
        return "substantiate-report"
    if "kono" in lower:
        return "kono-report"
    return ""


def _normalize_bucket(bucket: str) -> str:
    # Concord log output can redact domains with '*', but reports are always in this bucket.
    if "*" in bucket and "reports.dev" in bucket:
        return "reports.dev.aetion.com"
    return bucket


def _report_root_from_path(path: str) -> tuple[str, str] | None:
    parts = path.split("/")
    report_idx = -1
    for i, part in enumerate(parts):
        if part.startswith("report-"):
            report_idx = i
            break
    if report_idx < 1:
        return None
    return parts[report_idx], "/".join(parts[: report_idx + 1])


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


def discover_locations_from_concord_logs(
    log_texts: list[str],
) -> dict[str, tuple[str, str]]:
    """Extract exact report bucket/root from Concord 'Copy report to S3' output."""
    found_ranked: dict[str, tuple[str, str, str]] = {}
    root_hints: dict[str, str] = {}
    for text in log_texts:
        section_hint = ""
        for raw_line in text.splitlines():
            line = raw_line.strip()
            lower = line.lower()

            if "allure report" in lower or "generate allure report" in lower or "copy report to s3" in lower:
                maybe_hint = _logical_name_from_text(line)
                if maybe_hint:
                    section_hint = maybe_hint

            if "dst:" in lower:
                dst = line.split("dst:", 1)[1].strip()
                parsed = _report_root_from_path(dst)
                if parsed:
                    _, report_root = parsed
                    maybe_hint = _logical_name_from_text(dst) or section_hint
                    if maybe_hint:
                        root_hints[report_root] = maybe_hint

            for match in S3_URL_RE.finditer(line):
                bucket = _normalize_bucket(match.group(1))
                key = match.group(2)
                parsed = _report_root_from_path(key)
                if not parsed:
                    continue
                report_dir, report_root = parsed

                logical_name = _logical_name_from_text(key) or root_hints.get(report_root, "") or section_hint
                if not logical_name:
                    continue

                prev = found_ranked.get(logical_name)
                if prev is None or report_dir > prev[0]:
                    found_ranked[logical_name] = (report_dir, bucket, report_root)

    return {name: (value[1], value[2]) for name, value in found_ranked.items()}


def _candidate_roots(bucket: str, prefix: str, process_date: str | None) -> list[str]:
    normalized = prefix.strip("/")
    base = f"s3://{bucket}/{normalized}" if normalized else f"s3://{bucket}"
    if not process_date:
        return [f"{base}/"]

    try:
        d = datetime.strptime(process_date, "%Y-%m-%d").date()
    except ValueError:
        return [f"{base}/{process_date}/"]

    return [
        f"{base}/{d.strftime('%Y-%m-%d')}/",
        f"{base}/{d.strftime('%Y%m%d')}/",
    ]


def _discover_report_roots(
    bucket: str,
    aws_profile: str,
    prefix: str = "",
    process_date: str | None = None,
    instance_id: str | None = None,
) -> dict[str, tuple[str, str]]:
    latest: dict[str, tuple[str, str, str]] = {}
    scanned_roots: list[str] = []

    for root in _candidate_roots(bucket, prefix, process_date):
        scanned_roots.append(root)
        cmd = ["aws", "--profile", aws_profile, "s3", "ls", root, "--recursive"]
        try:
            listing = _run_aws(cmd)
        except RuntimeError:
            # Missing/empty prefixes can return non-zero; continue probing other date candidates.
            continue

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
            report_root = key.rsplit("/data/timeline.json", 1)[0]
            prev = latest.get(report_name)
            if prev is None or ts > prev[0]:
                latest[report_name] = (ts, bucket, report_root)

        if "kono-report" in latest and "substantiate-report" in latest:
            break

    missing = [name for name in ("kono-report", "substantiate-report") if name not in latest]
    if missing:
        fallback = _discover_from_reports_bucket(
            instance_id=instance_id,
            aws_profile=aws_profile,
            process_date=process_date,
        )
        for name, value in fallback.items():
            if name in missing:
                latest[name] = ("", value[0], value[1])
        missing = [name for name in ("kono-report", "substantiate-report") if name not in latest]
        if missing:
            global_fallback = _discover_from_global_reports_prefixes(
                aws_profile=aws_profile,
                process_date=process_date,
            )
            for name, value in global_fallback.items():
                if name in missing:
                    latest[name] = ("", value[0], value[1])
        missing = [name for name in ("kono-report", "substantiate-report") if name not in latest]
        if missing:
            raise RuntimeError(
                f"Unable to find report data in date-scoped paths for: {', '.join(missing)}. "
                f"Scanned: {', '.join(scanned_roots)}"
            )
    return {name: (latest[name][1], latest[name][2]) for name in latest}


def _discover_from_reports_bucket(
    instance_id: str | None,
    aws_profile: str,
    process_date: str | None,
) -> dict[str, tuple[str, str]]:
    if not instance_id:
        return {}
    reports_bucket = "reports.dev.aetion.com"
    yyyymmdd = (process_date or "").replace("-", "")

    candidates = {
        "kono-report": ["kono-integration"],
        "substantiate-report": ["substantiate-integration", "substantiate-e2e", "substantiate"],
    }

    discovered: dict[str, tuple[str, str]] = {}
    for logical_name, prefixes in candidates.items():
        for prefix in prefixes:
            root = f"s3://{reports_bucket}/{instance_id}/{prefix}/"
            cmd = ["aws", "--profile", aws_profile, "s3", "ls", root]
            try:
                listing = _run_aws(cmd)
            except RuntimeError:
                continue

            candidate_dirs: list[str] = []
            date_matched_dirs: list[str] = []
            for line in listing.splitlines():
                if " PRE " not in line:
                    continue
                dir_name = line.split()[-1].strip("/")
                if not dir_name.startswith("report-"):
                    continue
                candidate_dirs.append(dir_name)
                if yyyymmdd and yyyymmdd in dir_name:
                    date_matched_dirs.append(dir_name)

            if not candidate_dirs:
                continue

            chosen_pool = date_matched_dirs if date_matched_dirs else candidate_dirs
            chosen = sorted(chosen_pool)[-1]
            timeline_key = f"{instance_id}/{prefix}/{chosen}/data/timeline.json"
            check_cmd = [
                "aws", "--profile", aws_profile, "s3", "ls",
                f"s3://{reports_bucket}/{timeline_key}",
            ]
            try:
                check = _run_aws(check_cmd)
            except RuntimeError:
                continue
            if "timeline.json" not in check:
                continue
            report_root = f"{instance_id}/{prefix}/{chosen}"
            discovered[logical_name] = ("", report_root)
            break
    return {name: (reports_bucket, value[1]) for name, value in discovered.items()}


def _discover_from_global_reports_prefixes(
    aws_profile: str,
    process_date: str | None,
) -> dict[str, tuple[str, str]]:
    del process_date
    reports_bucket = "reports.dev.aetion.com"
    root = f"s3://{reports_bucket}/substantiate-e2e/"
    cmd = ["aws", "--profile", aws_profile, "s3", "ls", root]
    try:
        listing = _run_aws(cmd)
    except RuntimeError:
        return {}

    candidate_dirs: list[str] = []
    for line in listing.splitlines():
        if " PRE " not in line:
            continue
        dir_name = line.split()[-1].strip("/")
        if dir_name.startswith("report-"):
            candidate_dirs.append(dir_name)
    if not candidate_dirs:
        return {}

    def _rank(name: str) -> tuple[int, str]:
        suffix = name.removeprefix("report-")
        return (int(suffix), name) if suffix.isdigit() else (0, name)

    chosen = sorted(candidate_dirs, key=_rank)[-1]
    timeline_key = f"substantiate-e2e/{chosen}/data/timeline.json"
    check_cmd = [
        "aws", "--profile", aws_profile, "s3", "ls",
        f"s3://{reports_bucket}/{timeline_key}",
    ]
    try:
        check = _run_aws(check_cmd)
    except RuntimeError:
        return {}
    if "timeline.json" not in check:
        return {}
    return {"substantiate-report": (reports_bucket, f"substantiate-e2e/{chosen}")}


def fetch_reports(
    bucket: str,
    aws_profile: str,
    output_dir: str,
    prefix: str = "",
    process_date: str | None = None,
    instance_id: str | None = None,
    explicit_locations: dict[str, tuple[str, str]] | None = None,
    allow_discovery_fallback: bool = True,
) -> dict[str, str]:
    """Fetch kono-report and substantiate-report from S3."""
    roots: dict[str, tuple[str, str]] = dict(explicit_locations or {})
    if ("kono-report" not in roots or "substantiate-report" not in roots) and allow_discovery_fallback:
        try:
            discovered = _discover_report_roots(
                bucket,
                aws_profile,
                prefix=prefix,
                process_date=process_date,
                instance_id=instance_id,
            )
            roots.update(discovered)
        except RuntimeError:
            # Allow partial data sets; caller may choose to proceed with available reports.
            pass

    missing = [name for name in ("kono-report", "substantiate-report") if name not in roots]
    if missing:
        raise RuntimeError(
            f"Missing report locations for: {', '.join(missing)}. "
            "Check Concord steps 'Generate Allure report' and 'Copy report to S3'."
        )
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    downloads: dict[str, str] = {}
    for report_name, (source_bucket, report_root) in roots.items():
        src = f"s3://{source_bucket}/{report_root}/data"
        dest = out / report_name / "data"
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "aws", "--profile", aws_profile, "s3", "cp",
            src, str(dest), "--recursive",
        ]
        _run_aws(cmd)
        downloads[report_name] = str(dest)

    return downloads
