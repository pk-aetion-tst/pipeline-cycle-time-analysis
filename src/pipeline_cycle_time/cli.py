"""CLI entry point for pipeline cycle time analysis."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .analyzers import app_logs, correlator, metrics, orchestration, test_reports
from .fetchers import concord, discovery, loki, prometheus, s3_reports
from .report import generator


def _run_analysis_from_dir(base_dir: Path) -> str:
    orch_result = orchestration.analyze(str(base_dir / "logs" / "concord-log.txt"))

    kono = test_reports.analyze_timeline(
        str(base_dir / "kono-report" / "data" / "timeline.json"), "Kono"
    )
    substantiate = test_reports.analyze_timeline(
        str(base_dir / "substantiate-report" / "data" / "timeline.json"), "Substantiate"
    )

    app_logs_result = app_logs.analyze_webapp_logs(str(base_dir / "logs" / "webapp-logs.json"))
    dispatcher_result = app_logs.analyze_dispatcher_logs(str(base_dir / "logs" / "dispatcher-logs.json"))
    metrics_result = metrics.analyze(str(base_dir / "metrics"))

    correlation = correlator.correlate(
        orch_result, kono, substantiate, app_logs_result, dispatcher_result, metrics_result
    )

    return generator.generate(
        orch_result,
        kono,
        substantiate,
        app_logs_result,
        dispatcher_result,
        metrics_result,
        correlation,
    )


def _dispatcher_selector(pod_name: str) -> str:
    m = re.match(r"(dispatcher-batch-job-\d+)-", pod_name)
    if m:
        return f'{m.group(1)}.*'
    return pod_name


def analyze_fixtures(fixtures_dir: str, output: str | None = None) -> str:
    """Run analysis against local fixture data."""
    report = _run_analysis_from_dir(Path(fixtures_dir))

    if output:
        Path(output).write_text(report)
        print(f"Report written to {output}")
    else:
        print(report)

    return report


def analyze_live(
    process_id: str,
    grafana_cookie: str,
    aws_profile: str,
    output: str | None = None,
    output_dir: str | None = None,
    concord_url: str = concord.DEFAULT_CONCORD_URL,
    grafana_url: str = loki.DEFAULT_GRAFANA_URL,
) -> str:
    """Run live analysis by fetching Concord, S3, Loki, and Prometheus data."""
    base_dir = Path(output_dir) if output_dir else Path("output") / process_id
    logs_dir = base_dir / "logs"
    metrics_dir = base_dir / "metrics"
    logs_dir.mkdir(parents=True, exist_ok=True)
    metrics_dir.mkdir(parents=True, exist_ok=True)

    token = concord.get_token_from_keychain()
    concord_log = concord.fetch_log(process_id=process_id, token=token, concord_base_url=concord_url)
    concord_log_path = logs_dir / "concord-log.txt"
    concord_log_path.write_text(concord_log)

    meta = discovery.discover_from_log(concord_log)

    s3_reports.fetch_reports(meta.s3_bucket, aws_profile, str(base_dir))

    webapp_query = f'{{namespace="{meta.namespace}", pod="{meta.webapp_pod}"}}'
    dispatcher_query = (
        f'{{namespace="{meta.namespace}", pod=~"{_dispatcher_selector(meta.dispatcher_pod)}"}}'
    )
    webapp_logs = loki.fetch_logs(
        grafana_cookie=grafana_cookie,
        query=webapp_query,
        start_ns=meta.start_ns,
        end_ns=meta.end_ns,
        grafana_base_url=grafana_url,
    )
    dispatcher_logs = loki.fetch_logs(
        grafana_cookie=grafana_cookie,
        query=dispatcher_query,
        start_ns=meta.start_ns,
        end_ns=meta.end_ns,
        grafana_base_url=grafana_url,
    )

    (logs_dir / "webapp-logs.json").write_text(json.dumps(webapp_logs, indent=2))
    (logs_dir / "dispatcher-logs.json").write_text(json.dumps(dispatcher_logs, indent=2))

    metric_queries = {
        "webapp-cpu.json": f'rate(container_cpu_usage_seconds_total{{namespace="{meta.namespace}", pod="{meta.webapp_pod}"}}[1m])',
        "webapp-memory.json": f'container_memory_working_set_bytes{{namespace="{meta.namespace}", pod="{meta.webapp_pod}"}}',
        "dispatcher-cpu.json": f'rate(container_cpu_usage_seconds_total{{namespace="{meta.namespace}", pod="{meta.dispatcher_pod}"}}[1m])',
        "dispatcher-memory.json": f'container_memory_working_set_bytes{{namespace="{meta.namespace}", pod="{meta.dispatcher_pod}"}}',
        "hikaricp_connections_active.json": f'hikaricp_connections_active{{namespace="{meta.namespace}", pod="{meta.webapp_pod}"}}',
        "hikaricp_connections_pending.json": f'hikaricp_connections_pending{{namespace="{meta.namespace}", pod="{meta.webapp_pod}"}}',
        "jetty_threads_busy.json": f'jetty_threads_busy{{namespace="{meta.namespace}", pod="{meta.webapp_pod}"}}',
        "jvm_memory_used_bytes.json": f'jvm_memory_used_bytes{{namespace="{meta.namespace}", pod="{meta.webapp_pod}", area="heap"}}',
        "jvm_threads_current.json": f'jvm_threads_current{{namespace="{meta.namespace}", pod="{meta.webapp_pod}"}}',
        "rate_jvm_gc_collection_seconds_sum.json": f'rate(jvm_gc_collection_seconds_sum{{namespace="{meta.namespace}", pod="{meta.webapp_pod}"}}[1m])',
    }

    for filename, query in metric_queries.items():
        payload = prometheus.fetch_metric(
            grafana_cookie=grafana_cookie,
            query=query,
            start_s=meta.start_s,
            end_s=meta.end_s,
            step="30s",
            grafana_base_url=grafana_url,
        )
        (metrics_dir / filename).write_text(json.dumps(payload, indent=2))

    report = _run_analysis_from_dir(base_dir)
    if output:
        Path(output).write_text(report)
        print(f"Report written to {output}")
    else:
        print(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="CI Pipeline Cycle Time Analysis Agent")
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
        "--aws-profile",
        help="AWS profile name (live mode)",
    )
    analyze_cmd.add_argument(
        "--concord-url",
        default=concord.DEFAULT_CONCORD_URL,
        help=f"Concord base URL (default: {concord.DEFAULT_CONCORD_URL})",
    )
    analyze_cmd.add_argument(
        "--grafana-url",
        default=loki.DEFAULT_GRAFANA_URL,
        help=f"Grafana base URL (default: {loki.DEFAULT_GRAFANA_URL})",
    )
    analyze_cmd.add_argument(
        "--output-dir",
        help="Directory for raw fetched data (default: output/<process-id>/)",
    )
    analyze_cmd.add_argument(
        "--output",
        "-o",
        help="Output file path (default: stdout)",
    )

    args = parser.parse_args()

    if args.command != "analyze":
        parser.print_help()
        sys.exit(1)

    if args.fixtures_dir:
        analyze_fixtures(args.fixtures_dir, args.output)
        return

    if args.process_id:
        missing = [
            name
            for name, value in {
                "--grafana-cookie": args.grafana_cookie,
                "--aws-profile": args.aws_profile,
            }.items()
            if not value
        ]
        if missing:
            print(
                f"Live mode requires {', '.join(missing)}",
                file=sys.stderr,
            )
            sys.exit(1)

        analyze_live(
            process_id=args.process_id,
            grafana_cookie=args.grafana_cookie,
            aws_profile=args.aws_profile,
            output=args.output,
            output_dir=args.output_dir,
            concord_url=args.concord_url,
            grafana_url=args.grafana_url,
        )
        return

    print("Either --fixtures-dir or --process-id is required", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
