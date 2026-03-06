"""Tests for the auto-discovery module."""
import pytest
from pathlib import Path

from pipeline_cycle_time.fetchers.discovery import (
    discover, discover_report_locations, get_child_end_epoch_s,
    PipelineMetadata, ReportLocation,
)


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


class TestDiscoveryFromFixture:
    """Test discovery against the real 2026-02-24 Concord log fixture."""

    @pytest.fixture
    def meta(self) -> PipelineMetadata:
        log_path = FIXTURES_DIR / "2026-02-24-aep" / "logs" / "concord-log.txt"
        return discover(log_path.read_text())

    def test_namespace(self, meta):
        assert meta.namespace == "aep"

    def test_webapp_pod(self, meta):
        assert meta.webapp_pod == "webapp-5f9486b946-wlwbf"

    def test_dispatcher_pod(self, meta):
        assert "dispatcher-batch-job-236757" in meta.dispatcher_pod

    def test_s3_bucket(self, meta):
        assert meta.s3_bucket == "aep.dev.aetion.com"

    def test_s3_folder(self, meta):
        assert meta.s3_folder == "aep-dev"

    def test_grafana_base_url(self, meta):
        assert "monitoring" in meta.grafana_base_url
        assert meta.grafana_base_url.startswith("https://")

    def test_time_window(self, meta):
        assert meta.start_epoch_ns > 0
        assert meta.end_epoch_ns > meta.start_epoch_ns
        # Duration should be ~20 minutes (1200s) — sanity check
        duration_s = (meta.end_epoch_ns - meta.start_epoch_ns) / 1e9
        assert 1000 < duration_s < 2000

    def test_epoch_s_properties(self, meta):
        assert meta.start_epoch_s == meta.start_epoch_ns / 1e9
        assert meta.end_epoch_s == meta.end_epoch_ns / 1e9

    def test_children_extracted(self, meta):
        assert len(meta.children) == 2
        assert "806f92a1-ac9b-4a18-8bc3-7334cfc53878" in meta.children
        assert "c6cbe79e-0a06-4b8d-ac8c-3722e608fc2c" in meta.children


class TestDiscoveryFromMarch:
    """Test discovery against the 2026-03-06 fixture."""

    @pytest.fixture
    def meta(self) -> PipelineMetadata:
        log_path = FIXTURES_DIR / "2026-03-06-aep" / "logs" / "concord-log.txt"
        return discover(log_path.read_text())

    def test_namespace(self, meta):
        assert meta.namespace == "aep"

    def test_has_time_window(self, meta):
        assert meta.start_epoch_ns > 0
        assert meta.end_epoch_ns > meta.start_epoch_ns


class TestDiscoverySynthetic:
    """Test discovery with minimal synthetic log snippets."""

    def test_empty_log(self):
        meta = discover("")
        assert meta.namespace == ""
        assert meta.start_epoch_ns == 0

    def test_namespace_extraction(self):
        log = '    namespace: "myns"\n'
        meta = discover(log)
        assert meta.namespace == "myns"

    def test_s3_bucket_extraction(self):
        log = '  s3Bucket: "my.bucket.com"\n  s3Folder: "my-folder"\n'
        meta = discover(log)
        assert meta.s3_bucket == "my.bucket.com"
        assert meta.s3_folder == "my-folder"

    def test_timestamp_extraction(self):
        log = (
            "2026-01-01T10:00:00.000+0000 [INFO ] Start\n"
            "2026-01-01T10:10:00.000+0000 [INFO ] End\n"
        )
        meta = discover(log)
        duration_s = (meta.end_epoch_ns - meta.start_epoch_ns) / 1e9
        assert duration_s == pytest.approx(600.0, abs=1.0)

    def test_children_extraction(self):
        log = (
            '2026-01-01T10:00:00.000+0000 [INFO ] Started a process: '
            '<concord:instanceId>abc-111</concord:instanceId>\n'
            '2026-01-01T10:00:01.000+0000 [INFO ] Started a process: '
            '<concord:instanceId>def-222</concord:instanceId>\n'
        )
        meta = discover(log)
        assert meta.children == ["abc-111", "def-222"]


class TestDiscoverReportLocations:
    """Test report location discovery from child process logs."""

    def test_kono_report_s3_cp(self):
        log = (
            "Uploading report to s3://my-bucket/my-folder/kono-report/data/timeline.json\n"
            "upload complete\n"
        )
        locs = discover_report_locations("child-1", log)
        assert len(locs) == 1
        assert locs[0].report_name == "kono-report"
        assert locs[0].s3_uri == "s3://my-bucket/my-folder/kono-report/data/"
        assert locs[0].child_id == "child-1"

    def test_substantiate_report(self):
        log = "aws s3 cp ... s3://bucket/folder/substantiate-report/data/behaviors.json\n"
        locs = discover_report_locations("child-2", log)
        assert len(locs) == 1
        assert locs[0].report_name == "substantiate-report"
        assert locs[0].s3_uri == "s3://bucket/folder/substantiate-report/data/"

    def test_both_reports_in_same_log(self):
        log = (
            "s3://b/f/kono-report/data/timeline.json uploaded\n"
            "s3://b/f/substantiate-report/data/timeline.json uploaded\n"
        )
        locs = discover_report_locations("child-1", log)
        names = {loc.report_name for loc in locs}
        assert names == {"kono-report", "substantiate-report"}

    def test_deduplicates_same_report(self):
        log = (
            "s3://b/f/kono-report/data/timeline.json\n"
            "s3://b/f/kono-report/data/behaviors.json\n"
        )
        locs = discover_report_locations("child-1", log)
        assert len(locs) == 1

    def test_no_reports(self):
        log = "some random log line with no s3 URIs\n"
        locs = discover_report_locations("child-1", log)
        assert locs == []

    def test_empty_log(self):
        assert discover_report_locations("child-1", "") == []


class TestGetChildEndEpochS:
    """Test child process end time extraction."""

    def test_extracts_last_timestamp(self):
        log = (
            "2026-03-06T13:00:00.000+0000 [INFO ] Start\n"
            "2026-03-06T13:48:29.000+0000 [INFO ] Done\n"
        )
        end_s = get_child_end_epoch_s(log)
        assert end_s == pytest.approx(1772804909.0, abs=1.0)

    def test_empty_log_returns_zero(self):
        assert get_child_end_epoch_s("") == 0.0

    def test_no_timestamps_returns_zero(self):
        assert get_child_end_epoch_s("no timestamps here\n") == 0.0

    def test_later_than_test_end(self):
        """Child end time should be later than test assertions finish.

        In a real pipeline, GHA job teardown adds ~17s between test
        completion and the child process finishing.
        """
        # Test finishes at 13:48:12, child finishes at 13:48:29
        child_log = (
            "2026-03-06T13:40:00.000+0000 [INFO ] Tests starting\n"
            "2026-03-06T13:48:29.000+0000 [INFO ] Job cleanup complete\n"
        )
        test_end_epoch_s = 1772804892.0  # 13:48:12
        child_end = get_child_end_epoch_s(child_log)
        assert child_end > test_end_epoch_s
        assert child_end - test_end_epoch_s == pytest.approx(17.0, abs=1.0)


class TestPollingGapWithChildEndTime:
    """Verify correlator uses child end time for more accurate polling gap."""

    def test_polling_gap_shrinks_with_child_end_time(self):
        from pipeline_cycle_time.analyzers.correlator import correlate
        from pipeline_cycle_time.analyzers.orchestration import (
            OrchestrationResult, ResumeOverhead,
        )
        from pipeline_cycle_time.analyzers.test_reports import TestSuiteResult, TestInfo
        from pipeline_cycle_time.analyzers.app_logs import AppLogsResult, DispatcherTimeline
        from pipeline_cycle_time.analyzers.metrics import MetricsResult
        from datetime import datetime, timezone

        # Parent resumes at 13:49:03
        resume_time = datetime(2026, 3, 6, 13, 49, 3, tzinfo=timezone.utc)
        orch = OrchestrationResult(
            resume_overheads=[ResumeOverhead(
                resume_time=resume_time,
                repo_export_s=3.0, dep_resolution_s=5.0,
                on_critical_path=True,
            )],
            total_start=datetime(2026, 3, 6, 13, 30, 0, tzinfo=timezone.utc),
            total_end=datetime(2026, 3, 6, 13, 50, 0, tzinfo=timezone.utc),
        )
        # Tests ended at 13:48:12 (epoch 1772804892)
        test_end_ms = 1772804892 * 1000
        fake_test = TestInfo(
            name="t", uid="1", status="passed",
            start=test_end_ms - 60000, stop=test_end_ms,
            duration=60000, worker="w1",
        )
        kono = TestSuiteResult(name="Kono", tests=[fake_test])
        sub = TestSuiteResult(name="Sub", tests=[fake_test])

        app = AppLogsResult()
        disp = DispatcherTimeline()
        met = MetricsResult()

        # Without child end time: gap = 13:49:03 - 13:48:12 = 51s
        result_no_child = correlate(orch, kono, sub, app, disp, met)
        polling_findings = [f for f in result_no_child.findings
                           if "Suspend Polling" in f.title]
        assert len(polling_findings) == 1
        assert "51s" in polling_findings[0].evidence

        # Reset on_critical_path (correlate mutates it)
        orch.resume_overheads[0].on_critical_path = True

        # With child end time (13:48:29): gap = 13:49:03 - 13:48:29 = 34s
        child_end_epoch_s = 1772804909.0  # 13:48:29
        result_with_child = correlate(
            orch, kono, sub, app, disp, met,
            children_end_epoch_s=child_end_epoch_s,
        )
        polling_findings2 = [f for f in result_with_child.findings
                            if "Suspend Polling" in f.title]
        assert len(polling_findings2) == 1
        assert "34s" in polling_findings2[0].evidence
        assert "Child process ended" in polling_findings2[0].evidence
