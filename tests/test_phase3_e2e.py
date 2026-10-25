"""Real subprocess/pip/end-to-end restore, entirely in an isolated temporary venv."""
import ensurepip
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile


class OfflineWorkerE2E(unittest.TestCase):
    def test_real_downgrade_worker_restart_and_verification(self):
        self.scenario('1.0', '2.0')

    def test_real_upgrade_worker_restart_and_verification(self):
        self.scenario('2.0', '1.0')

    def test_real_same_version_different_source_restoration(self):
        self.scenario('1.0', '1.0')

    def test_real_package_rollback_after_later_file_failure(self):
        self.scenario('1.0', '2.0', rollback=True)

    def scenario(self, target_version, current_version, rollback=False):
        with tempfile.TemporaryDirectory(prefix='nodeops-phase3-e2e-') as temporary:
            root = Path(temporary).resolve()
            (root / 'ISOLATED_NODEOPS_TEST').touch()
            (root / 'scenario.json').write_text(json.dumps({'target': target_version, 'current': current_version, 'rollback': rollback}))
            core = root / 'ComfyUI'
            manager = core / 'custom_nodes' / 'NodeOps'
            shutil.copytree(Path(__file__).parents[1] / 'qbb_nodeops', manager / 'qbb_nodeops', ignore=shutil.ignore_patterns('__pycache__'))
            if rollback:
                # Failure injection exists only in this disposable copy, never in deployed code.
                module = manager / 'qbb_nodeops' / 'restore_materials.py'
                module.write_text(module.read_text(encoding='utf-8') + '\n_original_apply = apply_code\ndef apply_code(journal, action, current, target, payload):\n    if (target or current)["kind"] == "plugin":\n        raise OSError("isolated injected failure after package actions")\n    return _original_apply(journal, action, current, target, payload)\n', encoding='utf-8')
            for name in ('Local', 'Missing'):
                path = core / 'custom_nodes' / name
                path.mkdir()
                (path / '__init__.py').write_text('target = 1')
            shutil.copyfile(Path(__file__).with_name('phase3_fixture_main.py'), core / 'main.py')
            (core / 'extra_model_paths.yaml').write_text('target: state')
            (core / 'user').mkdir()
            (core / 'user' / 'personal.txt').write_text('must survive')
            wheels = root / 'wheels'
            wheels.mkdir()
            def wheel(name, version):
                filename = wheels / f'{name}-{version}-py3-none-any.whl'
                dist = f'{name}-{version}.dist-info'
                with zipfile.ZipFile(filename, 'w') as z:
                    z.writestr(name + '.py', f'__version__ = "{version}"\n')
                    z.writestr(dist + '/METADATA', f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n')
                    z.writestr(dist + '/WHEEL', 'Wheel-Version: 1.0\nGenerator: isolated-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
                    z.writestr(dist + '/RECORD', '')
                return filename
            for name, version in [('demo', '1.0'), ('demo', '2.0'), ('missing', '1.0'), ('extra', '1.0')]:
                wheel(name, version)
            (wheels / 'current').mkdir()
            shutil.copyfile(wheels / f'demo-{current_version}-py3-none-any.whl', wheels / 'current' / f'demo-{current_version}-py3-none-any.whl')
            def run(command, timeout=90):
                command = list(command)
                if 'pip' in command and '--disable-pip-version-check' not in command:
                    command.insert(command.index('pip') + 1, '--disable-pip-version-check')
                result = subprocess.run([str(x) for x in command], capture_output=True, text=True, errors='replace', timeout=timeout)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result
            run([sys.executable, '-B', '-m', 'venv', str(root / 'venv')])
            python = root / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            # Give bootstrap packages verifiable original source evidence, without network access.
            bundled = list((Path(ensurepip.__file__).parent / '_bundled').glob('*.whl'))
            self.assertTrue(bundled)
            run([python, '-B', '-m', 'pip', '--isolated', 'install', '--no-index', '--no-deps', '--force-reinstall',
                 *bundled, wheels / f'demo-{target_version}-py3-none-any.whl', wheels / 'missing-1.0-py3-none-any.whl'])
            with (root / 'initial.log').open('w', encoding='utf-8') as log:
                parent = subprocess.Popen([str(python), '-B', str(core / 'main.py')], cwd=core, stdout=log, stderr=log)
                parent.wait(timeout=90)
            if parent.returncode:
                # Failed handoff workers exit without authorization; wait for lock release before fixture cleanup.
                time.sleep(1)
            self.assertEqual(parent.returncode, 0, (root / 'initial.log').read_text(encoding='utf-8'))
            deadline = time.monotonic() + 90
            while not (root / 'result.json').exists() and time.monotonic() < deadline:
                time.sleep(0.1)
                active = list((manager / 'restore-tasks').glob('*/state.json'))
                if active and json.loads(active[0].read_text()).get('status') in ('awaiting_manual_restart', 'failed_rolled_back'):
                    run([python, '-B', core / 'main.py'])
                    break
                if active and json.loads(active[0].read_text()).get('status') in ('failed_rollback', 'interrupted', 'partial'):
                    break
            jobs = list((manager / 'restore-tasks').iterdir())
            self.assertEqual(len(jobs), 1)
            job = jobs[0]
            evidence = {p.name: p.read_text(encoding='utf-8', errors='replace') for p in job.iterdir() if p.suffix in ('.log',) or p.name == 'state.json'}
            self.assertTrue((root / 'result.json').exists(), json.dumps(evidence))
            result = json.loads((root / 'result.json').read_text())
            self.assertEqual(result['status'], 'failed_rolled_back' if rollback else 'completed', json.dumps(evidence))
            self.assertEqual((job / 'payload').exists(), rollback)
            self.assertEqual((job / 'backup').exists(), rollback)
            final = json.loads((root / 'independent-diff.json').read_text())
            self.assertEqual(final['state'], 'differences' if rollback else 'matched')
            self.assertEqual(final['change_count'] == 0, not rollback)
            self.assertEqual(final['unverified_count'], 0)
            if not rollback:
                receipt = json.loads((job / 'verification.json').read_text())
                self.assertEqual(receipt['status'], 'actions_applied')
                self.assertFalse(receipt['environment_rescanned'])
            self.assertEqual((core / 'user' / 'personal.txt').read_text(), 'must survive')
            self.assertEqual((core / 'custom_nodes' / 'Local' / '__init__.py').read_text(), 'current = 2' if rollback else 'target = 1')
            self.assertEqual((core / 'custom_nodes' / 'Missing').exists(), not rollback)
            self.assertEqual((core / 'custom_nodes' / 'Extra').exists(), rollback)
            installed = json.loads(run([python, '-B', '-m', 'pip', 'list', '--format=json', '--disable-pip-version-check']).stdout)
            versions = {r['name'].lower(): r['version'] for r in installed}
            self.assertEqual(versions['demo'], current_version if rollback else target_version)
            if rollback:
                self.assertNotIn('missing', versions)
                self.assertEqual(versions['extra'], '1.0')
            else:
                self.assertEqual(versions['missing'], '1.0')
                self.assertNotIn('extra', versions)
            # Persist compact evidence outside the disposable fixture; not the user's installation.
            evidence_dir = Path(os.environ.get('NODEOPS_TEST_EVIDENCE', Path(__file__).parents[1] / 'phase3-evidence'))
            evidence_dir.mkdir(parents=True, exist_ok=True)
            suffix = '-rollback' if rollback else ''
            (evidence_dir / f'worker-e2e-{current_version}-to-{target_version}{suffix}.json').write_text(json.dumps({'result': result,
                'target_statistics': json.loads((root / 'target-summary.json').read_text()),
                'actions': json.loads((root / 'plan.json').read_text())['actions'],
                'final_summary': {key: final[key] for key in ('state', 'change_count', 'unverified_count')}, 'packages': versions, 'logs': evidence}, ensure_ascii=False, indent=2), encoding='utf-8')
