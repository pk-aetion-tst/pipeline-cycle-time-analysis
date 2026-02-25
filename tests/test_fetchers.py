"""Tests for live-mode fetchers and discovery."""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from urllib import error

import pytest

from pipeline_cycle_time.fetchers import concord, discovery, loki, prometheus, s3_reports

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "2026-02-24-aep"


class _UrlOpenResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_discovery_from_fixture_log():
    text = (FIXTURES_DIR / "logs" / "concord-log.txt").read_text()
    result = discovery.discover_from_log(text)

    assert result.namespace == "aep"
    assert result.webapp_pod == "webapp-5f9486b946-wlwbf"
    assert result.dispatcher_pod == "dispatcher-batch-job-236757-jxdsd-9h9vt"
    assert result.s3_bucket == "aep.dev.aetion.com"
    assert result.start_ns < result.end_ns


def test_keychain_token_lookup(monkeypatch):
    class _RunResult:
        stdout = "token-from-keychain\n"

    def _fake_run(cmd, check, capture_output, text):
        assert cmd[:4] == ["security", "find-generic-password", "-w", "-s"]
        return _RunResult()

    monkeypatch.setattr(concord.subprocess, "run", _fake_run)
    assert concord.get_token_from_keychain() == "token-from-keychain"


def test_concord_fetch_log_auth_error(monkeypatch):
    def _fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, 401, "Unauthorized", hdrs=None, fp=BytesIO(b""))

    monkeypatch.setattr(concord.request, "urlopen", _fake_urlopen)

    with pytest.raises(RuntimeError, match="Concord authentication failed"):
        concord.fetch_log("abc", "bad-token")


def test_s3_fetch_reports_downloads_expected_paths(monkeypatch, tmp_path):
    calls = []

    class _RunResult:
        def __init__(self, stdout: str):
            self.stdout = stdout
            self.stderr = ""

    ls_output = "\n".join(
        [
            "2026-02-24 18:00:00      10 some/prefix/kono-report/data/timeline.json",
            "2026-02-24 18:00:00      10 some/prefix/substantiate-report/data/timeline.json",
        ]
    )

    def _fake_run(cmd, check, capture_output, text):
        calls.append(cmd)
        if cmd[:6] == ["aws", "--profile", "dev", "s3", "ls", "s3://bucket/"]:
            return _RunResult(ls_output)
        return _RunResult("")

    monkeypatch.setattr(s3_reports.subprocess, "run", _fake_run)

    result = s3_reports.fetch_reports("bucket", "dev", str(tmp_path))

    assert "kono-report" in result
    assert "substantiate-report" in result
    assert any("cp" in c for cmd in calls for c in cmd)


def test_s3_fetch_reports_scopes_listing_to_process_date(monkeypatch, tmp_path):
    calls = []

    class _RunResult:
        def __init__(self, stdout: str):
            self.stdout = stdout
            self.stderr = ""

    ls_for_day = "\n".join(
        [
            "2026-02-24 18:00:00      10 2026-02-24/kono-report/data/timeline.json",
            "2026-02-24 18:00:00      10 2026-02-24/substantiate-report/data/timeline.json",
        ]
    )

    def _fake_run(cmd, check, capture_output, text):
        calls.append(cmd)
        if cmd[:4] == ["aws", "--profile", "dev", "s3"] and cmd[4] == "ls":
            target = cmd[5]
            if target.endswith("/2026-02-24/"):
                return _RunResult(ls_for_day)
            return _RunResult("")
        return _RunResult("")

    monkeypatch.setattr(s3_reports.subprocess, "run", _fake_run)

    _ = s3_reports.fetch_reports(
        "bucket",
        "dev",
        str(tmp_path),
        prefix="aep-dev",
        process_date="2026-02-24",
    )

    ls_targets = [cmd[5] for cmd in calls if len(cmd) > 5 and cmd[4] == "ls"]
    assert all("/aep-dev/" in target for target in ls_targets)
    assert "s3://bucket/" not in ls_targets


def test_loki_fetch_uses_cookie_and_query(monkeypatch):
    captured = {}

    payload = {"status": "success", "data": {"result": []}}

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["cookie"] = req.headers.get("Cookie")
        return _UrlOpenResponse(json.dumps(payload).encode())

    monkeypatch.setattr(loki.request, "urlopen", _fake_urlopen)

    result = loki.fetch_logs(
        grafana_cookie="grafana_session=abc",
        query='{namespace="aep", pod="webapp"}',
        start_ns=1,
        end_ns=2,
    )
    assert result["status"] == "success"
    assert "query=" in captured["url"]
    assert captured["cookie"] == "grafana_session=abc"


def test_prometheus_fetch_uses_cookie_and_query(monkeypatch):
    captured = {}

    payload = {"status": "success", "data": {"result": []}}

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["cookie"] = req.headers.get("Cookie")
        return _UrlOpenResponse(json.dumps(payload).encode())

    monkeypatch.setattr(prometheus.request, "urlopen", _fake_urlopen)

    result = prometheus.fetch_metric(
        grafana_cookie="session-token",
        query="up",
        start_s=1.0,
        end_s=2.0,
    )
    assert result["status"] == "success"
    assert "query=up" in captured["url"]
    assert captured["cookie"] == "grafana_session=session-token"


def test_s3_locations_discovered_from_concord_logs():
    text = """
    upload: ... to s3://reports.dev.aetion.com/aep.dev.aetion.com/kono-integration/report-20260223-1359-08/data/timeline.json
    upload: ... to s3://reports.dev.aetion.com/aep.dev.aetion.com/substantiate-e2e/report-20260223-1410-55/data/timeline.json
    """
    found = s3_reports.discover_locations_from_concord_logs([text])
    assert found["kono-report"][0] == "reports.dev.aetion.com"
    assert "kono-integration/report-20260223-1359-08" in found["kono-report"][1]
    assert found["substantiate-report"][0] == "reports.dev.aetion.com"
    assert "substantiate-e2e/report-20260223-1410-55" in found["substantiate-report"][1]


def test_s3_locations_use_allure_report_context_and_dst_hint():
    text = """
    == ALLURE REPORT (SUBSTANTIATE) ==
    Copy report to S3
    dst: aep.dev.aetion.com/integration-tests/report-20260223-1410-55
    upload: ... to s3://reports.dev.aetion.com/aep.dev.aetion.com/integration-tests/report-20260223-1410-55/data/timeline.json
    == ALLURE REPORT (KONO) ==
    dst: aep.dev.aetion.com/e2e-tests/report-20260223-1359-08
    upload: ... to s3://reports.dev.aetion.com/aep.dev.aetion.com/e2e-tests/report-20260223-1359-08/data/timeline.json
    """
    found = s3_reports.discover_locations_from_concord_logs([text])
    assert found["substantiate-report"][0] == "reports.dev.aetion.com"
    assert "integration-tests/report-20260223-1410-55" in found["substantiate-report"][1]
    assert found["kono-report"][0] == "reports.dev.aetion.com"
    assert "e2e-tests/report-20260223-1359-08" in found["kono-report"][1]


def test_extract_child_process_ids():
    text = """
    Started a process: <concord:instanceId>11111111-1111-1111-1111-111111111111</concord:instanceId>
    Started a process: <concord:instanceId>22222222-2222-2222-2222-222222222222</concord:instanceId>
    Started a process: <concord:instanceId>11111111-1111-1111-1111-111111111111</concord:instanceId>
    """
    child_ids = discovery.extract_child_process_ids(text)
    assert child_ids == (
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    )
