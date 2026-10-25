"""Request-local price snapshot contracts; synthetic prices and SQLite only."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write_prices(home, rate="0.000002"):
    import pricing_catalog
    payload = json.dumps({"data": [
        {"id": f"synthetic/model-{i}", "pricing": {
            "prompt": rate, "completion": "0.000009", "input_cache_read": "0"}}
        for i in range(4)
    ]}).encode()
    return pricing_catalog.refresh_prices(home, sources={"nous": payload})


def write_usage(home):
    with closing(sqlite3.connect(home / "state.db")) as db, db:
        db.executescript("""
            CREATE TABLE sessions(id TEXT PRIMARY KEY, started_at REAL);
            INSERT INTO sessions VALUES ('synthetic-session', 1000000);
            CREATE TABLE session_model_usage(session_id TEXT, model TEXT, billing_provider TEXT,
                billing_mode TEXT, task TEXT, api_call_count INTEGER, input_tokens INTEGER,
                output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER,
                reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL, cost_status TEXT);
        """)
        db.executemany("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("synthetic-session", f"synthetic/model-{i}", "nous", "api", "", 1,
             10, 5, 0, 0, 0, None, None, "unknown") for i in range(4)
        ])


def input_rate(entry):
    assert entry is not None, "expected a supported tariff"
    return entry.input_cost_per_million


class PriceSnapshotTests(unittest.TestCase):
    def test_small_catalog_does_not_allocate_the_entire_cache_limit(self):
        import tracemalloc
        import pricing_catalog as catalog
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_prices(home)
            tracemalloc.start()
            try:
                snapshot = catalog.PriceSnapshot(home)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertEqual(snapshot.status()["price_count"], 4)
            self.assertLess(peak, 1024 * 1024, "small catalogs must not reserve the 16 MiB limit")

    def test_cache_size_limit_and_concurrent_growth_fail_closed(self):
        import pricing_catalog as catalog
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_prices(home)
            path = home / "cache/usage-plus/prices.json"
            with path.open("r+b") as stream:
                stream.truncate(catalog.MAX_CACHE_BYTES + 1)
            self.assertEqual(catalog.PriceSnapshot(home).status()["price_count"], 0)
            write_prices(home)
            real_fstat = catalog.os.fstat

            def grow_after_stat(fd):
                before = real_fstat(fd)
                with path.open("ab") as stream:
                    stream.write(b" ")
                return before

            with patch.object(catalog.os, "fstat", side_effect=grow_after_stat):
                self.assertEqual(catalog.PriceSnapshot(home).status()["price_count"], 0)
            # Outside the raced read, trailing whitespace is valid JSON again.
            self.assertEqual(catalog.PriceSnapshot(home).status()["price_count"], 4)

    def test_summary_reads_and_validates_catalog_once(self):
        import pricing_catalog
        import worker
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_prices(home)
            write_usage(home)
            with patch.object(pricing_catalog, "_read", wraps=pricing_catalog._read) as read:
                with patch("time.time", return_value=1000010):
                    result = worker.summary(home, 7)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(len(result["models"]), 4)
            self.assertAlmostEqual(result["totals"]["api_value_usd"], 4 * (10 * 2 + 5 * 9) / 1_000_000)
            self.assertEqual(read.call_count, 1, "one coherent catalog per summary, not per model")

    def test_summary_reports_the_price_refresh_notice_with_the_pricing_block(self):
        import worker
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_prices(home)
            write_usage(home)
            with patch("time.time", return_value=1000010):
                result = worker.summary(home, 7)
        self.assertEqual(result["pricing"]["notice"], worker.PRICING_NOTICE)
        self.assertNotIn(worker.PRICING_NOTICE, result["warnings"])
        self.assertEqual(result["warnings"][-1],
                         '속도는 설치 후 수집한 output tokens / 전체 API 요청 시간입니다. 첫 토큰 대기를 포함하며 순수 decode 속도가 아닙니다.')

    def test_refresh_affects_new_snapshots_not_in_flight_requests(self):
        from decimal import Decimal
        import pricing_catalog as catalog
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch("time.time", return_value=1000):
                write_prices(home)
            snapshot = catalog.PriceSnapshot(home)
            with patch("time.time", return_value=2000):
                write_prices(home, "0.000003")
                self.assertEqual(input_rate(snapshot.lookup_price("nous", "synthetic/model-0")), Decimal(2))
                self.assertEqual(snapshot.status()["updated_at"], 1000)
                self.assertEqual(input_rate(catalog.PriceSnapshot(home).lookup_price("nous", "synthetic/model-0")), Decimal(3))
                self.assertEqual(input_rate(catalog.lookup_price(home, "nous", "synthetic/model-0")), Decimal(3))
                self.assertEqual(catalog.status(home)["updated_at"], 2000)

    def test_profiles_and_returned_entries_do_not_share_mutable_state(self):
        from decimal import Decimal
        import pricing_catalog as catalog
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            write_prices(Path(first))
            write_prices(Path(second), "0.000007")
            a, b = catalog.PriceSnapshot(Path(first)), catalog.PriceSnapshot(Path(second))
            entry = a.lookup_price("nous", "synthetic/model-0")
            assert entry is not None
            entry.input_cost_per_million = Decimal(999)
            self.assertEqual(input_rate(a.lookup_price("nous", "synthetic/model-0")), Decimal(2))
            self.assertEqual(input_rate(b.lookup_price("nous", "synthetic/model-0")), Decimal(7))

    def test_missing_or_corrupt_cache_is_unavailable_without_network(self):
        import pricing_catalog as catalog
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with patch.object(catalog, "_fetch_sources", side_effect=AssertionError("read path must stay offline")):
                empty = catalog.PriceSnapshot(home)
                self.assertIsNone(empty.lookup_price("nous", "synthetic/model-0"))
                self.assertEqual(empty.status()["price_count"], 0)
                self.assertTrue(empty.status()["stale"])
                path = home / "cache/usage-plus/prices.json"
                path.parent.mkdir(parents=True)
                path.write_text("not valid JSON", encoding="utf-8")
                corrupt = catalog.PriceSnapshot(home)
                self.assertIsNone(corrupt.lookup_price("nous", "synthetic/model-0"))
                self.assertEqual(corrupt.status()["price_count"], 0)

    def test_snapshot_preserves_exact_route_native_and_ambiguous_lookup_contracts(self):
        from test_pricing_catalog import catalog, model, openai_page, anthropic_page
        from test_pricing_extra import deepseek_page, opencode_page, hive_page
        import pricing_catalog as pricing
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            pricing.refresh_prices(home, sources={
                "nous": catalog([model(name="openai/gpt-example", prompt="0", completion="0")]),
                "openai": openai_page([["gpt-example", 2, ".1", 10]]),
                "anthropic": anthropic_page(), "deepseek": deepseek_page(),
                "opencode": opencode_page(), "thehive": hive_page(),
            })
            snapshot = pricing.PriceSnapshot(home)
            pairs = [
                ("nous", "openai/gpt-example"), ("openai-codex", "gpt-example"),
                ("custom", "gpt-example"), ("infron", "openai/gpt-example:free"),
                ("opencodex", "google-antigravity/claude-sonnet-4-5"),
                ("opencode-go", "paid-exact.1"), ("opencode", "free-exact"),
                ("opencodex", "thehive/deepseek-ai-deepseek-v4.1-flash-1017"),
                ("infron", "deepseek/deepseek-v4.1-flash:free"),
                ("custom", "deepseek-v4.1-flash-9999"), ("custom", "evil/gpt-example"),
            ]
            with patch.object(pricing, "_read", wraps=pricing._read) as read:
                for provider, model_id in pairs:
                    for native_only in (False, True):
                        with self.subTest(provider=provider, model=model_id, native_only=native_only):
                            expected = pricing.lookup_price(home, provider, model_id, native_only=native_only)
                            actual = snapshot.lookup_price(provider, model_id, native_only=native_only)
                            self.assertEqual(actual, expected)
                self.assertEqual(read.call_count, len(pairs) * 2, "only standalone lookups reread the file")


if __name__ == "__main__":
    unittest.main()
