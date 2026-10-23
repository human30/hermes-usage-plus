"""Local timing-only telemetry: no prompts, responses, URLs or credentials persisted."""
from __future__ import annotations
import math
import queue
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

_QUEUE = queue.Queue(maxsize=256)
_LOCK = threading.Lock()
_THREAD = None


def make_record(payload):
    usage = payload.get('usage')
    duration = payload.get('api_duration')
    if payload.get('error') or not isinstance(usage, dict) or 'output_tokens' not in usage:
        return None
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        return None
    session = str(payload.get('session_id') or '')[:160]
    request = str(payload.get('api_request_id') or '')[:160]
    if not session or not request:
        return None
    started = payload.get('started_at')
    if not isinstance(started, (int, float)) or not math.isfinite(started):
        return None
    output = usage['output_tokens']
    if not isinstance(output, int) or output < 0:
        return None
    return (session, request, str(payload.get('provider') or '')[:128],
            str(payload.get('model') or '')[:256], float(started), float(duration), output)


def save_record(home: Path, record):
    if not record or not home.is_dir():
        return
    folder = home / 'cache' / 'usage-plus'
    folder.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(folder / 'metrics.db', timeout=.05)) as con:
        con.execute('''CREATE TABLE IF NOT EXISTS timing(
            session_id TEXT, request_id TEXT, provider TEXT, model TEXT,
            started_at REAL, duration REAL, output_tokens INTEGER,
            PRIMARY KEY(session_id,request_id))''')
        con.execute('INSERT OR IGNORE INTO timing VALUES(?,?,?,?,?,?,?)', record)
        # Bounded disk footprint; DELETE by primary rowid index, never scan conversation data.
        con.execute('DELETE FROM timing WHERE rowid < (SELECT MAX(rowid)-50000 FROM timing)')
        con.commit()


def read_timing(home: Path, cutoff):
    path = home / 'cache' / 'usage-plus' / 'metrics.db'
    if not path.exists():
        return {}
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.1)) as con:
        con.row_factory = sqlite3.Row
        con.execute('ATTACH DATABASE ? AS history', ((home/'state.db').resolve().as_uri()+'?mode=ro',))
        con.execute('PRAGMA query_only=ON')
        deadline = time.monotonic()+2
        con.set_progress_handler(lambda: int(time.monotonic()>deadline), 10000)
        rows = con.execute('''SELECT provider,model,COUNT(*) AS timing_samples,
            SUM(output_tokens)/SUM(duration) AS avg_tps,AVG(duration) AS avg_latency_s
            FROM timing t JOIN history.sessions s ON s.id=t.session_id
            WHERE s.started_at>=? GROUP BY provider,model''', (cutoff,)).fetchall()
        return {(r['provider'],r['model']):dict(r, timing_scope='provider_model') for r in rows}


def _drain():
    while True:
        item = _QUEUE.get()
        try:
            home, record = item
            save_record(home, record)
        except (OSError, sqlite3.Error, ValueError):
            # Metrics must never interrupt the user's LLM call; a dropped sample remains absent.
            pass
        finally:
            _QUEUE.task_done()


def record_call(**payload):
    global _THREAD
    try:
        record = make_record(payload)
        if record is None:
            return
        from hermes_constants import get_hermes_home
        home = Path(get_hermes_home())  # capture owning scope before crossing the thread boundary
        with _LOCK:
            if _THREAD is None or not _THREAD.is_alive():
                # Worker uses explicit paths only; it never reads ambient profile/secret state.
                _THREAD = threading.Thread(target=_drain, daemon=True, name='usage-plus-metrics')
                _THREAD.start()
        _QUEUE.put_nowait((home, record))
    except (queue.Full, OSError, ValueError, TypeError):
        return
