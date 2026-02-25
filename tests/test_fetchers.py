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
