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

    def test_native_reference_fallback_preserves_route_priority_and_exact_identity(self):
        p = self.module()
        p.refresh_prices(self.home, sources={
            'openai': openai_page([['gpt-example', 2, '.1', 10]]),
            'anthropic': anthropic_page(),
            'nous': catalog([model(name='openai/gpt-example', prompt='0', completion='0')]),
        })
        for provider, name in [('custom', 'gpt-example'), ('', 'openai/gpt-example'),
                               ('infron', 'openai/gpt-example:free'),
                               ('opencodex', 'google-antigravity/claude-sonnet-4-5')]:
            with self.subTest(provider=provider, model=name):
                price = p.lookup_price(self.home, provider, name)
                self.assertIsNotNone(price)
                assert price is not None
                self.assertEqual(price.pricing_basis, 'native_reference')
                self.assertIn(price.source_url, (p.SOURCES['openai'], p.SOURCES['anthropic']))
        direct = p.lookup_price(self.home, 'nous', 'openai/gpt-example')
        assert direct is not None
        self.assertEqual(direct.input_cost_per_million, Decimal(0))
        self.assertEqual(direct.pricing_basis, 'route')
        for name in ['gpt-example-unknown', 'anthropic/gpt-example', 'evil/gpt-example']:
            self.assertIsNone(p.lookup_price(self.home, 'custom', name))

    def test_extra_sources_integrate_route_priority_and_native_versions(self):
        from test_pricing_extra import deepseek_page, opencode_page, hive_page
        p = self.module()
        result = p.refresh_prices(self.home, sources={
            'deepseek': deepseek_page(), 'opencode': opencode_page(), 'thehive': hive_page()})
        self.assertTrue(all(s['status'] == 'ok' for s in result['sources'].values()))
        for provider, model_id, owner, rate, basis in [
                ('opencode', 'paid-exact.1', 'opencode', '1.23', 'route'),
                ('opencode', 'opencode/paid-exact.1', 'opencode', '1.23', 'route'),
                ('opencode-go', 'paid-exact.1', 'opencode', '1.23', 'provider_reference'),
                ('opencodex', 'opencode-go/paid-exact.1', 'opencode', '1.23', 'provider_reference'),
                ('opencode', 'free-exact', 'opencode', '0', 'route'),
                ('thehive', 'deepseek-ai/deepseek-v4.1-flash-1017', 'thehive', '1.25', 'route'),
                ('custom', 'deepseek-ai/deepseek-v4.1-flash-1017', 'deepseek', '2', 'native_reference'),
                ('infron', 'deepseek/deepseek-v4.1-flash:free', 'deepseek', '2', 'native_reference'),
                ('opencodex', 'thehive/deepseek-ai-deepseek-v4.1-flash-1017', 'thehive', '1.25', 'route'),
                ('opencodex', 'thehive/zai-org-glm-5.3-flash', 'thehive', '1.25', 'route'),
                ('custom', 'deepseek-flash', 'deepseek', '2', 'native_reference')]:
            with self.subTest(provider=provider, model=model_id):
                price = p.lookup_price(self.home, provider, model_id)
                assert price is not None
                self.assertEqual(price.input_cost_per_million, Decimal(rate))
                self.assertEqual(price.provider, owner)
                self.assertEqual(price.pricing_basis, basis)
        for provider, model_id in [('custom','paid-exact.1'), ('opencodex','paid-exact.1'),
                ('custom','deepseek-v4.1-flash-9999'), ('custom','deepseek-v4.2-flash')]:
            self.assertIsNone(p.lookup_price(self.home, provider, model_id))
        native = p.lookup_price(self.home, 'thehive', 'deepseek-ai/deepseek-v4.1-flash-1017', native_only=True)
        assert native is not None
        self.assertEqual(native.provider, 'deepseek')
        self.assertEqual(native.input_cost_per_million, Decimal('2'))

    def test_meta_prices_all_explicit_standard_and_contributor_ids(self):
        p = self.module()
        header = '<tr><th>Usage</th><th>Standard mode (per 1M tokens)</th><th>Contributor mode (per 1M tokens)</th></tr>'
        payload = '<h3 id="muse-wonder-pricing">Muse Wonder</h3><p>Model: muse-wonder-1.0.</p><table>' + header + (
            '<tr><td>Input</td><td>$4</td><td>Not available</td></tr>'
            '<tr><td>Output</td><td>$20</td><td>Not available</td></tr>'
            '<tr><td>Cached input</td><td>$0.2</td><td>Not available</td></tr></table>'
            '<h3 id="muse-spark-pricing">Muse Spark</h3><p>Models: muse-spark-1.3, muse-spark-1.2, muse-spark-1.1 in Standard mode; '
            'muse-spark-1.3-contributor and muse-spark-1.2-contributor in Contributor mode.</p><table>') + header + (
            '<tr><td>Input</td><td>$1.25</td><td>$0.10</td></tr>'
            '<tr><td>Output</td><td>$4.25</td><td>$0.20</td></tr>'
            '<tr><td>Cached input</td><td>$0.15</td><td>$0.002</td></tr></table>')
        result = p.refresh_prices(self.home, sources={'meta': payload})
        self.assertEqual(result['sources']['meta']['count'], 6)
        for model, input_rate in [('muse-wonder-1.0','4'), ('muse-spark-1.1','1.25'),
                ('muse-spark-1.2','1.25'), ('muse-spark-1.3','1.25'),
                ('muse-spark-1.2-contributor','0.10'), ('muse-spark-1.3-contributor','0.10')]:
            entry = p.lookup_price(self.home, 'custom', model)
            assert entry is not None
            self.assertEqual(entry.input_cost_per_million, Decimal(input_rate))
            self.assertEqual(entry.cache_write_cost_per_million, None)
        for model in ('muse-spark-1.1-contributor','muse-wonder-1.0-contributor','muse-spark-1.4'):
            self.assertIsNone(p.lookup_price(self.home, 'custom', model))

    def test_additional_native_sources_use_exact_tables_not_neighboring_tariffs(self):
        p = self.module()
        def table(headers, rows):
            return '<table><tr>' + ''.join('<th>'+x+'</th>' for x in headers) + '</tr>' + ''.join(
                '<tr>'+''.join('<td>'+x+'</td>' for x in row)+'</tr>' for row in rows) + '</table>'
        payloads = {
            'zai': '<p>All prices are in USD. Prices per 1M tokens.</p>' + table(
                ['Model', 'Input', 'Cached Input', 'Cached Input Storage', 'Output'],
                [['GLM-5.3-FlashX','$99','$98','Limited-time Free','$97'],
                 ['GLM-5.3-Flash','$2','$0.2','Limited-time Free','$5']]),
            'stepfun': table(['Model','Billing unit','Input (cache miss)','Input (cache hit)','Output'],
                [['step-5-preview','1M tokens','$3','$0.3','$7']]) +
                '<p>For step-5-preview, the cache-miss input price includes writing new content to the cache.</p>',
            'meta': '<h3 id="muse-wonder-pricing">Muse Wonder</h3>' + table(
                ['Usage','Standard mode (per 1M tokens)','Contributor mode (per 1M tokens)'],
                [['Input','$99','$99'],['Output','$99','$99'],['Cached input','$99','$99']]) +
                '<h3 id="muse-spark-pricing">Muse Spark</h3><p>muse-spark-1.3-contributor</p>' + table(
                ['Usage','Standard mode (per 1M tokens)','Contributor mode (per 1M tokens)'],
                [['Input','$99','$4'],['Output','$99','$8'],['Cached input','$99','$0.4']]),
        }
        result = p.refresh_prices(self.home, sources=payloads)
        for source, model_id, input_rate in [('zai','glm-5.3-flash','2'),
                ('stepfun','step-5-preview','3'), ('meta','muse-spark-1.3-contributor','4')]:
            with self.subTest(source=source):
                self.assertEqual(result['sources'][source]['status'], 'ok')
                entry = p.lookup_price(self.home, 'custom', model_id)
                assert entry is not None
                self.assertEqual(entry.input_cost_per_million, Decimal(input_rate))
                self.assertEqual(entry.pricing_basis, 'native_reference')
                self.assertEqual(entry.source_url, p.SOURCES[source])
                self.assertEqual(entry.cache_write_cost_per_million,
                    Decimal(input_rate) if source == 'stepfun' else None)
                broken = payloads[source].replace('1M tokens','1K tokens')
                failed = p.refresh_prices(self.home, sources={source: broken})
                self.assertEqual(failed['sources'][source]['status'], 'error')
                self.assertEqual(p.lookup_price(self.home, 'custom', model_id).input_cost_per_million, Decimal(input_rate))
        self.assertIsNone(p.lookup_price(self.home,'custom','glm-5.3-flashx'))
        self.assertIsNone(p.lookup_price(self.home,'custom','step-5-preview-free'))
        self.assertIsNone(p.lookup_price(self.home,'custom','muse-spark-1.3'))
        self.assertIsNotNone(p.lookup_price(self.home,'custom','zai-org/glm-5.3-flash'))
        self.assertIsNotNone(p.lookup_price(self.home,'custom','stepfun/step-5-preview:free'))

    def test_stepfun_write_semantics_require_affirmative_exact_model_statement(self):
        p = self.module()
        table = ('<table><tr><th>Model</th><th>Billing unit</th><th>Input (cache miss)</th>'
                 '<th>Input (cache hit)</th><th>Output</th></tr><tr><td>step-5-preview</td>'
                 '<td>1M tokens</td><td>$3</td><td>$0.3</td><td>$7</td></tr></table>')
        statement = 'For step-5-preview, the cache-miss input price includes writing new content to the cache.'
        for valid in (statement, statement.replace('step-5-preview', '<code>step-5-preview</code> ')):
            with self.subTest(valid=valid):
                rows, _ = p._parse('stepfun', table + '<p>' + valid + '</p>', 1000)
                self.assertEqual(rows[0]['cache_write_cost_per_million'], '3')
        rows, _ = p._parse('stepfun', table + '<span data-as="p">' + statement.replace(
            'step-5-preview', '<code>step-5-preview</code>') +
            ' Output tokens include both the model reasoning process and final answer.</span>', 1000)
        self.assertEqual(rows[0]['cache_write_cost_per_million'], '3')
        for invalid in (statement.replace('step-5-preview', 'step-other'),
                        statement.replace('step-5-preview', 'step-5-preview-other'),
                        statement.replace('includes', 'does not include'),
                        'It is not true that ' + statement,
                        statement.replace('For step-5-preview, the', 'The')):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'unverified_write_semantics'):
                p._parse('stepfun', table + '<p>' + invalid + '</p>', 1000)

    def test_zai_units_and_currency_are_applicable_to_target_table(self):
        p = self.module()
        table = ('<table><tr><th>Model</th><th>Input</th><th>Cached Input</th>'
                 '<th>Cached Input Storage</th><th>Output</th></tr>'
                 '<tr><td>GLM-5.3-Flash</td><td>$2</td><td>$0.2</td>'
                 '<td>Limited-time Free</td><td>$5</td></tr></table>')
        global_usd = '<p>All prices are in USD.</p>'
        latest = '<h3 id="latest-models">Latest Models</h3>'
        unrelated = ('<h3 id="other-models">Other Models</h3>'
                     '<p>All prices are in USD. Prices per 1M tokens.</p>'
                     + table.replace('GLM-5.3-Flash', 'Other-Model'))
        for prefix in (global_usd + '<p>Prices per 1M tokens.</p>',
                       global_usd + latest + '<p>Prices per 1M tokens.</p>'):
            with self.subTest(valid=prefix):
                rows, _ = p._parse('zai', prefix + table, 1000)
                self.assertEqual(rows[0]['input_cost_per_million'], '2')
        for intro in ('Prices per 1K tokens.', '',
                      'Prices per 1M tokens. Prices per 1K tokens.',
                      'Prices per 1M tokens. All prices are in EUR.',
                      'Prices per 1M tokens. Currency: CNY.',
                      'Prices per 1M tokens. Prices per 1000 tokens.'):
            with self.subTest(invalid=intro), self.assertRaisesRegex(ValueError, 'unverified_currency_or_units'):
                p._parse('zai', unrelated + latest + '<p>' + intro + '</p>' + table, 1000)

    def test_meta_requires_exact_model_token_and_unique_contributor_rows(self):
        p = self.module()
        intro = '<h3 id="muse-spark-pricing">Muse Spark</h3><p><code>muse-spark-1.3-contributor</code></p>'
        input_row = '<tr><td>Input</td><td>$99</td><td>$4</td></tr>'
        table = ('<table><tr><th>Usage</th><th>Standard mode (per 1M tokens)</th>'
                 '<th>Contributor mode (per 1M tokens)</th></tr>' + input_row
                 + '<tr><td>Output</td><td>$99</td><td>$8</td></tr>'
                 '<tr><td>Cached input</td><td>$99</td><td>$0.4</td></tr></table>')
        rows, _ = p._parse('meta', intro + table, 1000)
        self.assertEqual(rows[0]['input_cost_per_million'], '4')
        for invalid in (intro.replace('contributor</code>', 'contributor-other</code>') + table,
                        intro.replace('muse-spark-1.3', 'other-muse-spark-1.3') + table,
                        intro + table.replace('</table>', input_row + '</table>'),
                        intro + table.replace('<th>Contributor mode (per 1M tokens)</th>', '')
                                     .replace('<td>$4</td>', '').replace('<td>$8</td>', '')
                                     .replace('<td>$0.4</td>', '')):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                p._parse('meta', invalid, 1000)

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
        self.assertIsNotNone(p.lookup_price(self.home, 'opencode-go', 'gpt-example'))
        self.assertIsNotNone(p.lookup_price(self.home, 'custom', 'gpt-example'))
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
        self.assertIsNotNone(p.lookup_price(self.home, 'custom', 'claude-sonnet-4-5'))

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
