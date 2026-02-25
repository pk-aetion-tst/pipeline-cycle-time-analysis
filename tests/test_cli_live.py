"""CLI live-mode orchestration tests."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline_cycle_time import cli

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "2026-02-24-aep"


def test_analyze_live_fetches_and_persists_raw_data(monkeypatch, tmp_path):
    fixture_log = (FIXTURES_DIR / "logs" / "concord-log.txt").read_text()
    fixture_webapp = json.loads((FIXTURES_DIR / "logs" / "webapp-logs.json").read_text())
    fixture_dispatcher = json.loads((FIXTURES_DIR / "logs" / "dispatcher-logs.json").read_text())

    observed_queries = []

    monkeypatch.setattr(cli.concord, "get_token_from_keychain", lambda: "tok")
    monkeypatch.setattr(
        cli.concord,
        "fetch_log",
        lambda process_id, token, concord_base_url: fixture_log,
    )

    def _fake_fetch_reports(
        bucket,
        aws_profile,
        output_dir,
        prefix="",
        process_date=None,
        instance_id=None,
        explicit_locations=None,
        allow_discovery_fallback=True,
    ):
        out = Path(output_dir)
        (out / "kono-report" / "data").mkdir(parents=True, exist_ok=True)
        (out / "substantiate-report" / "data").mkdir(parents=True, exist_ok=True)
        (out / "kono-report" / "data" / "timeline.json").write_text(
            (FIXTURES_DIR / "kono-report" / "data" / "timeline.json").read_text()
        )
        (out / "substantiate-report" / "data" / "timeline.json").write_text(
            (FIXTURES_DIR / "substantiate-report" / "data" / "timeline.json").read_text()
        )
        return {
            "kono-report": str(out / "kono-report" / "data"),
            "substantiate-report": str(out / "substantiate-report" / "data"),
        }

    monkeypatch.setattr(cli.s3_reports, "fetch_reports", _fake_fetch_reports)

    def _fake_fetch_logs(grafana_cookie, query, start_ns, end_ns, grafana_base_url):
        observed_queries.append(query)
        if "webapp" in query:
            return fixture_webapp
        return fixture_dispatcher

    monkeypatch.setattr(cli.loki, "fetch_logs", _fake_fetch_logs)
    monkeypatch.setattr(
        cli.prometheus,
        "fetch_metric",
        lambda **kwargs: {"status": "success", "data": {"result": []}},
    )
    monkeypatch.setattr(cli, "_run_analysis_from_dir", lambda base_dir: "REPORT")

    output_dir = tmp_path / "out"
    report = cli.analyze_live(
        process_id="abc-123",
        grafana_cookie="cookie",
        aws_profile="dev",
        output="",
        output_dir=str(output_dir),
    )

    assert report == "REPORT"
    assert (output_dir / "logs" / "concord-log.txt").exists()
    assert (output_dir / "logs" / "webapp-logs.json").exists()
    assert (output_dir / "logs" / "dispatcher-logs.json").exists()
    assert (output_dir / "metrics" / "webapp-cpu.json").exists()
    assert any("pod=~\"dispatcher-batch-job-236757.*\"" in q for q in observed_queries)
