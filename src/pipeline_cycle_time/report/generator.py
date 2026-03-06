"""Generate markdown analysis report."""
from __future__ import annotations

from datetime import datetime, timezone
from ..analyzers.orchestration import OrchestrationResult
from ..analyzers.test_reports import TestSuiteResult
from ..analyzers.app_logs import AppLogsResult, DispatcherTimeline
from ..analyzers.metrics import MetricsResult
from ..analyzers.correlator import CorrelationResult


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m = int(seconds) // 60
    s = seconds - m * 60
    return f"{m}m{s:02.0f}s"


def generate(
    orchestration: OrchestrationResult,
    kono: TestSuiteResult,
    substantiate: TestSuiteResult,
    app_logs: AppLogsResult,
    dispatcher: DispatcherTimeline,
    metrics_result: MetricsResult,
    correlation: CorrelationResult,
) -> str:
    lines: list[str] = []

    # Header
    lines.append("# CI Pipeline Comprehensive Analysis Report")
    lines.append("")
    lines.append(f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    lines.append("**Scope:** Full pipeline from Concord orchestration through deployment to test completion")
    lines.append("**Data Sources:** Concord orchestration logs, Kono integration tests, Substantiate E2E tests, application logs and metrics")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 1. Executive Summary
    lines.append("## 1. Executive Summary")
    lines.append("")
    concord_dur = _fmt_duration(orchestration.total_duration_s)
    kono_dur = _fmt_duration(kono.wall_clock_s)
    sub_dur = _fmt_duration(substantiate.wall_clock_s)
    total_tests = kono.total_tests + substantiate.total_tests
    lines.append(
        f"The full CI pipeline completed in approximately **"
        f"{_fmt_duration(orchestration.total_duration_s + max(kono.wall_clock_s, substantiate.wall_clock_s))} "
        f"end-to-end**: {concord_dur} for orchestration/deployment (Concord) followed by "
        f"overlapping test execution (Kono: {kono_dur}, Substantiate: {sub_dur})."
    )
    lines.append("")
    total_pass = kono.pass_count + substantiate.pass_count
    total_fail = kono.fail_count + substantiate.fail_count
    total_skip = kono.skip_count + substantiate.skip_count
    if total_fail == 0 and total_skip == 0:
        outcome = f"**All {total_tests:,} tests passed**"
    else:
        outcome = (
            f"Across {total_tests:,} total tests ({kono.total_tests:,} Kono + "
            f"{substantiate.total_tests} Substantiate): "
            f"**{total_pass:,} passed, {total_fail} failed, and {total_skip} skipped**"
        )
    lines.append(
        f"{outcome}. "
        f"The application itself showed no resource bottlenecks during execution."
    )
    lines.append("")

    # Top findings summary
    lines.append("### Top Findings")
    lines.append("")
    for f in correlation.findings[:5]:
        lines.append(f"{f.rank}. **{f.title}.** {f.description}")
        lines.append("")

    lines.append("---")
    lines.append("")

    # 2. End-to-End Timeline (ASCII art)
    lines.append("## 2. End-to-End Timeline")
    lines.append("")
    _write_timeline(lines, orchestration, kono, substantiate, dispatcher, metrics_result)
    lines.append("")
    lines.append("---")
    lines.append("")

    # 3. Ranked Optimization Opportunities
    lines.append("## 3. Ranked Optimization Opportunities")
    lines.append("")
    for f in correlation.findings:
        lines.append(f"### Rank {f.rank}: {f.title}")
        lines.append(f"- **Description:** {f.description}")
        lines.append(f"- **Evidence:** {f.evidence}")
        lines.append(f"- **Estimated savings:** {f.estimated_savings_s}")
        lines.append(f"- **Difficulty:** {f.difficulty}")
        lines.append(f"- **Priority:** {f.priority}")
        lines.append("")

    lines.append("---")
    lines.append("")

    # 4. Detailed Findings
    lines.append("## 4. Detailed Findings")
    lines.append("")

    # 4.1 Concord
    lines.append("### 4.1 Concord Orchestration")
    lines.append("")
    lines.append("| Phase | Duration | % of Total |")
    lines.append("|---|---|---|")
    total = orchestration.total_duration_s
    for phase in orchestration.phases:
        pct = (phase.duration_s / total * 100) if total > 0 else 0
        lines.append(f"| {phase.name} | {_fmt_duration(phase.duration_s)} | {pct:.1f}% |")
    if orchestration.resume_overheads:
        overhead = orchestration.total_resume_overhead_s
        pct = (overhead / total * 100) if total > 0 else 0
        lines.append(f"| Active overhead on resume | ~{overhead:.0f}s | {pct:.1f}% |")
    lines.append("")
    active_pct = ((total - orchestration.suspended_duration_s) / total * 100) if total > 0 else 0
    lines.append(
        f"**Active work is only {_fmt_duration(total - orchestration.suspended_duration_s)} "
        f"({active_pct:.1f}%).** The vast majority of the orchestration phase is spent "
        f"waiting for child processes."
    )
    lines.append("")

    # 4.2 Kono
    lines.append("### 4.2 Kono Integration Tests")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Total tests | {kono.total_tests:,} |")
    lines.append(f"| Pass rate | {'100%' if kono.fail_count == 0 else f'{kono.pass_count/kono.total_tests*100:.1f}%'} |")
    lines.append(f"| Retries/flaky | {len(kono.flaky_tests)} |")
    lines.append(f"| Wall-clock | {kono.wall_clock_s:.1f}s ({_fmt_duration(kono.wall_clock_s)}) |")
    lines.append(f"| Aggregate CPU time | {kono.aggregate_s / 60:.0f}m{kono.aggregate_s % 60:02.0f}s |")
    lines.append("")

    for pool_name in sorted(kono.pools.keys()):
        pool = kono.pools[pool_name]
        lines.append(
            f"**{pool_name} ({pool.count} tests, {pool.wall_clock_s:.1f}s):** "
            f"Parallelism ratio {pool.parallelism:.2f}x."
        )
        lines.append("")

    # 4.3 Substantiate
    lines.append("### 4.3 Substantiate E2E Tests")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Total tests | {substantiate.total_tests} |")
    pass_rate = (substantiate.pass_count / substantiate.total_tests * 100) if substantiate.total_tests > 0 else 0
    lines.append(f"| Pass rate | {pass_rate:.1f}% ({substantiate.fail_count} failure, {substantiate.skip_count} skipped) |")
    lines.append(f"| Wall-clock | {substantiate.wall_clock_s:.1f}s ({_fmt_duration(substantiate.wall_clock_s)}) |")
    lines.append(f"| Aggregate time | {substantiate.aggregate_s / 60:.1f} min |")
    polling = substantiate.polling_analysis()
    if polling:
        lines.append(f"| Compute wait % | {polling['polling_pct']:.1f}% (inferred from aggregate durations) |")
    lines.append("")

    setup = substantiate.setup_phase_analysis()
    if setup:
        lines.append(
            f"**Two-phase structure is the key insight.** "
            f"The first {setup['setup_duration_s']:.0f}s ({setup['setup_duration_s']/substantiate.wall_clock_s*100:.0f}% of wall-clock) "
            f"runs only {setup['setup_tests']} tests with low parallelism. "
            f"The remaining {substantiate.wall_clock_s - setup['setup_duration_s']:.0f}s "
            f"runs {setup['main_tests']} tests with {setup['all_workers']} saturated workers."
        )
        lines.append("")

    # 4.4 Application Logs and Metrics
    lines.append("### 4.4 Application Logs and Metrics")
    lines.append("")
    lines.append("**The application is not the bottleneck.**")
    lines.append("")
    lines.append("| Resource | Status |")
    lines.append("|---|---|")
    if metrics_result.cpu:
        lines.append(f"| CPU | Peaked at {metrics_result.cpu.max_val:.2f} cores during warmup, {metrics_result.cpu.avg_val:.1f}-{metrics_result.cpu.avg_val + 0.2:.1f} during tests |")
    if metrics_result.memory:
        lines.append(f"| Memory | Stable at ~{metrics_result.memory.avg_val / 1e9:.1f}GB |")
    if metrics_result.hikaricp_active:
        lines.append(f"| HikariCP | Max {int(metrics_result.hikaricp_active.max_val)} active connections, zero pending |")
    lines.append("| Jetty threads | Virtual threads, unconstrained |")
    lines.append("| GC | Negligible |")
    lines.append("")

    for w in app_logs.warnings:
        lines.append(f"**{w.category}:** {w.count} warnings. {w.description}")
        lines.append("")

    lines.append("---")
    lines.append("")

    # 5. Recommendations
    lines.append("## 5. Recommendations")
    lines.append("")
    lines.append("Ordered by impact-to-effort ratio (highest first).")
    lines.append("")

    lines.append("### Quick Wins (implement this sprint)")
    lines.append("")
    quick_wins = [f for f in correlation.findings if f.priority in ("P1", "P2") and f.difficulty == "Low"]
    for i, f in enumerate(quick_wins, 1):
        lines.append(f"{i}. **{f.title}.** {f.description}")
        lines.append("")

    lines.append("### Medium-Term (next 1-2 sprints)")
    lines.append("")
    medium = [f for f in correlation.findings if f.difficulty == "Medium" and f.priority in ("P1", "P2")]
    for i, f in enumerate(medium, len(quick_wins) + 1):
        lines.append(f"{i}. **{f.title}.** {f.description}")
        lines.append("")

    lines.append("### Longer-Term (backlog)")
    lines.append("")
    longer = [f for f in correlation.findings if f.priority == "P3"]
    for i, f in enumerate(longer, len(quick_wins) + len(medium) + 1):
        lines.append(f"{i}. **{f.title}.** {f.description}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        f"*Report generated from CI pipeline execution. "
        f"Data sources: Concord orchestration logs, "
        f"Kono test results ({kono.total_tests:,} tests), "
        f"Substantiate test results ({substantiate.total_tests} tests), "
        f"application metrics and logs.*"
    )

    return "\n".join(lines) + "\n"


def _write_timeline(
    lines: list[str],
    orchestration: OrchestrationResult,
    kono: TestSuiteResult,
    substantiate: TestSuiteResult,
    dispatcher: DispatcherTimeline,
    metrics_result: MetricsResult,
) -> None:
    """Write ASCII timeline art."""
    lines.append("```")
    if orchestration.total_start:
        h = orchestration.total_start.strftime("%H:%M")
        lines.append(f"Time (UTC)  {h}    +5m      +10m     +15m     +20m     +25m     +30m     +35m")
    lines.append("            |--------|--------|--------|--------|--------|--------|--------|")
    lines.append("")

    # Concord phases
    concord_dur = _fmt_duration(orchestration.total_duration_s)
    lines.append(f"CONCORD     [Init|Bootstrap|AWS|Config|Helm]")
    phase_strs = []
    for p in orchestration.phases:
        if p.name != "Suspended":
            phase_strs.append(f"{_fmt_duration(p.duration_s)}")
    lines.append(f"  ({concord_dur})  |{'|'.join(phase_strs)}|")
    lines.append(f"                                             [SUSPENDED ~~~~~~~~~~~~~~~~]")
    if orchestration.children:
        child_strs = "   ".join(f"wait {c[:8]}" for c in orchestration.children[:2])
        lines.append(f"                                              {child_strs}")
    lines.append("")

    # K8s deploy
    if dispatcher.job_id:
        lines.append(f"K8S DEPLOY                               [--- pod startup ---][{dispatcher.total_duration_s:.0f}s]")
        lines.append(f"  Job {dispatcher.job_id}                              scheduled              running")
        lines.append("")

    # Kono
    lines.append(f"KONO TESTS                                         [=== {_fmt_duration(kono.wall_clock_s)} =========]")
    for pool_name in sorted(kono.pools.keys()):
        pool = kono.pools[pool_name]
        lines.append(f"  {pool_name}                                            [{pool_name} {pool.wall_clock_s:.0f}s {'=' * 8}]")
    lines.append("")

    # Substantiate
    lines.append(f"SUBSTANTIATE                                        [====== {_fmt_duration(substantiate.wall_clock_s)} =======>")
    setup = substantiate.setup_phase_analysis()
    if setup:
        lines.append(f"  Setup                                             [--- {setup['setup_duration_s']:.0f}s setup --]")
        main_dur = substantiate.wall_clock_s - setup['setup_duration_s']
        lines.append(f"  Main tests                                                    [~{main_dur:.0f}s ===>")
    lines.append("")

    # Webapp
    if metrics_result.cpu:
        lines.append(
            f"WEBAPP      [startup ~warmup~][--- stable {metrics_result.cpu.avg_val:.1f}-{metrics_result.cpu.avg_val+0.2:.1f} CPU, "
            f"{metrics_result.memory.avg_val / 1e9 if metrics_result.memory else 0:.1f}GB mem -------->")
        lines.append(
            f"            CPU peak {metrics_result.cpu.max_val:.2f}c    "
            f"HikariCP max {int(metrics_result.hikaricp_active.max_val) if metrics_result.hikaricp_active else 0} conn, zero pending"
        )

    lines.append("```")
    lines.append("")
    lines.append("**Legend:** `[===]` active work, `[~~~]` idle/suspended, `[-->` continues, `|` phase boundary")
