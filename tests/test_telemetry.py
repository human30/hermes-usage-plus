import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

class TelemetryTests(unittest.TestCase):
    def test_only_numeric_metadata_saved_deduplicated_and_timing_weighted(self):
        self.assertIsNotNone(importlib.util.find_spec('telemetry'), 'timing recorder missing')
        from telemetry import make_record, save_record, read_timing
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            payload = dict(api_request_id='r1', session_id='s', model='m', provider='p',
                api_duration=2.0, started_at=1000, usage={'output_tokens':100},
                assistant_message='PRIVATE', base_url='PRIVATE', response={'secret':'PRIVATE'})
            record = make_record(payload)
            self.assertNotIn('PRIVATE', str(record))
            save_record(home, record)
            save_record(home, record)
            payload.update(api_request_id='r2', api_duration=8.0, usage={'output_tokens':100})
            save_record(home, make_record(payload))
            con=sqlite3.connect(home/'state.db')
            con.execute('CREATE TABLE sessions(id TEXT, started_at REAL, model TEXT)')
            con.execute("INSERT INTO sessions VALUES('s',1000,'m')"); con.commit(); con.close()
            result = read_timing(home, 999)[('p','m')]
            self.assertEqual(result['timing_samples'], 2)
            self.assertEqual(result['timing_scope'], 'provider_model')
            self.assertNotIn('billing_mode', result)
            self.assertEqual(result['avg_tps'], 20)
            self.assertEqual(result['avg_latency_s'], 5)
            self.assertIsNone(make_record(dict(payload, api_duration=float('nan'))))
            self.assertIsNone(make_record(dict(payload, usage=None)))
            self.assertNotIn(b'PRIVATE', (home/'cache'/'usage-plus'/'metrics.db').read_bytes())

    def test_read_timing_graceful_without_state_db(self):
        from telemetry import make_record, save_record, read_timing
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            payload = dict(api_request_id='r1', session_id='s', model='m', provider='p',
                api_duration=2.0, started_at=1000, usage={'output_tokens':100})
            save_record(home, make_record(payload))
            # No state.db exists in home
            res = read_timing(home, 999)
            self.assertEqual(res, {})

    def test_plugin_registers_only_observer_hooks(self):
        self.assertTrue((ROOT/'__init__.py').exists(), 'plugin entry missing')
        spec=importlib.util.spec_from_file_location('usage_plus_test',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
        module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module)
        class Context:
            def __init__(self): self.hooks={}
            def register_hook(self,name,callback): self.hooks[name]=callback
        ctx=Context(); module.register(ctx)
        self.assertEqual(set(ctx.hooks), {'post_api_request','post_auxiliary_call'})

if __name__ == '__main__': unittest.main()
