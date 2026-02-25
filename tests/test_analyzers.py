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
        """Finding 1: Concord resume overhead (~20s from redundant repo export + dep resolution)."""
        from pipeline_cycle_time.analyzers.orchestration import analyze
        result = analyze(os.path.join(fixtures_dir, "logs", "concord-log.txt"))
        assert result.resume_count == 2, "Should detect 2 resume cycles"
        # Total overhead should be meaningful (golden says ~20s)
        assert result.total_resume_overhead_s > 15, (
            f"Resume overhead {result.total_resume_overhead_s}s is too low"
        )

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

    def test_all_seven_findings_present(self, fixtures_dir):
        """All 7 key findings must be present in the correlation output."""
        result = self._run_full_analysis(fixtures_dir)
        titles = [f.title for f in result.findings]

        # Finding 1: Concord resume overhead
        assert any("Resume Overhead" in t for t in titles), f"Missing resume overhead finding. Got: {titles}"

        # Finding 2: Sequential child checking
        assert any("Child" in t for t in titles), f"Missing sequential child finding. Got: {titles}"

        # Finding 3: K8s pod startup latency
        assert any("Pod" in t or "K8s" in t for t in titles), f"Missing pod startup finding. Got: {titles}"

        # Finding 4: Kono sequential pools
        assert any("ForkJoinPool" in t or "Concurrent" in t for t in titles), f"Missing kono pools finding. Got: {titles}"

        # Finding 5: Substantiate setup serialization
        assert any("Setup" in t for t in titles), f"Missing setup finding. Got: {titles}"

        # Finding 6: Polling interval
        assert any("Polling" in t for t in titles), f"Missing polling finding. Got: {titles}"

        # Finding 7: Application headroom
        assert any("Headroom" in t for t in titles), f"Missing headroom finding. Got: {titles}"

    def test_findings_have_evidence(self, fixtures_dir):
        result = self._run_full_analysis(fixtures_dir)
        for f in result.findings:
            assert f.evidence, f"Finding '{f.title}' missing evidence"
            assert f.description, f"Finding '{f.title}' missing description"
