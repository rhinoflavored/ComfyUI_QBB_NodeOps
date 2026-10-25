import importlib
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

package = types.ModuleType('_cuda_creation_tests')
package.__path__ = [str(Path(__file__).parents[1] / 'qbb_nodeops')]
sys.modules[package.__name__] = package
runtime = importlib.import_module(package.__name__ + '.runtime_install')
migration = importlib.import_module(package.__name__ + '.migration')
instances = importlib.import_module(package.__name__ + '.instances')


class CudaCreationTests(unittest.TestCase):
    def test_config_rejects_incompatible_gpu_even_when_skip_requested(self):
        with patch.object(runtime, 'detect', return_value={'devices': [], 'cuda_max': 0}), patch.object(runtime, 'resolve_config', return_value={'build': 'cpu'}):
            self.assertEqual(runtime.validate_config({'build': 'cpu'}), {'build': 'cpu'})
            with self.assertRaises(ValueError):
                runtime.validate_config({'build': 'cu128', 'verify_cuda': False})

    def test_install_only_skips_gpu_but_checks_packages_and_import(self):
        for verify in (True,):
            with self.subTest(verify=verify), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                uv = root / '.runtime-tools/bin' / ('uv.exe' if os.name == 'nt' else 'uv')
                uv.parent.mkdir(parents=True); uv.touch()
                calls = []
                def run(args, **kwargs):
                    calls.append(args)
                    gpu = any('torch.cuda.is_available' in str(arg) for arg in args)
                    return types.SimpleNamespace(returncode=1 if gpu else 0, stdout='no GPU' if gpu else str(root / '.python/python.exe'))
                config = {'build': 'cu128', 'python': '3.12', 'pins': {'torch': '2.7'}, 'verify_cuda': verify}
                with patch.object(runtime, 'validate_config', return_value=config), patch.object(runtime, 'pip_network', return_value=({}, ['--index-url', 'https://pypi.org/simple'], 'test')), patch.object(runtime.subprocess, 'run', side_effect=run):
                    if verify:
                        with self.assertRaises(runtime.CudaValidationError):
                            runtime.install_runtime(root, config, [], lambda line: None)
                    else:
                        runtime.install_runtime(root, config, [], lambda line: None)
                self.assertTrue(any(args[-2:] == ['pip', 'check'] for args in calls))
                self.assertTrue(any('print(torch.__version__' in str(args) for args in calls))
                self.assertEqual(any('torch.cuda.is_available' in str(args) for args in calls), verify)

    def test_failed_gpu_validation_preserves_install_and_writes_launcher(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / 'instance'
            def install(target, *args, **kwargs):
                (target / 'nodeops-runtime-constraints.txt').write_text('torch==2.7')
                raise runtime.CudaValidationError(target / '.venv/Scripts/python.exe', 'no GPU')
            def download(target):
                target.mkdir()
            with patch.object(migration, 'STATUS', migration.MigrationStatus()), patch.object(migration, '_download_comfyui', side_effect=download), patch.object(migration, 'install_runtime', side_effect=install), patch.object(migration, '_write_launcher', return_value=destination / 'run_comfyui.bat') as launcher, patch.object(instances, 'add_instance') as register, patch.object(migration, '_cleanup_failed_copy') as cleanup:
                migration._execute_new(destination, [], Path(temp), {'new_environment': {'build': 'cu128'}})
                self.assertEqual(migration.STATUS.status, 'needs_attention')
                self.assertEqual(migration.STATUS.error, 'no GPU')
                cleanup.assert_not_called(); launcher.assert_called_once(); register.assert_called_once()
                self.assertTrue((destination / 'python_env/nodeops-runtime-constraints.txt').is_file())


if __name__ == '__main__':
    unittest.main()
