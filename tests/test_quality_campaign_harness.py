"""Verify evidence helpers and deterministic faults without touching product data."""

import importlib.util
from http.client import RemoteDisconnected
from pathlib import Path
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.request import build_opener, ProxyHandler
from urllib.error import URLError

import pytest


def module(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / "campaign" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"quality_{name}", path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def test_fixture_reproducibility_and_labels(tmp_path):
    fixtures = module("fixtures")
    first = fixtures.materialize(tmp_path / "one")
    second = fixtures.materialize(tmp_path / "two")
    assert first == second
    assert first["F-IMG"]["semantic_cat_labels"] is False
    assert first["REID"]["state"] == "BLOCKED"
    assert list(fixtures.catalog_rows(40)) == list(fixtures.catalog_rows(40))


def test_metrics_never_promote_small_pilot_to_reliable_p99():
    measurements = module("measurements")
    result = measurements.summarize([{"elapsed_ms": i} for i in range(30)])
    assert result["p99"] is None
    assert result["n"] == 30
    assert "pilot" in result["uncertainty"]
    boundary = measurements.summarize([{"elapsed_ms": i} for i in range(20)])
    assert boundary["p95_ms_exploratory"] == 18


@pytest.mark.parametrize(
    "mode,expected_requests", [("drop_before", 0), ("drop_after", 1), ("delay", 1)]
)
def test_proxy_faults_distinguish_before_and_after_effect(mode, expected_requests):
    proxy_module = module("fault_proxy")
    effects = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass

        def do_GET(self):
            effects.append("committed-test-effect")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    proxy = proxy_module.server(
        f"http://127.0.0.1:{upstream.server_port}", mode=mode, delay_ms=5
    )
    threads = [Thread(target=s.serve_forever, daemon=True) for s in (upstream, proxy)]
    for thread in threads:
        thread.start()
    try:
        opener = build_opener(ProxyHandler({}))
        if mode.startswith("drop"):
            with pytest.raises((RemoteDisconnected, URLError)):
                opener.open(f"http://127.0.0.1:{proxy.server_port}", timeout=3)
        else:
            with opener.open(
                f"http://127.0.0.1:{proxy.server_port}", timeout=3
            ) as result:
                assert result.read() == b"ok"
        assert len(effects) == expected_requests
    finally:
        for instance in (proxy, upstream):
            instance.shutdown()
            instance.server_close()
        for thread in threads:
            thread.join(timeout=3)


def test_proxy_refuses_live_services_and_external_hosts():
    proxy = module("fault_proxy")
    for target in (
        "http://127.0.0.1:8000",
        "http://127.0.0.1:5432",
        "https://example.com:12345",
    ):
        with pytest.raises(ValueError):
            proxy.server(target)


def test_archive_preserves_raw_evidence_and_verifies_members(tmp_path):
    import json
    import tarfile

    archive = module("archive_evidence")
    run = tmp_path / "quality-campaign" / "synthetic"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text("{}", encoding="utf-8")
    observation = run / "observations.json"
    observation.write_text('{"synthetic": true}', encoding="utf-8")
    product = run / "sources" / "B" / "api" / "app.py"
    product.parent.mkdir(parents=True)
    product.write_text("not evidence", encoding="utf-8")
    archive.archive(run)
    folder = tmp_path / "quality-archives"
    receipt = json.loads(
        next(folder.glob("*.receipt.json")).read_text(encoding="utf-8")
    )
    assert receipt["originals_removed"] is False and receipt["verified_members"] == 2
    assert observation.exists() and product.exists()
    with tarfile.open(folder / receipt["archive"]) as stored:
        assert set(stored.getnames()) == {"manifest.json", "observations.json"}


def test_prepare_freezes_probe_from_flutter_package_and_preserves_snapshot(
    tmp_path, monkeypatch
):
    import hashlib
    import json

    script = Path(__file__).resolve().parents[1] / "scripts/quality_campaign.py"
    spec = importlib.util.spec_from_file_location("campaign_entry", script)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    root = tmp_path / "api"
    campaign = root / ".tmp/quality-campaign/synthetic"
    (campaign / "fixtures").mkdir(parents=True)
    (campaign / "fixtures/manifest.json").write_text("{}", encoding="utf-8")
    scripts = root / "scripts/campaign"
    scripts.mkdir(parents=True)
    (scripts / "fixtures.py").write_text(
        "# pre-existing synthetic fixtures", encoding="utf-8"
    )
    (root / "scripts/run_integration_checks.py").write_text("pass", encoding="utf-8")
    tools = tmp_path / "flutter/tool"
    tools.mkdir(parents=True)
    probe = tools / "campaign_probe_test.dart"
    probe.write_text("// first immutable probe snapshot", encoding="utf-8")
    (campaign / "manifest.json").write_text(
        json.dumps({"sources": {"B-flutter": {"repo": str(tools.parent)}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(loaded, "ROOT", root)
    loaded.prepare(campaign, "layout-01")
    frozen = campaign / "attempts/layout-01/executors/campaign_probe_test.dart"
    assert frozen.read_bytes() == probe.read_bytes()
    manifest = json.loads(
        (frozen.parents[1] / "executor_manifest.json").read_text(encoding="utf-8")
    )
    assert (
        manifest["hashes"][probe.name] == hashlib.sha256(probe.read_bytes()).hexdigest()
    )
    probe.write_text("// later edit", encoding="utf-8")
    assert frozen.read_text(encoding="utf-8") == "// first immutable probe snapshot"
    with pytest.raises(ValueError):
        loaded.prepare(campaign, "layout-01")
