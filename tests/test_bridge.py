import importlib.util
import sys
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

class IsolationTests(unittest.TestCase):
    def test_worker_crash_and_timeout_are_bounded_sanitized(self):
        self.assertIsNotNone(importlib.util.find_spec('bridge'), 'isolated bridge missing')
        from bridge import run_child
        start = time.monotonic()
        timed = run_child([sys.executable, '-c', 'import time; time.sleep(60)'], timeout=.2)
        self.assertEqual(timed['status'], 'error')
        self.assertLess(time.monotonic()-start, 4)
        failed = run_child([sys.executable, '-c', "import sys; print('SECRET',file=sys.stderr); sys.exit(7)"], timeout=2)
        self.assertEqual(failed['status'], 'error')
        self.assertNotIn('SECRET', str(failed))
        ok = run_child([sys.executable, '-c', 'print(\'{"status":"ok","models":[]}\')'], timeout=2)
        self.assertEqual(ok['status'], 'ok')

    def test_cache_reuses_success_retains_stale_and_bounds_concurrency(self):
        import bridge
        self.assertTrue(hasattr(bridge, 'SnapshotCache'), 'cache controller missing')
        cache = bridge.SnapshotCache()
        calls = []
        def success():
            calls.append(1)
            return {'status':'ok','fetched_at':10,'models':[{'model':'kept'}]}
        first = cache.get('x', success, ttl=60)
        self.assertEqual(cache.get('x', success, ttl=60), first)
        self.assertEqual(len(calls), 1)
        stale = cache.get('x', lambda: bridge.error_result('timeout'), ttl=0)
        self.assertEqual(stale['status'], 'stale')
        self.assertEqual(stale['models'][0]['model'], 'kept')
        self.assertEqual(stale['fetched_at'], 10)
        cache._slots.acquire(); cache._slots.acquire()
        try:
            denied = cache.get('new', success, ttl=0)
            self.assertEqual(denied['status'], 'error')
            self.assertEqual(len(calls), 1)
        finally:
            cache._slots.release(); cache._slots.release()

    def test_child_inherits_runtime_import_paths(self):
        import bridge
        from unittest.mock import patch
        bridge.CACHE = bridge.SnapshotCache()
        with patch('sys.path', sys.path + ['runtime-extra-package-path']), patch.object(bridge, 'run_child', return_value={'status':'ok'}) as run:
            bridge.fetch_snapshot('summary', 31)
            self.assertIn('runtime-extra-package-path', run.call_args.kwargs['env']['PYTHONPATH'])

if __name__ == '__main__': unittest.main()
