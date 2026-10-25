import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

package = types.ModuleType('_git_prerequisite_tests')
package.__path__ = [str(Path(__file__).parents[1] / 'qbb_nodeops')]
sys.modules[package.__name__] = package
tools = importlib.import_module(package.__name__ + '.git_tools')
updates = importlib.import_module(package.__name__ + '.update_check')
migration = importlib.import_module(package.__name__ + '.migration')
records = importlib.import_module(package.__name__ + '.version_records')
market = importlib.import_module(package.__name__ + '.market')
rebuild = importlib.import_module(package.__name__ + '.rebuild')


class GitPrerequisiteTests(unittest.TestCase):
    def test_missing_git_fails_without_subprocess(self):
        with patch.object(tools.shutil, 'which', return_value=None), patch.object(updates.subprocess, 'run') as run:
            for operation in [lambda: migration._run_git(['status'], Path('.')),
                              lambda: records.git(Path('.'), 'status'),
                              lambda: market.install('https://github.com/example/repo'),
                              lambda: rebuild.run(['git', 'status'])]:
                with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, '系统 Git.*PATH'):
                    operation()
            code, _, message = updates._run_git(['status'], '.')
            self.assertEqual(code, 127)
            self.assertIn('系统 Git', message)
            run.assert_not_called()

    def test_missing_git_stops_batch_before_scheduling(self):
        with patch.object(tools.shutil, 'which', return_value=None), patch.object(updates, '_iter_plugin_dirs', return_value=[('a', 'a'), ('b', 'b')]), patch.object(updates, 'ThreadPoolExecutor') as pool, patch.object(updates, 'UPDATE_REPORT', updates.UpdateReport()), patch.object(updates, '_STARTED', False):
            report = updates.run_update_check(force=True)
            self.assertEqual(report.status, 'error')
            self.assertIn('系统 Git', report.message)
            pool.assert_not_called()
            with patch.object(updates, 'pull_one') as pull:
                result = updates.pull_many(['a', 'b'])
                self.assertFalse(result['ok'])
                self.assertIn('系统 Git', result['error'])
                pull.assert_not_called()

    def test_local_file_inventory_does_not_require_git(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(tools.shutil, 'which', return_value=None):
            root = Path(temp)
            self.assertEqual(records.repository(root)['kind'], 'local')
            (root / '.git').mkdir()
            with self.assertRaisesRegex(ValueError, '系统 Git'):
                records.repository(root)

    def test_install_is_detected_on_next_check(self):
        with patch.object(tools.shutil, 'which', side_effect=[None, 'C:/Git/cmd/git.exe']):
            with self.assertRaises(ValueError):
                tools.require_git()
            self.assertEqual(tools.require_git(), 'C:/Git/cmd/git.exe')


if __name__ == '__main__':
    unittest.main()
