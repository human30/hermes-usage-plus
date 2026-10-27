"""Account display adapters. Credentials stay within Hermes's existing resolvers."""
from __future__ import annotations
import math
import re
import time


from typing import Any


def fetch_one(provider):
    if provider in {'antigravity-oauth', 'antigravity-pool', 'google-antigravity'}:
        return fetch_antigravity_pool()
    from agent.account_usage import fetch_account_usage, build_nous_credits_snapshot, _fetch_portal_account
    if provider == 'nous':
        return snapshot_view(provider, build_nous_credits_snapshot(_fetch_portal_account(8)))
    if provider not in {'openai-codex', 'anthropic', 'openrouter', 'opencode-go'}:
        return unavailable(provider, 'unsupported', '검증된 계정 쿼터·잔액 조회 adapter가 없습니다. 로컬 사용량에서 잔액을 추정하지 않습니다.')
    return snapshot_view(provider, fetch_account_usage(provider, read_only=True))


def fetch_antigravity_pool() -> dict[str, Any]:
    """Read local agy-pool state.json (zero network, strictly read-only)."""
    import json
    from datetime import datetime, timezone
    from pathlib import Path
    state_path = Path.home() / '.local' / 'state' / 'agy-pool' / 'state.json'
    if not state_path.is_file():
        return unavailable('antigravity-oauth', 'unavailable', '로컬 풀 상태 파일(~/.local/state/agy-pool/state.json)이 없습니다.')
    try:
        with state_path.open('r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as exc:
        return unavailable('antigravity-oauth', 'error', f'풀 상태 파일 읽기 오류: {exc}')

    queue = data.get('queue', [])
    current = data.get('current') or (queue[0] if queue else None)
    accounts_data = data.get('accounts', {})
    if not queue and not accounts_data:
        return unavailable('antigravity-oauth', 'unavailable', '풀에 등록된 계정이 없습니다.')

    def _extract_quota(model_entry):
        if not isinstance(model_entry, dict):
            return None, None
        sub = model_entry.get('quotaInfo')
        qi = sub if isinstance(sub, dict) else model_entry
        rem = qi.get('remainingFraction') if isinstance(qi, dict) else None
        reset_t = qi.get('resetTime') if isinstance(qi, dict) else None
        if rem is not None:
            try:
                return float(rem), reset_t
            except (ValueError, TypeError):
                pass
        return None, reset_t

    # Determine active account info
    active_acc = accounts_data.get(current) if isinstance(accounts_data, dict) else {}
    if not isinstance(active_acc, dict):
        active_acc = {}
    q = active_acc.get('apiQuota') if isinstance(active_acc, dict) else {}
    if not isinstance(q, dict):
        q = {}
    models = q.get('models') if isinstance(q, dict) else {}
    if not isinstance(models, dict):
        models = {}

    windows = []
    # Primary Gemini 5h rolling window
    gemini_entry = models.get('gemini-2.5-pro') or models.get('gemini-2.5-flash') or {}
    gemini_rem, gemini_reset = _extract_quota(gemini_entry)
    if gemini_rem is not None:
        rem_pct = round(gemini_rem * 100.0, 1)
        windows.append(dict(
            label='Gemini 5시간 한도 (현재 계정)',
            used_percent=round(100.0 - rem_pct, 1),
            remaining_percent=rem_pct,
            reset_at=gemini_reset,
            scope='account',
            detail='Gemini 2.5 Pro / Flash',
        ))

    # Primary Claude 5h rolling window
    claude_entry = models.get('claude-sonnet-4-6') or models.get('claude-opus-4-6-thinking') or {}
    claude_rem, claude_reset = _extract_quota(claude_entry)
    if claude_rem is not None:
        rem_pct = round(claude_rem * 100.0, 1)
        windows.append(dict(
            label='Claude 5시간 한도 (현재 계정)',
            used_percent=round(100.0 - rem_pct, 1),
            remaining_percent=rem_pct,
            reset_at=claude_reset,
            scope='account',
            detail='Claude Sonnet / Opus',
        ))

    # Weekly and Argon quotas from native groups if available
    native_quota = active_acc.get('nativeQuota') if isinstance(active_acc, dict) else {}
    native_groups = native_quota.get('groups') if isinstance(native_quota, dict) else {}
    if isinstance(native_groups, dict):
        # Gemini weekly
        gw_entry = native_groups.get('gemini-weekly')
        gw_rem, gw_reset = _extract_quota(gw_entry)
        if gw_rem is not None:
            gw_pct = round(gw_rem * 100.0, 1)
            windows.append(dict(
                label='Gemini 주간 한도 (현재 계정)',
                used_percent=round(100.0 - gw_pct, 1),
                remaining_percent=gw_pct,
                reset_at=gw_reset,
                scope='account',
                detail='Gemini Weekly Bucket',
            ))
        # Claude (3p) weekly
        cw_entry = native_groups.get('3p-weekly')
        cw_rem, cw_reset = _extract_quota(cw_entry)
        if cw_rem is not None:
            cw_pct = round(cw_rem * 100.0, 1)
            windows.append(dict(
                label='Claude 주간 한도 (현재 계정)',
                used_percent=round(100.0 - cw_pct, 1),
                remaining_percent=cw_pct,
                reset_at=cw_reset,
                scope='account',
                detail='Claude 3p Weekly Bucket',
            ))
        # Argon weekly
        argon_entry = native_groups.get('gemini-argon-wtu-weekly')
        if argon_entry is not None:
            ar_rem, ar_reset = _extract_quota(argon_entry)
            if ar_rem is not None:
                ar_pct = round(ar_rem * 100.0, 1)
                windows.append(dict(
                    label='Argon 주간 한도 (현재 계정)',
                    used_percent=round(100.0 - ar_pct, 1),
                    remaining_percent=ar_pct,
                    reset_at=ar_reset,
                    scope='account',
                    detail='Gemini Argon WTU',
                ))

    details = [
        f'활성 계정: {current or "미지정"} (풀 총 {len(queue)}개 계정 가용)',
    ]
    for email in queue:
        is_act = (email == current)
        acc = accounts_data.get(email) if isinstance(accounts_data, dict) else {}
        acc_dict = acc if isinstance(acc, dict) else {}
        acc_api = acc_dict.get('apiQuota') if isinstance(acc_dict.get('apiQuota'), dict) else {}
        acc_models = acc_api.get('models') if isinstance(acc_api, dict) and isinstance(acc_api.get('models'), dict) else {}
        g_entry = acc_models.get('gemini-2.5-pro') or acc_models.get('gemini-2.5-flash') if isinstance(acc_models, dict) else None
        c_entry = acc_models.get('claude-sonnet-4-6') or acc_models.get('claude-opus-4-6-thinking') if isinstance(acc_models, dict) else None
        g_rem, _ = _extract_quota(g_entry)
        c_rem, _ = _extract_quota(c_entry)
        g_str = f'{(g_rem * 100):.1f}%' if g_rem is not None else '미확인'
        c_str = f'{(c_rem * 100):.1f}%' if c_rem is not None else '미확인'
        act_tag = ' [Active]' if is_act else ''
        # Keep prefix before @ or masked domain so user can identify account without triggering email redaction regex
        masked_id = email.split('@')[0] if '@' in email else email
        details.append(f'{masked_id} ({email}){act_tag} · Gemini {g_str} · Claude {c_str}')

    interval_min = q.get('intervalMinutes')
    if interval_min is not None:
        details.append(f'동기화 주기: {interval_min}분 (Jitter 적용) · 60분±30분 (30~90분 간격)')

    last_scan = q.get('lastAttemptAt')
    fetched_epoch = time.time()
    if last_scan:
        try:
            dt = datetime.fromisoformat(last_scan.replace('Z', '+00:00'))
            fetched_epoch = dt.timestamp()
        except Exception:
            pass

    return dict(
        provider='antigravity-oauth',
        status='ok',
        source='agy-gw pool state (로컬)',
        fetched_at=fetched_epoch,
        plan='Antigravity Gateway Pool',
        windows=windows,
        details=details,
        reason=None,
    )


def collect_accounts(providers, *, fetch=None, budget=22) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor, wait
    import contextvars
    fetch = fetch or fetch_one
    names = sorted(set(str(p) for p in providers if p))[:64]
    executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix='usage-account')
    futures = {executor.submit(contextvars.copy_context().run, fetch, name): name for name in names}
    done, pending = wait(futures, timeout=budget)
    result = {}
    for future in done:
        name = futures[future]
        try:
            result[name] = future.result()
        except Exception:
            result[name] = unavailable(name, 'error', '서비스 조회 실패. 인증·네트워크·서비스 상태를 확인하세요.')
    for future in pending:
        future.cancel()
        result[futures[future]] = unavailable(futures[future], 'error', '서비스 조회 시간 초과')
    executor.shutdown(wait=False, cancel_futures=True)
    # Production caller is a bounded one-shot process, which exits after serializing this result.
    return dict(status='ok', fetched_at=time.time(), accounts=[result[n] for n in names],
                warnings=['서비스가 반환한 계정 전체 값입니다. Hermes 외부 사용도 포함될 수 있습니다.',
                          '동일 provider의 여러 계정 중 현재 Hermes가 선택한 자격 증명 기준입니다.'])


def safe_text(value):
    text = str(value or '')[:500]
    # Provider text is data, not markup; still remove URLs and common credential/account forms.
    text = re.sub(r'https?://\S+', '[link omitted]', text)
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[account]', text)
    text = re.sub(r'(?i)(bearer\s+\S+|sk-[A-Za-z0-9_-]+|eyJ[A-Za-z0-9_.-]+)', '[redacted]', text)
    return text


def unavailable(provider, status='unavailable', reason='인증 정보 또는 서비스 사용량 응답을 확인할 수 없습니다.'):
    return dict(provider=provider, status=status, source=None, fetched_at=None, plan=None,
                windows=[], details=[], reason=reason)


def snapshot_view(provider, snapshot):
    if snapshot is None:
        return unavailable(provider)
    if snapshot.unavailable_reason:
        return unavailable(provider)
    windows = []
    for item in snapshot.windows:
        used = item.used_percent
        used = float(used) if isinstance(used, (int, float)) and not isinstance(used, bool) and math.isfinite(used) else None
        windows.append(dict(label=safe_text(item.label), used_percent=used,
            remaining_percent=max(0, min(100, 100-used)) if used is not None else None,
            reset_at=item.reset_at.isoformat() if item.reset_at else None,
            scope=item.scope, detail=safe_text(item.detail) or None))
    return dict(provider=provider, status='ok' if windows or snapshot.details else 'unavailable',
        source=safe_text(snapshot.source), fetched_at=snapshot.fetched_at.timestamp(),
        plan=safe_text(snapshot.plan) or None, windows=windows,
        details=[safe_text(v) for v in snapshot.details], reason=None)
