"""Pure official-HTML price parsers. No network, files, or catalog imports.

Rates are USD per million tokens; missing prices remain None. Aliases only
name versions explicitly published by that source, never neighboring models.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser

SOURCES = {
    'deepseek': 'https://api-docs.deepseek.com/quick_start/pricing/',
    'opencode': 'https://opencode.ai/v2/docs/console/models/',
    'thehive': 'https://thehive.ai/pricing',
}
RATE_FIELDS = ('input_cost_per_million', 'output_cost_per_million',
               'cache_read_cost_per_million', 'cache_write_cost_per_million')
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

# Exact display-name/API-key pairs observed in official Hive docs. The dated
# and undated documents exist in different public page revisions; neither
# proves that those two API routes are aliases of one another.
HIVE_MODEL_ID_SOURCE = 'https://docs.thehive.ai/docs/chat-completions-openai-compatible-llms'
HIVE_MODEL_IDS = {
    'DeepSeek-V4.1-Flash': 'deepseek-ai/deepseek-v4.1-flash',
    'DeepSeek-V4.1-Flash-1017': 'deepseek-ai/deepseek-v4.1-flash-1017',
    'Z.AI GLM-5.3-Flash': 'zai-org/glm-5.3-flash',
}
ROUTE_ALIASES = {}  # No official evidence of cross-version Hive equivalence.


def _clean(text):
    return ' '.join(text.split())


def _row(name, model, checked_at, condition) -> dict:
    return dict(provider=name, model=model, source_id=name,
                source=SOURCES[name] + ' | ' + condition,
                pricing_version=datetime.fromtimestamp(checked_at, timezone.utc).isoformat() + ' UTC',
                checked_at=checked_at, request_cost=None, tier_threshold_tokens=None,
                **dict.fromkeys(RATE_FIELDS))


def _price(text):
    match = re.fullmatch(r'\$([0-9]+(?:\.[0-9]+)?)', text)
    if not match or len(match[1]) > 64:
        raise ValueError('unknown_price')
    value = Decimal(match[1])
    if value > Decimal('1e12'):
        raise ValueError('invalid_rate')
    return str(value)


class _Document(HTMLParser):
    """Small inert HTML reader, retaining physical table spans."""
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
            'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, deadline=None):
        super().__init__(convert_charrefs=True)
        self.deadline = deadline
        self.stack = []
        self.tables = []
        self.table = self.row = self.cell = None
        self.text = []
        self.section_start = 0
        self.table_intros = []
        self.heading = None
        self.hive_label = None
        self.paragraph = None
        self.paragraphs = []
        self.hive_labels = set()
        self.nodes = 0

    def check(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise ValueError('timeout')

    def handle_starttag(self, tag, attrs):
        self.check()
        self.nodes += 1
        if self.nodes > 100000 or len(self.stack) >= 256:
            raise ValueError('document_too_complex')
        attrs = dict(attrs)
        if tag in ('h5', 'h6'):
            self.hive_label = None
            self.heading = []
        if tag == 'p':
            self.paragraph = []
        if tag in ('h1', 'h2', 'h3'):
            self.section_start = len(self.text)
        if tag == 'table':
            if self.table is not None:
                raise ValueError('nested_table')
            self.table = []
            self.table_intros.append(_clean(' '.join(self.text[self.section_start:])))
        elif tag == 'tr' and self.table is not None:
            self.row = []
        elif tag in ('td', 'th') and self.row is not None:
            self.cell = {'text': '', 'rowspan': int(attrs.get('rowspan', '1')),
                         'colspan': int(attrs.get('colspan', '1'))}
            self.row.append(self.cell)
        if tag == 'br' and self.cell is not None:
            self.cell['text'] += ' '
        if tag not in self.VOID:
            struck = (tag in ('del', 's', 'strike') or
                      any(c in ('line-through', 'ptl-line-through') for c in attrs.get('class', '').split()) or
                      bool(re.search(r'text-decoration(?:-line)?\s*:[^;]*\bline-through\b', attrs.get('style', ''), re.I)))
            self.stack.append((tag, struck))

    def handle_data(self, data):
        self.check()
        if any(t in ('script', 'style', 'sup') or struck for t, struck in self.stack):
            return
        self.text.append(data)
        if self.heading is not None:
            self.heading.append(data)
        if self.paragraph is not None:
            self.paragraph.append(data)
        if self.cell is not None:
            self.cell['text'] += data
            if len(self.cell['text']) > 16384:
                raise ValueError('document_too_complex')

    def handle_endtag(self, tag):
        self.check()
        if tag in ('h5', 'h6') and self.heading is not None:
            self.hive_label = _clean(''.join(self.heading)) if tag == 'h6' else None
            if self.hive_label in HIVE_MODEL_IDS:
                self.hive_labels.add(self.hive_label)
            self.heading = None
        if tag == 'p' and self.paragraph is not None:
            self.paragraphs.append((self.hive_label, _clean(''.join(self.paragraph))))
            self.paragraph = None
        if tag in ('td', 'th'):
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == 'table' and self.table is not None:
            self.tables.append(self.table)
            self.table = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def _grid(table, document):
    if len(table) > 4096:
        raise ValueError('document_too_complex')
    occupied = {}
    for y, cells in enumerate(table):
        document.check()
        x = 0
        for cell in cells:
            document.check()
            while (y, x) in occupied:
                x += 1
            height, width = cell['rowspan'], cell['colspan']
            if not (1 <= height <= 64 and 1 <= width <= 32) or x + width > 32:
                raise ValueError('invalid_span')
            text = _clean(cell['text'])
            for dy in range(height):
                document.check()
                for dx in range(width):
                    key = (y + dy, x + dx)
                    if key in occupied or key[0] >= len(table):
                        raise ValueError('overlapping_or_dangling_span')
                    occupied[key] = text
            x += width
    width = max((x + 1 for _, x in occupied), default=0)
    return [[occupied.get((y, x), '') for x in range(width)] for y in range(len(table))]


def _deepseek(document, checked_at):
    tables = [_grid(t, document) for t in document.tables]
    candidates = [t for t in tables if t and t[0][:3] == ['MODEL'] * 3]
    if len(candidates) != 1:
        raise ValueError('missing_or_ambiguous_model_table')
    data = candidates[0]
    if len(data[0]) != 5 or data[0][3:] != ['deepseek-flash', 'deepseek-v4-pro']:
        raise ValueError('changed_model_columns')
    versions = [r[3:] for r in data if r[:3] == ['MODEL VERSION'] * 3]
    if len(versions) != 1:
        raise ValueError('missing_model_version')
    fields = {'1M INPUT TOKENS (CACHE HIT)': 'cache_read_cost_per_million',
              '1M INPUT TOKENS (CACHE MISS)': 'input_cost_per_million',
              '1M OUTPUT TOKENS': 'output_cost_per_million'}
    prices = {}
    for cells in data:
        if cells[0] != 'PRICING':
            continue
        if cells[1] not in fields or cells[2] not in ('PEAK', 'OFF-PEAK'):
            raise ValueError('unknown_price_condition')
        key = (cells[2], fields[cells[1]])
        if key in prices:
            raise ValueError('duplicate_price')
        prices[key] = [_price(c) for c in cells[3:]]
    if len(prices) != 6:
        raise ValueError('missing_peak_or_off_peak')
    result = []
    for i, model in enumerate(data[0][3:]):
        version = versions[0][i]
        expected = r'DeepSeek-V4\.1-Flash(?:-\d{4})?' if i == 0 else r'DeepSeek-V4-Pro(?:-\d{4})?'
        if not re.fullmatch(expected, version):
            raise ValueError('unknown_model_version')
        row = _row('deepseek', model, checked_at,
            'PEAK USD/1M token reference; peak/off-peak schedule and holidays vary; '
            'not actual hourly billed cost; tool/media charges excluded; MODEL VERSION ' + version)
        row.update({field: prices['PEAK', field][i] for field in fields.values()})
        row['off_peak_rates'] = {field: prices['OFF-PEAK', field][i] for field in fields.values()}
        row['model_version'] = version
        row['aliases'] = [version.lower()]
        result.append(row)
    return result, {'model_count': len(result), 'skipped_count': 0}


def _opencode(document, checked_at):
    tables = [_grid(t, document) for t in document.tables]
    endpoints = [t for t in tables if t and t[0] == ['Model', 'Model ID', 'Endpoint', 'AI SDK Package']]
    pricing = [t for t in tables if t and t[0] == ['Model', 'Input', 'Output', 'Cached Read', 'Cached Write']]
    if len(endpoints) != 1 or len(pricing) != 1:
        raise ValueError('missing_or_ambiguous_tables')
    intro = document.table_intros[tables.index(pricing[0])]
    if not re.search(r'\bper 1M tokens\b', intro):
        raise ValueError('unverified_units')
    ids = {}
    for cells in endpoints[0][1:]:
        document.check()
        label, identifier = cells[:2]
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:\-]{0,255}', identifier) or label in ids:
            raise ValueError('invalid_or_duplicate_model_id')
        ids[label] = identifier
    result, seen = [], set()
    for cells in pricing[0][1:]:
        document.check()
        label = cells[0]
        threshold = None
        condition = ''
        tier = re.fullmatch(r'(.+) \(([≤>]) ([1-9][0-9]{0,5})K tokens\)', label)
        discount = re.fullmatch(r'(.+) \(([1-9][0-9]?% off)\)', label)
        if tier:
            if tier[2] == '>':
                continue
            label = tier[1]
            threshold = int(tier[3]) * 1000
            condition = f'; base per-request prompt tier ≤ {threshold} tokens, never aggregate usage'
        elif discount:
            label = discount[1]
            condition = '; published active discount (' + discount[2] + '), not list price'
        if label not in ids:
            continue
        identifier = ids[label]
        if identifier in seen:
            raise ValueError('duplicate_price')
        seen.add(identifier)
        row = _row('opencode', identifier, checked_at,
            'OpenCode Console USD/1M token reference; not billed; published cache-write rate only; '
            'tools/media/processing fees excluded' + condition)
        row['tier_threshold_tokens'] = threshold
        for field, value in zip(RATE_FIELDS, cells[1:]):
            row[field] = None if value == '-' else '0' if value == 'Free' else _price(value)
        if any(row[f] is not None for f in RATE_FIELDS):
            result.append(row)
    if not result:
        raise ValueError('no_supported_prices')
    count = len(pricing[0]) - 1
    return result, {'model_count': count, 'skipped_count': count - len(result)}


def _hive(document, checked_at):
    found = {}
    units = {'Input': 'input_cost_per_million', 'Output': 'output_cost_per_million',
             'Cache Read': 'cache_read_cost_per_million'}
    for label, text in document.paragraphs:
        document.check()
        if label not in HIVE_MODEL_IDS or not ('$' in text or re.search(r'/\s*1[MK]\b', text)):
            continue
        match = re.fullmatch(r'(\$[0-9]+(?:\.[0-9]+)?)\s*/\s*1M (Input|Output|Cache Read) Tokens', text)
        if not match:
            raise ValueError('ambiguous_hive_active_price_or_units')
        identifier, field = HIVE_MODEL_IDS[label], units[match[2]]
        rate = _price(match[1])
        if identifier not in found:
            row = _row('thehive', identifier, checked_at,
                'Hive active published USD/1M token reference, not crossed-out list price; '
                'not billed; promotions may change; tool/media charges excluded')
            row['model_id_source'] = HIVE_MODEL_ID_SOURCE
            row['display_name'] = label
            found[identifier] = row
        if found[identifier][field] not in (None, rate):
            raise ValueError('conflicting_hive_duplicate')
        found[identifier][field] = rate
    if (not found or set(found) != {HIVE_MODEL_IDS[label] for label in document.hive_labels}
            or any(row[f] is None for row in found.values() for f in units.values())):
        raise ValueError('missing_hive_prices')
    return list(found.values()), {'model_count': len(found), 'skipped_count': 0}


def parse(name, payload, checked_at, deadline=None):
    """Return (rows, coverage), matching pricing_catalog._parse's row schema."""
    if name not in SOURCES:
        raise ValueError('unknown_source')
    if type(payload) not in (str, bytes):
        raise ValueError('invalid_payload')
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError('response_too_large')
    if type(payload) is str:
        payload = payload.encode('utf-8')
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError('response_too_large')
    document = _Document(min(deadline, time.monotonic() + 5) if deadline is not None else time.monotonic() + 5)
    document.check()
    text = payload.decode('utf-8')
    for start in range(0, len(text), 16384):
        document.check()
        document.feed(text[start:start + 16384])
    document.close()
    document.check()
    if document.table is not None or document.cell is not None or document.paragraph is not None or document.heading is not None:
        raise ValueError('incomplete_document')
    result = {'deepseek': _deepseek, 'opencode': _opencode, 'thehive': _hive}[name](document, checked_at)
    document.check()
    return result
