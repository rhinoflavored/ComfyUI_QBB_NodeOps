import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

package = types.ModuleType('_instance_availability_tests')
package.__path__ = [str(Path(__file__).parents[1] / 'qbb_nodeops')]
sys.modules[package.__name__] = package
instances = importlib.import_module(package.__name__ + '.instances')


class InstanceAvailabilityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.current, self.target = self.root / 'current', self.root / 'target'
        for directory in (self.current, self.target):
            (directory / 'custom_nodes').mkdir(parents=True)
        self.registry = self.root / 'registry.json'
        for name, value in [('CURRENT_COMFYUI_ROOT', self.current), ('REGISTRY_PATH', self.registry)]:
            context = patch.object(instances, name, value)
            context.start()
            self.addCleanup(context.stop)
        self.saved = instances.add_instance(str(self.target), 'Portable instance')

    def test_offline_listing_preserves_registry_and_reconnects(self):
        original = self.registry.read_bytes()
        detached = self.root / 'detached'
        self.target.rename(detached)
        for _ in range(2):
            offline = next(row for row in instances.list_instances() if row['id'] == self.saved['id'])
            self.assertFalse(offline['available'])
            self.assertEqual(offline['name'], self.saved['name'])
            self.assertEqual(offline['comfyui_root'], self.saved['comfyui_root'])
            self.assertEqual(self.registry.read_bytes(), original)
        with self.assertRaisesRegex(ValueError, '暂时无法访问'):
            instances.get_instance(self.saved['id'])
        detached.rename(self.target)
        self.assertTrue(instances.get_instance(self.saved['id'])['available'])
        self.assertEqual(instances.add_instance(str(self.target))['id'], self.saved['id'])
        self.assertEqual(self.registry.read_bytes(), original)

    def test_permission_error_does_not_unregister(self):
        original = self.registry.read_bytes()
        resolve = instances._resolve_comfyui
        def denied(path):
            if Path(path) == self.target:
                raise PermissionError('Access denied')
            return resolve(path)
        with patch.object(instances, '_resolve_comfyui', side_effect=denied):
            self.assertFalse(instances.list_instances()[1]['available'])
        self.assertEqual(self.registry.read_bytes(), original)
        self.assertTrue(instances.get_instance(self.saved['id'])['available'])

    def test_explicit_remove_works_while_offline(self):
        self.target.rename(self.root / 'detached')
        instances.remove_instance(self.saved['id'])
        self.assertEqual(len(instances.list_instances()), 1)
        with self.assertRaisesRegex(ValueError, 'instance not found'):
            instances.get_instance(self.saved['id'])


if __name__ == '__main__':
    unittest.main()
