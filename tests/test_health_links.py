import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest

package = types.ModuleType('_health_link_tests')
package.__path__ = [str(Path(__file__).parents[1] / 'qbb_nodeops')]
sys.modules[package.__name__] = package
health = importlib.import_module(package.__name__ + '.health')


class HealthLinkTests(unittest.TestCase):
    def test_model_below_link_is_pinned_but_link_itself_stays_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            actual = root / 'actual'; actual.mkdir()
            model = actual / 'model.safetensors'; model.write_bytes(b'model')
            link = root / 'models'
            if os.name == 'nt':
                result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(actual)], capture_output=True)
                self.assertEqual(result.returncode, 0)
            else:
                link.symlink_to(actual, target_is_directory=True)
            try:
                pinned = health._cleanup_path(link / model.name, allow_parent_links=True)
                self.assertEqual(pinned, model.resolve())
                self.assertEqual(health._cleanup_path(pinned, recursive=True), pinned)
                with self.assertRaises(ValueError):
                    health._cleanup_path(link, allow_parent_links=True)
                with self.assertRaises(ValueError):
                    health._cleanup_path(root, recursive=True)
                with self.assertRaises(ValueError):
                    health._cleanup_path(link / model.name)
                self.assertTrue(model.exists())
            finally:
                # Remove only the test link, never its target tree.
                if os.name == 'nt': link.rmdir()
                else: link.unlink()


if __name__ == '__main__': unittest.main()
