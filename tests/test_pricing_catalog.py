"""Offline contract tests; all fixture amounts are synthetic, not price snapshots."""
import importlib.util
import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def catalog(rows):
    return json.dumps({'data': rows}).encode()


def model(name='vendor/exact-model', **pricing):
    return {'id': name, 'pricing': pricing}

def openai_page(rows, *, tier='standard', component='TextTokenPricingTables', unit='Prices per 1M tokens.'):
    from html import escape
    props = {'tier': [0, tier], 'rows': [1, [[1, [[0, value] for value in row]] for row in rows]]}
    return (f'<article id="mainContent"><p>{unit}</p>'
            '<div id="content-switcher-latest-pricing">'
            f'<div data-content-switcher-pane="true" data-value="{tier}">'
            f'<astro-island component-export="{component}" props="{escape(json.dumps(props), quote=True)}">'
            '</astro-island></div></div></article>').encode()


def anthropic_page(*, input_cell='$3 / MTok', name='Claude Sonnet 4.5', headers=None):
    headers = headers or ['Name', 'Input', 'Output', '5m writes', '1h writes', 'Hits and refreshes']
    return ('<p>All prices are in USD.</p><h2 id="model-pricing">Model pricing</h2><table><thead><tr>'
            + ''.join(f'<th>{h}</th>' for h in headers) + '</tr></thead><tbody><tr>'
            + f'<td><a>{name}</a>Optional description</td><td>{input_cell}</td>'
            + '<td>$15 / MTok</td><td>$3.75 / MTok</td><td>$6 / MTok</td><td>$0.3 / MTok</td>'
            + '</tr></tbody></table>').encode()


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT.parents[1] / 'cache' / 'scratch')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('pricing_catalog'), 'pricing module missing')
        import pricing_catalog
        return pricing_catalog

    def test_api_prices_normalize_units_without_inventing_missing_cache_rates(self):
        p = self.module()
        result = p.refresh_prices(self.home, sources={'nous': catalog([
            model(prompt='0.000002', completion='0.000009', input_cache_read='0',
                  input_cache_write=None),
        ])})
        entry = p.lookup_price(self.home, 'nous', 'vendor/exact-model')
        self.assertEqual(entry.input_cost_per_million, Decimal('2'))
        self.assertEqual(entry.output_cost_per_million, Decimal('9'))
        self.assertEqual(entry.cache_read_cost_per_million, Decimal('0'))
        self.assertIsNone(entry.cache_write_cost_per_million)
        self.assertIsNone(entry.request_cost)
        self.assertIn('https://inference-api.nousresearch.com/v1/models', entry.source)
        self.assertIn('UTC', entry.pricing_version)
        self.assertIsInstance(result['updated_at'], (int, float))
        self.assertTrue((self.home / 'cache/usage-plus/prices.json').is_file())

    def test_failure_retains_last_good_prices_and_checked_timestamp_per_source(self):
        from unittest.mock import patch
        p = self.module()
        good = catalog([model(prompt='0.000002', completion='0.000009')])
        with patch.object(p.time, 'time', return_value=1000):
            old = p.refresh_prices(self.home, sources={'nous': good, 'openrouter': good})
        with patch.object(p.time, 'time', return_value=2000):
            result = p.refresh_prices(self.home, sources={
                'nous': RuntimeError('Bearer sk-secret https://user:password@host/?api_key=hidden'),
                'openrouter': catalog([model(prompt='0.000003', completion='0.000010')]),
            })
        self.assertEqual(result['sources']['nous']['checked_at'], 1000)
        self.assertEqual(result['sources']['nous']['attempted_at'], 2000)
        self.assertEqual(result['sources']['nous']['status'], 'error')
        self.assertEqual(result['sources']['openrouter']['checked_at'], 2000)
        self.assertEqual(p.lookup_price(self.home, 'nous', 'vendor/exact-model').input_cost_per_million, Decimal('2'))
        self.assertEqual(p.lookup_price(self.home, 'openrouter', 'vendor/exact-model').input_cost_per_million, Decimal('3'))
        self.assertEqual(p.lookup_price(self.home, 'nous', 'vendor/exact-model').pricing_version,
                         old['prices'][0]['pricing_version'])
        serialized = json.dumps(result)
        for secret in ('sk-secret', 'password', 'api_key', 'hidden', 'Bearer'):
            self.assertNotIn(secret, serialized)
        health = p.status(self.home)
        self.assertEqual(health['price_count'], 2)
        self.assertEqual(health['error_sources'], 1)
        self.assertEqual(health['healthy_sources'], 1)

    def test_atomic_replace_failure_preserves_existing_file_and_removes_temp(self):
        from unittest.mock import patch
        p = self.module()
        good = catalog([model(prompt='0', completion='0')])
        p.refresh_prices(self.home, sources={'nous': good})
        path = self.home / 'cache/usage-plus/prices.json'
        before = path.read_bytes()
        with patch('os.replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                p.refresh_prices(self.home, sources={'nous': catalog([model(prompt='1', completion='1')])})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual([f.name for f in path.parent.iterdir()], ['prices.json'])

    def test_wrong_schemas_and_invalid_numbers_fail_closed_retaining_old_data(self):
        p = self.module()
        good = catalog([model(prompt='0.000002', completion='0.000009')])
        invalid = [b'{}', b'{"data":{}}', b'{"data":[]}', b'not-json',
                   catalog([{'id': 'x', 'pricing': []}]),
                   catalog([model(prompt='-1', completion='0')]),
                   catalog([model(prompt='NaN', completion='0')]),
                   catalog([model(prompt='Infinity', completion='0')]),
                   catalog([model(prompt=True, completion='0')]),
                   catalog([model(prompt={'value': 1}, completion='0')]),
                   catalog([model(name=' x ', prompt='1', completion='2')]),
                   catalog([model(prompt='1', completion='2'), model(prompt='3', completion='4')]),
                   b'{"data":[],"data":[]}',
                   catalog([model(prompt='1', completion='2', new_dynamic_tariff='1')])]
        for payload in invalid:
            with self.subTest(payload=payload):
                p.refresh_prices(self.home, sources={'nous': good})
                result = p.refresh_prices(self.home, sources={'nous': payload})
                self.assertEqual(result['sources']['nous']['status'], 'error')
                self.assertEqual(p.lookup_price(self.home, 'nous', 'vendor/exact-model').input_cost_per_million, Decimal('2'))

    def test_known_api_cache_fields_request_fee_and_zero_are_not_conflated(self):
        p = self.module()
        p.refresh_prices(self.home, sources={'openrouter': catalog([
            model(prompt='0', completion='0', input_cache_read='0.0000003',
                  input_cache_write='0.00000375', input_cache_write_1h='0.000006', request='0.02'),
            model(name='vendor/unknown', prompt='0.000002', completion='0.000003'),
            model(name='vendor/unpriced', prompt=None, completion=None),
        ])})
        entry = p.lookup_price(self.home, 'openrouter', 'vendor/exact-model')
        self.assertEqual(entry.input_cost_per_million, Decimal(0))
        self.assertEqual(entry.cache_read_cost_per_million, Decimal('.3'))
        self.assertEqual(entry.cache_write_cost_per_million, Decimal('3.75'))
        self.assertEqual(entry.request_cost, Decimal('.02'))
        self.assertIsNone(p.lookup_price(self.home, 'openrouter', 'vendor/unknown').cache_read_cost_per_million)
        self.assertIsNone(p.lookup_price(self.home, 'openrouter', 'vendor/unpriced'))

    def test_context_base_tier_is_labelled_but_time_dependent_models_are_skipped(self):
        p = self.module()
        result = p.refresh_prices(self.home, sources={'nous': catalog([
            model(prompt='0.000003', completion='0.000015', overrides=[
                {'min_prompt_tokens': 200000, 'prompt': '0.000006', 'completion': '0.0000225'}]),
            model(name='vendor/time', prompt='0.000001', completion='0.000002', overrides=[
                {'utc_start': 0, 'utc_end': 100, 'prompt': '0.000003'}]),
            model(name='vendor/unknown-tier', prompt='0.000001', completion='0.000002', overrides=[
                {'new_condition': 'unknown', 'prompt': '0.000003'}]),
        ])})
        entry = p.lookup_price(self.home, 'nous', 'vendor/exact-model')
        self.assertEqual(entry.input_cost_per_million, Decimal(3))
        self.assertEqual(entry.tier_threshold_tokens, 200000)
        self.assertIn('base', entry.source.lower())
        self.assertIn('request', entry.source.lower())
        self.assertIsNone(p.lookup_price(self.home, 'nous', 'vendor/time'))
        self.assertIsNone(p.lookup_price(self.home, 'nous', 'vendor/unknown-tier'))
        self.assertEqual(result['sources']['nous']['skipped_count'], 2)

    def test_exact_provider_and_model_isolation_without_guessing_native_fallbacks(self):
        p = self.module()
        p.refresh_prices(self.home, sources={'nous': catalog([model(name='openai/gpt-example', prompt='0', completion='0')])})
        for provider, name in [('openrouter', 'openai/gpt-example'), ('nous', 'gpt-example'),
                               ('infron', 'openai/gpt-example'), ('opencode-go', 'gpt-example'),
                               ('custom', 'gpt-example'), ('nous', 'openai/gpt-example-high')]:
            self.assertIsNone(p.lookup_price(self.home, provider, name))
        with self.assertRaises(ValueError):
            p.refresh_prices(self.home, sources={'https://evil.invalid': b'{}'})

    def test_windows_transient_sharing_violation_retries_atomic_replace_boundedly(self):
        from unittest.mock import patch
        import os
        p = self.module()
        original = os.replace
        attempts = []
        def replace(source, target):
            attempts.append(1)
            if len(attempts) < 2:
                raise PermissionError('temporary sharing violation')
            return original(source, target)
        with patch('os.replace', side_effect=replace):
            result = p.refresh_prices(self.home, sources={'nous': catalog([model(prompt='0', completion='0')])})
        self.assertEqual(len(attempts), 2)
        self.assertEqual(result['sources']['nous']['status'], 'ok')

    def test_openai_standard_astro_schema_and_codex_are_equivalent_not_billed(self):
        p = self.module()
        page = openai_page([['gpt-example', 2, '.1', '2.5', 10],
                            ['gpt-legacy', '1.25', '.125', 10],
                            ['gpt-context (<272K context length)', 3, '.3', '-', 12]])
        p.refresh_prices(self.home, sources={'openai': page})
        api = p.lookup_price(self.home, 'openai', 'gpt-example')
        self.assertEqual(api.input_cost_per_million, Decimal('2'))
        self.assertEqual(api.output_cost_per_million, Decimal('10'))
        self.assertEqual(api.cache_read_cost_per_million, Decimal('.1'))
        self.assertEqual(api.cache_write_cost_per_million, Decimal('2.5'))
        self.assertIn('30m', api.source)
        subscription = p.lookup_price(self.home, 'openai-codex', 'gpt-example')
        self.assertEqual(subscription.input_cost_per_million, api.input_cost_per_million)
        self.assertIn('equivalent reference', subscription.source)
        self.assertIn('not billed', subscription.source)
        self.assertNotIn('openai-codex', api.source)
        self.assertIsNone(p.lookup_price(self.home, 'opencode-go', 'gpt-example'))
        self.assertIsNone(p.lookup_price(self.home, 'custom', 'gpt-example'))
        self.assertIsNone(p.lookup_price(self.home, 'openai', 'gpt-example-2026-10-01'))
        context = p.lookup_price(self.home, 'openai', 'gpt-context')
        self.assertEqual(context.tier_threshold_tokens, 272000)
        self.assertIn('base', context.source)
        self.assertIsNone(context.cache_write_cost_per_million)

    def test_openai_changed_units_tier_component_or_column_shape_fail_closed(self):
        p = self.module()
        good = openai_page([['gpt-example', 2, '.1', 10]])
        bad = [openai_page([['gpt-example', 2, '.1', 10]], tier='batch'),
               openai_page([['gpt-example', 2, '.1', 10]], component='UnknownPricing'),
               openai_page([['gpt-example', 2, '.1', 10]], unit='Prices per 1K tokens.'),
               openai_page([['gpt-example', 2, '.1', 10, 20, 30]]),
               openai_page([['gpt-example', 2, '.1', 10], ['gpt-example', 3, '.3', 15]])]
        for payload in bad:
            with self.subTest(payload=payload):
                p.refresh_prices(self.home, sources={'openai': good})
                result = p.refresh_prices(self.home, sources={'openai': payload})
                self.assertEqual(result['sources']['openai']['status'], 'error')
                self.assertEqual(p.lookup_price(self.home, 'openai', 'gpt-example').input_cost_per_million, Decimal(2))

    def test_anthropic_headers_cache_hits_and_write_duration_are_explicit(self):
        p = self.module()
        result = p.refresh_prices(self.home, sources={'anthropic': anthropic_page()})
        self.assertEqual(result['sources']['anthropic']['status'], 'ok')
        entry = p.lookup_price(self.home, 'anthropic', 'claude-sonnet-4-5')
        self.assertEqual(entry.input_cost_per_million, Decimal(3))
        self.assertEqual(entry.output_cost_per_million, Decimal(15))
        self.assertEqual(entry.cache_read_cost_per_million, Decimal('.3'))
        self.assertEqual(entry.cache_write_cost_per_million, Decimal('3.75'))
        self.assertIn('5m', entry.source)
        self.assertIsNone(p.lookup_price(self.home, 'anthropic', 'claude-sonnet-4.5'))
        self.assertIsNone(p.lookup_price(self.home, 'custom', 'claude-sonnet-4-5'))

    def test_anthropic_tiered_cell_is_base_only_with_threshold_and_unknown_names_not_guessed(self):
        p = self.module()
        p.refresh_prices(self.home, sources={'anthropic': anthropic_page(
            input_cell='$0.10 / MTok for prompts up to 100,000 tokens', name='Claude Haiku 5.5')})
        entry = p.lookup_price(self.home, 'anthropic', 'claude-haiku-5-5')
        self.assertEqual(entry.input_cost_per_million, Decimal('.1'))
        self.assertEqual(entry.tier_threshold_tokens, 100000)
        self.assertIn('base', entry.source)
        for payload in [anthropic_page(input_cell='$3 / 1K tokens'),
                        anthropic_page(name='Claude Future 9'),
                        anthropic_page(headers=['Name', 'Input', 'Output', 'New write', '1h writes', 'Hits and refreshes'])]:
            result = p.refresh_prices(self.home, sources={'anthropic': payload})
            self.assertEqual(result['sources']['anthropic']['status'], 'error')

    def test_default_refresh_fetches_fixed_official_sources_and_reads_never_fetch(self):
        from unittest.mock import patch
        p = self.module()
        self.assertTrue(hasattr(p, '_fetch_sources'), 'bounded public fetch transport missing')
        payloads = {'nous': catalog([model(prompt='0', completion='0')]),
                    'openrouter': catalog([model(prompt='0.000002', completion='0.000003')]),
                    'openai': openai_page([['gpt-example', 2, '.1', 10]]),
                    'anthropic': anthropic_page()}
        with patch.object(p, '_fetch_sources', return_value=payloads) as fetch:
            result = p.refresh_prices(self.home)
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(len(result['sources']), 4)
        with patch.object(p, '_fetch_sources', side_effect=AssertionError('network on read')):
            self.assertIsNotNone(p.lookup_price(self.home, 'nous', 'vendor/exact-model'))
            self.assertEqual(p.status(self.home)['healthy_sources'], 4)
            p.refresh_prices(self.home, sources={})
        for info in result['sources'].values():
            self.assertEqual(len(info['response_sha256']), 64)
            self.assertGreater(info['response_bytes'], 0)

    def test_payload_size_limits_apply_to_injected_payloads_too(self):
        p = self.module()
        self.assertTrue(hasattr(p, 'MAX_RESPONSE_BYTES'), 'response limit missing')
        result = p.refresh_prices(self.home, sources={'nous': b' ' * (p.MAX_RESPONSE_BYTES + 1)})
        self.assertEqual(result['sources']['nous']['status'], 'error')
        self.assertEqual(result['sources']['nous']['error'], 'response_too_large')
        self.assertIsNone(p.lookup_price(self.home, 'nous', 'vendor/exact-model'))

    def test_missing_or_corrupt_cache_is_unknown_and_stale_not_zero_or_exception(self):
        p = self.module()
        self.assertIsNone(p.lookup_price(self.home, 'openai', 'gpt-unknown'))
        health = p.status(self.home)
        self.assertEqual(health['price_count'], 0)
        self.assertTrue(health['stale'])
        path = self.home / 'cache/usage-plus/prices.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        for raw in ('not json', '[]', '{}', '{"schema_version":99,"prices":[]}'):
            path.write_text(raw, encoding='utf-8')
            self.assertIsNone(p.lookup_price(self.home, 'openai', 'gpt-unknown'))
            self.assertEqual(p.status(self.home)['price_count'], 0)

    def test_http_transport_rejects_unofficial_redirects_and_limits_response_reads(self):
        from unittest.mock import patch, MagicMock
        p = self.module()
        self.assertTrue(hasattr(p, '_fetch_http'), 'HTTP fetch missing')
        response = MagicMock()
        response.headers = {'Content-Type': 'application/json', 'Content-Length': '10'}
        response.geturl.return_value = p.SOURCES['nous']
        response.read.return_value = b'{"data":[]}'
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.return_value = response
        with patch.object(p.urllib.request, 'build_opener', return_value=opener):
            self.assertEqual(p._fetch_http('nous'), b'{"data":[]}')
            self.assertEqual(response.read.call_args.args, (p.MAX_RESPONSE_BYTES + 1,))
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, p.SOURCES['nous'])
            self.assertNotIn('Authorization', request.headers)
            self.assertLessEqual(opener.open.call_args.kwargs['timeout'], 10)
            response.headers['Content-Length'] = str(p.MAX_RESPONSE_BYTES + 1)
            with self.assertRaises(ValueError):
                p._fetch_http('nous')
        handler = p._OfficialRedirect('nous')
        for url in ('http://inference-api.nousresearch.com/v1/models',
                    'https://evil.invalid/models',
                    'https://user:password@inference-api.nousresearch.com/v1/models',
                    'https://inference-api.nousresearch.com/v1/models?token=secret'):
            with self.assertRaises(ValueError):
                handler.redirect_request(request, None, 302, 'Found', {}, url)

    def test_fetch_process_timeout_is_killed_and_all_requests_have_total_budget(self):
        from unittest.mock import patch, MagicMock
        import subprocess
        p = self.module()
        self.assertTrue(hasattr(p, '_fetch_sources'), 'hard deadline process supervisor missing')
        workers = []
        def launch(*args, **kwargs):
            worker = MagicMock()
            worker.returncode = 0
            if not workers:
                worker.communicate.side_effect = [subprocess.TimeoutExpired('fetch', 12), (b'', None)]
            else:
                worker.communicate.return_value = (catalog([model(prompt='0', completion='0')]), None)
            workers.append(worker)
            return worker
        with patch.object(p.subprocess, 'Popen', side_effect=launch), patch.object(p.time, 'monotonic', return_value=100):
            payloads = p._fetch_sources(140)
        self.assertEqual(len(workers), len(p.SOURCES))
        self.assertTrue(workers[0].kill.called)
        self.assertIsInstance(payloads['nous'], Exception)
        self.assertIsInstance(payloads['openrouter'], bytes)
        self.assertLessEqual(p.TOTAL_BUDGET_SECONDS, 60)
        for worker in workers:
            self.assertLessEqual(worker.communicate.call_args_list[0].kwargs['timeout'], p.SOURCE_PROCESS_SECONDS)

    def test_openrouter_dynamic_route_negative_sentinel_is_unknown_not_a_negative_price(self):
        p = self.module()
        result = p.refresh_prices(self.home, sources={'openrouter': catalog([
            model(name='openrouter/auto', prompt='-1', completion='-1'),
            model(prompt='0.000002', completion='0.000003')])})
        self.assertEqual(result['sources']['openrouter']['status'], 'ok')
        self.assertEqual(result['sources']['openrouter']['skipped_count'], 1)
        self.assertIsNone(p.lookup_price(self.home, 'openrouter', 'openrouter/auto'))
        self.assertIsNotNone(p.lookup_price(self.home, 'openrouter', 'vendor/exact-model'))


if __name__ == '__main__':
    unittest.main()
