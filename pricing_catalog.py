"""Official-source USD price catalog. Read paths never access the network."""
from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit
from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

# Isolated (-I) fetch subprocesses exclude the script directory from sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pricing_extra import SOURCES as EXTRA_SOURCES, parse as parse_extra

SOURCES = {
    'nous': 'https://inference-api.nousresearch.com/v1/models',
    'openrouter': 'https://openrouter.ai/api/v1/models',
    'openai': 'https://developers.openai.com/api/docs/pricing',
    'anthropic': 'https://platform.claude.com/docs/en/about-claude/pricing',
    'zai': 'https://docs.z.ai/guides/overview/pricing',
    'stepfun': 'https://platform.stepfun.ai/docs/en/guides/pricing/details',
    'meta': 'https://dev.meta.ai/docs/pricing-rate-limits',
    **EXTRA_SOURCES,
}
RATE_FIELDS = {
    'input_cost_per_million': 'prompt',
    'output_cost_per_million': 'completion',
    'cache_read_cost_per_million': 'input_cache_read',
    'cache_write_cost_per_million': 'input_cache_write',
}
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_CACHE_BYTES = 16 * 1024 * 1024
TOTAL_BUDGET_SECONDS = 55
FETCH_BUDGET_SECONDS = 40
SOURCE_PROCESS_SECONDS = 12
ERROR_CODES = {'source_failed', 'schema_error', 'timeout', 'response_too_large', 'transport_error'}


class _SourceError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code if code in ERROR_CODES else 'source_failed'


def _official_url(name, url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != urlsplit(SOURCES[name]).hostname
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError('unofficial_redirect')


class _OfficialRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 1

    def __init__(self, name):
        self.name = name

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _official_url(self.name, newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch_http(name):
    # No auth, cookies, credential files, environment proxies, or custom endpoints.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _OfficialRedirect(name))
    request = urllib.request.Request(SOURCES[name], headers={
        'User-Agent': 'Mozilla/5.0 HermesUsagePlusPricing/1.0', 'Accept-Encoding': 'identity',
        'Accept': 'application/json' if name in ('nous', 'openrouter') else 'text/html',
    })
    with opener.open(request, timeout=8) as response:
        _official_url(name, response.geturl())
        expected = 'application/json' if name in ('nous', 'openrouter') else 'text/html'
        if response.headers.get('Content-Type', '').split(';')[0].strip().lower() != expected:
            raise ValueError('unexpected_content_type')
        if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
            raise ValueError('unexpected_encoding')
        length = response.headers.get('Content-Length')
        if length is not None and (not length.isdigit() or int(length) > MAX_RESPONSE_BYTES):
            raise _SourceError('response_too_large')
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise _SourceError('response_too_large')
        return payload


def _fetch_sources(deadline):
    """Killable network processes bound DNS, slow-drip bodies, and socket timeouts.

    One bulk request per source (plus at most 3 same-host redirects), no retries.
    All sources start together. No worker may survive this function.
    """
    from concurrent.futures import ThreadPoolExecutor
    import contextlib

    workers, results = {}, {}
    worker_deadline = min(deadline, time.monotonic() + SOURCE_PROCESS_SECONDS)
    try:
        for name in SOURCES:
            if time.monotonic() >= deadline:
                results[name] = _SourceError('timeout')
                continue
            try:
                workers[name] = subprocess.Popen(
                    [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--fetch', name],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except OSError:
                results[name] = _SourceError('transport_error')

        def _collect(item):
            name, worker = item
            try:
                remaining = max(0.05, worker_deadline - time.monotonic())
                payload, _ = worker.communicate(timeout=remaining)
                if worker.returncode:
                    return name, _SourceError('transport_error')
                if len(payload) > MAX_RESPONSE_BYTES:
                    return name, _SourceError('response_too_large')
                return name, payload
            except subprocess.TimeoutExpired:
                worker.kill()
                with contextlib.suppress(Exception):
                    worker.communicate(timeout=1)
                return name, _SourceError('timeout')
            except OSError:
                return name, _SourceError('transport_error')

        if workers:
            with ThreadPoolExecutor(max_workers=len(workers)) as pool:
                for name, res in pool.map(_collect, workers.items()):
                    results[name] = res
    finally:
        for worker in workers.values():
            if worker.poll() is None:
                worker.kill()
                with contextlib.suppress(Exception):
                    worker.communicate(timeout=1)
    return results


def _path(home):
    return Path(home) / 'cache' / 'usage-plus' / 'prices.json'


def _read(home):
    try:
        with _path(home).open('rb') as stream:
            # Read from the open handle's size, not the 16 MiB ceiling.
            size = os.fstat(stream.fileno()).st_size
            if size > MAX_CACHE_BYTES:
                raise ValueError('cache_too_large')
            payload = stream.read(size + 1)
        if len(payload) != size:
            raise ValueError('cache_changed')
        data = json.loads(payload)
        if (not isinstance(data, dict) or data.get('schema_version') != 1
                or not isinstance(data.get('sources'), dict) or not isinstance(data.get('prices'), list)
                or set(data['sources']) - set(SOURCES) or 'updated_at' not in data):
            raise ValueError('invalid_cache')
        seen = set()
        for row in data['prices']:
            name = row['source_id']
            pair = (name, row['model'])
            if (name not in SOURCES or row['provider'] != name or pair in seen
                    or not row['source'].startswith(SOURCES[name] + ' | ')):
                raise ValueError('invalid_provenance')
            seen.add(pair)
            for field in (*RATE_FIELDS, 'request_cost'):
                _rate(row[field])
        for name, info in data['sources'].items():
            if info['status'] not in ('ok', 'error') or info['url'] != SOURCES[name]:
                raise ValueError('invalid_source')
        return data
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {'schema_version': 1, 'updated_at': None, 'sources': {}, 'prices': []}


def _json(payload):
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError('duplicate_key')
            obj[key] = value
        return obj
    return json.loads(payload, object_pairs_hook=unique, parse_float=Decimal)


def _rate(value, multiplier=1):
    if value is None:
        return None
    if type(value) not in (str, int, float, Decimal) or len(str(value)) > 64:
        raise ValueError('invalid_rate')
    number = Decimal(str(value))
    if not number.is_finite() or number < 0 or number > Decimal('1e12'):
        raise ValueError('invalid_rate')
    return str(number * multiplier)


def _row(name, model, checked_at, condition, threshold=None):
    version = datetime.fromtimestamp(checked_at, timezone.utc).isoformat() + ' UTC'
    return dict(provider=name, model=model, source_id=name,
                source=SOURCES[name] + ' | ' + condition,
                pricing_version=version, checked_at=checked_at, request_cost=None,
                tier_threshold_tokens=threshold, **dict.fromkeys(RATE_FIELDS))


def _parse_api(name, payload, checked_at):
    document = _json(payload)
    if not isinstance(document, dict) or not isinstance(document.get('data'), list):
        raise ValueError('invalid_catalog')
    data = document['data']
    if not 0 < len(data) <= 4096 or document.get('links', {}).get('next'):
        raise ValueError('incomplete_catalog')
    allowed = set(RATE_FIELDS.values()) | {'request', 'input_cache_write_1h', 'overrides',
        'original', 'image', 'image_output', 'audio', 'audio_output', 'input_audio_cache',
        'internal_reasoning', 'web_search'}
    rows, seen = [], set()
    for model in data:
        if not isinstance(model, dict):
            raise ValueError('invalid_model')
        identifier = model.get('id')
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9~][A-Za-z0-9_./:~+\-]{0,255}', identifier) or identifier in seen:
            raise ValueError('invalid_model_id')
        seen.add(identifier)
        pricing = model.get('pricing')
        if pricing is None:
            continue
        if not isinstance(pricing, dict) or set(pricing) - allowed:
            raise ValueError('invalid_pricing_schema')
        # OpenRouter's bulk listing uses -1/-1 for dynamically selected routes.
        # No fixed tariff can be inferred; do not zero it or poison fixed-model rows.
        if name == 'openrouter' and pricing.get('prompt') == '-1' and pricing.get('completion') == '-1':
            continue
        overrides = pricing.get('overrides') or []
        if not isinstance(overrides, list):
            raise ValueError('invalid_overrides')
        thresholds, unsupported = [], False
        for override in overrides:
            if not isinstance(override, dict):
                raise ValueError('invalid_override')
            if (set(override) - (set(RATE_FIELDS.values()) | {'min_prompt_tokens', 'input_cache_write_1h'})
                    or type(override.get('min_prompt_tokens')) is not int or override['min_prompt_tokens'] <= 0):
                unsupported = True
                break
            thresholds.append(override['min_prompt_tokens'])
            for key in set(override) - {'min_prompt_tokens'}:
                _rate(override[key])
        if unsupported:
            continue
        threshold = min(thresholds) if thresholds else None
        condition = ('published base USD/text-token reference; not a billed invoice; '
                     'default cache-write TTL only; tool/media/alternative TTL charges excluded')
        if threshold:
            condition += f'; base prompt tier below {threshold} tokens per request, never aggregate usage'
        row = _row(name, identifier, checked_at, condition, threshold)
        for field, key in RATE_FIELDS.items():
            row[field] = _rate(pricing.get(key), 1000000)
        row['request_cost'] = _rate(pricing.get('request'))
        if any(row[field] is not None for field in (*RATE_FIELDS, 'request_cost')):
            rows.append(row)
    if not rows:
        raise ValueError('no_supported_prices')
    return rows, {'model_count': len(data), 'skipped_count': len(data) - len(rows)}


class _Document(HTMLParser):
    """Read inert HTML only. Never evaluate scripts or follow links."""
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, deadline=None):
        super().__init__(convert_charrefs=True)
        self.deadline = deadline
        self.stack, self.text, self.openai, self.tables = [], [], [], []
        self.heading = None
        self.heading_start = 0
        self.table = self.row = self.cell = None
        self.paragraphs, self.paragraph = [], None

    def handle_starttag(self, tag, attrs):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise _SourceError('timeout')
        attrs = dict(attrs)
        if tag in ('p', 'li') or (tag == 'span' and attrs.get('data-as') == 'p'):
            self.paragraph = []
        if tag == 'astro-island' and attrs.get('component-export') == 'TextTokenPricingTables':
            ancestors = [a for _, a in self.stack]
            if (any(a.get('id') == 'mainContent' for a in ancestors)
                    and any(a.get('id') == 'content-switcher-latest-pricing' for a in ancestors)
                    and any(a.get('data-content-switcher-pane') == 'true' and a.get('data-value') == 'standard' for a in ancestors)):
                recent = ' '.join(' '.join(self.text).split())[-1000:]
                if 'Prices per 1M tokens.' not in recent:
                    raise ValueError('unverified_units')
                self.openai.append(_json(attrs['props']))
        if tag in ('h2', 'h3'):
            self.heading = attrs.get('id')
            self.heading_start = len(self.text)
        if tag == 'table':
            if self.table is not None:
                raise ValueError('nested_table')
            self.table = {'heading': self.heading, 'rows': [], 'intro': ' '.join(self.text[self.heading_start:])}
        elif tag == 'tr' and self.table is not None:
            self.row = []
        elif tag in ('td', 'th') and self.row is not None:
            self.cell = {'text': '', 'anchor': ''}
            self.row.append(self.cell)
        if tag not in self.VOID:
            self.stack.append((tag, attrs))
        elif tag == 'br' and self.cell is not None:
            self.cell['text'] += ' '

    def handle_data(self, data):
        if any(tag in ('script', 'style') for tag, _ in self.stack):
            return
        self.text.append(data)
        if self.paragraph is not None:
            self.paragraph.append(data)
        if self.cell is not None:
            self.cell['text'] += data
            if any(tag == 'a' for tag, _ in self.stack):
                self.cell['anchor'] += data

    def handle_endtag(self, tag):
        paragraph_end = tag in ('p', 'li') or (tag == 'span' and self.stack
                        and self.stack[-1][1].get('data-as') == 'p')
        if paragraph_end and self.paragraph is not None:
            self.paragraphs.append(' '.join(' '.join(self.paragraph).split()))
            self.paragraph = None
        if tag in ('td', 'th'):
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            assert self.table is not None
            self.table['rows'].append(self.row)
            self.row = None
        elif tag == 'table' and self.table is not None:
            self.tables.append(self.table)
            self.table = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def _astro(value, code):
    if not isinstance(value, list) or len(value) != 2 or type(value[0]) is not int or value[0] != code:
        raise ValueError('changed_astro_schema')
    return value[1]


def _doc_rate(value):
    return None if value in (None, '-') else _rate(value)


def _parse_openai(document, checked_at):
    if len(document.openai) != 1:
        raise ValueError('missing_standard_table')
    props = document.openai[0]
    if not isinstance(props, dict) or set(props) - {'tier', 'rows', 'collapsedLatestRowCount', 'latestSectionLabel'}:
        raise ValueError('changed_pricing_props')
    if _astro(props['tier'], 0) != 'standard':
        raise ValueError('wrong_tier')
    values = _astro(props['rows'], 1)
    if not isinstance(values, list) or not 0 < len(values) <= 4096:
        raise ValueError('invalid_rows')
    rows, seen = [], set()
    for value in values:
        cells = _astro(value, 1)
        if not isinstance(cells, list) or len(cells) not in (4, 5):
            raise ValueError('changed_columns')
        cells = [_astro(cell, 0) for cell in cells]
        label = cells[0]
        if not isinstance(label, str):
            raise ValueError('invalid_model_label')
        match = re.fullmatch(r'([a-z0-9][a-z0-9.\-]*)(?: \(<([0-9]+)K context length\))?', label)
        if not match:
            raise ValueError('unknown_model_condition')
        identifier = match[1]
        if identifier in seen:
            raise ValueError('duplicate_model')
        seen.add(identifier)
        threshold = int(match[2]) * 1000 if match[2] else None
        condition = ('OpenAI API standard USD/1M text tokens, base prompt tier reference; '
                     '30m cache writes when published; 12h writes/tools excluded; '
                     'not a billed invoice; per-request tiers never selected from aggregate usage')
        if threshold:
            condition += f'; prompt <{threshold} tokens per request'
        row = _row('openai', identifier, checked_at, condition, threshold)
        row['input_cost_per_million'] = _doc_rate(cells[1])
        row['cache_read_cost_per_million'] = _doc_rate(cells[2])
        row['output_cost_per_million'] = _doc_rate(cells[-1])
        if len(cells) == 5:
            row['cache_write_cost_per_million'] = _doc_rate(cells[3])
        rows.append(row)
    return rows, {'model_count': len(values), 'skipped_count': 0}


# Explicit API-ID map verified against Anthropic's official models overview.
# No name-to-ID rewriting, dated snapshot inference, or third-party catalog fallback.
ANTHROPIC_IDS = {
    'Claude Fable 5.1': 'claude-fable-5-1',
    'Claude Opus 5.5': 'claude-opus-5-5',
    'Claude Sonnet 5.5': 'claude-sonnet-5-5',
    'Claude Haiku 5.5': 'claude-haiku-5-5',
    'Claude Opus 4.8': 'claude-opus-4-8',
    'Claude Opus 4.7': 'claude-opus-4-7',
    'Claude Opus 4.6': 'claude-opus-4-6',
    'Claude Opus 4.5': 'claude-opus-4-5',
    'Claude Sonnet 4.6': 'claude-sonnet-4-6',
    'Claude Sonnet 4.5': 'claude-sonnet-4-5',
    'Claude Haiku 4.5': 'claude-haiku-4-5',
}


def _parse_anthropic(document, checked_at):
    if 'All prices are in USD.' not in ' '.join(document.text):
        raise ValueError('unverified_currency')
    candidates = [table for table in document.tables if table['heading'] == 'model-pricing']
    if len(candidates) != 1:
        raise ValueError('missing_model_table')
    data = candidates[0]['rows']
    headers = ['Name', 'Input', 'Output', '5m writes', '1h writes', 'Hits and refreshes']
    header_rows = [i for i, row in enumerate(data) if [' '.join(c['text'].split()) for c in row] == headers]
    if len(header_rows) != 1:
        raise ValueError('changed_headers')
    rows, seen, count = [], set(), 0
    for cells in data[header_rows[0] + 1:]:
        if not cells:
            continue
        count += 1
        label = ' '.join((cells[0]['anchor'] or cells[0]['text']).split())
        if label not in ANTHROPIC_IDS:
            continue
        if len(cells) != 6 or label in seen:
            raise ValueError('changed_model_row')
        seen.add(label)
        rates, threshold = [], None
        for index, cell in enumerate(cells[1:]):
            text = ' '.join(cell['text'].split())
            match = re.fullmatch(r'\$([0-9]+(?:\.[0-9]+)?)\s*/\s*MTok(?:\s*for prompts up to ([0-9,]+) tokens)?', text)
            if not match or (match[2] and index != 0):
                raise ValueError('unknown_price_condition')
            rates.append(_rate(match[1]))
            if match[2]:
                threshold = int(match[2].replace(',', ''))
        condition = ('Anthropic standard USD/1M text tokens, base prompt tier reference; '
                     '5m cache writes; 1h writes/tools/region premiums excluded; not a billed invoice; '
                     'per-request tiers never selected from aggregate usage')
        if threshold:
            condition += f'; prompt up to {threshold} tokens per request'
        row = _row('anthropic', ANTHROPIC_IDS[label], checked_at, condition, threshold)
        row.update(input_cost_per_million=rates[0], output_cost_per_million=rates[1],
                   cache_write_cost_per_million=rates[2], cache_read_cost_per_million=rates[4])
        rows.append(row)
    if not rows:
        raise ValueError('no_supported_prices')
    return rows, {'model_count': count, 'skipped_count': count - len(rows)}


def _parse_native_table(name, document, checked_at):
    """Small exact-ID contracts; adjacent model/tier tariffs are never borrowed."""
    headers = {
        'zai': ['Model', 'Input', 'Cached Input', 'Cached Input Storage', 'Output'],
        'stepfun': ['Model', 'Billing unit', 'Input (cache miss)', 'Input (cache hit)', 'Output'],
        'meta': ['Usage', 'Standard mode (per 1M tokens)', 'Contributor mode (per 1M tokens)'],
    }[name]
    if name == 'zai':
        currencies = [currency for paragraph in document.paragraphs for currency in re.findall(
            r'(?:^|[.!?]\s+)All prices are in ([A-Za-z]+)\.(?=\s|$)', paragraph)]
        if not currencies or set(currencies) != {'USD'}:
            raise ValueError('unverified_currency_or_units')
    found = []
    for table in document.tables:
        cells = [[' '.join(c['text'].split()) for c in r] for r in table['rows']]
        if not cells or cells[0] != headers:
            continue
        if name == 'meta':
            supported = {
                'muse-wonder-pricing': ('muse-wonder-1.0',),
                'muse-spark-pricing': ('muse-spark-1.1', 'muse-spark-1.2', 'muse-spark-1.3',
                                       'muse-spark-1.2-contributor', 'muse-spark-1.3-contributor'),
            }.get(table['heading'], ())
            models = [model for model in supported if re.search(
                r'(?<![\w./:~+\-])' + re.escape(model) + r'(?![\w/:~+\-]|\.[\w])', table['intro'])]
            if not models:
                continue
            values = {r[0]: r for r in cells[1:] if len(r) == 3}
            if len(values) != len(cells) - 1 or set(values) != {'Input', 'Output', 'Cached input'}:
                raise ValueError('changed_native_rows')
            for model in models:
                column = 2 if model.endswith('-contributor') else 1
                found.append((model, [values[k][column] for k in ('Input','Cached input','Output')]))
        else:
            labels = {'GLM-5.3-Flash'} if name == 'zai' else {
                'step-5-preview', 'step-3.7-flash', 'step-3.5-flash',
                'step-3.5-flash-2603', 'step-1o-turbo-vision'}
            for r in cells[1:]:
                if not r or r[0] not in labels:
                    continue
                label = r[0]
                if len(r) != 5 or (name == 'stepfun' and r[1] != '1M tokens'):
                    raise ValueError('changed_native_rows')
                if name == 'zai':
                    intro = ' '.join(table['intro'].split())
                    units = re.findall(r'\bper\s+([^;]+?\btokens?\b)', intro, re.I)
                    currencies = re.findall(
                        r'\b(?:prices?(?: are)? in|currency\s*:?)\s+([A-Za-z]+)', intro, re.I)
                    if ('Prices per 1M tokens.' not in intro or set(units) != {'1M tokens'}
                            or any(currency != 'USD' for currency in currencies)):
                        raise ValueError('unverified_currency_or_units')
                found.append(('glm-5.3-flash' if name == 'zai' else label,
                              [r[1],r[2],r[4]] if name == 'zai' else r[2:5]))
    if not found or (name == 'zai' and len(found) != 1) or len({m for m, _ in found}) != len(found):
        raise ValueError('missing_or_ambiguous_native_table')
    rows = []
    for model, cells in found:
        rates = []
        for cell in cells:
            match = re.fullmatch(r'\$([0-9]+(?:\.[0-9]+)?)', cell)
            if not match:
                raise ValueError('unknown_native_rate')
            rates.append(_rate(match[1]))
        condition = 'Model-owner official USD/1M token API reference; tools/storage excluded; not billed'
        if name == 'meta':
            condition += ('; Contributor mode only; discounted rate requires permission for model training'
                          if model.endswith('-contributor') else '; Standard mode')
        row = _row(name, model, checked_at, condition)
        row.update(input_cost_per_million=rates[0], cache_read_cost_per_million=rates[1], output_cost_per_million=rates[2])
        if name == 'stepfun' and model == 'step-5-preview':
            if not any(re.match(
                    r'For step-5-preview\s*, the cache-miss input price includes '
                    r'writing new content to the cache\.(?=\s|$)', paragraph)
                    for paragraph in document.paragraphs):
                raise ValueError('unverified_write_semantics')
            row['cache_write_cost_per_million'] = rates[0]
            row['source'] += '; cache writes included in cache-miss input price'
        rows.append(row)
    return rows, {'model_count': len(rows), 'skipped_count': 0}


def _parse(name, payload, checked_at, deadline=None):
    if type(payload) is str:
        payload = payload.encode('utf-8')
    if type(payload) is not bytes:
        raise ValueError('invalid_payload')
    if len(payload) > MAX_RESPONSE_BYTES:
        raise _SourceError('response_too_large')
    if name in ('nous', 'openrouter'):
        return _parse_api(name, payload, checked_at)
    if name in EXTRA_SOURCES:
        return parse_extra(name, payload, checked_at, deadline)
    document = _Document(deadline)
    document.feed(payload.decode('utf-8') if isinstance(payload, bytes) else payload)
    document.close()
    if name in ('zai', 'stepfun', 'meta'):
        return _parse_native_table(name, document, checked_at)
    return (_parse_openai if name == 'openai' else _parse_anthropic)(document, checked_at)


def refresh_prices(home: Path, *, sources=None) -> dict:
    """Refresh official sources; explicit payload mapping is for offline testing."""
    started = time.monotonic()
    deadline = started + TOTAL_BUDGET_SECONDS
    now = time.time()
    if sources is not None and (not isinstance(sources, dict) or set(sources) - set(SOURCES)):
        raise ValueError('unknown_source')
    result = _read(home)
    payloads = _fetch_sources(started + FETCH_BUDGET_SECONDS) if sources is None else sources
    for name, payload in payloads.items():
        previous = result['sources'].get(name, {})
        try:
            if isinstance(payload, Exception):
                raise payload
            if time.monotonic() >= deadline:
                raise _SourceError('timeout')
            checked_at = time.time()
            rows, coverage = _parse(name, payload, checked_at, deadline - 1)
            if time.monotonic() >= deadline - 1:
                raise _SourceError('timeout')
            raw = payload.encode('utf-8') if isinstance(payload, str) else payload
            result['prices'] = [r for r in result['prices'] if r['source_id'] != name] + rows
            result['sources'][name] = dict(status='ok', checked_at=checked_at, attempted_at=now,
                                         url=SOURCES[name], count=len(rows), **coverage,
                                         response_sha256=hashlib.sha256(raw).hexdigest(), response_bytes=len(raw))
        except Exception as exc:
            # Never persist exception text, response bodies, headers, or credentials.
            result['sources'][name] = dict(previous, status='error', attempted_at=now,
                                         error=exc.code if isinstance(exc, _SourceError) else
                                         ('schema_error' if isinstance(exc, ValueError) else 'source_failed'),
                                         checked_at=previous.get('checked_at'),
                                         url=SOURCES.get(name), count=previous.get('count', 0))
    result['updated_at'] = now
    path = _path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.prices-', suffix='.tmp', delete=False) as stream:
            temporary = stream.name
            json.dump(result, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(4):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(0.025 * (attempt + 1))
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
    return result


class PriceSnapshot:
    """One validated catalog per request, shared by lookup and health reporting.

    No process-global cache: a new request sees refreshed prices, while an
    in-flight request cannot mix tariffs from two different file versions.
    """

    def __init__(self, home: Path):
        self._data = _read(home)

    def lookup_price(self, provider: str, model: str, *, native_only=False) -> SimpleNamespace | None:
        return _lookup_price(self._data['prices'], provider, model, native_only=native_only)

    def status(self) -> dict:
        return _status(self._data)


def lookup_price(home: Path, provider: str, model: str, *, native_only=False) -> SimpleNamespace | None:
    """Fresh standalone lookup; reuse PriceSnapshot for a batch of models."""
    return _lookup_price(_read(home)['prices'], provider, model, native_only=native_only)


def _native_ids(row):
    identifiers = {row['model']}
    if row['provider'] == 'deepseek':
        version = row.get('model_version', '')
        if version:
            identifiers.add(version.lower())
        if row['model'] == 'deepseek-flash' and re.fullmatch(r'DeepSeek-V4\.1-Flash(?:-\d{4})?', version):
            identifiers.add('deepseek-v4.1-flash')
    return identifiers


def _native_reference(rows, model):
    owners = {'openai', 'anthropic', 'google', 'zai', 'stepfun', 'meta', 'deepseek'}
    wrappers = {'opencode', 'opencode-go', 'opencodex', 'google-antigravity',
                'aistudio', 'thehive', 'infron'}
    namespaces = {'zai-org': 'zai', 'deepseek-ai': 'deepseek'}
    name = model
    for _ in range(4):
        prefix, sep, rest = name.partition('/')
        if not sep or prefix not in wrappers:
            break
        name = rest
    name = name.removesuffix(':free').removesuffix('-free')
    prefix, sep, rest = name.partition('/')
    if sep:
        owner = namespaces.get(prefix, prefix)
        if owner not in owners:
            return None, []
        owners, name = {owner}, rest
    candidates = [r for r in rows if r['provider'] in owners]
    matches = [r for r in candidates if name in _native_ids(r)]
    if not matches and not sep and model != name:
        # Known transports encode maker/model as maker-model. Match a unique,
        # complete native ID/version; do not trim a dated snapshot or generation.
        matches = [r for r in candidates if any(
            name == namespace + '-' + identifier
            for namespace in ({r['provider']} | {k for k, v in namespaces.items() if v == r['provider']})
            for identifier in _native_ids(r))]
    adjustments = []
    if not matches:
        standard = name
        effort = r'(?:minimal|none|low|medium|high|xhigh|max|ultra)'
        if name.startswith('gpt-'):
            suffix = r'(?:--?fast|-[1-9][0-9]*(?:k|m)|-' + effort + r')$'
            family = 'openai'
        elif name.startswith('gemini-'):
            suffix, family = '-' + effort + '$', 'google'
        else:
            suffix, family = r'(?!)', None
        for _ in range(3):
            candidate = re.sub(suffix, '', standard)
            if candidate == standard:
                break
            standard = candidate
            matches = [r for r in candidates if r['provider'] == family and r['model'] == standard]
            if matches:
                if len(matches) == 1:
                    adjustments.append('standard_variant')
                break
    return (matches[0], adjustments) if len(matches) == 1 else (None, [])


def _lookup_price(rows, provider: str, model: str, *, native_only=False) -> SimpleNamespace | None:
    """Published paid route first, then the model owner's official API reference.

    Free routes use owner API reference values, never zero as a savings proxy.
    Known transport encodings and bounded standard-variant rules affect reference
    lookup only; original provider/model IDs and billed costs remain unchanged.
    """
    requested = provider
    route_model = model
    prefix, sep, rest = model.partition('/')
    if sep and (prefix == provider or (provider in ('', 'custom', 'opencodex')
                                     and prefix in ('opencode', 'opencode-go', 'thehive'))):
        provider, route_model = prefix, rest
    reference_route = provider == 'opencode-go'
    provider = {'openai-codex': 'openai', 'opencode-go': 'opencode'}.get(provider, provider)
    direct = next((r for r in rows if r['provider'] == provider and r['model'] == model), None)
    if direct is None:
        direct = next((r for r in rows if r['provider'] == provider and r['model'] == route_model), None)
    if direct is None and requested == 'opencodex' and prefix == 'thehive':
        # OpenCodex transport IDs encode the upstream namespace slash as '-'.
        # Require a unique complete published ID; never trim model/version suffixes.
        encoded = [r for r in rows if r['provider'] == 'thehive'
                   and r['model'].replace('/', '-') == route_model]
        if len(encoded) == 1:
            direct = encoded[0]
    adjustments = []
    zero_route = (direct is not None and direct['input_cost_per_million'] is not None
                  and direct['output_cost_per_million'] is not None
                  and all(direct.get(f) is None or Decimal(direct[f]) == 0
                          for f in (*RATE_FIELDS, 'request_cost')))
    paid_route = direct is not None and any(direct.get(f) is not None and Decimal(direct[f]) > 0
                                             for f in (*RATE_FIELDS, 'request_cost'))
    free_route = zero_route or (model.endswith((':free', '-free')) and not paid_route)
    if free_route:
        adjustments.append('free_route')
    row = None if native_only or free_route else direct
    basis = 'native_reference' if requested == 'openai-codex' or native_only else 'route'
    if row is not None and reference_route:
        basis = 'provider_reference'
    if row is None:
        row, native_adjustments = _native_reference(rows, model)
        if row is None:
            return None
        adjustments.extend(native_adjustments)
        basis = 'native_reference'
    entry = dict(row)
    for field in (*RATE_FIELDS, 'request_cost'):
        entry[field] = Decimal(entry[field]) if entry[field] is not None else None
    entry.update(pricing_basis=basis, source_url=SOURCES[row['source_id']],
                 requested_model=model, reference_adjustments=adjustments)
    if 'standard_variant' in adjustments:
        entry['source'] += ' | standard model API reference; variant-specific billing not reproduced'
    if 'free_route' in adjustments:
        entry['source'] += ' | free route valued at model-owner API tariff, not free-route billing'
    if basis == 'provider_reference':
        entry['source'] += ' | OpenCode Console API equivalent reference, not Go subscription billing'
    if basis == 'native_reference':
        entry['source'] += ' | model-owner official API equivalent reference, not billed'
        if row['provider'] == 'deepseek' and model.removesuffix(':free').removesuffix('-free').endswith('deepseek-v4.1-flash'):
            entry['source'] += '; undated V4.1-Flash generation reference uses the currently published MODEL VERSION, not a historical invoice'
    return SimpleNamespace(**entry)


def status(home: Path) -> dict:
    """Cache-only freshness and per-source health; failure doesn't renew freshness."""
    return _status(_read(home))


def _status(data) -> dict:
    sources = {}
    now = time.time()
    for name, info in data['sources'].items():
        checked = info.get('checked_at')
        age = max(0, now - checked) if checked is not None else None
        sources[name] = dict(info, age_seconds=age, stale=age is None or age > 86400)
    return dict(updated_at=data['updated_at'], sources=sources, price_count=len(data['prices']),
                stale=not sources or any(s['stale'] for s in sources.values()),
                healthy_sources=sum(s['status'] == 'ok' for s in sources.values()),
                error_sources=sum(s['status'] == 'error' for s in sources.values()))


if __name__ == '__main__':
    # Internal unauthenticated fetch worker; no cache/config writes in children.
    if len(sys.argv) != 3 or sys.argv[1] != '--fetch' or sys.argv[2] not in SOURCES:
        sys.exit(2)
    try:
        sys.stdout.buffer.write(_fetch_http(sys.argv[2]))
    except Exception:
        sys.exit(2)  # No remote or credential-bearing exception text on stderr.
