import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

class SummaryTests(unittest.TestCase):
    def test_standard_reference_metadata_keeps_billed_zero_and_original_model(self):
        from contextlib import closing
        from test_price_snapshot import write_usage
        from test_pricing_catalog import openai_page
        from usage_core import build_summary
        import pricing_catalog as prices
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            write_usage(home)
            with closing(sqlite3.connect(home/'state.db')) as db, db:
                db.execute("UPDATE session_model_usage SET model='gpt-example-900k', billing_provider='openai-codex', actual_cost_usd=0, estimated_cost_usd=0, cost_status='actual'")
            before = (home/'state.db').read_bytes()
            prices.refresh_prices(home, sources={'openai':openai_page([['gpt-example',2,'.1',10]])})
            snapshot=prices.PriceSnapshot(home)
            result=build_summary(home, 7, now=1000010, price_lookup=snapshot.lookup_price)
            row=result['models'][0]
            self.assertEqual(row['model'], 'gpt-example-900k')
            self.assertEqual(row['provider'], 'openai-codex')
            self.assertEqual(row['actual_cost_usd'], 0)
            self.assertEqual(row['estimated_cost_usd'], 0)
            self.assertEqual(row['pricing_model'], 'gpt-example')
            self.assertEqual(row.get('pricing_reference_adjustments'), ['standard_variant'])
            self.assertGreater(row['api_value_usd'], 0)
            self.assertEqual(before, (home/'state.db').read_bytes())

    def test_cost_sum_requires_every_corresponding_cost_with_accepted_status(self):
        from usage_core import build_summary
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with sqlite3.connect(home / 'state.db') as con:
                con.executescript('''
                    CREATE TABLE sessions(id TEXT PRIMARY KEY, started_at REAL);
                    INSERT INTO sessions VALUES ('s',1000000);
                    CREATE TABLE session_model_usage(session_id TEXT,model TEXT,billing_provider TEXT,
                        billing_mode TEXT,task TEXT,api_call_count INTEGER,input_tokens INTEGER,
                        output_tokens INTEGER,cache_read_tokens INTEGER,cache_write_tokens INTEGER,
                        reasoning_tokens INTEGER,estimated_cost_usd REAL,actual_cost_usd REAL,cost_status TEXT);
                ''')
                cases = {
                    'actual_missing': [(1, 1, 'actual'), (1, None, 'actual')],
                    'estimated_missing': [(1, 1, 'actual'), (None, 1, 'estimated')],
                    'included_missing': [(1, 1, 'actual'), (None, 0, 'included')],
                    'complete_zero': [(0, 0, 'actual'), (0, 0, 'actual')],
                    'complete': [(1, 2, 'actual'), (3, 4, 'actual')],
                }
                for model, costs in cases.items():
                    for estimated, actual, status in costs:
                        con.execute('INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            ('s', model, 'test', 'api', '', 1, 1, 1, 0, 0, 0, estimated, actual, status))
            con.close()
            rows = {r['model']: r for r in build_summary(home, now=1000010,
                price_lookup=lambda p, m: None)['models']}
            with self.subTest(cost='actual'):
                self.assertIsNone(rows['actual_missing']['actual_cost_usd'])
            for model in ('estimated_missing', 'included_missing'):
                with self.subTest(model=model):
                    self.assertIsNone(rows[model]['estimated_cost_usd'])
            self.assertEqual(rows['actual_missing']['estimated_cost_usd'], 2)
            self.assertEqual(rows['complete_zero']['actual_cost_usd'], 0)
            self.assertEqual(rows['complete_zero']['estimated_cost_usd'], 0)
            self.assertEqual(rows['complete']['actual_cost_usd'], 6)
            self.assertEqual(rows['complete']['estimated_cost_usd'], 4)

    def test_model_switch_auxiliary_and_canonical_cache_not_double_counted(self):
        self.assertIsNotNone(importlib.util.find_spec('usage_core'), 'usage_core implementation missing')
        from usage_core import build_summary
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            con = sqlite3.connect(home / 'state.db')
            con.executescript('''
              CREATE TABLE sessions(id TEXT PRIMARY KEY, started_at REAL);
              CREATE TABLE session_model_usage(session_id TEXT,model TEXT,billing_provider TEXT,billing_mode TEXT,task TEXT,
              api_call_count INTEGER,input_tokens INTEGER,output_tokens INTEGER,cache_read_tokens INTEGER,cache_write_tokens INTEGER,
              reasoning_tokens INTEGER,estimated_cost_usd REAL,actual_cost_usd REAL,cost_status TEXT);
              INSERT INTO sessions VALUES ('s', 1000000), ('old', 1);
              INSERT INTO session_model_usage VALUES ('s','model-a','test','api','',2,100,20,80,20,5,0,0,'unknown');
              INSERT INTO session_model_usage VALUES ('s','model-a','test','api','title_generation',1,10,5,0,0,0,0,0,'unknown');
              INSERT INTO session_model_usage VALUES ('s','model-b','test','subscription_included','',1,20,7,0,0,2,0,0,'included');
              INSERT INTO session_model_usage VALUES ('old','model-a','test','api','',90,900,900,0,0,0,0,0,'unknown');
            ''')
            con.commit(); con.close()
            before = (home / 'state.db').read_bytes()
            result = build_summary(home, 7, now=1000010, price_lookup=lambda p,m: None)
            a, b = result['models']
            self.assertEqual(a['api_calls'], 3)
            self.assertEqual(a['aux_calls'], 1)
            self.assertEqual(a['prompt_tokens'], 210)
            self.assertAlmostEqual(a['cache_hit_pct'], 80/210*100)
            self.assertIsNone(a['actual_cost_usd'])
            self.assertIsNone(a['avg_tps'])
            self.assertIsNone(b['cache_hit_pct'])
            self.assertEqual(result['totals']['api_calls'], 4)
            self.assertEqual(result['totals']['models'], 2)
            self.assertIsNone(result['totals']['api_value_usd'])
            self.assertEqual(before, (home / 'state.db').read_bytes())

    def test_native_fallback_completes_missing_route_components_without_changing_billing(self):
        from decimal import Decimal
        from types import SimpleNamespace
        from usage_core import build_summary
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            con = sqlite3.connect(home / 'state.db')
            con.executescript('''
                CREATE TABLE sessions(id TEXT PRIMARY KEY, started_at REAL);
                INSERT INTO sessions VALUES ('s',1000000);
                CREATE TABLE session_model_usage(session_id TEXT,model TEXT,billing_provider TEXT,
                    billing_mode TEXT,task TEXT,api_call_count INTEGER,input_tokens INTEGER,
                    output_tokens INTEGER,cache_read_tokens INTEGER,cache_write_tokens INTEGER,
                    reasoning_tokens INTEGER,estimated_cost_usd REAL,actual_cost_usd REAL,cost_status TEXT);
                INSERT INTO session_model_usage VALUES ('s','gpt-example','unknown','', '',1,1000000,0,1000000,0,0,NULL,NULL,'unknown');
            ''')
            con.commit(); con.close()
            route = SimpleNamespace(input_cost_per_million=Decimal('99'), output_cost_per_million=Decimal('99'),
                cache_read_cost_per_million=None, cache_write_cost_per_million=None, request_cost=None,
                source='route', pricing_version='route-version')
            native = SimpleNamespace(input_cost_per_million=Decimal('2'), output_cost_per_million=Decimal('10'),
                cache_read_cost_per_million=Decimal('.2'), cache_write_cost_per_million=None, request_cost=None,
                source='official', source_url='https://example.test/prices', pricing_version='native-version',
                pricing_basis='native_reference', provider='openai', model='gpt-example')
            result = build_summary(home, now=1000010, price_lookup=lambda p,m: route,
                native_price_lookup=lambda p,m: native)
            row = result['models'][0]
            self.assertEqual(row['api_value_usd'], 2.2)
            self.assertEqual(row['pricing_source_url'], native.source_url)
            self.assertEqual(row['pricing_basis'], 'native_reference')
            self.assertEqual(row['provider'], 'unknown')
            self.assertEqual(row['billing_mode'], '')
            self.assertIsNone(row['actual_cost_usd'])
            self.assertIsNone(row['estimated_cost_usd'])

class PricingTests(unittest.TestCase):
    def test_api_equivalent_is_base_tariff_not_subscription_zero_or_aggregate_tier(self):
        import usage_core
        self.assertTrue(hasattr(usage_core, 'api_value'), 'API value calculation missing')
        from decimal import Decimal
        from types import SimpleNamespace
        entry = SimpleNamespace(input_cost_per_million=Decimal('2'), output_cost_per_million=Decimal('10'),
            cache_read_cost_per_million=Decimal('.2'), cache_write_cost_per_million=Decimal('2.5'),
            request_cost=None, source='test_snapshot', pricing_version='test', tier_threshold_tokens=100)
        row = dict(input_tokens=1000000, output_tokens=1000000, cache_read_tokens=1000000,
                   cache_write_tokens=1000000, api_calls=4)
        self.assertEqual(usage_core.api_value(row, entry), 14.7)
        entry.cache_read_cost_per_million = None
        self.assertIsNone(usage_core.api_value(row, entry))
        self.assertIsNone(usage_core.api_value(row, None))

if __name__ == '__main__':
    unittest.main()
