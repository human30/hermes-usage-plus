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

SOURCES = {
    'nous': 'https://inference-api.nousresearch.com/v1/models',
    'openrouter': 'https://openrouter.ai/api/v1/models',
    'openai': 'https://developers.openai.com/api/docs/pricing',
    'anthropic': 'https://platform.claude.com/docs/en/about-claude/pricing',
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
        'User-Agent': 'HermesUsagePlusPricing/1.0', 'Accept-Encoding': 'identity',
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
    All four start together. No worker may survive this function.
    """
    workers, results = {}, {}
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
        for name, worker in workers.items():
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired('official_fetch', 0)
                payload, _ = worker.communicate(timeout=min(SOURCE_PROCESS_SECONDS, remaining))
                if worker.returncode:
                    results[name] = _SourceError('transport_error')
                elif len(payload) > MAX_RESPONSE_BYTES:
                    results[name] = _SourceError('response_too_large')
                else:
                    results[name] = payload
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.communicate(timeout=1)
                results[name] = _SourceError('timeout')
            except OSError:
                results[name] = _SourceError('transport_error')
    finally:
        for worker in workers.values():
            if worker.poll() is None:
                worker.kill()
                worker.communicate(timeout=1)
    return results


def _path(home):
    return Path(home) / 'cache' / 'usage-plus' / 'prices.json'


def _read(home):
    try:
        with _path(home).open('rb') as stream:
            payload = stream.read(MAX_CACHE_BYTES + 1)
        if len(payload) > MAX_CACHE_BYTES:
            raise ValueError('cache_too_large')
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
        self.table = self.row = self.cell = None

    def handle_starttag(self, tag, attrs):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise _SourceError('timeout')
        attrs = dict(attrs)
        if tag == 'astro-island' and attrs.get('component-export') == 'TextTokenPricingTables':
            ancestors = [a for _, a in self.stack]
            if (any(a.get('id') == 'mainContent' for a in ancestors)
                    and any(a.get('id') == 'content-switcher-latest-pricing' for a in ancestors)
                    and any(a.get('data-content-switcher-pane') == 'true' and a.get('data-value') == 'standard' for a in ancestors)):
                recent = ' '.join(' '.join(self.text).split())[-1000:]
                if 'Prices per 1M tokens.' not in recent:
                    raise ValueError('unverified_units')
                self.openai.append(_json(attrs['props']))
        if tag == 'h2':
            self.heading = attrs.get('id')
        if tag == 'table':
            if self.table is not None:
                raise ValueError('nested_table')
            self.table = {'heading': self.heading, 'rows': []}
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
        if self.cell is not None:
            self.cell['text'] += data
            if any(tag == 'a' for tag, _ in self.stack):
                self.cell['anchor'] += data

    def handle_endtag(self, tag):
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


def _parse(name, payload, checked_at, deadline=None):
    if type(payload) is str:
        payload = payload.encode('utf-8')
    if type(payload) is not bytes:
        raise ValueError('invalid_payload')
    if len(payload) > MAX_RESPONSE_BYTES:
        raise _SourceError('response_too_large')
    if name in ('nous', 'openrouter'):
        return _parse_api(name, payload, checked_at)
    document = _Document(deadline)
    document.feed(payload.decode('utf-8') if isinstance(payload, bytes) else payload)
    document.close()
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


def lookup_price(home: Path, provider: str, model: str) -> SimpleNamespace | None:
    requested = provider
    provider = {'openai-codex': 'openai'}.get(provider, provider)
    for row in _read(home)['prices']:
        if row['provider'] == provider and row['model'] == model:
            entry = dict(row)
            for field in (*RATE_FIELDS, 'request_cost'):
                entry[field] = Decimal(entry[field]) if entry[field] is not None else None
            if requested == 'openai-codex':
                entry['source'] += ' | openai-codex subscription: OpenAI API equivalent reference, not billed'
            return SimpleNamespace(**entry)
    return None


def status(home: Path) -> dict:
    """Cache-only freshness and per-source health; failure doesn't renew freshness."""
    data = _read(home)
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
