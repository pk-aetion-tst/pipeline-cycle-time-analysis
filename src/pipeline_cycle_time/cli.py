"""CLI entry point for pipeline cycle time analysis."""
from __future__ import annotations

import argparse
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

    # Correlate findings
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
        "--aws-profile",
        help="AWS profile name (live mode)",
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
        print("Live mode not yet implemented in v0.1", file=sys.stderr)
        sys.exit(1)
    else:
        print("Either --fixtures-dir or --process-id is required", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
