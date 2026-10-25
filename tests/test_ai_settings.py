"""Credential routing tests use temporary settings and fake keys; no network."""
import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

package = types.ModuleType('_ai_settings_tests')
package.__path__ = [str(Path(__file__).parents[1]/'qbb_nodeops')]
sys.modules[package.__name__] = package
ai = importlib.import_module(package.__name__+'.ai_service')
communication = importlib.import_module(package.__name__+'.communication_test')


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'settings.json'
        for name, value in [('AI_SETTINGS_PATH', self.path), ('_CACHE', None)]:
            p = patch.object(ai, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(ai, '_json_request', return_value={'choices':[{'message':{'content':'OK'},'finish_reason':'stop'}]})
        self.request = p.start(); self.addCleanup(p.stop)
        ai.save_ai_settings({'base_url':'https://provider-a.example/v1', 'api_key':'fake-key-a'})

    def test_changed_address_blank_key_rejected_without_writes_or_requests(self):
        before = self.path.read_bytes()
        for url in ('https://provider-b.example/v1', 'https://provider-a.example/other', 'https://provider-a.example:8443/v1'):
            for key in (None, '', '   '):
                draft = {'base_url':url}
                if key is not None: draft['api_key'] = key
                with self.subTest(url=url, key=key):
                    with self.assertRaisesRegex(ValueError, 'API 地址已更改'):
                        ai.save_ai_settings(draft)
                    with self.assertRaisesRegex(ValueError, 'API 地址已更改'):
                        communication.test_ai(draft)
                    self.assertEqual(self.path.read_bytes(), before)
                    self.assertEqual(ai._load_raw()['base_url'], 'https://provider-a.example/v1')
        self.request.assert_not_called()

    def test_analysis_after_rejected_save_still_uses_original_address(self):
        with self.assertRaises(ValueError):
            ai.save_ai_settings({'base_url':'https://provider-b.example'})
        ai._chat('system', 'test')
        args = self.request.call_args.args
        self.assertEqual(args[0], 'https://provider-a.example/v1/chat/completions')
        self.assertEqual(args[2], 'fake-key-a')

    def test_same_normalized_address_retains_key(self):
        draft = {'base_url':'  https://provider-a.example/v1/  ', 'api_key':'  ', 'model':'new-model'}
        communication.test_ai(draft)
        self.assertEqual(self.request.call_args.args[2], 'fake-key-a')
        ai.save_ai_settings(draft)
        self.assertEqual(ai._load_raw()['api_key'], 'fake-key-a')

    def test_new_address_with_explicit_new_key_for_test_and_analysis(self):
        draft = {'base_url':'https://provider-b.example/v2', 'api_key':'fake-key-b'}
        before = self.path.read_bytes()
        communication.test_ai(draft)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.request.call_args.args[2], 'fake-key-b')
        ai.save_ai_settings(draft)
        ai._chat('system', 'test')
        self.assertEqual(self.request.call_args.args[0], 'https://provider-b.example/v2/chat/completions')
        self.assertEqual(self.request.call_args.args[2], 'fake-key-b')

    def test_explicit_clear_changes_address_but_blocks_remote_requests(self):
        ai.save_ai_settings({'base_url':'https://provider-b.example', 'clear_api_key':True})
        self.assertFalse(ai.public_settings()['api_key_configured'])
        for operation in (lambda:ai._chat('s','u'), lambda:communication.test_ai({})):
            with self.assertRaises(ValueError): operation()
        self.request.assert_not_called()
        self.assertNotIn('fake-key-a', self.path.read_text())

    def test_clear_takes_precedence_over_new_key(self):
        ai.save_ai_settings({'base_url':'https://provider-b.example', 'api_key':'fake-key-b', 'clear_api_key':True})
        self.assertEqual(ai._load_raw()['api_key'], '')

    def test_local_provider_does_not_bypass_remote_address_binding(self):
        with self.assertRaises(ValueError):
            ai.save_ai_settings({'provider':'local','base_url':'https://provider-b.example'})
        ai.save_ai_settings({'provider':'local','local_model':'local-test'})
        ai._chat('s','u')
        self.assertEqual(self.request.call_args.args[2], '')
        self.assertTrue(self.request.call_args.kwargs['local'])
        ai.save_ai_settings({'provider':'remote'})
        ai._chat('s','u')
        self.assertEqual(self.request.call_args.args[0], 'https://provider-a.example/v1/chat/completions')

    def test_binding_survives_reload_and_public_settings_never_return_key(self):
        ai._CACHE = None
        with self.assertRaises(ValueError):
            ai.save_ai_settings({'base_url':'https://provider-b.example'})
        self.assertNotIn('api_key', ai.public_settings())
        self.assertNotIn('fake-key-a', json.dumps(ai.public_settings()))

    def test_invalid_url_keeps_original_settings(self):
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            ai.save_ai_settings({'base_url':'http://remote.example', 'api_key':'fake-key-b'})
        self.assertEqual(self.path.read_bytes(), before)
        self.request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
