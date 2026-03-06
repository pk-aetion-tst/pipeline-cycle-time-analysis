"""Tests for the auto-discovery module."""
import pytest
from pathlib import Path

from pipeline_cycle_time.fetchers.discovery import (
    discover, discover_report_locations, PipelineMetadata, ReportLocation,
    DeployedComponent,
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

    def test_deployed_components(self, meta):
        labels = {c.label for c in meta.deployed_components}
        assert "webapp" in labels
        assert "dispatcher" in labels

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

    def test_deployed_components_from_monitoring_urls(self):
        log = (
            '<https://monitoring.dev.example.com/d/ptfAcf04z?var-namespace=aep'
            '&var-pod=unified-homepage-d5f74bdd9-h8rlt&from=123&to=456|unified-homepage>\n'
            '<https://monitoring.dev.example.com/d/ptfAcf04z?var-namespace=aep'
            '&var-pod=discover-68fb747b5b-nqlk7&from=123&to=456|discover>\n'
            '<https://monitoring.dev.example.com/d/ptfAcf04z?var-namespace=aep'
            '&var-pod=fe-aep-56c794d5bb-2xprt&from=123&to=456|fe-aep>\n'
        )
        meta = discover(log)
        labels = {c.label for c in meta.deployed_components}
        assert labels == {"unified-homepage", "discover", "fe-aep"}
        assert meta.grafana_base_url == "https://monitoring.dev.example.com"
        # webapp_pod/dispatcher_pod convenience properties return "" when not present
        assert meta.webapp_pod == ""
        assert meta.dispatcher_pod == ""

    def test_deployed_components_deduplicates(self):
        log = (
            '<https://monitoring.example.com/d/x?var-pod=webapp-abc123&from=1|webapp>\n'
            '<https://monitoring.example.com/d/x?var-pod=webapp-abc123&from=2|webapp>\n'
        )
        meta = discover(log)
        assert len(meta.deployed_components) == 1

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
