"""Tests for report generation."""
import os
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "2026-02-24-aep"


@pytest.fixture
def fixtures_dir():
    assert FIXTURES_DIR.exists(), f"Fixtures not found at {FIXTURES_DIR}"
    return str(FIXTURES_DIR)


@pytest.fixture
def generated_report(fixtures_dir):
    from pipeline_cycle_time.cli import analyze_fixtures
    return analyze_fixtures(fixtures_dir)


class TestReportStructure:
    def test_has_executive_summary(self, generated_report):
        assert "## 1. Executive Summary" in generated_report

    def test_has_timeline(self, generated_report):
        assert "## 2. End-to-End Timeline" in generated_report

    def test_has_ranked_opportunities(self, generated_report):
        assert "## 3. Ranked Optimization Opportunities" in generated_report

    def test_has_detailed_findings(self, generated_report):
        assert "## 4. Detailed Findings" in generated_report

    def test_has_recommendations(self, generated_report):
        assert "## 5. Recommendations" in generated_report

    def test_has_quick_wins(self, generated_report):
        assert "### Quick Wins" in generated_report

    def test_has_medium_term(self, generated_report):
        assert "### Medium-Term" in generated_report


class TestReportContent:
    def test_total_tests_mentioned(self, generated_report):
        assert "1,501" in generated_report or "1501" in generated_report
        # Should report accurate pass/fail/skip breakdown
        assert "1,494 passed" in generated_report, "Should show correct pass count"
        assert "All 1,501 tests passed" not in generated_report, "Should not claim all tests passed"

    def test_kono_test_count(self, generated_report):
        assert "1,245" in generated_report or "1245" in generated_report

    def test_substantiate_test_count(self, generated_report):
        assert "256" in generated_report

    def test_concord_duration(self, generated_report):
        assert "20m" in generated_report

    def test_kono_duration(self, generated_report):
        assert "4m43s" in generated_report

    def test_ascii_timeline_present(self, generated_report):
        assert "CONCORD" in generated_report
        assert "KONO TESTS" in generated_report
        assert "SUBSTANTIATE" in generated_report
        assert "WEBAPP" in generated_report

    def test_all_key_findings_in_report(self, generated_report):
        """The report must mention all key findings."""
        report = generated_report.lower()
        assert "resume" in report, "Missing resume overhead"
        assert "polling delay" in report, "Missing polling delay"
        assert "child" in report, "Missing child checking"
        assert "pod" in report or "k8s" in report, "Missing pod startup"
        assert "forkjoinpool" in report or "pool-1" in report, "Missing kono pools"
        assert "setup" in report, "Missing setup serialization"
        assert "polling interval" in report, "Missing polling interval"
        assert "headroom" in report or "bottleneck" in report, "Missing resource headroom"

    def test_critical_path_in_report(self, generated_report):
        """Report must distinguish critical-path vs overlapping resume overhead."""
        assert "critical path" in generated_report.lower()
        assert "overlaps" in generated_report.lower()

    def test_report_date_from_data(self, generated_report):
        """Report date should come from fixture data, not wall-clock time."""
        assert "**Date:** 2026-02-24" in generated_report


class TestReportAgainstGoldenBaseline:
    def test_structural_similarity(self, fixtures_dir, generated_report):
        """Generated report should have same major sections as golden baseline."""
        golden_path = os.path.join(fixtures_dir, "ANALYSIS-REPORT.md")
        golden = Path(golden_path).read_text()

        # Both should have the same section headers
        for section in [
            "Executive Summary",
            "End-to-End Timeline",
            "Ranked Optimization",
            "Detailed Findings",
            "Recommendations",
        ]:
            assert section in generated_report, f"Missing section: {section}"
            assert section in golden, f"Golden missing section: {section}"

    def test_key_metrics_match(self, fixtures_dir, generated_report):
        """Key numeric metrics should match between generated and golden."""
        # These specific values should be the same since they come from the same data
        assert "1,245" in generated_report  # Kono test count
        assert "256" in generated_report  # Substantiate test count
        assert "283.4" in generated_report  # Kono wall-clock
        assert "2.09" in generated_report  # CPU peak


class TestReportDeterminism:
    """The same fixture data must always produce the exact same report."""

    def test_snapshot_match(self, generated_report):
        """Generated report must match committed snapshot exactly.

        If the report format changes intentionally, update the snapshot:
            python -c "
            from pipeline_cycle_time.cli import analyze_fixtures
            r = analyze_fixtures('fixtures/2026-02-24-aep')
            open('tests/ANALYSIS-REPORT.md','w').write(r)
            "
        """
        snapshot_path = Path(__file__).parent / "ANALYSIS-REPORT.md"
        assert snapshot_path.exists(), (
            "Snapshot not found. Generate it with: "
            "python -c \"from pipeline_cycle_time.cli import analyze_fixtures; "
            "open('tests/ANALYSIS-REPORT.md','w').write(analyze_fixtures('fixtures/2026-02-24-aep'))\""
        )
        snapshot = snapshot_path.read_text()
        if generated_report != snapshot:
            # Find first differing line for clear error
            gen_lines = generated_report.splitlines()
            snap_lines = snapshot.splitlines()
            for i, (g, s) in enumerate(zip(gen_lines, snap_lines)):
                if g != s:
                    pytest.fail(
                        f"Report differs from snapshot at line {i+1}:\n"
                        f"  generated: {g!r}\n"
                        f"  snapshot:  {s!r}\n"
                        f"If intentional, regenerate snapshot (see docstring)."
                    )
            if len(gen_lines) != len(snap_lines):
                pytest.fail(
                    f"Report has {len(gen_lines)} lines, snapshot has {len(snap_lines)} lines."
                )

    def test_idempotent(self, fixtures_dir):
        """Running analysis twice produces identical output."""
        from pipeline_cycle_time.cli import analyze_fixtures
        report1 = analyze_fixtures(fixtures_dir)
        report2 = analyze_fixtures(fixtures_dir)
        assert report1 == report2
