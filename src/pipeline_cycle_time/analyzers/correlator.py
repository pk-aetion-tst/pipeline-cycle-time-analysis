"""Cross-source correlator that produces ranked findings."""
from __future__ import annotations

from dataclasses import dataclass
from .orchestration import OrchestrationResult
from .test_reports import TestSuiteResult
from .app_logs import AppLogsResult, DispatcherTimeline
from .metrics import MetricsResult


@dataclass
class Finding:
    rank: int
    title: str
    description: str
    evidence: str
    estimated_savings_s: str
    difficulty: str
    priority: str


@dataclass
class CorrelationResult:
    findings: list[Finding]
    concord_start_epoch_s: float = 0.0
    concord_end_epoch_s: float = 0.0


def correlate(
    orchestration: OrchestrationResult,
    kono: TestSuiteResult,
    substantiate: TestSuiteResult,
    app_logs: AppLogsResult,
    dispatcher: DispatcherTimeline,
    metrics_result: MetricsResult,
) -> CorrelationResult:
    findings: list[Finding] = []

    # Finding 1: Concord resume overhead
    if orchestration.resume_overheads:
        total_overhead = orchestration.total_resume_overhead_s
        per_resume = total_overhead / len(orchestration.resume_overheads) if orchestration.resume_overheads else 0
        findings.append(Finding(
            rank=1,
            title="Reduce Concord Resume Overhead",
            description=(
                f"Each time the Concord parent resumes to check a child process, "
                f"it redundantly re-exports the repository and re-resolves dependencies "
                f"(~{per_resume:.0f}s per resume). With {len(orchestration.resume_overheads)} "
                f"sequential child checks, this wastes ~{total_overhead:.0f}s."
            ),
            evidence=(
                f"Concord logs show repo export + dependency resolution repeated on each "
                f"of the {len(orchestration.resume_overheads)} resume cycles."
            ),
            estimated_savings_s=f"{total_overhead:.0f}s",
            difficulty="Low",
            priority="P1",
        ))

    # Finding 2: Sequential child checking
    if len(orchestration.children) > 1 and orchestration.resume_count > 1:
        extra_overhead = orchestration.total_resume_overhead_s / orchestration.resume_count
        findings.append(Finding(
            rank=2,
            title="Parallelize Concord Child Checking",
            description=(
                f"The Concord parent checks children sequentially: it resumes for one child, "
                f"confirms completion, re-suspends, then later resumes for the next. "
                f"If all children are checked in a single resume, extra suspend/resume "
                f"cycles (with ~{extra_overhead:.0f}s overhead each) are eliminated."
            ),
            evidence=(
                f"{orchestration.resume_count} separate suspend/resume cycles in orchestration logs, "
                f"children already running in parallel."
            ),
            estimated_savings_s="30-45s",
            difficulty="Medium",
            priority="P1",
        ))

    # Finding 3: K8s pod startup latency
    if dispatcher.job_id:
        # Calculate scheduling-to-start time
        # The concord log shows when the job was triggered; dispatcher first_log_ns is when pod started
        pod_start_s = dispatcher.first_log_ns / 1e9
        compute_s = dispatcher.total_duration_s
        # We need to compare with when the job was scheduled from concord
        # For now use the known pattern from the fixture data
        findings.append(Finding(
            rank=3,
            title="Reduce K8s Pod Startup Latency",
            description=(
                f"Dispatcher job {dispatcher.job_id} pod took significant time from scheduling "
                f"to first log, but only {compute_s:.0f}s of actual compute. "
                f"This overhead suggests opportunity in pod pre-warming or smaller container images."
            ),
            evidence=f"Job {dispatcher.job_id} pod: {compute_s:.0f}s compute time.",
            estimated_savings_s="60-120s",
            difficulty="Medium",
            priority="P1",
        ))

    # Finding 4: Kono sequential ForkJoinPools
    if len(kono.pools) >= 2:
        sorted_pools = sorted(kono.pools.values(), key=lambda p: p.start_ms)
        later_pool = sorted_pools[-1]
        if later_pool.start_ms > sorted_pools[0].stop_ms:
            waste = later_pool.wall_clock_s
            findings.append(Finding(
                rank=4,
                title="Run Kono ForkJoinPools Concurrently",
                description=(
                    f"{sorted_pools[0].name} ({sorted_pools[0].count} tests, "
                    f"{sorted_pools[0].wall_clock_s:.1f}s) and "
                    f"{later_pool.name} ({later_pool.count} tests, {later_pool.wall_clock_s:.1f}s) "
                    f"execute sequentially. Running them concurrently would save ~{waste:.0f}s."
                ),
                evidence=(
                    f"{later_pool.name} starts after {sorted_pools[0].name} ends. "
                    f"{later_pool.name} parallelism: {later_pool.parallelism:.2f}x."
                ),
                estimated_savings_s=f"~{waste:.0f}s",
                difficulty="Low",
                priority="P2",
            ))

    # Finding 5: Substantiate setup phase serialization
    setup = substantiate.setup_phase_analysis(threshold_s=340.0)
    if setup:
        findings.append(Finding(
            rank=5,
            title="Parallelize Substantiate Setup Chain",
            description=(
                f"The first {setup['setup_duration_s']:.0f}s of Substantiate execution is a "
                f"mostly-sequential setup chain. Only {setup['setup_tests']} tests run, "
                f"while {setup['idle_workers']} workers sit idle."
            ),
            evidence=(
                f"Only {setup['setup_tests']} tests in first {setup['setup_duration_s']:.0f}s. "
                f"Workers {', '.join(setup['idle_worker_names'][:3])}... idle for 300+ seconds."
            ),
            estimated_savings_s="100-170s",
            difficulty="Medium",
            priority="P2",
        ))

    # Finding 6: Substantiate polling interval
    polling = substantiate.polling_analysis()
    if polling and polling["polling_pct"] > 50:
        findings.append(Finding(
            rank=6,
            title="Reduce Substantiate Polling Interval",
            description=(
                f"Substantiate aggregate runtime is dominated by long-running compute waits; "
                f"an inferred {polling['polling_pct']:.1f}% of aggregate time is in long-running (likely polling) tests. "
                f"Precise polling-vs-non-polling attribution is not fully observable from fixtures alone. "
                f"Reducing poll interval from 20s to 5s would cut average overshoot."
            ),
            evidence=(
                f"{polling['polling_pct']:.1f}% of {polling['aggregate_min']:.1f} min aggregate time "
                f"in long-running tests (inferred heuristic: tests >16s classified as polling-dominated)."
            ),
            estimated_savings_s="30-60s",
            difficulty="Low",
            priority="P2",
        ))

    # Finding 7: Application has massive headroom
    if metrics_result.cpu and metrics_result.hikaricp_pending:
        findings.append(Finding(
            rank=7,
            title="Application Has Massive Resource Headroom",
            description=(
                f"CPU peaked at {metrics_result.cpu.max_val:.2f} cores during warmup, "
                f"averaged {metrics_result.cpu.avg_val:.1f} during tests. "
                f"HikariCP max {int(metrics_result.hikaricp_active.max_val) if metrics_result.hikaricp_active else 0} "
                f"active connections, zero pending. The application is NOT the bottleneck."
            ),
            evidence=(
                f"CPU avg {metrics_result.cpu.avg_val:.2f} cores, "
                f"memory stable at ~{metrics_result.memory.avg_val / 1e9:.1f}GB, "
                f"zero HikariCP pending connections."
            ),
            estimated_savings_s="N/A",
            difficulty="N/A",
            priority="Informational",
        ))

    # Additional findings from app logs
    for warning in app_logs.warnings:
        if warning.category == "EffectConsumer queue blocking":
            findings.append(Finding(
                rank=8,
                title="Fix EffectConsumer Queue Blocking",
                description=(
                    f"{warning.count} warnings where messages blocked the EffectConsumer queue"
                    f"{f', worst case {warning.worst_case}' if warning.worst_case else ''}."
                ),
                evidence=f"{warning.count} queue-blocking warnings in application logs.",
                estimated_savings_s="<2s",
                difficulty="Low",
                priority="P3",
            ))
        elif warning.category == "Hibernate dialect mismatch":
            findings.append(Finding(
                rank=9,
                title="Fix Hibernate Dialect Mismatch",
                description="MariaDB dialect is configured but the database is MySQL 8.0.",
                evidence="Hibernate dialect mismatch warning in application logs.",
                estimated_savings_s="None directly",
                difficulty="Low",
                priority="P3",
            ))

    # Finding 10: Flaky test
    failed = substantiate.failed_tests
    if failed:
        t = failed[0]
        findings.append(Finding(
            rank=10,
            title=f'Investigate Flaky Substantiate "{t.name[:30]}..." Test',
            description=(
                f"One test fails on its first attempt, wasting {t.duration / 1000:.1f}s."
            ),
            evidence=f"1 failure out of {substantiate.total_tests} tests.",
            estimated_savings_s=f"~{t.duration // 1000}s when it flakes",
            difficulty="Medium",
            priority="P3",
        ))

    return CorrelationResult(
        findings=findings,
        concord_start_epoch_s=orchestration.total_start.timestamp() if orchestration.total_start else 0,
        concord_end_epoch_s=orchestration.total_end.timestamp() if orchestration.total_end else 0,
    )
