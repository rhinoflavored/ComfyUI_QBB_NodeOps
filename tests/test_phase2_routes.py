import importlib
import json
import types
import unittest
from unittest.mock import patch
from aiohttp import web
from test_phase1_snapshot import package

routes_module = importlib.import_module(package.__name__ + '.routes.records')
diff = importlib.import_module(package.__name__ + '.state_diff')


class PlanRoutes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        routes = web.RouteTableDef()
        routes_module.register(routes)
        self.handler = next(r.handler for r in routes if r.path == '/qbb_nodeops/version-records/{record_id}/recovery-plan')
        self.payload = {}
        async def body():
            return self.payload
        self.request = types.SimpleNamespace(remote='127.0.0.1', host='localhost:8188', headers={'X-QBB-NodeOps': '1'},
                                             content_type='application/json', match_info={'record_id': 'a' * 32}, json=body)

    async def test_local_readonly_plan(self):
        with patch.object(diff, 'inspect', return_value={'read_only': True, 'execution_allowed': False}) as call:
            result = await self.handler(self.request)
        self.assertEqual(result.status, 200)
        call.assert_called_once_with('a' * 32, None)
        self.assertFalse(json.loads(result.text)['execution_allowed'])

    async def test_saved_current_endpoint_is_forwarded(self):
        self.payload = {'current_record_id': 'b' * 32}
        with patch.object(diff, 'inspect', return_value={}) as call:
            await self.handler(self.request)
        call.assert_called_once_with('a' * 32, 'b' * 32)

    async def test_execution_or_path_options_rejected(self):
        for field in ('target', 'execute', 'confirmed', 'choices', 'core', 'plugin_roots'):
            self.payload = {field: True}
            with patch.object(diff, 'inspect') as call:
                result = await self.handler(self.request)
            self.assertEqual(result.status, 400)
            call.assert_not_called()

    async def test_remote_access_rejected(self):
        self.request.remote = '192.0.2.1'
        with patch.object(diff, 'inspect') as call:
            result = await self.handler(self.request)
        self.assertEqual(result.status, 403)
        call.assert_not_called()

    async def test_unstable_scan_never_returns_plan(self):
        with patch.object(diff, 'inspect', side_effect=ValueError('Environment changed')):
            result = await self.handler(self.request)
        self.assertEqual(result.status, 400)
        self.assertIn('changed', json.loads(result.text)['error'])
