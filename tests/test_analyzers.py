"""Tests for pipeline cycle time analyzers, validated against fixture data."""
import os
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "2026-02-24-aep"


@pytest.fixture
def fixtures_dir():
    assert FIXTURES_DIR.exists(), f"Fixtures not found at {FIXTURES_DIR}"
    return str(FIXTURES_DIR)


# --- Orchestration Analyzer ---

class TestOrchestration:
    def test_total_duration(self, fixtures_dir):
        from pipeline_cycle_time.analyzers.orchestration import analyze
        result = analyze(os.path.join(fixtures_dir, "logs", "concord-log.txt"))
        # Golden: 20m26s = 1226s (allow some margin)
        assert 1200 < result.total_duration_s < 1260

    def test_idle_percentage(self, fixtures_dir):
        from pipeline_cycle_time.analyzers.orchestration import analyze
        result = analyze(os.path.join(fixtures_dir, "logs", "concord-log.txt"))
        # Golden: ~89.8% idle (suspended time includes resume overhead)
        assert result.idle_pct > 85

    def test_children_detected(self, fixtures_dir):
        from pipeline_cycle_time.analyzers.orchestration import analyze
        result = analyze(os.path.join(fixtures_dir, "logs", "concord-log.txt"))
        assert len(result.children) == 2
        assert any("806f92a1" in c for c in result.children)
        assert any("c6cbe79e" in c for c in result.children)

    def test_finding_1_resume_overhead(self, fixtures_dir):
        """Finding 1: Concord resume overhead — per-cycle breakdown."""
        from pipeline_cycle_time.analyzers.orchestration import analyze
        result = analyze(os.path.join(fixtures_dir, "logs", "concord-log.txt"))
        assert result.resume_count == 2, "Should detect 2 resume cycles"
        # Per-cycle overhead matches log evidence (PR #7):
        #   Resume 1: ~30s (18:29:41 → 18:30:11)
        #   Resume 2: ~9s  (18:41:14 → 18:41:23)
        assert 28 < result.resume_overheads[0].total_s < 32, (
            f"Resume 1 should be ~30s, got {result.resume_overheads[0].total_s:.1f}s"
        )
        assert 7 < result.resume_overheads[1].total_s < 11, (
            f"Resume 2 should be ~9s, got {result.resume_overheads[1].total_s:.1f}s"
        )
        # Total overhead ~38s
        assert 36 < result.total_resume_overhead_s < 42

    def test_finding_2_sequential_child_checking(self, fixtures_dir):
        """Finding 2: Sequential child checking (extra resume cycle)."""
        from pipeline_cycle_time.analyzers.orchestration import analyze
        result = analyze(os.path.join(fixtures_dir, "logs", "concord-log.txt"))
        # Two children but checked sequentially = 2 suspend/resume cycles
        assert len(result.children) >= 2
        assert result.resume_count >= 2


# --- Kono Test Reports ---

class TestKonoReports:
    def test_total_tests(self, fixtures_dir):
        from pipeline_cycle_time.analyzers.test_reports import analyze_timeline
        result = analyze_timeline(
            os.path.join(fixtures_dir, "kono-report", "data", "timeline.json"), "Kono"
        )
        assert result.total_tests == 1245

    def test_all_pass(self, fixtures_dir):
        from pipeline_cycle_time.analyzers.test_reports import analyze_timeline
        result = analyze_timeline(
            os.path.join(fixtures_dir, "kono-report", "data", "timeline.json"), "Kono"
        )
        assert result.fail_count == 0
        assert result.pass_count == 1245

    def test_wall_clock(self, fixtures_dir):
        from pipeline_cycle_time.analyzers.test_reports import analyze_timeline
        result = analyze_timeline(
            os.path.join(fixtures_dir, "kono-report", "data", "timeline.json"), "Kono"
        )
        # Golden: 283.4s (4m43s)
        assert 280 < result.wall_clock_s < 290

    def test_two_pools(self, fixtures_dir):
        from pipeline_cycle_time.analyzers.test_reports import analyze_timeline
        result = analyze_timeline(
            os.path.join(fixtures_dir, "kono-report", "data", "timeline.json"), "Kono"
        )
        assert "Pool-1" in result.pools
        assert "Pool-2" in result.pools

    def test_finding_4_sequential_pools(self, fixtures_dir):
        """Finding 4: Kono sequential ForkJoinPools (~58s waste)."""
        from pipeline_cycle_time.analyzers.test_reports import analyze_timeline
        result = analyze_timeline(
            os.path.join(fixtures_dir, "kono-report", "data", "timeline.json"), "Kono"
        )
        pool1 = result.pools["Pool-1"]
        pool2 = result.pools["Pool-2"]
        # Pool-2 starts after Pool-1 ends (sequential)
        assert pool2.start_ms > pool1.stop_ms, "Pools should run sequentially"
        # Pool-2 wall-clock is the waste
        assert 50 < pool2.wall_clock_s < 65, f"Pool-2 waste should be ~58s, got {pool2.wall_clock_s}"

    def test_pool1_stats(self, fixtures_dir):
        from pipeline_cycle_time.analyzers.test_reports import analyze_timeline
        result = analyze_timeline(
            os.path.join(fixtures_dir, "kono-report", "data", "timeline.json"), "Kono"
        )
        pool1 = result.pools["Pool-1"]
        assert pool1.count == 185
        assert 220 < pool1.wall_clock_s < 225

    def test_pool2_parallelism(self, fixtures_dir):
        from pipeline_cycle_time.analyzers.test_reports import analyze_timeline
        result = analyze_timeline(
            os.path.join(fixtures_dir, "kono-report", "data", "timeline.json"), "Kono"
        )
        pool2 = result.pools["Pool-2"]
        assert pool2.count == 1060
        # Golden: 7.13x parallelism
        assert 6.5 < pool2.parallelism < 7.5


# --- Substantiate Test Reports ---

class TestSubstantiateReports:
    def test_total_tests(self, fixtures_dir):
        from pipeline_cycle_time.analyzers.test_reports import analyze_timeline
        result = analyze_timeline(
            os.path.join(fixtures_dir, "substantiate-report", "data", "timeline.json"),
            "Substantiate"
        )
        assert result.total_tests == 256

    def test_pass_fail_skip(self, fixtures_dir):
        from pipeline_cycle_time.analyzers.test_reports import analyze_timeline
        result = analyze_timeline(
            os.path.join(fixtures_dir, "substantiate-report", "data", "timeline.json"),
            "Substantiate"
        )
        assert result.pass_count == 249
        assert result.fail_count == 1
        assert result.skip_count == 6

    def test_wall_clock(self, fixtures_dir):
        from pipeline_cycle_time.analyzers.test_reports import analyze_timeline
        result = analyze_timeline(
            os.path.join(fixtures_dir, "substantiate-report", "data", "timeline.json"),
            "Substantiate"
        )
        # Golden: 907.8s (15m06s)
        assert 900 < result.wall_clock_s < 915

    def test_finding_5_setup_phase(self, fixtures_dir):
        """Finding 5: Substantiate setup phase serialization (340s)."""
        from pipeline_cycle_time.analyzers.test_reports import analyze_timeline
        result = analyze_timeline(
            os.path.join(fixtures_dir, "substantiate-report", "data", "timeline.json"),
            "Substantiate"
        )
        setup = result.setup_phase_analysis(threshold_s=340.0)
        assert setup["setup_tests"] == 19, f"Expected 19 setup tests, got {setup['setup_tests']}"
        assert setup["idle_workers"] >= 4, "Should have idle workers during setup"

    def test_finding_6_polling_overshoot(self, fixtures_dir):
        """Finding 6: Substantiate polling interval overshoot (20s)."""
        from pipeline_cycle_time.analyzers.test_reports import analyze_timeline
        result = analyze_timeline(
            os.path.join(fixtures_dir, "substantiate-report", "data", "timeline.json"),
            "Substantiate"
        )
        polling = result.polling_analysis()
        # Golden: 76.8% of aggregate time is compute wait/polling (inferred heuristic)
        assert 75 < polling["polling_pct"] < 82, (
            f"Polling percentage {polling['polling_pct']:.1f}% should be ~76.8% (inferred)"
        )

    def test_flaky_test_detected(self, fixtures_dir):
        from pipeline_cycle_time.analyzers.test_reports import analyze_timeline
        result = analyze_timeline(
            os.path.join(fixtures_dir, "substantiate-report", "data", "timeline.json"),
            "Substantiate"
        )
        failed = result.failed_tests
        assert len(failed) == 1
        assert "result views" in failed[0].name.lower()


# --- App Logs ---

class TestAppLogs:
    def test_effect_consumer_warnings(self, fixtures_dir):
        from pipeline_cycle_time.analyzers.app_logs import analyze_webapp_logs
        result = analyze_webapp_logs(
            os.path.join(fixtures_dir, "logs", "webapp-logs.json")
        )
        ec = [w for w in result.warnings if "EffectConsumer" in w.category]
        assert len(ec) == 1
        assert ec[0].count == 7

    def test_dispatcher_timeline(self, fixtures_dir):
        from pipeline_cycle_time.analyzers.app_logs import analyze_dispatcher_logs
        result = analyze_dispatcher_logs(
            os.path.join(fixtures_dir, "logs", "dispatcher-logs.json")
        )
        assert result.job_id == "236757"
        # Compute time is ~17s
        assert 10 < result.total_duration_s < 25

    def test_finding_3_pod_startup_latency(self, fixtures_dir):
        """Finding 3: K8s pod startup latency (196s for 17s job)."""
        from pipeline_cycle_time.analyzers.app_logs import analyze_dispatcher_logs
        result = analyze_dispatcher_logs(
            os.path.join(fixtures_dir, "logs", "dispatcher-logs.json")
        )
        # The dispatcher compute time is short compared to scheduling overhead
        assert result.total_duration_s < 30, (
            f"Compute time should be short (~17s), got {result.total_duration_s}"
        )


# --- Metrics ---

class TestMetrics:
    def test_finding_7_massive_headroom(self, fixtures_dir):
        """Finding 7: Application has massive resource headroom."""
        from pipeline_cycle_time.analyzers.metrics import analyze
        result = analyze(os.path.join(fixtures_dir, "metrics"))
        assert result.cpu is not None
        # CPU peaked at ~2.09 during warmup, avg very low
        assert result.cpu.max_val > 1.5
        assert result.cpu.avg_val < 0.5
        # HikariCP: zero pending
        assert result.hikaricp_pending is not None
        assert result.hikaricp_pending.max_val == 0
        # HikariCP active max = 4
        assert result.hikaricp_active is not None
        assert result.hikaricp_active.max_val == 4
        # Memory stable ~1.6GB
        assert result.memory is not None
        assert result.memory.avg_val > 1e9


# --- Correlator (Integration) ---

class TestCorrelator:
    def _run_full_analysis(self, fixtures_dir):
        from pipeline_cycle_time.analyzers import (
            orchestration, test_reports, app_logs, metrics, correlator
        )
        d = fixtures_dir
        orch = orchestration.analyze(os.path.join(d, "logs", "concord-log.txt"))
        kono = test_reports.analyze_timeline(
            os.path.join(d, "kono-report", "data", "timeline.json"), "Kono"
        )
        sub = test_reports.analyze_timeline(
            os.path.join(d, "substantiate-report", "data", "timeline.json"), "Substantiate"
        )
        app = app_logs.analyze_webapp_logs(os.path.join(d, "logs", "webapp-logs.json"))
        disp = app_logs.analyze_dispatcher_logs(os.path.join(d, "logs", "dispatcher-logs.json"))
        met = metrics.analyze(os.path.join(d, "metrics"))
        return correlator.correlate(orch, kono, sub, app, disp, met)

    def test_all_key_findings_present(self, fixtures_dir):
        """All key findings must be present in the correlation output."""
        result = self._run_full_analysis(fixtures_dir)
        titles = [f.title for f in result.findings]

        assert any("Resume Overhead" in t for t in titles), f"Missing resume overhead. Got: {titles}"
        assert any("Polling Delay" in t for t in titles), f"Missing polling delay. Got: {titles}"
        assert any("Child" in t for t in titles), f"Missing child checking. Got: {titles}"
        assert any("Pod" in t or "K8s" in t for t in titles), f"Missing pod startup. Got: {titles}"
        assert any("ForkJoinPool" in t or "Concurrent" in t for t in titles), f"Missing kono pools. Got: {titles}"
        assert any("Setup" in t for t in titles), f"Missing setup. Got: {titles}"
        assert any("Polling Interval" in t for t in titles), f"Missing polling interval. Got: {titles}"
        assert any("Headroom" in t for t in titles), f"Missing headroom. Got: {titles}"

    def test_findings_have_evidence(self, fixtures_dir):
        result = self._run_full_analysis(fixtures_dir)
        for f in result.findings:
            assert f.evidence, f"Finding '{f.title}' missing evidence"
            assert f.description, f"Finding '{f.title}' missing description"

    def test_findings_sorted_by_rank(self, fixtures_dir):
        """Findings must be sorted by rank for deterministic output."""
        result = self._run_full_analysis(fixtures_dir)
        ranks = [f.rank for f in result.findings]
        assert ranks == sorted(ranks), f"Findings not sorted by rank: {ranks}"

    def test_critical_path_analysis(self, fixtures_dir):
        """Resume cycles overlapping with test execution are not on critical path."""
        from pipeline_cycle_time.analyzers import (
            orchestration, test_reports, app_logs, metrics, correlator
        )
        d = fixtures_dir
        orch = orchestration.analyze(os.path.join(d, "logs", "concord-log.txt"))
        kono = test_reports.analyze_timeline(
            os.path.join(d, "kono-report", "data", "timeline.json"), "Kono"
        )
        sub = test_reports.analyze_timeline(
            os.path.join(d, "substantiate-report", "data", "timeline.json"), "Substantiate"
        )
        app = app_logs.analyze_webapp_logs(os.path.join(d, "logs", "webapp-logs.json"))
        disp = app_logs.analyze_dispatcher_logs(os.path.join(d, "logs", "dispatcher-logs.json"))
        met = metrics.analyze(os.path.join(d, "metrics"))

        # Correlate — this triggers critical path marking
        correlator.correlate(orch, kono, sub, app, disp, met)

        # Resume 1 (~18:29:41) overlaps with Substantiate (ends ~18:40:18)
        assert not orch.resume_overheads[0].on_critical_path, (
            "Resume 1 should NOT be on critical path (tests still running)"
        )
        # Resume 2 (~18:41:14) is after all tests finished
        assert orch.resume_overheads[1].on_critical_path, (
            "Resume 2 should be on critical path (all tests done)"
        )
        # Critical-path overhead is only the second resume (~9s)
        assert orch.critical_path_resume_overhead_s < 12, (
            f"Critical path overhead should be ~9s, got {orch.critical_path_resume_overhead_s:.1f}s"
        )

    def test_polling_delay_finding(self, fixtures_dir):
        """The gap between test end and parent resume is surfaced as a finding."""
        result = self._run_full_analysis(fixtures_dir)
        polling_findings = [f for f in result.findings if "Polling Delay" in f.title]
        assert len(polling_findings) == 1
        f = polling_findings[0]
        # Substantiate ends ~18:40:18, parent resumes ~18:41:14 => ~56s gap
        assert "56s" in f.estimated_savings_s or "55s" in f.estimated_savings_s or "56" in f.description, (
            f"Polling delay should be ~56s, got: {f.description}"
        )

    def test_resume_savings_not_overstated(self, fixtures_dir):
        """Resume overhead savings must reflect only critical-path waste (PR #7)."""
        result = self._run_full_analysis(fixtures_dir)
        resume_finding = [f for f in result.findings if "Resume Overhead" in f.title][0]
        # Should say 9s, not 38s or 60s
        assert "9s" in resume_finding.estimated_savings_s, (
            f"Savings should be 9s (critical path only), got: {resume_finding.estimated_savings_s}"
        )
