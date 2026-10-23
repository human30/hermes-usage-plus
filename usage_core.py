"""Bounded, read-only Hermes usage aggregation. No network or credential access."""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from typing import Any
from pathlib import Path

COUNTERS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens')


def open_readonly(path: Path):
    con = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.25)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    deadline = time.monotonic() + 3
    con.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
    return con


def reference_price(provider: str, model: str):
    """Use Hermes's bundled official rate snapshots only; never fetch on summary reads.

    Values are a base-tier reference, NOT the user's route's billing tariff.
    Unknown aliases are intentionally not guessed.
    """
    from agent.usage_pricing import BillingRoute, _lookup_official_docs_pricing
    known = {'openai', 'anthropic', 'google', 'deepseek', 'xai', 'xiaomi'}
    owner = {'openai-codex': 'openai', 'xai-oauth': 'xai'}.get(provider, provider)
    if '/' in model:
        prefix, rest = model.split('/', 1)
        if prefix in known:
            owner, model = prefix, rest
    if model.startswith('gpt-') or model.startswith(('o1', 'o3', 'o4')):
        owner = 'openai'
    elif model.startswith('claude-'):
        owner = 'anthropic'
    elif model.startswith('gemini-'):
        owner = 'google'
    return _lookup_official_docs_pricing(BillingRoute(owner, model))


def api_value(row, entry):
    from decimal import Decimal
    if entry is None:
        return None
    amount = Decimal(0)
    fields = (('input_tokens', 'input_cost_per_million'), ('output_tokens', 'output_cost_per_million'),
              ('cache_read_tokens', 'cache_read_cost_per_million'), ('cache_write_tokens', 'cache_write_cost_per_million'))
    for field, rate_key in fields:
        tokens = int(row.get(field) or 0)
        rate = getattr(entry, rate_key)
        if tokens and rate is None:
            return None
        if rate is not None:
            amount += Decimal(tokens) * rate / Decimal(1000000)
    if entry.request_cost is not None:
        amount += Decimal(row['api_calls']) * entry.request_cost
    return float(amount) if amount.is_finite() and amount >= 0 else None


def build_summary(home: Path, days: int = 30, *, now=None, price_lookup=None, native_price_lookup=None) -> dict[str, Any]:
    now = time.time() if now is None else now
    days = max(1, min(365, int(days)))
    cutoff = now - days * 86400
    sums = ','.join(f'SUM(u.{key}) AS {key}' for key in COUNTERS)
    sql = f'''SELECT u.model, u.billing_provider AS provider, u.billing_mode,
        {sums}, SUM(u.api_call_count) AS api_calls,
        SUM(CASE WHEN u.task='' THEN u.api_call_count ELSE 0 END) AS main_calls,
        SUM(CASE WHEN u.task!='' THEN u.api_call_count ELSE 0 END) AS aux_calls,
        COUNT(DISTINCT u.session_id) AS sessions,
        CASE WHEN MIN(CASE WHEN u.cost_status='actual' AND u.actual_cost_usd IS NOT NULL THEN 1 ELSE 0 END)=1
             THEN SUM(u.actual_cost_usd) END AS actual_cost_usd,
        CASE WHEN MIN(CASE WHEN u.cost_status IN ('actual','estimated','included') AND u.estimated_cost_usd IS NOT NULL THEN 1 ELSE 0 END)=1
             THEN SUM(u.estimated_cost_usd) END AS estimated_cost_usd
        FROM session_model_usage u JOIN sessions s ON s.id=u.session_id
        WHERE s.started_at >= ?
        GROUP BY u.model,u.billing_provider,u.billing_mode
        ORDER BY SUM(u.input_tokens+u.cache_read_tokens+u.cache_write_tokens+u.output_tokens) DESC
        LIMIT 1000'''
    with closing(open_readonly(home / 'state.db')) as con:
        models = [dict(row) for row in con.execute(sql, (cutoff,))]
    warnings = ['선택 기간에 시작한 세션의 누적값입니다. 날짜별 실제 소비량이 아니며 Hermes 외부 사용은 제외됩니다.',
                'API 환산액은 실제 청구액이 아닙니다. 캐시 필드가 없는 과거 기록은 0%로 단정하지 않습니다.']
    warnings.append('API 환산액: 공식 가격표의 기본 구간 단가 기준. long-context·fast/batch 요율과 과거 가격 변경은 반영하지 않은 참고 추정액입니다.')
    lookup = price_lookup or reference_price
    for row in models:
        for key in (*COUNTERS, 'api_calls', 'main_calls', 'aux_calls'):
            row[key] = int(row[key] or 0)
        row['prompt_tokens'] = row['input_tokens'] + row['cache_read_tokens'] + row['cache_write_tokens']
        observed = row['cache_read_tokens'] > 0 or row['cache_write_tokens'] > 0
        row['cache_status'] = 'observed' if observed else 'unknown'
        row['cache_hit_pct'] = row['cache_read_tokens'] / row['prompt_tokens'] * 100 if observed and row['prompt_tokens'] else None
        row.update(api_value_usd=None, pricing_source=None, pricing_version=None,
                   pricing_source_url=None, pricing_basis=None, pricing_provider=None, pricing_model=None,
                   avg_tps=None, avg_latency_s=None, timing_samples=0)
        entry = lookup(row['provider'], row['model'])
        row['api_value_usd'] = api_value(row, entry)
        if row['api_value_usd'] is None and native_price_lookup is not None:
            native = native_price_lookup(row['provider'], row['model'])
            value = api_value(row, native)
            if value is not None:
                entry, row['api_value_usd'] = native, value
        if entry:
            row['pricing_source'] = entry.source
            row['pricing_version'] = entry.pricing_version
            row['pricing_source_url'] = getattr(entry, 'source_url', None)
            row['pricing_basis'] = getattr(entry, 'pricing_basis', 'route')
            row['pricing_provider'] = getattr(entry, 'provider', None)
            row['pricing_model'] = getattr(entry, 'model', None)
    totals: dict[str, Any] = {key: sum(m[key] for m in models) for key in ('api_calls', 'input_tokens', 'output_tokens', 'prompt_tokens', 'cache_read_tokens')}
    priced = [m['api_value_usd'] for m in models if m['api_value_usd'] is not None]
    totals.update(api_value_usd=sum(priced) if priced else None, priced_models=len(priced), models=len(models))
    if len(models) == 1000:
        warnings.append('모델 그룹 1,000개 제한에 도달했습니다. 표시 및 합계가 부분 집계일 수 있습니다.')
    return dict(status='ok', fetched_at=now, period_days=days, period_basis='session_started_at',
                models=models, totals=totals, warnings=warnings)
