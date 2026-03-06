"""CLI entry point for pipeline cycle time analysis."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyzers import orchestration, test_reports, app_logs, metrics, correlator
from .report import generator


def analyze_fixtures(fixtures_dir: str, output: str | None = None) -> str:
    """Run analysis against local fixture data."""
    d = Path(fixtures_dir)

    # Analyze orchestration
    orch_result = orchestration.analyze(str(d / "logs" / "concord-log.txt"))

    # Analyze test reports
    kono = test_reports.analyze_timeline(
        str(d / "kono-report" / "data" / "timeline.json"), "Kono"
    )
    substantiate = test_reports.analyze_timeline(
        str(d / "substantiate-report" / "data" / "timeline.json"), "Substantiate"
    )

    # Analyze app logs
    app_logs_result = app_logs.analyze_webapp_logs(str(d / "logs" / "webapp-logs.json"))
    dispatcher_result = app_logs.analyze_dispatcher_logs(str(d / "logs" / "dispatcher-logs.json"))

    # Analyze metrics
    metrics_result = metrics.analyze(str(d / "metrics"))

    # Correlate findings (includes critical-path analysis)
    correlation = correlator.correlate(
        orch_result, kono, substantiate, app_logs_result, dispatcher_result, metrics_result
    )

    # Generate report
    report = generator.generate(
        orch_result, kono, substantiate, app_logs_result, dispatcher_result,
        metrics_result, correlation
    )

    if output:
        Path(output).write_text(report)
        print(f"Report written to {output}")
    else:
        print(report)

    return report


def analyze_live(
    process_id: str,
    output: str | None = None,
    output_dir: str | None = None,
    grafana_cookie: str | None = None,
    grafana_token: str | None = None,
    aws_profile: str = "dev",
    concord_url: str | None = None,
) -> str:
    """Run analysis by fetching live data from production APIs."""
    from .fetchers import concord, discovery, loki, prometheus as prom, s3_reports
    from .fetchers.prometheus import METRIC_QUERIES, DISPATCHER_QUERIES

    concord_base = concord_url or concord.CONCORD_BASE_URL

    if not grafana_cookie and not grafana_token:
        print("Error: --grafana-cookie or --grafana-token is required for live mode",
              file=sys.stderr)
        sys.exit(1)

    grafana_auth = {"grafana_cookie": grafana_cookie, "grafana_token": grafana_token}

    # Set up output directory
    out = Path(output_dir or f"output/{process_id}")
    out.mkdir(parents=True, exist_ok=True)

    # 1. Fetch Concord log
    print(f"Fetching Concord log for process {process_id}...", file=sys.stderr)
    log_text = concord.fetch_log(process_id, base_url=concord_base)
    log_dir = out / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "concord-log.txt").write_text(log_text)

    # 2. Discover metadata from log
    meta = discovery.discover(log_text)
    if not meta.namespace:
        print("Error: could not extract namespace from Concord log", file=sys.stderr)
        sys.exit(1)

    # Use discovered URL unless it contains redacted content (e.g. ******)
    discovered_url = meta.grafana_base_url
    if discovered_url and "***" not in discovered_url:
        grafana_base_url = discovered_url
    else:
        grafana_base_url = loki.GRAFANA_BASE_URL
    comp_summary = ", ".join(f"{c.label}={c.pod}" for c in meta.deployed_components)
    print(f"  namespace={meta.namespace}  components: {comp_summary or 'none found'}",
          file=sys.stderr)

    # 3. Fetch child process logs and discover S3 report locations
    # Concord redacts org name in child logs (e.g. aetion → ******).
    # Reconstruct it from the unredacted s3_bucket in the parent config.
    redaction_domain = ""
    if meta.s3_bucket and "." in meta.s3_bucket:
        # e.g. "aep.dev.aetion.com" → "aetion"
        parts = meta.s3_bucket.split(".")
        if len(parts) >= 3:
            redaction_domain = parts[-2]  # "aetion"

    report_locations = []
    for child_id in meta.children:
        print(f"Fetching child process log {child_id[:8]}...", file=sys.stderr)
        try:
            child_log = concord.fetch_log(child_id, base_url=concord_base)
            (log_dir / f"child-{child_id[:8]}.txt").write_text(child_log)
            # Unredact org name in S3 URIs for report discovery
            parse_log = child_log
            if redaction_domain:
                parse_log = parse_log.replace("******", redaction_domain)
            report_locations.extend(
                discovery.discover_report_locations(child_id, parse_log)
            )
        except Exception as e:
            print(f"  Warning: failed to fetch child {child_id[:8]}: {e}",
                  file=sys.stderr)

    if report_locations:
        print(f"Discovered {len(report_locations)} report(s) from child logs:",
              file=sys.stderr)
        for rl in report_locations:
            print(f"  {rl.report_name} → {rl.s3_uri}", file=sys.stderr)

    # 4. Fetch S3 reports (from discovered locations or fallback)
    if report_locations:
        for rl in report_locations:
            try:
                s3_reports.fetch_report(
                    rl.s3_uri, rl.report_name, aws_profile, str(out),
                )
            except RuntimeError as e:
                print(f"Warning: {rl.report_name} fetch failed: {e}",
                      file=sys.stderr)
    elif meta.s3_folder:
        print(f"No reports found in child logs, falling back to "
              f"s3://{s3_reports.REPORTS_BUCKET}/{meta.s3_folder}/...",
              file=sys.stderr)
        try:
            s3_reports.fetch_reports(meta.s3_folder, aws_profile, str(out))
        except RuntimeError as e:
            print(f"Warning: S3 report fetch failed: {e}", file=sys.stderr)
    else:
        print("Warning: could not determine S3 report locations", file=sys.stderr)

    # 5. Fetch Loki logs for all deployed components
    empty_loki = '{"status":"success","data":{"resultType":"streams","result":[]}}'

    # Map component labels to the log filenames the analyzers expect
    LABEL_TO_LOG_FILE = {"webapp": "webapp-logs.json", "dispatcher": "dispatcher-logs.json"}

    for comp in meta.deployed_components:
        log_file = LABEL_TO_LOG_FILE.get(comp.label, f"{comp.label}-logs.json")
        print(f"Fetching {comp.label} logs (pod={comp.pod})...", file=sys.stderr)
        try:
            data = loki.fetch_logs(
                namespace=meta.namespace,
                pod_selector=comp.pod,
                start_ns=meta.start_epoch_ns,
                end_ns=meta.end_epoch_ns,
                grafana_base_url=grafana_base_url,
                **grafana_auth,
            )
            (log_dir / log_file).write_text(json.dumps(data))
        except Exception as e:
            print(f"  Warning: {comp.label} log fetch failed: {e}", file=sys.stderr)
            (log_dir / log_file).write_text(empty_loki)

    # Ensure webapp-logs.json and dispatcher-logs.json exist for analyzers
    for required in ("webapp-logs.json", "dispatcher-logs.json"):
        if not (log_dir / required).exists():
            (log_dir / required).write_text(empty_loki)

    # 6. Fetch Prometheus metrics
    metrics_dir = out / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)

    start_s = meta.start_epoch_s
    end_s = meta.end_epoch_s

    # Webapp metrics (use first component labeled "webapp", if any)
    if meta.webapp_pod:
        print(f"Fetching webapp metrics...", file=sys.stderr)
        for query_tpl, filename, desc in METRIC_QUERIES:
            query = query_tpl.format(namespace=meta.namespace, pod=meta.webapp_pod)
            try:
                data = prom.fetch_metric(
                    query=query, start_s=start_s, end_s=end_s,
                    grafana_base_url=grafana_base_url, **grafana_auth,
                )
                (metrics_dir / filename).write_text(json.dumps(data))
            except Exception as e:
                print(f"  Warning: {desc} fetch failed: {e}", file=sys.stderr)

    # Dispatcher metrics (use first component labeled "dispatcher", if any)
    if meta.dispatcher_pod:
        print(f"Fetching dispatcher metrics...", file=sys.stderr)
        for query_tpl, filename, desc in DISPATCHER_QUERIES:
            query = query_tpl.format(namespace=meta.namespace, pod=meta.dispatcher_pod)
            try:
                data = prom.fetch_metric(
                    query=query, start_s=start_s, end_s=end_s,
                    grafana_base_url=grafana_base_url, **grafana_auth,
                )
                (metrics_dir / filename).write_text(json.dumps(data))
            except Exception as e:
                print(f"  Warning: {desc} fetch failed: {e}", file=sys.stderr)

    # 7. Run analysis on fetched data
    print("Running analysis...", file=sys.stderr)
    return analyze_fixtures(str(out), output)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CI Pipeline Cycle Time Analysis Agent"
    )
    sub = parser.add_subparsers(dest="command")

    analyze_cmd = sub.add_parser("analyze", help="Analyze pipeline data")
    analyze_cmd.add_argument(
        "--fixtures-dir",
        help="Path to local fixtures directory (offline mode)",
    )
    analyze_cmd.add_argument(
        "--process-id",
        help="Concord process ID (live mode)",
    )
    analyze_cmd.add_argument(
        "--grafana-cookie",
        help="Grafana session cookie (live mode)",
    )
    analyze_cmd.add_argument(
        "--grafana-token",
        help="Grafana service account token (preferred over --grafana-cookie)",
    )
    analyze_cmd.add_argument(
        "--aws-profile",
        default="dev",
        help="AWS profile name (live mode, default: dev)",
    )
    analyze_cmd.add_argument(
        "--concord-url",
        help="Concord base URL (default: https://concord.dev.aetion.com)",
    )
    analyze_cmd.add_argument(
        "--output-dir",
        help="Directory to save raw fetched data (default: output/<process-id>/)",
    )
    analyze_cmd.add_argument(
        "--output", "-o",
        help="Output file path (default: stdout)",
    )

    args = parser.parse_args()

    if args.command != "analyze":
        parser.print_help()
        sys.exit(1)

    if args.fixtures_dir:
        analyze_fixtures(args.fixtures_dir, args.output)
    elif args.process_id:
        analyze_live(
            process_id=args.process_id,
            output=args.output,
            output_dir=args.output_dir,
            grafana_cookie=args.grafana_cookie,
            grafana_token=args.grafana_token,
            aws_profile=args.aws_profile,
            concord_url=args.concord_url,
        )
    else:
        print("Either --fixtures-dir or --process-id is required", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
