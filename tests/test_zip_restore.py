"""ZIP preparation, offline writes and rollback in a disposable Python layout."""
import base64
import csv
import hashlib
import importlib
import importlib.metadata
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch
import zipfile

from test_phase1_snapshot import CaptureFixture, package, state, records

restore = importlib.import_module(package.__name__ + '.current_restore')
installed = restore.restore_installed
diff = restore.diff
material = restore.material


class ZipRestoreTests(CaptureFixture):
    def test_restore_saved_runtime_data_files_from_zip(self):
        self.write_package()
        relative = ['share/jupyter/demo.json', 'etc/jupyter/demo.json', 'include/demo.h', 'tests/demo.txt', 'SPIRV-Tools/cmake/demo.cmake', 'msvcp140.dll']
        for name in relative:
            path = self.python / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'saved package data')
        with (self.library / 'demo-1.0.dist-info/RECORD').open('a', newline='', encoding='utf-8') as stream:
            csv.writer(stream).writerows([['../../' + name, '', ''] for name in relative])
        target = self.save()
        for name in relative:
            (self.python / name).write_bytes(b'changed data')
        job, value = self.prepare(target)
        self.assertEqual(self.execute(job, value), 'applied')
        for name in relative:
            self.assertEqual((self.python / name).read_bytes(), b'saved package data')
        self.assert_matches(target)

    def test_export_import_fresh_store_then_restore_saved_zip_contents(self):
        archives = importlib.import_module(package.__name__ + '.record_archives')
        binary = self.write_package()
        target = self.save()
        coverage = state.summary(target)['scopes']
        self.assertEqual(coverage['code']['status'], 'recorded')
        self.assertEqual(coverage['python_environment']['status'], 'installed_files_saved')
        bundle = self.root / 'record.zip'
        preview = archives.preview_export(target['id'], str(bundle))
        archives.export_bundle(target['id'], str(bundle), True, token=preview['token'])
        original_store = self.store
        self.store = self.root / 'imported-store'
        self.store.mkdir()
        with patch.object(records, 'directory', side_effect=lambda: self.store):
            preview = archives.preview_import(str(bundle))
            result = archives.import_bundle(preview['token'], True)
            self.assertFalse(result['restoration_started'])
            imported = records.read(result['id'])
            shutil.rmtree(original_store)
            binary.write_bytes(b'changed after export')
            (self.local / '__init__.py').write_text('changed plugin')
            with (patch.object(material, 'run', side_effect=AssertionError('No subprocess')),
                  patch.object(material, 'download', side_effect=AssertionError('No downloads'))):
                job, value = self.prepare(imported)
                self.assertEqual(binary.read_bytes(), b'changed after export')
                self.assertEqual(self.execute(job, value), 'applied')
            self.assertEqual(binary.read_bytes(), b'target binary')
            self.assertEqual((self.local / '__init__.py').read_text(), 'original')
            self.assert_matches(imported)
            self.assertEqual(self.finalize(job)['status'], 'completed')
            self.assertFalse((job / 'payload').exists())

    def test_record_summary_does_not_treat_unknown_origin_as_missing_code(self):
        self.write_package()
        target = self.save()
        self.assertTrue(any(r['recoverability']['reason'] == 'source_unknown' for r in target['resources'] if r['kind'] == 'package'))
        coverage = state.summary(target)['scopes']
        self.assertEqual(coverage['code']['status'], 'recorded')
        self.assertEqual(coverage['python_environment']['status'], 'installed_files_saved')
        target['backup']['components']['package:demo']['installed_files_saved'] = False
        self.assertEqual(state.summary(target)['scopes']['python_environment']['status'], 'installed_files_partial')
        target['backup']['version'] = 1
        self.assertEqual(state.summary(target)['scopes']['python_environment']['status'], 'package_management_only')

    def setUp(self):
        super().setUp()
        self.python = self.root / 'python'
        self.library = self.python / 'Lib/site-packages'
        self.library.mkdir(parents=True)
        self.scripts = self.python / 'Scripts'
        self.scripts.mkdir()
        self.layout = {'root': str(self.python), 'libraries': [str(self.library)], 'scripts': str(self.scripts)}
        for patched in [patch.object(installed, 'layout', return_value=self.layout),
                        patch.object(state.local_archives, 'package_inventories', side_effect=self.inventories)]:
            patched.start()
            self.addCleanup(patched.stop)

    def inventories(self, distributions, manager):
        return self.original_inventories(distributions, manager, self.python, [self.library], self.scripts)

    original_inventories = staticmethod(state.local_archives.package_inventories)

    def scan(self, label='', read_only=False, **kwargs):
        distributions = list(importlib.metadata.distributions(path=[str(self.library)]))
        return self.original_scan(self.core, [self.nodes], self.manager, distributions, self.store, label, read_only)

    def runtime(self):
        return {'core': str(self.core), 'manager': str(self.manager), 'plugin_roots': [str(self.nodes)],
                'store': str(self.store), 'python': sys.executable, 'cwd': str(self.core),
                'argv': [sys.executable, str(self.core / 'main.py')],
                'parent_pid': os.getpid(), 'parent_birth': restore.restore_process.identity(os.getpid())}

    def write_package(self, name='demo', version='1.0', value=b'target binary'):
        metadata = f'{name}-{version}.dist-info'
        entries = {f'{name}/__init__.py': b'answer = 1\n', f'{name}/native.pyd': value,
                   f'{metadata}/METADATA': f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n'.encode(),
                   f'../../Scripts/{name}.exe': b'launcher:' + value}
        rows = []
        for relative, content in entries.items():
            path = self.library / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            sha = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b'=').decode()
            rows.append([relative, 'sha256=' + sha, str(len(content))])
        record = self.library / metadata / 'RECORD'
        with record.open('w', newline='', encoding='utf-8') as stream:
            csv.writer(stream).writerows([*rows, [f'{metadata}/RECORD', '', '']])
        return self.library / name / 'native.pyd'

    def remove_package(self, name='demo', version='1.0'):
        shutil.rmtree(self.library / name)
        shutil.rmtree(self.library / f'{name}-{version}.dist-info')
        (self.scripts / (name + '.exe')).unlink()

    def prepare(self, target):
        plan = diff.inspect(target['id'])
        self.assertFalse(plan['blockers'], plan)
        self.assertTrue(plan['execution_ready'], plan)
        prepared = restore.prepare(target['id'], plan['fingerprint'], True, runtime=self.runtime())
        job = restore.job_path(prepared['id'], self.manager)
        return job, restore.frozen(job, prepared['plan_hash'])

    def execute(self, job, value):
        with patch.object(restore.restore_process, 'identity', return_value=None):
            return restore.execute(job, value)

    def finalize(self, job, outcome='applied'):
        restore.update(job, status='awaiting_verification', restarted_pid=os.getpid(),
                       restarted_birth=restore.restore_process.identity(os.getpid()), outcome=outcome)
        return restore.reconnect(runtime=self.runtime(), job_id=job.name, service_ready=True)

    def assert_matches(self, target):
        current = restore.scan(self.runtime())
        result = diff.compare(current, target)
        self.assertEqual(result['state'], 'matched', result)
        self.assertEqual(result['unverified_count'], 0)

    def test_same_version_unknown_binary_restored_without_pip_or_network(self):
        binary = self.write_package()
        target = self.save()
        binary.write_bytes(b'current binary')
        with patch.object(material, 'run', side_effect=AssertionError('No subprocess')), patch.object(material, 'download', side_effect=AssertionError('No downloads')), patch.object(zipfile.ZipFile, 'extractall', side_effect=AssertionError('No extractall')):
            job, value = self.prepare(target)
            self.assertEqual(binary.read_bytes(), b'current binary', 'Preparation must not mutate the environment')
            rollback = Path(value['actions'][0]['file_restore']['rollback']) / 'Lib/site-packages/demo/native.pyd'
            self.assertEqual(rollback.read_bytes(), b'current binary')
            self.assertEqual(self.execute(job, value), 'applied')
        self.assertEqual(binary.read_bytes(), b'target binary')
        self.assert_matches(target)
        self.assertEqual(self.finalize(job)['status'], 'completed')
        self.assertFalse((job / 'backup').exists())
        self.assertFalse((job / 'payload').exists())

    def test_missing_package_restores_metadata_binary_and_script(self):
        self.write_package()
        target = self.save()
        self.remove_package()
        job, value = self.prepare(target)
        self.assertEqual(self.execute(job, value), 'applied')
        self.assertEqual((self.scripts / 'demo.exe').read_bytes(), b'launcher:target binary')
        self.assert_matches(target)
        self.assertEqual(self.finalize(job)['status'], 'completed')

    def test_known_missing_installed_file_can_be_repaired(self):
        binary = self.write_package()
        target = self.save()
        binary.unlink()
        job, value = self.prepare(target)
        self.assertEqual(self.execute(job, value), 'applied')
        self.assertEqual(binary.read_bytes(), b'target binary')
        self.assert_matches(target)

    def test_extra_package_removed_but_unlisted_namespace_data_preserved(self):
        target = self.save()
        self.write_package()
        keep = self.library / 'demo/user-file.txt'
        keep.write_bytes(b'keep unlisted data')
        job, value = self.prepare(target)
        self.assertEqual(self.execute(job, value), 'applied')
        self.assertEqual(keep.read_bytes(), b'keep unlisted data')
        self.assertFalse((self.library / 'demo-1.0.dist-info').exists())
        self.assertFalse((self.scripts / 'demo.exe').exists())
        self.assert_matches(target)

    def test_version_change_replaces_dist_info_without_source_rebuild(self):
        self.write_package()
        target = self.save()
        self.remove_package()
        self.write_package(version='2.0', value=b'current v2')
        job, value = self.prepare(target)
        self.assertEqual(self.execute(job, value), 'applied')
        self.assertFalse((self.library / 'demo-2.0.dist-info').exists())
        self.assertTrue((self.library / 'demo-1.0.dist-info').exists())
        self.assert_matches(target)

    def test_later_failure_rolls_back_version_metadata_binary_and_cache(self):
        self.write_package()
        target = self.save()
        self.remove_package()
        self.write_package(version='2.0', value=b'original v2')
        module = self.library / 'demo/__init__.py'
        module.write_bytes(b'answer = 2\n')
        cache = module.parent / '__pycache__/__init__.cpython-test.pyc'
        cache.parent.mkdir()
        cache.write_bytes(b'original bytecode')
        (self.local / '__init__.py').write_text('current plugin')
        before = material.inventory(self.python)
        job, value = self.prepare(target)
        with patch.object(material, 'apply_code', side_effect=ValueError('injected later failure')):
            self.assertEqual(self.execute(job, value), 'rolled_back')
        self.assertEqual(material.inventory(self.python), before)
        self.assertTrue((job / 'backup').exists())
        self.assertEqual(self.finalize(job, 'rolled_back')['status'], 'failed_rolled_back')
        self.assertTrue((job / 'payload').exists())

    def test_new_package_failure_removes_only_created_files_and_directories(self):
        self.write_package()
        target = self.save()
        self.remove_package()
        (self.local / '__init__.py').write_text('current plugin')
        before = material.inventory(self.python)
        job, value = self.prepare(target)
        with patch.object(material, 'apply_code', side_effect=ValueError('injected later failure')):
            self.assertEqual(self.execute(job, value), 'rolled_back')
        self.assertEqual(material.inventory(self.python), before)

    def test_changed_python_module_invalidates_its_bytecode_only(self):
        self.write_package()
        target = self.save()
        module = self.library / 'demo/__init__.py'
        module.write_bytes(b'answer = 2\n')
        cache = module.parent / '__pycache__'
        cache.mkdir()
        (cache / '__init__.cpython-test.pyc').write_bytes(b'stale cache')
        unrelated = cache / 'unrelated.cpython-test.pyc'
        unrelated.write_bytes(b'unrelated')
        job, value = self.prepare(target)
        self.assertEqual(self.execute(job, value), 'applied')
        self.assertFalse((cache / '__init__.cpython-test.pyc').exists())
        self.assertEqual(unrelated.read_bytes(), b'unrelated')
        self.assert_matches(target)

    def test_corrupt_zip_is_blocked_before_job_and_live_mutation(self):
        binary = self.write_package()
        target = self.save()
        binary.write_bytes(b'current')
        row = self.resource(target, 'package:demo')
        entry = target['artifacts'][row['archive_ref']]['files'][0]
        files_path = state.files.object_path(self.store, entry['sha256'])
        files_path.write_bytes(b'corrupt ZIP')
        plan = diff.inspect(target['id'])
        with self.assertRaisesRegex(ValueError, 'Restore blocked'):
            restore.prepare(target['id'], plan['fingerprint'], True, runtime=self.runtime())
        self.assertFalse((self.manager / 'restore-tasks').exists())
        self.assertEqual(binary.read_bytes(), b'current')

    def test_payload_tamper_prevents_execution(self):
        binary = self.write_package()
        target = self.save()
        binary.write_bytes(b'current')
        job, value = self.prepare(target)
        path = Path(value['actions'][0]['file_restore']['content']) / 'Lib/site-packages/demo/native.pyd'
        path.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'payload changed'):
            restore.frozen(job, restore.status(job.name, self.manager)['plan_hash'])
        self.assertEqual(binary.read_bytes(), b'current')

    def test_change_after_preparation_prevents_offline_mutation(self):
        binary = self.write_package()
        target = self.save()
        binary.write_bytes(b'current')
        job, value = self.prepare(target)
        binary.write_bytes(b'user edit')
        with self.assertRaisesRegex(ValueError, 'changed since preparation'):
            self.execute(job, value)
        self.assertEqual(binary.read_bytes(), b'user edit')

    def test_rollback_refuses_to_overwrite_concurrent_user_change(self):
        binary = self.write_package()
        target = self.save()
        binary.write_bytes(b'current')
        (self.local / '__init__.py').write_text('current plugin')
        job, value = self.prepare(target)
        def failure(*args):
            binary.write_bytes(b'user edit during execution')
            raise ValueError('later failure')
        with patch.object(material, 'apply_code', side_effect=failure):
            with self.assertRaisesRegex(ValueError, 'Rollback incomplete'):
                self.execute(job, value)
        self.assertEqual(binary.read_bytes(), b'user edit during execution')
        self.assertEqual(restore.status(job.name, self.manager)['status'], 'failed_rollback')
        self.assertTrue((job / 'backup').exists())

    def test_unrecorded_metadata_files_prevent_package_removal(self):
        target = self.save()
        self.write_package()
        keep = self.library / 'demo-1.0.dist-info/keep.txt'
        keep.write_text('keep')
        plan = diff.inspect(target['id'])
        with self.assertRaisesRegex(ValueError, 'Unrecorded metadata contents'):
            restore.prepare(target['id'], plan['fingerprint'], True, runtime=self.runtime())
        self.assertEqual(keep.read_text(), 'keep')
        self.assertTrue((self.library / 'demo/native.pyd').exists())

    def test_unowned_existing_target_file_is_not_overwritten(self):
        self.write_package()
        target = self.save()
        self.remove_package()
        binary = self.library / 'demo/native.pyd'
        binary.parent.mkdir()
        binary.write_bytes(b'unowned content')
        plan = diff.inspect(target['id'])
        with self.assertRaisesRegex(ValueError, 'unrecorded content'):
            restore.prepare(target['id'], plan['fingerprint'], True, runtime=self.runtime())
        self.assertEqual(binary.read_bytes(), b'unowned content')

    def test_runtime_and_manager_paths_rejected_at_destination_boundary(self):
        for relative in ['python.exe', 'Lib/os.py', 'Scripts/python.exe', 'Scripts/pip.exe', 'Lib/site-packages/pip/__init__.py', '../outside.txt']:
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                installed.destination(relative, self.layout, self.manager)
        with self.assertRaisesRegex(ValueError, 'protected manager'):
            installed.destination('Lib/site-packages/manager/secret.py', self.layout, self.library / 'manager')

    def test_bytecode_change_after_preparation_is_detected(self):
        self.write_package()
        target = self.save()
        module = self.library / 'demo/__init__.py'
        module.write_bytes(b'answer = 2\n')
        cache = module.parent / '__pycache__/__init__.cpython-test.pyc'
        cache.parent.mkdir()
        cache.write_bytes(b'before')
        job, value = self.prepare(target)
        cache.write_bytes(b'after')
        with self.assertRaisesRegex(ValueError, 'bytecode changed'):
            self.execute(job, value)
        self.assertEqual(module.read_bytes(), b'answer = 2\n')

    def test_zip_code_fallback_works_when_original_objects_are_missing(self):
        target = self.save()
        row = self.resource(target, 'plugin:custom_nodes/LocalPlugin')
        for entry in target['artifacts'][row['artifact_ref']]['files']:
            state.files.object_path(self.store, entry['sha256']).unlink()
        (self.local / '__init__.py').write_text('current')
        job, value = self.prepare(target)
        self.assertEqual(self.execute(job, value), 'applied')
        self.assertEqual((self.local / '__init__.py').read_text(), 'original')
        self.assert_matches(target)

    def test_low_space_preparation_never_modifies_live_files(self):
        binary = self.write_package()
        target = self.save()
        binary.write_bytes(b'current')
        plan = diff.inspect(target['id'])
        with patch.object(installed.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 0})()):
            with self.assertRaisesRegex(ValueError, 'Insufficient'):
                restore.prepare(target['id'], plan['fingerprint'], True, runtime=self.runtime())
        self.assertEqual(binary.read_bytes(), b'current')

    def test_new_bytecode_appearing_after_preparation_prevents_mutation(self):
        self.write_package()
        target = self.save()
        module = self.library / 'demo/__init__.py'
        module.write_bytes(b'answer = 2\n')
        job, value = self.prepare(target)
        cache = module.parent / '__pycache__/__init__.cpython-new.pyc'
        cache.parent.mkdir()
        cache.write_bytes(b'new cache')
        with self.assertRaisesRegex(ValueError, 'bytecode inventory changed'):
            self.execute(job, value)
        self.assertEqual(module.read_bytes(), b'answer = 2\n')

    def test_readonly_target_binary_can_be_applied_and_rolled_back(self):
        binary = self.write_package()
        binary.chmod(0o444)
        target = self.save()
        binary.chmod(0o666)
        binary.write_bytes(b'current writable binary')
        (self.local / '__init__.py').write_text('current plugin')
        before = material.inventory(self.python)
        job, value = self.prepare(target)
        with patch.object(material, 'apply_code', side_effect=ValueError('later failure')):
            self.assertEqual(self.execute(job, value), 'rolled_back')
        self.assertEqual(material.inventory(self.python), before)

    def test_package_transactions_do_not_move_files_across_volumes(self):
        binary = self.write_package()
        target = self.save()
        binary.write_bytes(b'current')
        (self.local / '__init__.py').write_text('current plugin')
        before = material.inventory(self.python)
        job, value = self.prepare(target)
        with patch.object(Path, 'rename', side_effect=OSError('Cross-device rename refused')), patch.object(material, 'apply_code', side_effect=ValueError('later failure')):
            self.assertEqual(self.execute(job, value), 'rolled_back')
        self.assertEqual(material.inventory(self.python), before)

    def test_shared_installed_file_prevents_extra_package_removal(self):
        self.write_package(name='one')
        shared = self.library / 'shared.py'
        shared.write_text('shared')
        with (self.library / 'one-1.0.dist-info/RECORD').open('a', newline='') as stream:
            csv.writer(stream).writerow(['shared.py', '', ''])
        target = self.save()
        self.write_package(name='two')
        with (self.library / 'two-1.0.dist-info/RECORD').open('a', newline='') as stream:
            csv.writer(stream).writerow(['shared.py', '', ''])
        plan = diff.inspect(target['id'])
        self.assertTrue(plan['blockers'])
        with self.assertRaisesRegex(ValueError, 'Restore blocked'):
            restore.prepare(target['id'], plan['fingerprint'], True, runtime=self.runtime())
        self.assertEqual(shared.read_text(), 'shared')
        self.assertTrue((self.library / 'two/native.pyd').exists())

    def test_zip_configuration_fallback_restores_a_single_file(self):
        path = self.core / 'extra_model_paths.yaml'
        path.write_text('models: target')
        target = self.save()
        row = self.resource(target, 'config:extra_model_paths.yaml')
        entry = target['artifacts'][row['artifact_ref']]['files'][0]
        state.files.object_path(self.store, entry['sha256']).unlink()
        path.write_text('models: current')
        job, value = self.prepare(target)
        self.assertEqual(self.execute(job, value), 'applied')
        self.assertEqual(path.read_text(), 'models: target')
        self.assert_matches(target)

    def test_zip_core_fallback_preserves_user_models_and_manager(self):
        target = self.save()
        row = self.resource(target, 'core')
        for entry in target['artifacts'][row['artifact_ref']]['files']:
            state.files.object_path(self.store, entry['sha256']).unlink()
        (self.core / 'main.py').write_text('current main')
        model = self.core / 'models/model.bin'
        model.parent.mkdir()
        model.write_bytes(b'preserved model')
        job, value = self.prepare(target)
        self.assertEqual(self.execute(job, value), 'applied')
        self.assertEqual((self.core / 'main.py').read_text(), 'pass')
        self.assertEqual(model.read_bytes(), b'preserved model')
        self.assertEqual((self.manager / '__init__.py').read_text(), 'manager')
        self.assert_matches(target)

    def test_mutation_between_file_operations_is_not_overwritten(self):
        binary = self.write_package()
        target = self.save()
        binary.write_bytes(b'current')
        job, value = self.prepare(target)
        original = installed.apply_package
        def concurrent(journal, spec):
            binary.write_bytes(b'user edit just before write')
            original(journal, spec)
        with patch.object(installed, 'apply_package', side_effect=concurrent):
            self.assertEqual(self.execute(job, value), 'rolled_back')
        self.assertEqual(binary.read_bytes(), b'user edit just before write')

    def test_handoff_accepts_zip_without_original_artifact_and_preserves_flags(self):
        binary = self.write_package()
        target = self.save()
        binary.write_bytes(b'current')
        plan = diff.inspect(target['id'])
        runtime = {**self.runtime(), 'python_flags': ['-s', '-E']}
        prepared = restore.prepare(target['id'], plan['fingerprint'], True, runtime=runtime)
        job = restore.job_path(prepared['id'], self.manager)
        restore.atomic_json(job / 'worker-ready.json', {'pid': 123, 'plan_hash': prepared['plan_hash']})
        process = type('Process', (), {'pid': 123, 'poll': lambda self: None})()
        with patch.object(restore, 'context', return_value=runtime), patch.object(restore, 'idle'), patch.object(restore.subprocess, 'Popen', return_value=process) as spawn, patch.object(restore.restore_process, 'spawned_process', return_value=True):
            result = restore.start(job.name, prepared['plan_hash'], True)
        self.assertEqual(result['status'], 'waiting_for_exit')
        self.assertEqual(spawn.call_args.args[0][:4], [sys.executable, '-s', '-E', '-B'])
        self.assertTrue((job / 'handoff.json').exists())
        self.assertEqual(binary.read_bytes(), b'current', 'Handoff test must not mutate the live fixture')

    def test_missing_install_roots_are_created_for_target_files(self):
        self.write_package()
        target = self.save()
        self.remove_package()
        self.library.rmdir()
        self.scripts.rmdir()
        job, value = self.prepare(target)
        self.assertEqual(self.execute(job, value), 'applied')
        self.assertEqual((self.scripts / 'demo.exe').read_bytes(), b'launcher:target binary')
        self.assert_matches(target)

    def test_unrecorded_empty_metadata_directory_is_preserved_and_blocks_removal(self):
        target = self.save()
        self.write_package()
        keep = self.library / 'demo-1.0.dist-info/user-empty-directory'
        keep.mkdir()
        plan = diff.inspect(target['id'])
        with self.assertRaisesRegex(ValueError, 'Unrecorded metadata contents'):
            restore.prepare(target['id'], plan['fingerprint'], True, runtime=self.runtime())
        self.assertTrue(keep.is_dir())
        self.assertTrue((self.library / 'demo-1.0.dist-info/METADATA').is_file())
