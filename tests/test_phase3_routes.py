import importlib
import json
import types
import unittest
from unittest.mock import patch, AsyncMock
from aiohttp import web
from test_phase1_snapshot import package

routes_module = importlib.import_module(package.__name__ + '.routes.current_restore')
restore = routes_module.restore


class RoutesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        table = web.RouteTableDef()
        self.instance = types.SimpleNamespace(app=web.Application())
        routes_module.register(table, self.instance)
        self.handler = next(r.handler for r in table if r.method == 'POST')
        self.payload = {'record_id': 'a' * 32, 'fingerprint': 'b' * 64, 'confirmed': True}
        async def body():
            return self.payload
        self.request = types.SimpleNamespace(remote='127.0.0.1', host='localhost:8188',
            headers={'X-QBB-NodeOps': '1'}, content_type='application/json',
            match_info={'action': 'prepare'}, json=body)

    async def test_prepare_does_not_schedule_shutdown(self):
        with patch.object(restore, 'prepare', return_value={'status': 'prepared'}) as call, patch.object(routes_module, 'controlled_stop') as stop:
            result = await self.handler(self.request)
        self.assertEqual(result.status, 200)
        call.assert_called_once_with(**self.payload)
        stop.assert_not_called()

    async def test_start_schedules_controlled_stop_only_on_success(self):
        self.request.match_info['action'] = 'start'
        self.payload = {'job_id': 'a' * 32, 'plan_hash': 'b' * 64, 'confirmed': True}
        with patch.object(restore, 'start', return_value={'id': 'a' * 32}) as start, patch.object(routes_module, 'controlled_stop', new_callable=AsyncMock) as stop:
            result = await self.handler(self.request)
            await __import__('asyncio').sleep(0)
        self.assertEqual(result.status, 200)
        stop.assert_awaited_once_with(self.instance, 'a' * 32)
        with patch.object(restore, 'start', side_effect=ValueError('stale')), patch.object(routes_module, 'controlled_stop') as stop:
            result = await self.handler(self.request)
        self.assertEqual(result.status, 409)
        stop.assert_not_called()

    async def test_external_request_denied(self):
        self.request.remote = '192.0.2.1'
        with patch.object(restore, 'prepare') as call:
            result = await self.handler(self.request)
        self.assertEqual(result.status, 403)
        call.assert_not_called()

    async def test_alternate_paths_and_execute_options_denied(self):
        for field in ('runtime', 'core', 'manager', 'python', 'target', 'command', 'force', 'allow_partial'):
            self.payload[field] = 'unsafe'
            with patch.object(restore, 'prepare') as call:
                response = await self.handler(self.request)
            self.assertEqual(response.status, 409)
            call.assert_not_called()
            del self.payload[field]

    async def test_maintenance_blocks_prompt_and_mutating_apis(self):
        routes_module.install_maintenance_guard(self.instance)
        guard = self.instance.app.middlewares[-1]
        handler = AsyncMock(return_value='ok')
        with patch.object(restore, 'maintenance', return_value=True):
            for path in ('/prompt', '/qbb_nodeops/current-restore/start', '/qbb_nodeops/ops/install'):
                response = await guard(types.SimpleNamespace(method='POST', path=path), handler)
                self.assertEqual(response.status, 503)
            self.assertEqual(await guard(types.SimpleNamespace(method='GET'), handler), 'ok')
        self.assertEqual(handler.await_count, 1)

    async def test_controlled_stop_checks_idle_again(self):
        with patch.object(routes_module.asyncio, 'sleep', new_callable=AsyncMock), patch.object(restore, 'idle', side_effect=ValueError('busy')), patch.object(restore, 'update') as update, patch.object(routes_module._thread, 'interrupt_main') as interrupt:
            await routes_module.controlled_stop(self.instance, 'a' * 32)
        update.assert_called_once()
        interrupt.assert_not_called()

    async def test_final_verification_after_real_local_service_probe(self):
        from aiohttp.test_utils import TestServer
        import os
        table = web.RouteTableDef()
        routes_module.register(table, self.instance)
        self.instance.app.add_routes(table)
        self.instance.address = '127.0.0.1'
        async with TestServer(self.instance.app, host='127.0.0.1') as server:
            url = str(server.make_url('/qbb_nodeops/current-restore'))
            with patch.object(restore, 'status', return_value={'id': 'a' * 32, 'status': 'awaiting_verification'}), patch.object(restore, 'context', return_value={'probe_url': url}), patch.object(restore, 'reconnect', return_value={'status': 'completed'}) as verify:
                await routes_module.reconnect(self.instance)
            verify.assert_called_once_with(service_ready=True)
