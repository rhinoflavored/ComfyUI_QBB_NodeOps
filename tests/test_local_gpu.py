import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

package = types.ModuleType('_local_gpu_tests')
package.__path__ = [str(Path(__file__).parents[1] / 'qbb_nodeops')]
sys.modules[package.__name__] = package
gpu = importlib.import_module(package.__name__ + '.local_gpu')


class LocalGpuTests(unittest.TestCase):
    def test_driver_header_formats(self):
        for label in ['CUDA Version:', 'CUDA UMD Version:']:
            with self.subTest(label=label), patch.object(gpu.subprocess, 'run', side_effect=[
                types.SimpleNamespace(stdout=f'| NVIDIA-SMI 610.88  {label} 13.3 |'),
                types.SimpleNamespace(stdout='NVIDIA GeForce RTX 5090, 12.0\n'),
            ]):
                machine = gpu.detect()
            self.assertEqual(machine['cuda_max'], 133)
            self.assertEqual(machine['devices'], [{'name': 'NVIDIA GeForce RTX 5090', 'capability': 12.0}])
            self.assertTrue(gpu.supports('cu128', machine))

    def test_unrecognized_cuda_header_remains_cpu_only(self):
        with patch.object(gpu.subprocess, 'run', side_effect=[
            types.SimpleNamespace(stdout='CUDA Version: N/A'),
            types.SimpleNamespace(stdout='NVIDIA GeForce RTX 5090, 12.0\n'),
        ]):
            machine = gpu.detect()
        self.assertEqual(machine['devices'], [])
        self.assertFalse(gpu.supports('cu128', machine))

    def test_5090_and_driver_limit(self):
        machine = {'devices': [{'capability': 12.0}], 'cuda_max': 130}
        for build, expected in [('cpu', True), ('cu118', False), ('cu126', False), ('cu128', True), ('cu130', True), ('cu132', False)]:
            self.assertEqual(gpu.supports(build, machine), expected)

    def test_old_gpu_and_no_gpu(self):
        machine = {'devices': [{'capability': 6.1}], 'cuda_max': 130}
        self.assertTrue(gpu.supports('cu126', machine))
        self.assertFalse(gpu.supports('cu128', machine))
        with patch.object(gpu.subprocess, 'run', side_effect=FileNotFoundError):
            machine = gpu.detect()
        self.assertFalse(gpu.supports('cu128', machine))
        self.assertTrue(gpu.supports('cpu', machine))

    def test_probe_and_filter(self):
        with patch.object(gpu.subprocess, 'run', side_effect=[types.SimpleNamespace(stdout='CUDA Version: 13.0'), types.SimpleNamespace(stdout='NVIDIA RTX 5090, 12.0\n')]):
            machine = gpu.detect()
        data = gpu.filter_catalog({'builds': ['cpu', 'cu118', 'cu128'], 'combinations': [{'build': 'cu118'}]}, machine)
        self.assertEqual(data['builds'], ['cpu', 'cu128'])
        self.assertEqual(data['combinations'], [])
