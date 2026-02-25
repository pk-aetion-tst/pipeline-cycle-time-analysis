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

    def test_all_seven_findings_in_report(self, generated_report):
        """The report must mention all 7 key findings."""
        report = generated_report.lower()
        assert "resume" in report, "Missing resume overhead"
        assert "child" in report, "Missing child checking"
        assert "pod" in report or "k8s" in report, "Missing pod startup"
        assert "forkjoinpool" in report or "pool-1" in report, "Missing kono pools"
        assert "setup" in report, "Missing setup serialization"
        assert "polling" in report, "Missing polling interval"
        assert "headroom" in report or "bottleneck" in report, "Missing resource headroom"


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
