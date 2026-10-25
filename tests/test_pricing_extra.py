"""Offline parser contracts; fixture prices are synthetic, not snapshots."""
import importlib.util
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def deepseek_page(version='DeepSeek-V4.1-Flash-1017'):
    return ('<p>Prices per 1M tokens.</p><table>'
        '<tr><td colspan="3">MODEL</td><td>deepseek-flash<sup>(1)</sup></td><td>deepseek-v4-pro</td></tr>'
        '<tr><td colspan="3">MODEL VERSION</td><td>' + version + '</td><td>DeepSeek-V4-Pro-0813</td></tr>'
        '<tr><td rowspan="6">PRICING<sup>(2)</sup></td><td rowspan="2">1M INPUT TOKENS<br>(CACHE HIT)</td>'
        '<td>OFF-PEAK</td><td>$0.01</td><td>$0.02</td></tr>'
        '<tr><td>PEAK</td><td>$0.02</td><td>$0.04</td></tr>'
        '<tr><td rowspan="2">1M INPUT TOKENS<br>(CACHE MISS)</td><td>OFF-PEAK</td><td>$1</td><td>$2</td></tr>'
        '<tr><td>PEAK</td><td>$2</td><td>$4</td></tr>'
        '<tr><td rowspan="2">1M OUTPUT TOKENS</td><td>OFF-PEAK</td><td>$3</td><td>$4</td></tr>'
        '<tr><td>PEAK</td><td>$6</td><td>$8</td></tr></table>')


def table(headers, rows):
    return '<table><tr>' + ''.join('<th>' + h + '</th>' for h in headers) + '</tr>' + ''.join(
        '<tr>' + ''.join('<td>' + c + '</td>' for c in row) + '</tr>' for row in rows) + '</table>'


def opencode_page(prices=None, endpoints=None):
    return '<h2>Endpoints</h2>' + table(['Model', 'Model ID', 'Endpoint', 'AI SDK Package'],
        endpoints or [['Paid Display', 'paid-exact.1', 'https://opencode.ai/inference/openai/v1/responses', '-'],
                      ['Free Display', 'free-exact', 'https://opencode.ai/inference/openai/v1/responses', '-']]) + (
        '<h2>Pricing</h2><p>Prices per <strong>1M tokens</strong>.</p>') + table(
        ['Model', 'Input', 'Output', 'Cached Read', 'Cached Write'], prices or [
            ['Paid Display', '$1.23', '$4.56', '$0.001', '-'],
            ['Free Display', 'Free', 'Free', 'Free', '-'],
            ['Paid Display Neighbor', '$99', '$99', '$99', '$99']])


GOOGLE_PAID = 'Paid Tier, per 1M tokens in USD'


def google_page(sections=None, *, paid=GOOGLE_PAID):
    sections = sections if sections is not None else [
        ('gemini-4-argon', [
            ('Input price', '$2.00 through November 30, 2026. $4.00 starting December 1, 2026.'),
            ('Output price (including thinking tokens)',
             '$10.00 through November 30, 2026. $20.00 starting December 1, 2026.'),
            ('Context caching price', '$0.10 through November 30, 2026. $0.20 starting December 1, 2026. '
             'Implicit caching only (explicit caching is not supported).'),
            ('Grounding with Google Search*', '5,000 free requests, then $14 per 1,000 requests.'),
        ]),
        ('gemini-3.1-pro-preview', [
            ('Input price', '$2.00, prompts <= 200k tokens $4.00, prompts > 200k tokens'),
            ('Output price (including thinking tokens)', '$12.00, prompts <= 200k tokens $18.00, prompts > 200k'),
            ('Context caching price', '$0.20, prompts <= 200k tokens $0.40, prompts > 200k '
             '$4.50 / 1,000,000 tokens per hour (storage price)'),
        ]),
        ('gemini-2.5-flash', [
            ('Input price', '$0.30 (text / image / video) $1.00 (audio)'),
            ('Output price (including thinking tokens)', '$2.50'),
            ('Context caching price', '$0.03 (text / image / video) $0.1 (audio) '
             '$1.00 / 1,000,000 tokens per hour (storage price)'),
        ]),
    ]
    body = ''
    for anchor, rows in sections:
        body += ('<h2 id="' + anchor + '">Display name</h2><h3 id="standard">Standard</h3><table>'
                 '<tr><th></th><th>Free Tier</th><th>' + paid + '</th></tr>')
        body += ''.join('<tr><td>' + label + '</td><td>Free of charge</td><td>' + value + '</td></tr>'
                        for label, value in rows)
        body += '</table><h3 id="batch">Batch</h3><table><tr><th></th><th>Free Tier</th><th>' + paid
        body += ('</th></tr><tr><td>Input price</td><td>Not available</td>'
                 '<td>$99.00</td></tr></table>')
    # Unmapped media section: text-only coverage must not borrow its image rate.
    body += ('<h2 id="gemini-2.5-flash-image">Image</h2><h3 id="standard_9">Standard</h3><table>'
             '<tr><th></th><th>Free Tier</th><th>' + paid + '</th></tr>'
             '<tr><td>Input price</td><td>Not available</td><td>$0.30 (text / image)</td></tr>'
             '<tr><td>Output price</td><td>Not available</td><td>$0.039 per image*</td></tr></table>')
    return body


def hive_page(label='DeepSeek-V4.1-Flash-1017', discounted=True):
    body = ''
    for name in (label, 'Z.AI GLM-5.3-Flash'):
        body += '<div><h6>' + name + '</h6><span>Description</span>'
        for unit, rate in [('Input', '1.25'), ('Output', '4.50'), ('Cache Read', '0.03')]:
            price = ('<span class="line-through">$99</span> ' if discounted else '') + '$' + rate
            body += '<p class="hidden md:block">' + price + ' / 1M ' + unit + ' Tokens</p>'
            body += '<p class="block md:hidden">' + price + ' / 1M ' + unit + ' Tokens</p>'
        body += '</div>'
    return body + '<h6>Neighbor Model</h6><p>$999 / 1M Input Tokens</p>'


class ExtraTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('pricing_extra'), 'pricing_extra missing')
        import pricing_extra
        return pricing_extra

    def test_deepseek_spans_peak_reference_and_exact_versions(self):
        p = self.module()
        rows, stats = p.parse('deepseek', deepseek_page(), 1000)
        self.assertEqual(stats, {'model_count': 2, 'skipped_count': 0})
        self.assertEqual([r['model'] for r in rows], ['deepseek-flash', 'deepseek-v4-pro'])
        self.assertEqual(rows[0]['input_cost_per_million'], '2')
        self.assertEqual(rows[0]['output_cost_per_million'], '6')
        self.assertEqual(rows[0]['cache_read_cost_per_million'], '0.02')
        self.assertEqual(rows[1]['input_cost_per_million'], '4')
        self.assertEqual(rows[0]['model_version'], 'DeepSeek-V4.1-Flash-1017')
        self.assertEqual(rows[0]['aliases'], ['deepseek-v4.1-flash-1017'])
        self.assertEqual(rows[1]['aliases'], ['deepseek-v4-pro-0813'])
        self.assertEqual(rows[0]['off_peak_rates']['input_cost_per_million'], '1')
        for row in rows:
            self.assertEqual(row['provider'], 'deepseek')
            self.assertEqual(row['source_id'], 'deepseek')
            self.assertTrue(row['source'].startswith(p.SOURCES['deepseek'] + ' | '))
            self.assertIn('PEAK', row['source'])
            self.assertIn('off-peak', row['source'])
            self.assertIn('not actual hourly billed', row['source'])
            self.assertIsNone(row['cache_write_cost_per_million'])
            self.assertIsNone(row['request_cost'])
            self.assertIsNone(row['tier_threshold_tokens'])
            self.assertEqual(row['checked_at'], 1000)
            self.assertIn('UTC', row['pricing_version'])


    def test_opencode_joins_exact_endpoint_ids_and_preserves_free_and_unknown(self):
        p = self.module()
        rows, stats = p.parse('opencode', opencode_page().encode(), 1000)
        self.assertEqual(stats, {'model_count': 3, 'skipped_count': 1})
        self.assertEqual([r['model'] for r in rows], ['paid-exact.1', 'free-exact'])
        self.assertEqual([rows[0][f] for f in p.RATE_FIELDS], ['1.23', '4.56', '0.001', None])
        self.assertEqual([rows[1][f] for f in p.RATE_FIELDS], ['0', '0', '0', None])
        self.assertTrue(all(r['source'].startswith(p.SOURCES['opencode'] + ' | ') for r in rows))
        self.assertTrue(all(r['provider'] == r['source_id'] == 'opencode' for r in rows))


    def test_opencode_base_context_tiers_and_active_discount(self):
        p = self.module()
        payload = opencode_page([
            ['Paid Display (≤ 272K tokens)', '$2', '$7', '$0.2', '$2.50'],
            ['Paid Display (> 272K tokens)', '$4', '$11', '$0.4', '$5'],
            ['Free Display (50% off)', '<del>$8</del> $4', '<s>$16</s> $8', '-', '-'],
        ])
        rows, stats = p.parse('opencode', payload, 1000)
        self.assertEqual(stats, {'model_count': 3, 'skipped_count': 1})
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['tier_threshold_tokens'], 272000)
        self.assertIn('per-request', rows[0]['source'])
        self.assertIn('272000', rows[0]['source'])
        self.assertEqual(rows[1]['input_cost_per_million'], '4')
        self.assertEqual(rows[1]['output_cost_per_million'], '8')
        self.assertIn('50% off', rows[1]['source'])

    def test_opencode_units_must_belong_to_pricing_section(self):
        p = self.module()
        payload = '<h2>Unrelated</h2><p>per 1M tokens</p>' + opencode_page().replace('1M tokens', '1K tokens')
        with self.assertRaisesRegex(ValueError, 'unverified_units'):
            p.parse('opencode', payload, 1000)


    def test_hive_active_prices_duplicate_layout_and_verified_exact_ids(self):
        p = self.module()
        for label, identifier in [('DeepSeek-V4.1-Flash-1017', 'deepseek-ai/deepseek-v4.1-flash-1017'),
                                  ('DeepSeek-V4.1-Flash', 'deepseek-ai/deepseek-v4.1-flash')]:
            with self.subTest(label=label):
                rows, stats = p.parse('thehive', hive_page(label), 1000)
                self.assertEqual(stats, {'model_count': 2, 'skipped_count': 0})
                self.assertEqual([r['model'] for r in rows], [identifier, 'zai-org/glm-5.3-flash'])
                for row in rows:
                    self.assertEqual([row[f] for f in p.RATE_FIELDS], ['1.25', '4.50', '0.03', None])
                    self.assertEqual(row['provider'], 'thehive')
                    self.assertEqual(row['source_id'], 'thehive')
                    self.assertTrue(row['source'].startswith(p.SOURCES['thehive'] + ' | '))
                    self.assertIn('docs.thehive.ai', row['model_id_source'])
                self.assertNotIn('deepseek-ai/deepseek-v4.1-flash-1017', rows[0].get('aliases', []))


    def test_google_standard_text_rates_scheduled_change_and_tiers(self):
        from datetime import datetime, timezone
        p = self.module()
        checked = datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp()
        rows, stats = p.parse('google', google_page(), checked)
        self.assertEqual([r['model'] for r in rows],
                         ['gemini-4-argon', 'gemini-3.1-pro-preview', 'gemini-2.5-flash'])
        self.assertEqual(stats, {'model_count': len(p.GOOGLE_MODEL_SECTIONS),
                                 'skipped_count': len(p.GOOGLE_MODEL_SECTIONS) - 3})
        argon = rows[0]
        self.assertEqual([argon[f] for f in p.RATE_FIELDS], ['2.00', '10.00', '0.10', None])
        self.assertEqual(argon['current_until'], '2026-11-30')
        self.assertEqual(argon['next_rates'], {'input_cost_per_million': '4.00',
                                              'output_cost_per_million': '20.00',
                                              'cache_read_cost_per_million': '0.20'})
        self.assertIsNone(argon['tier_threshold_tokens'])
        tiered = rows[1]
        self.assertEqual([tiered[f] for f in p.RATE_FIELDS], ['2.00', '12.00', '0.20', None])
        self.assertEqual(tiered['tier_threshold_tokens'], 200000)
        self.assertEqual(tiered['above_threshold_rates'], {'input_cost_per_million': '4.00',
                                                           'output_cost_per_million': '18.00',
                                                           'cache_read_cost_per_million': '0.40'})
        self.assertIsNone(tiered.get('current_until'))
        self.assertEqual([rows[2][f] for f in p.RATE_FIELDS], ['0.30', '2.50', '0.03', None])
        for row in rows:
            self.assertEqual(row['provider'], 'google')
            self.assertEqual(row['source_id'], 'google')
            self.assertTrue(row['source'].startswith(p.SOURCES['google'] + ' | '))
            self.assertIn('Standard', row['source'])
            self.assertIn('not billed', row['source'])
            self.assertEqual(row['checked_at'], checked)
            self.assertIn('UTC', row['pricing_version'])

    def test_google_scheduled_rate_switches_after_the_published_transition(self):
        from datetime import datetime, timezone
        p = self.module()
        rows, _ = p.parse('google', google_page(), datetime(2026, 12, 2, tzinfo=timezone.utc).timestamp())
        self.assertEqual([rows[0][f] for f in p.RATE_FIELDS], ['4.00', '20.00', '0.20', None])
        self.assertNotIn('current_until', rows[0])
        self.assertNotIn('next_rates', rows[0])
        boundary, _ = p.parse('google', google_page(), datetime(2026, 12, 1, tzinfo=timezone.utc).timestamp())
        self.assertEqual(boundary[0]['input_cost_per_million'], '4.00')
        last_day, _ = p.parse('google', google_page(), datetime(2026, 11, 30, 23, tzinfo=timezone.utc).timestamp())
        self.assertEqual(last_day[0]['input_cost_per_million'], '2.00')

    def test_google_units_conditions_and_mixed_tiers_fail_closed(self):
        p = self.module()
        mixed = [('gemini-2.5-pro', [
            ('Input price', '$1.25, prompts <= 200k tokens $2.50, prompts > 200k tokens'),
            ('Output price (including thinking tokens)', '$10.00, prompts <= 100k tokens $15.00, prompts > 100k tokens'),
        ])]
        bad_schedule = [('gemini-2.5-pro', [
            ('Input price', '$1.25 through November 30, 2026. $2.50 starting December 2, 2026.'),
            ('Output price (including thinking tokens)', '$10.00'),
        ])]
        unsupported = [('gemini-9.9-unknown', [
            ('Input price', '$1.00'), ('Output price (including thinking tokens)', '$10.00')])]
        cases = [
            (google_page(paid='Paid Tier, per 1M tokens in EUR'), 'unverified_units'),
            (google_page([('gemini-2.5-pro', [
                ('Input price', 'Contact sales'),
                ('Output price (including thinking tokens)', '$10.00')])]), 'unknown_price'),
            (google_page([('gemini-2.5-pro', [
                ('Input price', '$1.00 (audio)'),
                ('Output price (including thinking tokens)', '$10.00')])]), 'no_text_rate'),
            (google_page([('gemini-2.5-pro', [('Input price', '$1.00')])]), 'missing_text_rates'),
            (google_page(mixed), 'inconsistent_tier'),
            (google_page(bad_schedule), 'inconsistent_schedule'),
            (google_page([('gemini-2.5-pro', [
                ('Input price', '$1.00'), ('Output price (including thinking tokens)', '$10.00')])] * 2),
             'duplicate_model_section'),
            (google_page(unsupported), 'no_supported_prices'),
        ]
        for payload, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                p.parse('google', payload, 1000)

    def test_google_never_borrows_batch_or_unmapped_media_rates(self):
        p = self.module()
        rows, _ = p.parse('google', google_page(), 1000)
        self.assertNotIn('gemini-2.5-flash-image', [r['model'] for r in rows])
        for row in rows:
            self.assertNotIn('99', [row[f] for f in p.RATE_FIELDS if row[f]])

    def test_input_limits_deadlines_and_unknown_source(self):
        p = self.module()
        for name, payload, error in [
            ('other', deepseek_page(), 'unknown_source'),
            ('deepseek', {}, 'invalid_payload'),
            ('deepseek', b'x' * (p.MAX_RESPONSE_BYTES + 1), 'response_too_large'),
            ('deepseek', '<div>' * 300 + deepseek_page(), 'document_too_complex'),
        ]:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                p.parse(name, payload, 1000)
        for payload in ('', deepseek_page()):
            with self.assertRaisesRegex(ValueError, 'timeout'):
                p.parse('deepseek', payload, 1000, deadline=time.monotonic() - 1)

    def test_opencode_rejects_extra_columns_and_duplicate_id_bindings(self):
        p = self.module()
        for payload in [opencode_page().replace('<td>$1.23</td>', '<td>$1.23</td><td>$0.001</td>'),
                        opencode_page().replace('free-exact', 'paid-exact.1')]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                p.parse('opencode', payload, 1000)

    def test_hive_missing_active_prices_cannot_silently_drop_a_model(self):
        p = self.module()
        payload = hive_page().replace('$1.25', 'Contact sales').replace('$4.50', 'Contact sales').replace('$0.03', 'Contact sales')
        payload += '<h6>Z.AI GLM-5.3-Flash</h6><p>$1 / 1M Input Tokens</p><p>$2 / 1M Output Tokens</p><p>$3 / 1M Cache Read Tokens</p>'
        with self.assertRaises(ValueError):
            p.parse('thehive', payload, 1000)


    def test_hive_duplicate_without_an_active_price_is_ambiguous(self):
        p = self.module()
        payload = hive_page(discounted=False).replace('$1.25 / 1M Input Tokens', 'Contact sales / 1M Input Tokens', 1)
        with self.assertRaisesRegex(ValueError, 'ambiguous_hive'):
            p.parse('thehive', payload, 1000)

    def test_hive_discount_syntax_and_conflicts_fail_closed(self):
        p = self.module()
        for old_markup in ('<del>$99</del>', '<s>$99</s>',
                           '<span style="text-decoration: line-through">$99</span>',
                           '<span style="text-decoration-line: line-through">$99</span>',
                           '<span class="ptl-line-through">$99</span>'):
            payload = hive_page().replace('<span class="line-through">$99</span>', old_markup)
            rows, _ = p.parse('thehive', payload, 1000)
            self.assertEqual(rows[0]['input_cost_per_million'], '1.25')
        for payload in [
            hive_page().replace('class="line-through"', 'class="unknown-price-class"'),
            hive_page().replace('$1.25', '$1.99', 1),
            hive_page().replace('1M Input Tokens', '1K Input Tokens'),
            hive_page().replace('$1.25', '$1.25 or $0.60'),
            hive_page().replace('1M Cache Read Tokens', '1M Cache Write Tokens'),
        ]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                p.parse('thehive', payload, 1000)

    def test_deepseek_undated_version_never_gains_a_dated_alias(self):
        p = self.module()
        rows, _ = p.parse('deepseek', deepseek_page('DeepSeek-V4.1-Flash'), 1000)
        self.assertEqual(rows[0]['model_version'], 'DeepSeek-V4.1-Flash')
        self.assertEqual(rows[0]['aliases'], ['deepseek-v4.1-flash'])
        self.assertNotIn('1017', repr(rows[0]))

    def test_deepseek_malformed_spans_missing_peak_and_unknown_versions_fail(self):
        p = self.module()
        for payload in [
            deepseek_page().replace('rowspan="6"', 'rowspan="999999"'),
            deepseek_page().replace('colspan="3"', 'colspan="0"'),
            deepseek_page().replace('<td>PEAK</td>', '<td>AVERAGE</td>', 1),
            deepseek_page().replace('1M INPUT TOKENS', '1K INPUT TOKENS'),
            deepseek_page().replace('DeepSeek-V4.1-Flash-1017', 'DeepSeek-V4.2-Flash'),
            deepseek_page() + deepseek_page(),
        ]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                p.parse('deepseek', payload, 1000)

    def test_opencode_unknown_conditions_and_missing_prices_not_invented(self):
        p = self.module()
        rows, stats = p.parse('opencode', opencode_page([
            ['Paid Display (special tier)', '$99', '$99', '$99', '$99'],
            ['Paid Display-1017', '$99', '$99', '$99', '$99'],
            ['Paid Display', '-', '-', '-', '-'],
            ['Free Display', 'Free', '-', '-', '-'],
        ]), 1000)
        self.assertEqual([r['model'] for r in rows], ['free-exact'])
        self.assertEqual(rows[0]['input_cost_per_million'], '0')
        self.assertIsNone(rows[0]['output_cost_per_million'])
        self.assertEqual(stats, {'model_count': 4, 'skipped_count': 3})

    def test_script_prices_are_inert_and_invalid_rates_are_rejected(self):
        p = self.module()
        rows, _ = p.parse('opencode', '<script>' + opencode_page() + '</script>' + opencode_page(), 1000)
        self.assertEqual(len(rows), 2)
        for bad in ('$NaN', '$Infinity', '$-1', '$1e2', '$1000000000001', '$1 $2', 'Free*'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                p.parse('opencode', opencode_page().replace('$1.23', bad), 1000)


    def test_oversized_cells_and_truncated_html_fail_closed(self):
        p = self.module()
        with self.assertRaisesRegex(ValueError, 'document_too_complex'):
            p.parse('opencode', opencode_page().replace('Paid Display', 'x' * 17000), 1000)
        with self.assertRaisesRegex(ValueError, 'incomplete_document'):
            p.parse('opencode', opencode_page() + '<table><tr><td>unfinished', 1000)


if __name__ == '__main__':
    unittest.main()
