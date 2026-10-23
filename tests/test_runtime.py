import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

class RuntimeTests(unittest.TestCase):
    def test_worker_missing_database_returns_json_error_not_traceback(self):
        self.assertTrue((ROOT/'worker.py').exists(), 'worker entry missing')
        with tempfile.TemporaryDirectory(dir=ROOT.parents[1]/'cache'/'scratch') as tmp:
            result=subprocess.run([sys.executable,str(ROOT/'worker.py'),'summary','--home',tmp],
                capture_output=True,text=True,encoding='utf-8',timeout=20)
            self.assertEqual(result.returncode,0)
            data=json.loads(result.stdout)
            self.assertEqual(data['status'],'error')
            self.assertNotIn(tmp, data.get('error',''))

    def test_route_does_not_block_event_loop_when_child_hangs(self):
        self.assertTrue((ROOT/'dashboard'/'plugin_api.py').exists(), 'API entry missing')
        from fastapi import FastAPI
        import httpx
        spec=importlib.util.spec_from_file_location('test_usage_api',ROOT/'dashboard'/'plugin_api.py')
        mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        app=FastAPI(); app.include_router(mod.router)
        @app.get('/health')
        async def health(): return {'ok':True}
        def hung(*args,**kwargs):
            return mod._bridge.run_child([sys.executable,'-c','import time;time.sleep(30)'],timeout=.5)
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                with patch.object(mod._bridge,'fetch_snapshot',side_effect=hung):
                    pending=asyncio.create_task(client.get('/summary'))
                    await asyncio.sleep(.05)
                    start=time.monotonic()
                    response=await client.get('/health')
                    self.assertEqual(response.status_code,200)
                    self.assertLess(time.monotonic()-start,.2)
                    result=await pending
                    self.assertEqual(result.json()['status'],'error')
        asyncio.run(check())

    def test_register_refreshes_loaded_bridge_without_gateway_restart(self):
        import types
        spec=importlib.util.spec_from_file_location('usage_plus_refresh_test',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
        module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module)
        old=types.ModuleType('_hermes_usage_plus_bridge')
        old.fetch_snapshot=None
        sys.modules[old.__name__]=old
        try:
            module.register(types.SimpleNamespace(register_hook=lambda *a: None))
            self.assertTrue(callable(old.fetch_snapshot))
        finally: sys.modules.pop(old.__name__,None)

if __name__=='__main__': unittest.main()
