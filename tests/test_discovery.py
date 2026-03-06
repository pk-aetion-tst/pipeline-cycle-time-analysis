"""Tests for the auto-discovery module."""
import pytest
from pathlib import Path

from pipeline_cycle_time.fetchers.discovery import discover, PipelineMetadata


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
