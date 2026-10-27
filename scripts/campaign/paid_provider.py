"""Explicitly authorized Gemini probes with a shared conservative budget ledger.

Prices verified 2026-10-27: https://ai.google.dev/gemini-api/docs/pricing
Standard gemini-3.5-flash-lite: USD .30/M input, 2.50/M output/thinking.
No keys, images, prompts or raw provider error text are persisted here.
"""

import json
import logging
from pathlib import Path
import sqlite3
import time

from google import genai
from google.genai import types


class BudgetExceeded(RuntimeError):
    pass


class PaidClient:
    def __init__(self, *, api_key, ledger, phase, max_calls=64, max_usd=1.0):
        assert api_key
        self.ledger, self.phase = Path(ledger), phase
        self.max_calls, self.max_usd = max_calls, max_usd
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        for name in ("httpx", "httpcore", "google_genai"):
            logging.getLogger(name).setLevel(logging.WARNING)
        self.client = genai.Client(api_key=api_key, vertexai=False, http_options=types.HttpOptions(
            base_url="https://generativelanguage.googleapis.com", timeout=60000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ))
        self.models = self
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, phase TEXT, model TEXT, state TEXT, reserved_usd REAL, charged_estimate_usd REAL, metadata TEXT)")

    def connect(self):
        # The connection context alone does not close sqlite3 connections.
        from contextlib import contextmanager

        @contextmanager
        def opened():
            db = sqlite3.connect(self.ledger, timeout=15)
            try:
                with db:
                    yield db
            finally:
                db.close()
        return opened()

    def generate_content(self, *, model, contents, config):
        assert model == "gemini-3.5-flash-lite", "A different model needs verified pricing"
        assert 0 < config.max_output_tokens <= 1024, "Preserve the evaluated production output limit"
        count_started = time.perf_counter()
        counted = self.client.models.count_tokens(model=model, contents=contents)
        input_tokens = int(counted.total_tokens or 0)
        count_seconds = time.perf_counter()-count_started
        # Reserve far above the requested 1024 outputs to cover provider thinking,
        # plus 10k input tokens for schema/protocol overhead. Release only when
        # usage metadata confirms the actual lower estimate.
        reserved = (input_tokens+10000)*0.30/1_000_000 + 65536*2.50/1_000_000
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            calls, committed = db.execute("SELECT COUNT(*),COALESCE(SUM(COALESCE(charged_estimate_usd,reserved_usd)),0) FROM calls").fetchone()
            if calls >= self.max_calls or committed+reserved > self.max_usd:
                raise BudgetExceeded("Shared authorized campaign budget reached; no generation sent")
            cursor = db.execute("INSERT INTO calls(phase,model,state,reserved_usd,metadata) VALUES(?,?,?,?,?)",
                (self.phase, model, "reserved", reserved, json.dumps({"counted_input_tokens": input_tokens, "count_tokens_seconds": count_seconds})))
            ident = cursor.lastrowid
        started = time.perf_counter()
        try:
            response = self.client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as error:
            with self.connect() as db:
                db.execute("UPDATE calls SET state=?, metadata=? WHERE id=?", ("provider-error",
                    json.dumps({"error_type": type(error).__name__, "seconds": time.perf_counter()-started}), ident))
            raise
        usage = response.usage_metadata
        metadata = {"sdk_generate_seconds": time.perf_counter()-started,
            "count_tokens_seconds": count_seconds, "response_model_version": getattr(response, "model_version", None)}
        cost = None
        if usage is not None:
            values = {name: int(getattr(usage, name, None) or 0) for name in (
                "prompt_token_count", "candidates_token_count", "thoughts_token_count", "cached_content_token_count", "total_token_count")}
            metadata["usage"] = values
            cost = values["prompt_token_count"]*0.30/1_000_000 + (values["candidates_token_count"]+values["thoughts_token_count"])*2.50/1_000_000
            assert cost <= reserved, "Provider usage exceeded conservative reservation; stop"
        metadata["finish_reasons"] = [str(c.finish_reason) for c in response.candidates or []]
        with self.connect() as db:
            db.execute("UPDATE calls SET state=?,charged_estimate_usd=?,metadata=? WHERE id=?",
                ("returned", cost, json.dumps(metadata), ident))
        return response

    def close(self):
        self.client.close()


def ledger_summary(path):
    db = sqlite3.connect(path)
    try:
        rows = db.execute("SELECT phase,model,state,reserved_usd,charged_estimate_usd,metadata FROM calls ORDER BY id").fetchall()
        return [{"phase": r[0], "model": r[1], "state": r[2], "reserved_usd": r[3],
            "charged_estimate_usd": r[4], "metadata": json.loads(r[5])} for r in rows]
    finally:
        db.close()
