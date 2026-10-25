"""Installed-file ZIP capture in an isolated runtime; no pip operations."""
import copy
import importlib
import importlib.metadata
import json
from pathlib import Path
import unittest
import threading
from unittest.mock import patch
import zipfile

from test_phase1_snapshot import CaptureFixture, Distribution, state, records, archives

local = state.local_archives


class InstalledDistribution(Distribution):
    def __init__(self, library, name='demo', extra=()):
        super().__init__(name=name)
        self.library = library
        self.files = [f'{name}/__init__.py', f'{name}/native.pyd',
                      f'{name}-1.0.dist-info/METADATA', f'{name}-1.0.dist-info/RECORD', *extra]
        for entry in self.files:
            path = self.locate_file(entry)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((name + ':' + entry).encode())

    def locate_file(self, entry):
        return self.library / str(entry)


class LocalArchiveTests(CaptureFixture):
    def test_hashing_uses_four_workers_and_preserves_all_inventory_entries(self):
        dist = InstalledDistribution(self.library, extra=[f'demo/file-{i}.bin' for i in range(64)])
        barrier = threading.Barrier(4)
        original = local.files.stable_hash
        threads = set()
        lock = threading.Lock()
        def read(path, signature):
            with lock:
                threads.add(threading.get_ident())
            barrier.wait(timeout=5)
            return original(path, signature)
        with patch.object(local.files, 'stable_hash', side_effect=read):
            inventory = local.package_inventories([dist], self.manager)['package:demo']
        self.assertEqual(len(threads), 4)
        self.assertEqual(len(inventory['files']), 68)
        self.assertEqual(inventory['gaps'], [])
        self.assertEqual([e['path'] for e in inventory['files']], sorted(e['path'] for e in inventory['files']))

    def setUp(self):
        super().setUp()
        self.runtime = self.root / 'python'
        self.library = self.runtime / 'Lib/site-packages'
        self.library.mkdir(parents=True)
        self.scripts = self.runtime / 'Scripts'
        self.scripts.mkdir()
        original = local.package_inventories
        replacement = lambda distributions, manager: original(distributions, manager, self.runtime, [self.library], self.scripts)
        patched = patch.object(local, 'package_inventories', side_effect=replacement)
        patched.start()
        self.addCleanup(patched.stop)

    def descriptor(self, record, name='demo'):
        return record['backup']['components']['package:' + name]

    def archive_path(self, record, name='demo'):
        return self.store / self.descriptor(record, name)['storage']['object_path']

    def test_unknown_package_saved_with_metadata_binary_and_script(self):
        self.distributions = [InstalledDistribution(self.library, extra=['../../Scripts/demo.exe'])]
        unlisted = self.library / 'unlisted-secret.txt'
        unlisted.write_text('not a package file')
        record = self.save()
        descriptor = self.descriptor(record)
        self.assertTrue(descriptor['installed_files_saved'])
        self.assertEqual(descriptor['status'], 'local_saved')
        self.assertEqual(descriptor['saved_content'], 'installed_files_and_metadata')
        self.assertEqual(descriptor['storage']['format'], 'zip')
        self.assertEqual(descriptor['recovery_reason'], 'source_unknown', 'Step 2 does not change restore rules')
        with zipfile.ZipFile(self.archive_path(record)) as archive:
            self.assertIsNone(archive.testzip())
            index = json.loads(archive.read(local.INDEX_NAME))
            self.assertEqual(index['component_id'], 'package:demo')
            self.assertIn('Scripts/demo.exe', archive.namelist())
            self.assertIn('Lib/site-packages/demo/native.pyd', archive.namelist())
            self.assertFalse(any('unlisted-secret' in name for name in archive.namelist()))
            for entry in index['files']:
                self.assertEqual(archive.read(entry['path']), (self.runtime / entry['path']).read_bytes())
        self.assertEqual(archives.check_record(record['id'])['status'], 'passed')
        (self.library / 'demo/__init__.py').unlink()
        with zipfile.ZipFile(self.archive_path(record)) as archive:
            self.assertTrue(archive.read('Lib/site-packages/demo/__init__.py'))

    def test_changed_installed_file_invalidates_preview(self):
        self.distributions = [InstalledDistribution(self.library)]
        preview = records.preview()
        (self.library / 'demo/__init__.py').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'Source changed during ZIP capture'):
            records.create('changed', preview_id=preview['preview_id'])
        self.assertEqual(list(self.store.glob('*.json')), [])

    def test_shared_files_saved_completely_with_separate_ownership(self):
        first = InstalledDistribution(self.library, 'one', ['shared.py'])
        second = InstalledDistribution(self.library, 'two', ['shared.py'])
        self.distributions = [first, second]
        record = self.save()
        for name in ('one', 'two'):
            descriptor = self.descriptor(record, name)
            self.assertEqual(descriptor['status'], 'local_saved')
            self.assertTrue(descriptor['installed_files_saved'])
            self.assertEqual(descriptor['shared_files'][0]['owners'], ['package:one', 'package:two'])
            self.assertFalse(descriptor['gaps'])

    def test_missing_file_partial_but_remaining_files_saved(self):
        dist = InstalledDistribution(self.library)
        (self.library / 'demo/native.pyd').unlink()
        self.distributions = [dist]
        descriptor = self.descriptor(self.save())
        self.assertFalse(descriptor['installed_files_saved'])
        self.assertEqual(descriptor['status'], 'incomplete')
        self.assertTrue(descriptor['files'])
        self.assertTrue(any(g['reason'] == 'installed_file_missing_or_not_regular' for g in descriptor['gaps']))

    def test_outside_runtime_and_interpreter_never_archived(self):
        dist = InstalledDistribution(self.library)
        dist.files += ['../../../outside.txt', '../../python.exe']
        (self.root / 'outside.txt').write_text('outside')
        (self.runtime / 'python.exe').write_text('interpreter')
        self.distributions = [dist]
        record = self.save()
        descriptor = self.descriptor(record)
        self.assertFalse(descriptor['installed_files_saved'])
        with zipfile.ZipFile(self.archive_path(record)) as archive:
            self.assertFalse(any('outside.txt' in name or 'python.exe' in name for name in archive.namelist()))

    def test_record_missing_and_bytecode_exclusion(self):
        dist = InstalledDistribution(self.library, extra=['demo/__pycache__/cached.pyc'])
        (self.library / 'demo-1.0.dist-info/RECORD').unlink()
        self.distributions = [dist]
        descriptor = self.descriptor(self.save())
        self.assertTrue(any(e['reason'] == 'regenerable_bytecode' for e in descriptor['exclusions']))
        self.assertTrue(any(e['reason'] == 'installed_metadata_incomplete' for e in descriptor['gaps']))

    def test_duplicate_record_paths_reuse_same_saved_file(self):
        dist = InstalledDistribution(self.library)
        dist.files.append('demo/../demo/__init__.py')
        self.distributions = [dist]
        descriptor = self.descriptor(self.save())
        self.assertFalse(descriptor['gaps'])
        self.assertEqual(len(descriptor['files']), 4)

    def test_package_data_outside_site_packages_saved_inside_runtime(self):
        self.distributions = [InstalledDistribution(self.library, extra=['../../share/man/man1/demo.1', '../../etc/jupyter/demo.json', '../../include/demo.h', '../../tests/demo.txt', '../../SPIRV-Tools/cmake/demo.cmake', '../../msvcp140.dll', '../../Scripts/__pycache__/demo.pyc'])]
        descriptor = self.descriptor(self.save())
        self.assertTrue(descriptor['installed_files_saved'])
        self.assertTrue({'share/man/man1/demo.1', 'etc/jupyter/demo.json', 'include/demo.h'}.issubset({e['path'] for e in descriptor['files']}))
        self.assertTrue({'tests/demo.txt', 'SPIRV-Tools/cmake/demo.cmake', 'msvcp140.dll'}.issubset({e['path'] for e in descriptor['files']}))
        self.assertTrue(any(e['reason'] == 'regenerable_bytecode' for e in descriptor['exclusions']))

    def test_zip_reuse_and_gc_references(self):
        self.distributions = [InstalledDistribution(self.library)]
        first = self.save()
        second = self.save()
        self.assertEqual(self.archive_path(first), self.archive_path(second))
        self.assertEqual(second['statistics']['zip_new_bytes'], 0)
        self.assertGreater(second['statistics']['zip_reused_bytes'], 0)
        (self.store / (first['id'] + '.json')).unlink()
        self.assertEqual(archives.gc_inventory(self.store)[0], [])

    def test_zip_export_and_import_includes_package_archive(self):
        self.distributions = [InstalledDistribution(self.library)]
        record = self.save()
        target = self.root / 'backup.zip'
        preview = archives.preview_export(record['id'], str(target))
        archives.export_bundle(record['id'], str(target), True, token=preview['token'])
        archive_object = self.descriptor(record)['storage']['object_path']
        with zipfile.ZipFile(target) as exported:
            self.assertIn(archive_object, exported.namelist())
            self.assertEqual(exported.read(archive_object), self.archive_path(record).read_bytes())
        imported_store = self.root / 'imported-store'
        with patch.object(records, 'directory', return_value=imported_store):
            reviewed = archives.preview_import(str(target))
            imported = archives.import_bundle(reviewed['token'], True)
            imported_record = records.read(imported['id'])
            self.assertEqual(imported_record['backup'], record['backup'])
            self.assertTrue((imported_store / archive_object).is_file())
            self.assertEqual(archives.check_record(imported['id'])['status'], 'passed')

    def test_invalid_installed_inventory_path_rejected(self):
        self.distributions = [InstalledDistribution(self.library)]
        record = self.save()
        record.pop('backup')
        row = next(r for r in record['resources'] if r['kind'] == 'package')
        row['installed_inventory']['files'][0]['path'] = '../outside'
        with self.assertRaisesRegex(ValueError, 'Unsafe snapshot path'):
            state.validate(record)

    def test_old_version_one_contract_still_reads(self):
        record = self.save()
        record.pop('backup')
        for row in record['resources']:
            if row.get('archive_ref'):
                record['artifacts'].pop(row.pop('archive_ref'))
                row.pop('archive_content')
                row.pop('archive_verification', None)
        old = state.validate(record)
        self.assertEqual(old['backup']['version'], 1)
        self.assertEqual(state.validate(copy.deepcopy(old)), old)

    def test_real_distribution_record_is_used_without_installing(self):
        dist = InstalledDistribution(self.library)
        metadata = self.library / 'demo-1.0.dist-info'
        (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n')
        (metadata / 'RECORD').write_text(''.join(entry + ',,\n' for entry in dist.files))
        self.distributions = [importlib.metadata.PathDistribution(metadata)]
        descriptor = self.descriptor(self.save())
        self.assertTrue(descriptor['installed_files_saved'])
        self.assertEqual(len(descriptor['files']), 4)

    def test_bytecode_can_be_missing_without_losing_completeness(self):
        dist = InstalledDistribution(self.library, extra=['demo/__pycache__/cached.pyc'])
        (self.library / 'demo/__pycache__/cached.pyc').unlink()
        self.distributions = [dist]
        descriptor = self.descriptor(self.save())
        self.assertTrue(descriptor['installed_files_saved'])
        self.assertEqual(descriptor['gaps'], [])

    def test_zip_hash_mismatch_refuses_publication(self):
        self.distributions = [InstalledDistribution(self.library)]
        original = local.write_zip
        def changed(destination, component_id, entries, source_for, *args):
            if component_id == 'package:demo':
                (self.library / 'demo/__init__.py').write_text('changed during ZIP')
            return original(destination, component_id, entries, source_for, *args)
        preview = records.preview()
        with patch.object(local, 'write_zip', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'Source (changed|grew)'):
                records.create('raced', preview_id=preview['preview_id'])
        self.assertEqual(list(self.store.glob('*.json')), [])

    def test_script_cannot_claim_pip_or_other_package_cannot_claim_pip_files(self):
        dist = InstalledDistribution(self.library, extra=['../../Scripts/pip3.11.exe', 'pip/__init__.py'])
        self.distributions = [dist]
        record = self.save()
        with zipfile.ZipFile(self.archive_path(record)) as archive:
            self.assertNotIn('Scripts/pip3.11.exe', archive.namelist())
            self.assertNotIn('Lib/site-packages/pip/__init__.py', archive.namelist())

    def test_reserve_includes_zip_content_and_temporary_copy(self):
        self.distributions = [InstalledDistribution(self.library)]
        preview = records.preview()
        self.assertGreater(preview['space']['installed_file_bytes'], 0)
        self.assertGreater(preview['required_bytes'], 2 * preview['space']['zip_upper_bound_bytes'])
        self.assertGreater(preview['space']['new_bytes'], preview['space']['zip_upper_bound_bytes'])

    def test_malformed_real_record_is_a_gap(self):
        dist = InstalledDistribution(self.library)
        metadata = self.library / 'demo-1.0.dist-info'
        (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n')
        (metadata / 'RECORD').write_text('demo/__init__.py,too,many,fields\n')
        self.distributions = [importlib.metadata.PathDistribution(metadata)]
        descriptor = self.descriptor(self.save())
        self.assertEqual(descriptor['status'], 'incomplete')
        self.assertTrue(any(g['reason'] == 'invalid_installed_file_manifest' for g in descriptor['gaps']))

    def test_directory_in_file_manifest_is_a_gap(self):
        dist = InstalledDistribution(self.library)
        dist.files.append('.')
        self.distributions = [dist]
        descriptor = self.descriptor(self.save())
        self.assertFalse(descriptor['installed_files_saved'])
        self.assertTrue(any(g['reason'] == 'installed_file_missing_or_not_regular' for g in descriptor['gaps']))

    def test_protected_resource_cannot_claim_archive(self):
        record = self.save()
        record.pop('backup')
        protected = next(r for r in record['resources'] if r.get('protected'))
        local_row = next(r for r in record['resources'] if r.get('archive_ref'))
        protected['archive_ref'] = protected['id'] + ':zip'
        protected['archive_content'] = 'managed_files'
        record['artifacts'][protected['archive_ref']] = copy.deepcopy(record['artifacts'][local_row['archive_ref']])
        with self.assertRaisesRegex(ValueError, 'Cannot archive protected'):
            state.validate(record)


if __name__ == '__main__':
    unittest.main()
