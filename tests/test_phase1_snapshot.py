"""Phase 1 fixtures: no package installation or live-instance mutations."""
import copy
import importlib
import json
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

package = types.ModuleType('_phase1')
package.__path__ = [str(Path(__file__).parents[1] / 'qbb_nodeops')]
sys.modules[package.__name__] = package
state = importlib.import_module(package.__name__ + '.snapshot_state')
records = state.records
archives = importlib.import_module(package.__name__ + '.record_archives')
manifest = importlib.import_module(package.__name__ + '.manifest')


class Distribution:
    def __init__(self, name='demo', direct=None, version='1.0'):
        self.metadata = {'Name': name}
        self.version = version
        self.direct = direct

    def read_text(self, name):
        return json.dumps(self.direct) if self.direct is not None else None


class CaptureFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.core = self.root / 'ComfyUI'
        self.nodes = self.core / 'custom_nodes'
        self.nodes.mkdir(parents=True)
        (self.core / 'main.py').write_text('pass')
        self.local = self.nodes / 'LocalPlugin'
        self.local.mkdir()
        (self.local / '__init__.py').write_text('original')
        self.manager = self.nodes / 'renamed-manager'
        self.manager.mkdir()
        (self.manager / '__init__.py').write_text('manager')
        self.store = self.root / 'store'
        self.distributions = []
        self.original_scan = state.scan_environment
        self.scan_patch = patch.object(state, 'scan_environment', side_effect=self.scan)
        self.scan_patch.start()
        self.addCleanup(self.scan_patch.stop)
        p = patch.object(records, 'directory', return_value=self.store)
        p.start()
        self.addCleanup(p.stop)
        records._PREVIEWS.clear()

    def scan(self, label=''):
        return self.original_scan(self.core, [self.nodes], self.manager, self.distributions, self.store, label)

    def save(self):
        preview = records.preview()
        return records.read(records.create('fixture', preview_id=preview['preview_id'])['id'])

    def resource(self, record, resource_id):
        return next(r for r in record['resources'] if r['id'] == resource_id)

    def wheel(self):
        path = self.root / 'demo-1.0-py3-none-any.whl'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('demo-1.0.dist-info/METADATA', 'Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n')
            archive.writestr('demo.py', 'answer = 42')
        self.distributions = [Distribution(direct={'url': path.as_uri(), 'archive_info': {}})]
        return path


class Phase1Tests(CaptureFixture):
    def test_save_reuses_preview_and_scans_environment_once_for_final_validation(self):
        preview = records.preview()
        with patch.object(state, 'scan_environment', wraps=state.scan_environment) as scanned:
            result = records.create('Saved label', preview_id=preview['preview_id'])
        self.assertEqual(scanned.call_count, 1, 'Only final verification scans the environment')
        self.assertEqual(records.read(result['id'])['label'], 'Saved label')

    def test_local_plugin_content_saved(self):
        record = self.save()
        row = self.resource(record, 'plugin:custom_nodes/LocalPlugin')
        self.assertEqual(row['recoverability']['status'], 'artifact_saved')
        artifact = record['artifacts'][row['artifact_ref']]
        self.assertEqual(state.files.object_path(self.store, artifact['files'][0]['sha256']).read_text(), 'original')

    def test_two_records_reuse_objects_and_do_not_depend_on_parent(self):
        first = self.save()
        second = self.save()
        self.assertEqual(second['statistics']['new_bytes'], 0)
        self.assertGreater(second['statistics']['reused_bytes'], 0)
        self.assertNotIn('parent', second)
        (self.store / (first['id'] + '.json')).unlink()
        candidates, _ = archives.gc_inventory(self.store)
        self.assertEqual(candidates, [])
        self.assertEqual(archives.check_record(second['id'])['status'], 'passed')

    def test_changed_file_adds_only_changed_content(self):
        self.save()
        (self.local / '__init__.py').write_text('changed-content')
        record = self.save()
        self.assertEqual(record['statistics']['new_bytes'], len('changed-content') + record['statistics']['zip_new_bytes'])
        self.assertGreater(record['statistics']['reused_bytes'], 0)

    def test_original_wheel_saved_and_original_may_disappear(self):
        path = self.wheel()
        record = self.save()
        row = self.resource(record, 'package:demo')
        self.assertEqual(row['recoverability']['status'], 'artifact_saved')
        sha = row['source']['wheel']['sha256']
        expected = path.read_bytes()
        path.unlink()
        self.assertEqual(state.files.object_path(self.store, sha).read_bytes(), expected)
        self.assertEqual(archives.check_record(record['id'])['status'], 'passed')

    def test_missing_original_wheel_has_no_fallback(self):
        self.wheel().unlink()
        record = self.save()
        row = self.resource(record, 'package:demo')
        self.assertEqual(row['recoverability']['reason'], 'original_artifact_missing')
        self.assertEqual(row['recoverability']['status'], 'unrestorable')
        self.assertIsNone(row['artifact_ref'])

    def test_unknown_index_not_assumed_to_be_pypi(self):
        self.distributions = [Distribution()]
        row = self.resource(self.save(), 'package:demo')
        self.assertEqual(row['source'], {'kind': 'unknown'})
        self.assertEqual(row['recoverability']['reason'], 'source_unknown')

    def test_hash_pinned_remote_wheel_records_source_only(self):
        self.distributions = [Distribution(direct={'url': 'https://example.org/demo-1.0-py3-none-any.whl', 'archive_info': {'hashes': {'sha256': 'a' * 64}}})]
        row = self.resource(self.save(), 'package:demo')
        self.assertEqual(row['recoverability']['status'], 'source_recorded')
        self.assertIsNone(row['artifact_ref'])

    def test_signed_url_not_silently_rewritten_as_reproducible(self):
        self.distributions = [Distribution(direct={'url': 'https://example.org/demo.whl?token=secret', 'archive_info': {'hashes': {'sha256': 'a' * 64}}})]
        record = self.save()
        self.assertEqual(self.resource(record, 'package:demo')['recoverability']['status'], 'unrestorable')
        self.assertNotIn('secret', json.dumps(record))

    def test_local_editable_source_saved_without_build_guarantee(self):
        source = self.core / 'development'
        source.mkdir()
        (source / 'demo.py').write_text('local')
        self.distributions = [Distribution(direct={'url': source.as_uri(), 'dir_info': {'editable': True}})]
        row = self.resource(self.save(), 'package:demo')
        self.assertIsNotNone(row['artifact_ref'])
        self.assertTrue(row['source']['editable'])
        self.assertEqual(row['recoverability']['reason'], 'local_build_not_verified')

    def test_external_editable_record_only(self):
        source = self.root / 'outside'
        source.mkdir()
        (source / 'secret.py').write_text('external')
        self.distributions = [Distribution(direct={'url': source.as_uri(), 'dir_info': {'editable': True}})]
        row = self.resource(self.save(), 'package:demo')
        self.assertFalse(row['managed'])
        self.assertIsNone(row['artifact_ref'])
        self.assertEqual(row['recoverability']['reason'], 'external_source_record_only')

    def test_manager_is_protected_even_when_renamed(self):
        row = self.resource(self.save(), 'plugin:custom_nodes/renamed-manager')
        self.assertTrue(row['protected'])
        self.assertFalse(row['managed'])
        self.assertEqual(row['recoverability']['status'], 'protected_resource')
        self.assertIsNone(row['artifact_ref'])

    def test_external_plugin_root_record_only(self):
        outside = self.root / 'external_nodes'
        plugin = outside / 'ExternalPlugin'
        plugin.mkdir(parents=True)
        (plugin / '__init__.py').write_text('external')
        record, captures = self.original_scan(self.core, [self.nodes, outside], self.manager, [], self.store)
        row = next(r for r in record['resources'] if r['name'] == 'ExternalPlugin')
        self.assertFalse(row['managed'])
        self.assertEqual(row['recoverability']['reason'], 'external_or_linked_resource_record_only')
        self.assertNotIn(row['id'], captures)

    def test_linked_plugin_not_traversed(self):
        original = state.files.no_links
        def guard(path):
            if Path(path) == self.local:
                raise ValueError('Linked path is not allowed')
            return original(path)
        with patch.object(state.files, 'no_links', side_effect=guard):
            record, captures = self.scan()
        row = self.resource(record, 'plugin:custom_nodes/LocalPlugin')
        self.assertFalse(row['managed'])
        self.assertNotIn(row['id'], captures)

    def test_personal_data_excluded_at_core_and_plugin_levels(self):
        for root in (self.core, self.local):
            for name in ('models', 'user', 'input', 'output', 'workflows'):
                (root / name).mkdir()
                (root / name / 'private.txt').write_text('private')
        record = self.save()
        paths = [f['path'] for a in record['artifacts'].values() for f in a['files']]
        self.assertFalse(any('private.txt' in p for p in paths))

    def test_configuration_allowlist_and_absence(self):
        record = self.save()
        self.assertEqual(self.resource(record, 'config:extra_model_paths.yaml')['recoverability']['status'], 'absent_recorded')
        (self.core / 'extra_model_paths.yaml').write_text('models: {}')
        (self.core / 'private.json').write_text('{"secret": true}')
        record = self.save()
        self.assertEqual(self.resource(record, 'config:extra_model_paths.yaml')['recoverability']['status'], 'artifact_saved')
        core = self.resource(record, 'core')
        self.assertNotIn('extra_model_paths.yaml', [f['path'] for f in core['content']['files']])
        self.assertNotIn('private.json', [f['path'] for f in core['content']['files']])

    def test_scope_cannot_be_expanded_through_old_options(self):
        for options in ({'core_dirs': ['user']}, {'core_dirs': ['models']}, {'on_error': 'github'}, {'on_error': 'metadata'}, {'mode': 'offline'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                records.preview(options)

    def test_source_change_invalidates_preview(self):
        preview = records.preview()
        (self.local / '__init__.py').write_text('different')
        with self.assertRaisesRegex(ValueError, 'changed since preview'):
            records.create(preview_id=preview['preview_id'])

    def test_object_corruption_blocks_reuse(self):
        record = self.save()
        entry = next(iter(record['artifacts'].values()))['files'][0]
        state.files.object_path(self.store, entry['sha256']).write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, 'corrupt'):
            records.preview()

    def test_new_schema_cannot_reach_old_executor_or_diff(self):
        record = self.save()
        rebuild = importlib.import_module(package.__name__ + '.rebuild')
        compare = importlib.import_module(package.__name__ + '.record_compare')
        with self.assertRaisesRegex(ValueError, 'legacy'):
            rebuild.plan(record['id'])
        with self.assertRaisesRegex(ValueError, 'Phase 2'):
            compare.compare_record(record, {}, self.store)

    def test_export_import_references_include_local_wheel(self):
        path = self.wheel()
        record = self.save()
        bundle = self.root / 'record.zip'
        archives.export_bundle(record['id'], str(bundle), True)
        imported = archives.import_bundle(archives.preview_import(str(bundle))['token'], True)
        self.assertEqual(imported['space']['new_bytes'], 0)
        new = records.read(imported['id'])
        self.assertEqual(new['artifacts'], record['artifacts'])
        self.assertEqual(archives.check_record(new['id'])['status'], 'passed')
        self.assertIn(state.files.hash_file(path), archives.references(new)[1])

    def test_legacy_schemas_still_read_without_rewriting(self):
        self.store.mkdir()
        for schema in (1, 2, 3):
            value = {'schema': schema, 'kind': 'version_record', 'id': str(schema) * 32, 'created': 1, 'label': 'old',
                     'core': {'name': 'ComfyUI', 'kind': 'local', 'path': str(self.core)}, 'plugins': [], 'packages': [], 'python': {'version': '3.11'}}
            path = self.store / (value['id'] + '.json')
            path.write_text(json.dumps(value))
            before = path.read_bytes()
            self.assertEqual(records.read(value['id'])['schema'], schema)
            self.assertEqual(path.read_bytes(), before)

    def test_dangling_artifact_reference_rejected(self):
        record = self.save()
        record['artifacts'].clear()
        with self.assertRaises(ValueError):
            manifest.normalize(record)

    def test_automatic_checkpoint_paused_without_capture(self):
        checkpoints = importlib.import_module(package.__name__ + '.checkpoint_policy')
        with patch.object(records, 'create', side_effect=AssertionError('Must not create')):
            checkpoints.tick()
        self.assertEqual(checkpoints.STATE['status'], 'paused')

    def test_real_git_clean_dirty_and_unpushed_classification(self):
        def git(*args):
            result = subprocess.run(['git', '-c', 'safe.directory=' + str(self.local), '-C', str(self.local), *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()
        git('init')
        git('config', 'user.name', 'Fixture')
        git('config', 'user.email', 'fixture@example.invalid')
        git('add', '.')
        git('commit', '-m', 'fixture')
        git('remote', 'add', 'origin', 'https://example.org/plugin.git')
        git('update-ref', 'refs/remotes/origin/main', git('rev-parse', 'HEAD'))
        original = records.git
        def trusted(path, *args):
            return git(*args) if Path(path) == self.local else original(path, *args)
        with patch.object(records, 'git', side_effect=trusted):
            row = self.resource(self.save(), 'plugin:custom_nodes/LocalPlugin')
            self.assertEqual(row['recoverability']['status'], 'source_recorded')
            self.assertIsNone(row['artifact_ref'])
            (self.local / '__init__.py').write_text('modified')
            row = self.resource(self.save(), 'plugin:custom_nodes/LocalPlugin')
            self.assertEqual(row['recoverability']['status'], 'artifact_saved')
            git('add', '.')
            git('commit', '-m', 'local-only')
            row = self.resource(self.save(), 'plugin:custom_nodes/LocalPlugin')
            self.assertEqual(row['recoverability']['reason'], 'git_commit_not_retrievable')
            self.assertIsNotNone(row['artifact_ref'])


if __name__ == '__main__':
    unittest.main()
