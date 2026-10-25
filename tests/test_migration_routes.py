import asyncio
import importlib
import json
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch

from aiohttp import web

package = types.ModuleType('_migration_route_tests')
package.__path__ = [str(Path(__file__).parents[1] / 'qbb_nodeops')]
sys.modules[package.__name__] = package
migrate = importlib.import_module(package.__name__ + '.routes.migrate')


class MigrationRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_network_failure_preserves_local_hardware(self):
        gpu = {'devices': [{'name': 'RTX 5090', 'capability': 12.0}], 'cuda_max': 133, 'message': 'GPU detected'}
        request = types.SimpleNamespace(remote='127.0.0.1', host='localhost:8188', headers={'X-QBB-NodeOps': '1'}, query={})
        with patch.object(migrate, 'detect', return_value=gpu), patch.object(migrate, 'catalog', side_effect=RuntimeError('proxy unavailable')):
            response = await self.handlers['GET', '/qbb_nodeops/migration/runtime-catalog'](request)
        self.assertEqual(response.status, 502)
        data = json.loads(response.text)
        self.assertEqual(data['hardware'], gpu)
        self.assertIn('proxy unavailable', data['error'])

    def setUp(self):
        routes = web.RouteTableDef()
        migrate.register(routes)
        self.handlers = {(route.method, route.path): route.handler for route in routes}
        async def body():
            return {'path': 'example', 'name': 'Example'}
        self.request = types.SimpleNamespace(
            remote='127.0.0.1', host='localhost:8188',
            headers={'X-QBB-NodeOps': '1'}, content_type='application/json',
            rel_url=types.SimpleNamespace(query={'source_instance_id': 'example'}),
            match_info={'instance_id': 'example'}, json=body)

    async def test_scan_keeps_event_loop_responsive(self):
        loop = asyncio.get_running_loop()
        main_thread = threading.get_ident()
        heartbeat = threading.Event()
        def source(instance_id):
            self.assertNotEqual(threading.get_ident(), main_thread)
            self.assertEqual(instance_id, 'example')
            return {'comfyui_root': 'example'}
        def scan(path):
            self.assertNotEqual(threading.get_ident(), main_thread)
            self.assertEqual(path, Path('example'))
            # The event loop must run this callback while the scan is waiting.
            loop.call_soon_threadsafe(heartbeat.set)
            self.assertTrue(heartbeat.wait(2), 'scan blocked the event loop')
            return {'plugins': [], 'status': {'status': 'idle'}}
        with patch.object(migrate, 'get_instance', side_effect=source), patch.object(migrate, 'scan_migration_plugins', side_effect=scan):
            response = await self.handlers['GET', '/qbb_nodeops/migration/scan'](self.request)
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.text)['plugins'], [])

    async def test_instance_operations_run_off_event_loop(self):
        main_thread = threading.get_ident()
        def checked(result):
            def call(*args):
                self.assertNotEqual(threading.get_ident(), main_thread)
                return result
            return call
        with patch.object(migrate, 'list_instances', side_effect=checked([])), patch.object(migrate, 'add_instance', side_effect=checked({'id': 'example'})), patch.object(migrate, 'remove_instance', side_effect=checked(None)):
            for key in [('GET', '/qbb_nodeops/instances'), ('POST', '/qbb_nodeops/instances'), ('DELETE', '/qbb_nodeops/instances/{instance_id}')]:
                with self.subTest(route=key):
                    response = await self.handlers[key](self.request)
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(response.text)['instances'], [])

    async def test_scan_errors_keep_existing_response(self):
        with patch.object(migrate, 'get_instance', side_effect=ValueError('instance not found')):
            response = await self.handlers['GET', '/qbb_nodeops/migration/scan'](self.request)
        self.assertEqual(response.status, 400)
        self.assertEqual(json.loads(response.text), {'ok': False, 'error': 'instance not found'})

    async def test_remote_scan_is_rejected_before_work(self):
        self.request.remote = '192.0.2.1'
        with patch.object(migrate, 'get_instance') as lookup:
            response = await self.handlers['GET', '/qbb_nodeops/migration/scan'](self.request)
        self.assertEqual(response.status, 403)
        lookup.assert_not_called()


if __name__ == '__main__':
    unittest.main()
