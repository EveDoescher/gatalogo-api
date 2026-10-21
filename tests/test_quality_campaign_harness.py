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


@pytest.mark.parametrize("mode,expected_requests", [("drop_before", 0), ("drop_after", 1), ("delay", 1)])
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
    proxy = proxy_module.server(f"http://127.0.0.1:{upstream.server_port}", mode=mode, delay_ms=5)
    threads = [Thread(target=s.serve_forever, daemon=True) for s in (upstream, proxy)]
    for thread in threads:
        thread.start()
    try:
        opener = build_opener(ProxyHandler({}))
        if mode.startswith("drop"):
            with pytest.raises((RemoteDisconnected, URLError)):
                opener.open(f"http://127.0.0.1:{proxy.server_port}", timeout=3)
        else:
            with opener.open(f"http://127.0.0.1:{proxy.server_port}", timeout=3) as result:
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
    for target in ("http://127.0.0.1:8000", "http://127.0.0.1:5432", "https://example.com:12345"):
        with pytest.raises(ValueError):
            proxy.server(target)
