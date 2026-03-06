"""Tests for live fetchers (mocked HTTP responses)."""
from __future__ import annotations

import json
import io
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

from pipeline_cycle_time.fetchers import concord, loki, prometheus, s3_reports
from pipeline_cycle_time.fetchers.prometheus import METRIC_QUERIES, DISPATCHER_QUERIES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_urlopen(body: bytes | str, status: int = 200):
    """Return a context-manager mock for urllib.request.urlopen."""
    if isinstance(body, str):
        body = body.encode("utf-8")
    resp = io.BytesIO(body)
    resp.status = status
    cm = mock.MagicMock()
    cm.__enter__ = mock.Mock(return_value=resp)
    cm.__exit__ = mock.Mock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# Concord fetcher
# ---------------------------------------------------------------------------

class TestConcordFetcher:

    @mock.patch("urllib.request.urlopen")
    def test_fetch_log_with_token(self, mock_open):
        mock_open.return_value = _mock_urlopen("line1\nline2\n")
        result = concord.fetch_log("abc-123", token="my-token")
        assert result == "line1\nline2\n"

        # Verify correct URL and auth header
        call_args = mock_open.call_args
        req = call_args[0][0]
        assert "abc-123" in req.full_url
        assert req.get_header("Authorization") == "Bearer my-token"

    @mock.patch("subprocess.run")
    @mock.patch("urllib.request.urlopen")
    def test_fetch_log_from_keychain(self, mock_open, mock_run):
        mock_run.return_value = mock.Mock(returncode=0, stdout="keychain-token\n")
        mock_open.return_value = _mock_urlopen("log data")
        result = concord.fetch_log("abc-123")
        assert result == "log data"

        req = mock_open.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer keychain-token"

    @mock.patch("subprocess.run")
    def test_fetch_log_keychain_failure(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=1, stdout="", stderr="not found")
        with pytest.raises(RuntimeError, match="keychain"):
            concord.fetch_log("abc-123")


# ---------------------------------------------------------------------------
# Loki fetcher
# ---------------------------------------------------------------------------

class TestLokiFetcher:

    LOKI_RESPONSE = json.dumps({
        "status": "success",
        "data": {"resultType": "streams", "result": [
            {"stream": {"pod": "webapp-abc"}, "values": [["123456", "log line"]]}
        ]}
    })

    @mock.patch("urllib.request.urlopen")
    def test_fetch_with_token(self, mock_open):
        mock_open.return_value = _mock_urlopen(self.LOKI_RESPONSE)
        result = loki.fetch_logs(
            namespace="aep", pod_selector="webapp-abc",
            start_ns=100, end_ns=200,
            grafana_token="tok123",
        )
        assert result["status"] == "success"
        req = mock_open.call_args[0][0]
        assert req.get_header("Authorization") == "Bearer tok123"

    @mock.patch("urllib.request.urlopen")
    def test_fetch_with_cookie(self, mock_open):
        mock_open.return_value = _mock_urlopen(self.LOKI_RESPONSE)
        result = loki.fetch_logs(
            namespace="aep", pod_selector="webapp-abc",
            start_ns=100, end_ns=200,
            grafana_cookie="sess123",
        )
        assert result["status"] == "success"
        req = mock_open.call_args[0][0]
        assert "grafana_session=sess123" in req.get_header("Cookie")

    def test_fetch_no_auth_raises(self):
        with pytest.raises(ValueError, match="grafana_cookie or grafana_token"):
            loki.fetch_logs(
                namespace="aep", pod_selector="webapp-abc",
                start_ns=100, end_ns=200,
            )

    @mock.patch("urllib.request.urlopen")
    def test_regex_selector(self, mock_open):
        mock_open.return_value = _mock_urlopen(self.LOKI_RESPONSE)
        loki.fetch_logs(
            namespace="aep", pod_selector="dispatcher-batch-job-123.*",
            start_ns=100, end_ns=200,
            grafana_token="tok",
        )
        req = mock_open.call_args[0][0]
        # =~ gets URL-encoded as %3D~ in the query string
        from urllib.parse import unquote
        assert "=~" in unquote(req.full_url)


# ---------------------------------------------------------------------------
# Prometheus fetcher
# ---------------------------------------------------------------------------

class TestPrometheusFetcher:

    PROM_RESPONSE = json.dumps({
        "status": "success",
        "data": {"resultType": "matrix", "result": [
            {"metric": {"container": "webapp"}, "values": [[1000, "0.5"]]}
        ]}
    })

    @mock.patch("urllib.request.urlopen")
    def test_fetch_with_token(self, mock_open):
        mock_open.return_value = _mock_urlopen(self.PROM_RESPONSE)
        result = prometheus.fetch_metric(
            query="rate(cpu[1m])", start_s=100, end_s=200,
            grafana_token="tok",
        )
        assert result["status"] == "success"

    @mock.patch("urllib.request.urlopen")
    def test_fetch_with_cookie(self, mock_open):
        mock_open.return_value = _mock_urlopen(self.PROM_RESPONSE)
        result = prometheus.fetch_metric(
            query="rate(cpu[1m])", start_s=100, end_s=200,
            grafana_cookie="sess",
        )
        assert result["status"] == "success"

    def test_fetch_no_auth_raises(self):
        with pytest.raises(ValueError, match="grafana_cookie or grafana_token"):
            prometheus.fetch_metric(query="x", start_s=0, end_s=1)

    def test_metric_queries_defined(self):
        assert len(METRIC_QUERIES) == 8
        assert len(DISPATCHER_QUERIES) == 2

    def test_query_templates_have_placeholders(self):
        for query_tpl, filename, desc in METRIC_QUERIES + DISPATCHER_QUERIES:
            formatted = query_tpl.format(namespace="aep", pod="webapp-xyz")
            assert "aep" in formatted
            assert "webapp-xyz" in formatted


# ---------------------------------------------------------------------------
# S3 report fetcher
# ---------------------------------------------------------------------------

class TestS3Fetcher:

    @mock.patch("subprocess.run")
    def test_fetch_report_single(self, mock_run, tmp_path):
        mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        result = s3_reports.fetch_report(
            "s3://bucket/folder/kono-report/data/", "kono-report", "dev", str(tmp_path),
        )
        assert "kono-report" in result
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "aws"
        assert "s3" in cmd
        assert "--profile" in cmd

    @mock.patch("subprocess.run")
    def test_fetch_report_failure(self, mock_run, tmp_path):
        mock_run.return_value = mock.Mock(returncode=1, stdout="", stderr="Access Denied")
        with pytest.raises(RuntimeError, match="Access Denied"):
            s3_reports.fetch_report(
                "s3://bucket/folder/kono-report/data/", "kono-report", "dev", str(tmp_path),
            )

    @mock.patch("subprocess.run")
    def test_fetch_reports_fallback(self, mock_run, tmp_path):
        mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        result = s3_reports.fetch_reports("aep-dev", "dev", str(tmp_path))
        assert "kono-report" in result
        assert "substantiate-report" in result
        assert mock_run.call_count == 2


# ---------------------------------------------------------------------------
# CLI live mode integration (mocked fetchers)
# ---------------------------------------------------------------------------

class TestLiveModeIntegration:
    """Test that analyze_live orchestrates fetchers correctly."""

    @mock.patch("pipeline_cycle_time.fetchers.s3_reports.fetch_report")
    @mock.patch("pipeline_cycle_time.fetchers.prometheus.fetch_metric")
    @mock.patch("pipeline_cycle_time.fetchers.loki.fetch_logs")
    @mock.patch("pipeline_cycle_time.fetchers.concord.fetch_log")
    def test_live_mode_discovers_reports_from_children(
        self, mock_concord, mock_loki, mock_prom, mock_s3_report, tmp_path
    ):
        fixture_dir = Path(__file__).parent.parent / "fixtures" / "2026-02-24-aep"
        parent_log = (fixture_dir / "logs" / "concord-log.txt").read_text()

        # Child logs contain S3 report upload paths
        child_log_kono = (
            "Running kono tests...\n"
            "Uploading report to s3://my-bucket/aep-dev/kono-report/data/timeline.json\n"
        )
        child_log_sub = (
            "Running substantiate tests...\n"
            "aws s3 cp ... s3://my-bucket/aep-dev/substantiate-report/data/timeline.json\n"
        )

        # fetch_log returns parent log first, then child logs
        mock_concord.side_effect = [parent_log, child_log_kono, child_log_sub]

        loki_resp = {"status": "success", "data": {"resultType": "streams", "result": []}}
        mock_loki.return_value = loki_resp

        prom_resp = {"status": "success", "data": {"resultType": "matrix", "result": []}}
        mock_prom.return_value = prom_resp

        # S3 fetch_report copies fixture data for each report
        def fake_s3_report(s3_uri, report_name, profile, output_dir):
            import shutil
            src = fixture_dir / report_name / "data"
            dst = Path(output_dir) / report_name / "data"
            if src.exists():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            return str(Path(output_dir) / report_name)
        mock_s3_report.side_effect = fake_s3_report

        from pipeline_cycle_time.cli import analyze_live
        out_dir = tmp_path / "output"
        report = analyze_live(
            process_id="test-uuid",
            output_dir=str(out_dir),
            grafana_token="test-token",
            aws_profile="dev",
        )

        # Verify raw data was saved
        assert (out_dir / "logs" / "concord-log.txt").exists()
        assert (out_dir / "logs" / "webapp-logs.json").exists()
        assert (out_dir / "logs" / "dispatcher-logs.json").exists()

        # Verify child logs were saved
        child_logs = list((out_dir / "logs").glob("child-*.txt"))
        assert len(child_logs) == 2

        # Verify report was generated
        assert "pipeline" in report.lower()

        # Verify fetch_log called 3 times: parent + 2 children
        assert mock_concord.call_count == 3

        # Verify S3 reports fetched via discovered URIs (not fallback)
        assert mock_s3_report.call_count == 2
        s3_calls = [call[0] for call in mock_s3_report.call_args_list]
        s3_uris = {call[0] for call in s3_calls}
        assert "s3://my-bucket/aep-dev/kono-report/data/" in s3_uris
        assert "s3://my-bucket/aep-dev/substantiate-report/data/" in s3_uris

        # Verify Loki called with correct auth
        for call in mock_loki.call_args_list:
            assert call.kwargs.get("grafana_token") == "test-token"

    @mock.patch("pipeline_cycle_time.fetchers.s3_reports.fetch_reports")
    @mock.patch("pipeline_cycle_time.fetchers.prometheus.fetch_metric")
    @mock.patch("pipeline_cycle_time.fetchers.loki.fetch_logs")
    @mock.patch("pipeline_cycle_time.fetchers.concord.fetch_log")
    def test_live_mode_falls_back_when_no_reports_in_children(
        self, mock_concord, mock_loki, mock_prom, mock_s3_fallback, tmp_path
    ):
        fixture_dir = Path(__file__).parent.parent / "fixtures" / "2026-02-24-aep"
        parent_log = (fixture_dir / "logs" / "concord-log.txt").read_text()

        # Child logs have no S3 report URIs
        child_log = "Running tests...\nDone.\n"
        mock_concord.side_effect = [parent_log, child_log, child_log]

        loki_resp = {"status": "success", "data": {"resultType": "streams", "result": []}}
        mock_loki.return_value = loki_resp
        prom_resp = {"status": "success", "data": {"resultType": "matrix", "result": []}}
        mock_prom.return_value = prom_resp

        def fake_s3_fallback(s3_folder, profile, output_dir, **kwargs):
            import shutil
            for name in ("kono-report", "substantiate-report"):
                src = fixture_dir / name / "data"
                dst = Path(output_dir) / name / "data"
                if src.exists():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
            return {
                "kono-report": str(Path(output_dir) / "kono-report"),
                "substantiate-report": str(Path(output_dir) / "substantiate-report"),
            }
        mock_s3_fallback.side_effect = fake_s3_fallback

        from pipeline_cycle_time.cli import analyze_live
        out_dir = tmp_path / "output"
        report = analyze_live(
            process_id="test-uuid",
            output_dir=str(out_dir),
            grafana_token="test-token",
            aws_profile="dev",
        )

        # Fallback fetch_reports should have been called
        mock_s3_fallback.assert_called_once()
        assert "pipeline" in report.lower()
