"""Thin gateway bridge. Heavy imports, SQL and HTTP run in killable child processes."""
from __future__ import annotations
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

MAX_RESPONSE_BYTES = 2_000_000


class SnapshotCache:
    def __init__(self):
        import threading
        self._guard = threading.Lock()
        self._slots = threading.BoundedSemaphore(2)
        self._values = {}
        self._busy = set()

    def _failure(self, key, reason):
        prior = self._values.get(key)
        if prior:
            return {**prior[1], 'status': 'stale', 'error': reason}
        return error_result(reason)

    def get(self, key, loader, *, ttl=60):
        with self._guard:
            cached = self._values.get(key)
            if cached and time.monotonic() - cached[0] < ttl:
                return cached[1]
            if key in self._busy or not self._slots.acquire(blocking=False):
                return self._failure(key, '다른 조회가 진행 중입니다. 잠시 후 다시 확인하세요.')
            self._busy.add(key)
        try:
            try:
                result = loader()
            except Exception:
                result = error_result('조회 실패')
            with self._guard:
                if result.get('status') == 'ok':
                    if len(self._values) >= 32 and key not in self._values:
                        self._values.pop(next(iter(self._values)))
                    self._values[key] = (time.monotonic(), result)
                    return result
                return self._failure(key, result.get('error') or '조회 실패')
        finally:
            with self._guard:
                self._busy.discard(key)
            self._slots.release()


CACHE = SnapshotCache()


def fetch_snapshot(kind, days=30):
    import sys
    import hermes_constants
    from tools.environments.local import served_profile_child_env
    home = Path(hermes_constants.get_hermes_home())
    if kind not in {'summary','accounts','pricing-status'}:
        return error_result('지원하지 않는 조회')
    key = (str(home.resolve()), kind, days if kind=='summary' else 0)
    def load():
        env = served_profile_child_env(target_home=home, inherit_credentials=True)
        env['HERMES_HOME'] = str(home)
        env['PYTHONPATH'] = os.pathsep.join(dict.fromkeys([str(Path(hermes_constants.__file__).resolve().parent), *[p for p in sys.path if p]]))
        env['PYTHONIOENCODING'] = 'utf-8'
        command = [sys.executable, str(Path(__file__).with_name('worker.py')), kind,
                   '--home', str(home), '--days', str(days)]
        return run_child(command, timeout=30, env=env, scratch=home/'cache'/'scratch')
    return CACHE.get(key, load, ttl=300 if kind=='accounts' else 60)


def error_result(reason):
    return dict(status='error', fetched_at=time.time(), error=reason, warnings=[reason])


def run_child(command, *, timeout: float=30, env=None, scratch=None):
    """Spool output to disk, cap what the gateway reads, kill/reap on deadline.

    Worker code is trusted, not a sandbox. The process boundary contains normal
    Python crashes and blocking I/O; it does not impose OS CPU/RAM limits.
    """
    scratch = scratch or os.environ.get('TMPDIR')
    if not scratch:
        from hermes_constants import get_hermes_home
        scratch = str(get_hermes_home() / 'cache' / 'scratch')
    Path(scratch).mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryFile(dir=scratch) as output:
            proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                stderr=subprocess.DEVNULL, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
                return error_result('조회 시간 제한 초과: 작업 프로세스를 종료했습니다.')
            if proc.returncode:
                return error_result('조회 작업 실패: gateway는 계속 실행됩니다.')
            if output.tell() > MAX_RESPONSE_BYTES:
                return error_result('조회 결과 크기 제한 초과')
            output.seek(0)
            result = json.load(output)
            if not isinstance(result, dict) or result.get('status') not in {'ok','error','stale'}:
                return error_result('잘못된 조회 응답')
            return result
    except (OSError, ValueError):
        return error_result('조회 프로세스를 실행하거나 응답을 읽을 수 없습니다.')
