"""Account display adapters. Credentials stay within Hermes's existing resolvers."""
from __future__ import annotations
import math
import re
import time


from typing import Any


def fetch_one(provider):
    from agent.account_usage import fetch_account_usage, build_nous_credits_snapshot, _fetch_portal_account
    if provider == 'nous':
        return snapshot_view(provider, build_nous_credits_snapshot(_fetch_portal_account(8)))
    if provider not in {'openai-codex', 'anthropic', 'openrouter', 'opencode-go'}:
        return unavailable(provider, 'unsupported', '검증된 계정 쿼터·잔액 조회 adapter가 없습니다. 로컬 사용량에서 잔액을 추정하지 않습니다.')
    return snapshot_view(provider, fetch_account_usage(provider, read_only=True))


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
