import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

class AccountsTests(unittest.TestCase):
    def test_snapshot_allowlist_unknown_percent_and_no_raw_credentials(self):
        self.assertIsNotNone(importlib.util.find_spec('accounts'), 'account adapter missing')
        from accounts import snapshot_view
        snap = SimpleNamespace(source='test-api', fetched_at=datetime.now(timezone.utc), plan='test',
            unavailable_reason=None, raw={'token':'SECRET'}, identity='private@example.test',
            details=('Credits balance: $1.23',), windows=(
                SimpleNamespace(label='weekly', used_percent=25, reset_at=None, scope='account', detail=None),
                SimpleNamespace(label='model', used_percent=None, reset_at=None, scope='model', detail=None)))
        out = snapshot_view('test', snap)
        self.assertEqual(out['windows'][0]['remaining_percent'], 75)
        self.assertIsNone(out['windows'][1]['remaining_percent'])
        self.assertNotIn('SECRET', str(out))
        self.assertNotIn('private@', str(out))
        self.assertEqual(out['details'], ['Credits balance: $1.23'])
        self.assertEqual(snapshot_view('test', None)['status'], 'unavailable')

    def test_one_provider_failure_does_not_hide_other_accounts(self):
        import accounts
        self.assertTrue(hasattr(accounts, 'collect_accounts'), 'account collector missing')
        def fetch(provider):
            if provider == 'bad': raise RuntimeError('SECRET')
            return accounts.unavailable(provider, 'unsupported', 'not supported')
        out = accounts.collect_accounts(['bad', 'other'], fetch=fetch, budget=1)
        self.assertEqual([a['provider'] for a in out['accounts']], ['bad', 'other'])
        self.assertEqual(out['accounts'][0]['status'], 'error')
        self.assertNotIn('SECRET', str(out))
        self.assertEqual(out['accounts'][1]['status'], 'unsupported')

if __name__ == '__main__': unittest.main()
