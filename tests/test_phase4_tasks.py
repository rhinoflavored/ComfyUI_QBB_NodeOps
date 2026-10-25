import importlib
import json
import os
from pathlib import Path
import threading
import time
import types
import unittest
from unittest.mock import patch
from aiohttp import web
from test_phase1_snapshot import CaptureFixture, package, records, state, Distribution

tasks = importlib.import_module(package.__name__ + '.record_tasks')
restore = importlib.import_module(package.__name__ + '.current_restore')
routes_module = importlib.import_module(package.__name__ + '.routes.records')
restore_routes = importlib.import_module(package.__name__ + '.routes.current_restore')


class CaptureTasks(CaptureFixture):
    def setUp(self):
        super().setUp()
        p = patch.object(tasks, 'ROOT', self.manager / 'record-tasks')
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.finish_threads)

    def finish_threads(self):
        for thread in threading.enumerate():
            if thread.name.startswith('qbb-record-'):
                thread.join(10)

    def wait(self, task_id):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            value = tasks.status(task_id)
            if value['status'] not in tasks.RUNNING:
                return value
            time.sleep(.01)
        self.fail('Capture did not finish')

    def test_real_preview_create_progress_and_result(self):
        events = []
        original = tasks.atomic_json
        def write(path, value):
            if value.get('phase'):
                events.append(dict(value))
            original(path, value)
        with patch.object(tasks, 'atomic_json', side_effect=write):
            tasks.start('preview', 'a' * 32, {'options': {}})
            preview = self.wait('a' * 32)
            self.assertEqual(preview['status'], 'completed')
            tasks.start('create', 'b' * 32, {'label': 'test', 'preview_id': preview['result']['preview_id'], 'confirmed': True})
            result = self.wait('b' * 32)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(records.read(result['result']['id'])['label'], 'test')
        phases = {event['phase'] for event in events}
        self.assertTrue({'scan_core', 'scan_plugins', 'save_objects', 'verify_objects', 'verify_environment', 'publish', 'completed'} <= phases)
        counted = [e for e in events if e.get('total') is not None]
        self.assertTrue(counted)
        self.assertTrue(all(0 <= e['done'] <= e['total'] for e in counted))
        self.assertEqual(tasks.status()['id'], 'b' * 32)

    def test_duplicate_request_never_publishes_twice(self):
        preview = records.preview()
        payload = {'preview_id': preview['preview_id'], 'confirmed': True}
        tasks.start('create', 'a' * 32, payload)
        tasks.start('create', 'a' * 32, payload)
        first = self.wait('a' * 32)
        replay = tasks.start('create', 'a' * 32, payload)
        self.assertEqual(replay['result']['id'], first['result']['id'])
        self.assertEqual(len(list(self.store.glob('*.json'))), 1)
        with self.assertRaisesRegex(ValueError, 'another request'):
            tasks.start('create', 'a' * 32, {**payload, 'label': 'changed'})

    def test_unrestorable_is_saved_but_explicit_in_completion(self):
        self.distributions = [Distribution()]
        preview = records.preview()
        tasks.start('create', 'a' * 32, {'preview_id': preview['preview_id'], 'confirmed': True})
        value = self.wait('a' * 32)
        self.assertEqual(value['status'], 'completed')
        self.assertEqual(value['result']['unrestorable'][0]['recoverability']['reason'], 'source_unknown')

    def test_stale_preview_reports_failure_and_no_record(self):
        preview = records.preview()
        (self.local / '__init__.py').write_text('changed')
        tasks.start('create', 'a' * 32, {'preview_id': preview['preview_id'], 'confirmed': True})
        self.assertEqual(self.wait('a' * 32)['status'], 'failed')
        self.assertEqual(list(self.store.glob('*.json')), [])

    def test_create_requires_confirmation_and_safe_request_id(self):
        with self.assertRaisesRegex(ValueError, 'Confirm'):
            tasks.start('create', 'a' * 32, {'preview_id': 'none'})
        with self.assertRaisesRegex(ValueError, 'Invalid'):
            tasks.start('preview', '../outside', {})
        with self.assertRaisesRegex(ValueError, 'Invalid'):
            tasks.start('preview', 'a' * 32, {'core': str(self.root)})
        self.assertFalse(tasks.ROOT.exists())

    def test_concurrent_task_is_rejected(self):
        gate = threading.Event()
        try:
            with patch.object(records, 'preview', side_effect=lambda value: gate.wait(5)):
                tasks.start('preview', 'a' * 32, {})
                with self.assertRaisesRegex(ValueError, 'already running'):
                    tasks.start('preview', 'b' * 32, {})
                gate.set()
                self.wait('a' * 32)
        finally:
            gate.set()

    def test_interrupted_capture_not_claimed_success(self):
        tasks.ROOT.mkdir()
        tasks.atomic_json(tasks.path('a' * 32), {'id': 'a' * 32, 'status': 'running', 'pid': os.getpid(), 'birth': 'old-process'})
        self.assertEqual(tasks.status('a' * 32)['status'], 'interrupted')

    def test_phase_override_cleared_after_exception(self):
        with self.assertRaises(ValueError), tasks.phase('verify_environment'):
            raise ValueError('test')
        self.assertIsNone(tasks.LOCAL.phase)

    def test_json_atomic_replace_retries_brief_windows_reader_lock(self):
        helper = importlib.import_module(package.__name__ + '.rebuild_executor')
        destination = self.root / 'status.json'
        original = Path.replace
        attempts = []
        def replace(source, target):
            attempts.append(source)
            if len(attempts) < 3:
                raise PermissionError('brief sharing violation')
            return original(source, target)
        with patch.object(Path, 'replace', replace), patch.object(helper.time, 'sleep'):
            helper.atomic_json(destination, {'status': 'completed'})
        self.assertEqual(len(attempts), 3)
        self.assertEqual(helper.read_json(destination), {'status': 'completed'})

    def test_json_permanent_permission_error_is_not_hidden(self):
        helper = importlib.import_module(package.__name__ + '.rebuild_executor')
        with patch.object(Path, 'read_text', side_effect=PermissionError('denied')) as read, patch.object(helper.time, 'sleep'):
            with self.assertRaises(PermissionError):
                helper.read_json(self.root / 'status.json')
        self.assertEqual(read.call_count, 20)


class TaskRoutes(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        table = web.RouteTableDef()
        routes_module.register(table)
        restore_routes.register(table, types.SimpleNamespace(app=web.Application()))
        self.routes = {(r.method, r.path): r.handler for r in table}
        self.payload = {'request_id': 'a' * 32, 'payload': {}}
        async def body():
            return self.payload
        self.request = types.SimpleNamespace(remote='127.0.0.1', host='localhost:8188',
            headers={'X-QBB-NodeOps': '1'}, content_type='application/json',
            match_info={'action': 'preview'}, json=body)

    async def test_async_start_and_status_are_local_only(self):
        handler = self.routes['POST', '/qbb_nodeops/record-tasks/{action}']
        with patch.object(tasks, 'start', return_value={'id': 'a' * 32, 'status': 'queued'}) as call:
            result = await handler(self.request)
        self.assertEqual(result.status, 202)
        call.assert_called_once_with('preview', 'a' * 32, {})
        self.request.remote = '192.0.2.1'
        with patch.object(tasks, 'start') as call:
            self.assertEqual((await handler(self.request)).status, 403)
        call.assert_not_called()
        self.assertEqual((await self.routes['GET', '/qbb_nodeops/record-tasks'](self.request)).status, 403)

    async def test_unexpected_payload_rejected(self):
        self.payload['target'] = '/unsafe'
        with patch.object(tasks, 'start') as call:
            response = await self.routes['POST', '/qbb_nodeops/record-tasks/{action}'](self.request)
        self.assertEqual(response.status, 409)
        call.assert_not_called()

    async def test_restore_details_no_mutation(self):
        self.request.match_info = {'job_id': 'a' * 32}
        with patch.object(restore, 'describe', return_value={'status': 'partial', 'verification': {'status': 'blocked'}}) as describe:
            result = await self.routes['GET', '/qbb_nodeops/current-restore/{job_id}'](self.request)
        self.assertEqual(result.status, 200)
        describe.assert_called_once_with('a' * 32)
        self.assertEqual(json.loads(result.text)['status'], 'partial')
