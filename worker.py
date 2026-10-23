"""One-shot isolated worker. stdout is a sanitized JSON response, never diagnostics."""
from __future__ import annotations
import argparse
import contextlib
import json
import os
import sys
import time
from pathlib import Path


def _bind(home):
    from hermes_constants import set_hermes_home_override
    set_hermes_home_override(home)
    from agent.secret_scope import build_profile_secret_scope, set_secret_scope
    set_secret_scope(build_profile_secret_scope(home), profile_home=str(home))


def summary(home, days):
    from usage_core import build_summary, reference_price
    native_lookup = None
    try:
        from pricing_catalog import lookup_price, status
        def lookup(provider, model):
            return lookup_price(home, provider, model)
        def lookup_native(provider, model):
            return lookup_price(home, provider, model, native_only=True)
        native_lookup = lookup_native
        pricing = status(home)
    except ImportError:
        lookup = reference_price
        pricing = {'status':'unavailable', 'reason':'가격 자동 갱신 모듈이 없습니다.'}
    result = build_summary(home, days, price_lookup=lookup, native_price_lookup=native_lookup)
    result['pricing'] = pricing
    from telemetry import read_timing
    try:
        timings = read_timing(home, time.time()-days*86400)
        for row in result['models']:
            timing = timings.get((row['provider'], row['model']))
            if timing:
                row.update({k:timing[k] for k in ('avg_tps','avg_latency_s','timing_samples','timing_scope')})
    except Exception:
        result['warnings'].append('토큰 속도 측정 기록을 읽을 수 없습니다.')
    result['warnings'].append('속도는 설치 후 수집한 output tokens / 전체 API 요청 시간입니다. 첫 토큰 대기를 포함하며 순수 decode 속도가 아닙니다.')
    result['warnings'].append('가격표는 매일 자동 갱신합니다. 단가 미확인 모델은 환산 합계에서 제외합니다.')
    return result


def accounts(home):
    from accounts import collect_accounts
    from hermes_cli.config import load_config_readonly
    from usage_core import open_readonly
    from contextlib import closing
    config = load_config_readonly() or {}
    providers = {'nous','openai-codex','anthropic','openrouter','opencode-go'}
    providers.update((config.get('providers') or {}).keys())
    try:
        with closing(open_readonly(home/'state.db')) as con:
            providers.update(row[0] for row in con.execute('SELECT DISTINCT billing_provider FROM session_model_usage LIMIT 64') if row[0])
    except Exception:
        pass  # Existing account adapters remain useful without a session database.
    return collect_accounts(providers)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('kind',choices=['summary','accounts','prices','pricing-status'])
    parser.add_argument('--home',type=Path)
    parser.add_argument('--days',type=int,default=30)
    args=parser.parse_args()
    from hermes_constants import get_hermes_home
    home=args.home or Path(get_hermes_home())
    _bind(home)
    if args.kind=='summary': return summary(home,max(1,min(365,args.days)))
    if args.kind=='accounts': return accounts(home)
    from pricing_catalog import refresh_prices, status
    if args.kind=='prices':
        value=refresh_prices(home)
        return {'status':'ok','fetched_at':time.time(),'pricing':status(home)}
    return {'status':'ok','fetched_at':time.time(),'pricing':status(home)}


if __name__=='__main__':
    stream=sys.stdout
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result=main()
    except Exception as exc:
        result={'status':'error','fetched_at':time.time(),'error':'조회 실패 ('+type(exc).__name__+')', 'warnings':[]}
    stream.write(json.dumps(result,ensure_ascii=True,allow_nan=False)+'\n')
    stream.flush()
    # Account futures can outlive their provider timeout. A one-shot process owns them,
    # so exit after publishing partial results instead of waiting for an SDK's stuck thread.
    os._exit(0)
