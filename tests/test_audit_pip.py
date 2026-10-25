import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest

package = types.ModuleType('_audit_pip')
package.__path__ = [str(Path(__file__).parents[1] / 'qbb_nodeops')]
sys.modules[package.__name__] = package
environment = importlib.import_module(package.__name__ + '.recovery_environment')

class OfflinePipTests(unittest.TestCase):
    def test_version_inventory_does_not_check_pypi_for_pip_updates(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            if 'list' in args:
                self.assertIn('--disable-pip-version-check', args)
                return '[]'
            return ''
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment.install([], root, root / 'python.exe', run, lambda **kwargs: None)
            self.assertEqual((root / 'installed-packages.json').read_text(), '[]')
        self.assertEqual(sum('list' in call for call in calls), 1)
