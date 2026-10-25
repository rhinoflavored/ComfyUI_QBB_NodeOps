import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
import json

package = types.ModuleType('_runtime_source_tests')
package.__path__ = [str(Path(__file__).parents[1] / 'qbb_nodeops')]
sys.modules[package.__name__] = package
catalog = importlib.import_module(package.__name__ + '.runtime_catalog')


class RuntimeSourceTests(unittest.TestCase):
    def test_primary_failure_uses_backup(self):
        with patch.object(catalog, 'fetch', side_effect=[RuntimeError('404'), '{}']) as fetch, patch.object(catalog, 'python_versions', return_value={'3.12'}):
            self.assertEqual(catalog.available_python_versions(True), {'3.12'})
            self.assertEqual([c.args for c in fetch.call_args_list], [(source, True) for source in catalog.PYTHON_SOURCES])

    def test_invalid_primary_metadata_uses_backup(self):
        with patch.object(catalog, 'fetch', side_effect=['not json', '{}']), patch.object(catalog, 'python_versions', return_value={'3.12'}):
            self.assertEqual(catalog.available_python_versions(), {'3.12'})

    def test_404_does_not_blame_proxy_port(self):
        opener = MagicMock()
        opener.open.side_effect = HTTPError(catalog.PYTHONS, 404, 'Not Found', {}, None)
        with patch.object(catalog, 'load_settings', return_value={}), patch.object(catalog, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(RuntimeError, '不能据此判断代理端口错误'):
                catalog.fetch(catalog.PYTHONS, True)

    def test_cache_survives_memory_reset(self):
        result = {'builds': ['cpu'], 'combinations': [{'build': 'cpu', 'python': '3.12', 'torch': '2.7.1', 'pins': {'torch': '2.7.1'}}]}
        with tempfile.TemporaryDirectory() as folder, patch.object(catalog, 'CACHE_DIR', Path(folder)), patch.object(catalog, '_catalog_results', {}), patch.object(catalog, 'load_settings', return_value={}), patch.object(catalog, '_collect_catalog', return_value=result):
            self.assertEqual(catalog.catalog(), result)
            catalog._catalog_results.clear()
            with patch.object(catalog, '_collect_catalog', side_effect=RuntimeError('offline')):
                cached = catalog.catalog()
            self.assertTrue(cached['stale'])
            self.assertEqual(cached['combinations'], result['combinations'])

    def test_expired_corrupt_and_wrong_build_cache_rejected(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(catalog, 'CACHE_DIR', Path(folder)):
            path = catalog.catalog_cache_path('cpu')
            for content in ['invalid', json.dumps({'saved_at': 0, 'data': {}}), json.dumps({'saved_at': catalog.time.time(), 'data': {'builds': ['cpu'], 'combinations': [{'build': 'cu128'}]}})]:
                path.write_text(content, encoding='utf-8')
                self.assertIsNone(catalog.read_catalog_cache('cpu'))
            with self.assertRaises(ValueError):
                catalog.catalog_cache_path('../escape')


if __name__ == '__main__':
    unittest.main()
