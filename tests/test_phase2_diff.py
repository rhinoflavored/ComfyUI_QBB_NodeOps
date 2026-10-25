"""Current -> Target mutations in temporary fixtures; never install or restore."""
import copy
from contextlib import closing
import importlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import unittest
from unittest.mock import patch

from test_phase1_snapshot import CaptureFixture, Distribution, state, records, package

diff = importlib.import_module(package.__name__ + '.state_diff')
legacy = importlib.import_module(package.__name__ + '.record_compare')


class Phase2Tests(CaptureFixture):
    def scan(self, label='', read_only=False):
        return self.original_scan(self.core, [self.nodes], self.manager, self.distributions, self.store, label, read_only)

    def compare(self, target):
        current, _ = diff.current_state()
        return diff.compare(current, target)

    def row(self, result, identity='plugin:custom_nodes/LocalPlugin'):
        return next(r for r in result['resources'] if r['resource_id'] == identity)

    def remote_package(self, version='1.0', url='https://example.org/demo.whl'):
        self.distributions = [Distribution(version=version, direct={'url': url, 'archive_info': {'hashes': {'sha256': 'a' * 64}}})]

    def test_no_changes_and_no_execution_authorization(self):
        target = self.save()
        plan = diff.inspect(target['id'])
        self.assertEqual(plan['status'], 'no_changes')
        self.assertEqual(plan['diff']['state'], 'matched')
        self.assertEqual(plan['direction'], 'current_to_target')
        self.assertFalse(plan['execution_allowed'])

    def test_added_plugin_is_extra_and_remove_proposal(self):
        target = self.save()
        plugin = self.nodes / 'Added'
        plugin.mkdir()
        (plugin / '__init__.py').write_text('new')
        row = self.row(self.compare(target), 'plugin:custom_nodes/Added')
        self.assertEqual(row['differences'][0]['status'], 'extra')
        self.assertEqual(row['proposals'], ['remove_resource'])
        self.assertTrue(plugin.is_dir())

    def test_deleted_plugin_is_missing_and_restore_proposal(self):
        target = self.save()
        shutil.rmtree(self.local)
        row = self.row(self.compare(target))
        self.assertEqual(row['differences'][0]['status'], 'missing')
        self.assertEqual(row['proposals'], ['restore_resource'])
        self.assertFalse(self.local.exists())

    def test_modified_local_content_has_current_and_target_hashes(self):
        target = self.save()
        (self.local / '__init__.py').write_text('modified')
        row = self.row(self.compare(target))
        change = next(c for c in row['differences'] if c['status'] == 'content_mismatch')
        self.assertNotEqual(change['current']['sha256'], change['target']['sha256'])
        self.assertEqual(row['proposals'], ['restore_content'])

    def test_extra_file_removed_and_missing_file_restored_direction(self):
        (self.local / 'remove.py').write_text('original')
        target = self.save()
        (self.local / 'remove.py').unlink()
        (self.local / 'add.py').write_text('new')
        changes = {r['path']: r['status'] for r in self.row(self.compare(target))['differences']}
        self.assertEqual(changes['remove.py'], 'missing')
        self.assertEqual(changes['add.py'], 'extra')

    def test_empty_directory_and_type_change(self):
        (self.local / 'empty').mkdir()
        target = self.save()
        (self.local / 'empty').rmdir()
        (self.local / 'empty').write_text('file now')
        changes = self.row(self.compare(target))['differences']
        self.assertTrue(any(c.get('path') == 'empty' and c['status'] == 'type_mismatch' for c in changes))

    def test_permission_difference(self):
        target = self.save()
        current, _ = diff.current_state()
        row = self.resource(current, 'plugin:custom_nodes/LocalPlugin')
        row['content']['files'][0]['mode'] ^= 0o200
        changes = self.row(diff.compare(current, target))['differences']
        self.assertEqual(changes[0]['status'], 'permissions_mismatch')

    def test_timestamps_not_state(self):
        target = self.save()
        current, _ = diff.current_state()
        current['created'] = 100
        current['id'] = 'unrelated'
        current['label'] = 'other label'
        self.assertEqual(diff.compare(current, target)['state'], 'matched')

    def test_package_upgrade_and_downgrade_direction(self):
        self.remote_package('2.0')
        target = self.save()
        for version in ('1.0', '3.0'):
            with self.subTest(current=version):
                self.remote_package(version)
                row = self.row(self.compare(target), 'package:demo')
                version_change = next(c for c in row['differences'] if c['field'] == 'version')
                self.assertEqual((version_change['current'], version_change['target']), (version, '2.0'))
                self.assertIn('set_package_version', row['proposals'])

    def test_package_source_change_same_version(self):
        self.remote_package()
        target = self.save()
        self.remote_package(url='https://another.example.org/demo.whl')
        row = self.row(self.compare(target), 'package:demo')
        self.assertIn('set_package_source', row['proposals'])
        self.assertTrue(any(c['status'] == 'source_mismatch' for c in row['differences']))

    def test_missing_package_install_proposal(self):
        self.remote_package()
        target = self.save()
        self.distributions = []
        self.assertEqual(self.row(self.compare(target), 'package:demo')['proposals'], ['install_package'])

    def test_extra_package_uninstall_proposal(self):
        target = self.save()
        self.remote_package()
        self.assertEqual(self.row(self.compare(target), 'package:demo')['proposals'], ['uninstall_package'])

    def test_unknown_source_even_equal_is_not_match(self):
        self.distributions = [Distribution()]
        target = self.save()
        plan = diff.inspect(target['id'])
        self.assertEqual(plan['status'], 'comparison_incomplete')
        row = self.row(plan['diff'], 'package:demo')
        self.assertFalse(row['blockers'])
        self.assertFalse(plan['blockers'])
        self.assertIn('source_unknown', row['warnings'])
        self.assertFalse(any(c['status'] == 'source_mismatch' for c in row['differences']))
        self.assertEqual(plan['diff']['change_count'], 0)
        self.assertGreater(plan['diff']['unverified_count'], 0)

    def test_known_to_unknown_source_not_false_confirmed_mismatch(self):
        self.remote_package()
        target = self.save()
        self.distributions = [Distribution()]
        row = self.row(self.compare(target), 'package:demo')
        self.assertTrue(any(c['status'] == 'unknown' for c in row['differences']))
        self.assertFalse(any(c['status'] == 'source_mismatch' for c in row['differences']))

    def test_missing_original_wheel_target_blocks_install(self):
        self.wheel().unlink()
        target = self.save()
        self.distributions = []
        plan = diff.inspect(target['id'])
        action = next(a for a in plan['actions'] if a['resource_id'] == 'package:demo')
        self.assertEqual(action['status'], 'blocked')
        self.assertIn('original_artifact_missing', action['blockers'])

    def test_saved_wheel_still_installable_when_original_deleted(self):
        self.wheel()
        target = self.save()
        (self.root / 'demo-1.0-py3-none-any.whl').unlink()
        self.distributions = []
        plan = diff.inspect(target['id'])
        action = next(a for a in plan['actions'] if a['resource_id'] == 'package:demo')
        self.assertEqual(action['status'], 'proposed')
        self.assertEqual(plan['artifact_health']['package:demo']['status'], 'verified')

    def test_python_difference_blocks_packages_but_not_plugin_proposals(self):
        self.remote_package()
        target = self.save()
        current, _ = diff.current_state()
        current['python']['version'] = '3.99.0'
        self.resource(current, 'package:demo')['version'] = '3.0'
        self.resource(current, 'plugin:custom_nodes/LocalPlugin')['content']['files'][0]['sha256'] = 'b' * 64
        result = diff.compare(current, target)
        self.assertIn('python_interpreter_incompatible_or_unknown', self.row(result, 'package:demo')['blockers'])
        self.assertFalse(self.row(result)['blockers'])
        self.assertEqual(result['runtime'][0]['reason'], 'interpreter_replacement_out_of_scope')

    def test_missing_python_metadata_cannot_match(self):
        target = self.save()
        current, _ = diff.current_state()
        current['python'].pop('architecture')
        self.assertEqual(diff.compare(current, target)['state'], 'unknown')

    def test_config_missing_extra_and_content_mismatch(self):
        path = self.core / 'extra_model_paths.yaml'
        absent = self.save()
        path.write_text('models: {}')
        self.assertEqual(self.row(self.compare(absent), 'config:extra_model_paths.yaml')['proposals'], ['remove_configuration'])
        target = self.save()
        path.write_text('models: changed')
        self.assertIn('restore_content', self.row(self.compare(target), 'config:extra_model_paths.yaml')['proposals'])
        path.unlink()
        self.assertEqual(self.row(self.compare(target), 'config:extra_model_paths.yaml')['proposals'], ['restore_configuration'])

    def test_manager_absent_from_target_never_removed(self):
        target = self.save()
        target['resources'] = [r for r in target['resources'] if not r.get('protected')]
        row = self.row(self.compare(target), 'plugin:custom_nodes/renamed-manager')
        self.assertEqual(row['status'], 'protected_resource')
        self.assertFalse(row['proposals'])

    def test_manager_version_never_replaced(self):
        target = self.save()
        row = self.resource(target, 'plugin:custom_nodes/renamed-manager')
        row['source']['commit'] = 'f' * 40
        self.assertFalse(self.row(self.compare(target), row['id'])['proposals'])

    def test_manager_ancestor_cannot_be_removed(self):
        target = self.save()
        current, _ = diff.current_state()
        parent = copy.deepcopy(self.resource(current, 'plugin:custom_nodes/LocalPlugin'))
        parent.update(id='plugin:custom_nodes', relative_path='custom_nodes')
        current['resources'].append(parent)
        self.assertEqual(self.row(diff.compare(current, target), parent['id'])['status'], 'protected_resource')

    def test_external_resource_not_removed_or_restored(self):
        target = self.save()
        current, _ = diff.current_state()
        external = copy.deepcopy(self.resource(current, 'plugin:custom_nodes/LocalPlugin'))
        external.update(id='plugin:external', managed=False, relative_path=None)
        current['resources'].append(external)
        row = self.row(diff.compare(current, target), 'plugin:external')
        self.assertEqual(row['status'], 'external_resource')
        self.assertFalse(row['proposals'])

    def test_scope_scan_gap_blocks_destructive_proposal(self):
        target = self.save()
        current, _ = diff.current_state()
        target['resources'] = [r for r in target['resources'] if r['id'] != 'plugin:custom_nodes/LocalPlugin']
        target['artifacts'].pop('plugin:custom_nodes/LocalPlugin')
        target['issues'].append({'reason': 'unreadable_or_linked_plugin_root'})
        self.assertIn('scope_incomplete', self.row(diff.compare(current, target))['blockers'])

    def test_object_corruption_is_backup_warning_when_no_changes(self):
        target = self.save()
        entry = target['artifacts']['plugin:custom_nodes/LocalPlugin']['files'][0]
        state.files.object_path(self.store, entry['sha256']).write_bytes(b'corrupted')
        plan = diff.inspect(target['id'])
        self.assertEqual(plan['diff']['state'], 'matched')
        self.assertEqual(plan['status'], 'no_changes')
        self.assertFalse(plan['blockers'])
        self.assertTrue(plan['backup_issues'])
        self.assertEqual(plan['artifact_health']['plugin:custom_nodes/LocalPlugin']['status'], 'failed')

    def test_missing_original_object_uses_verified_zip(self):
        target = self.save()
        entry = target['artifacts']['plugin:custom_nodes/LocalPlugin']['files'][0]
        state.files.object_path(self.store, entry['sha256']).unlink()
        shutil.rmtree(self.local)
        plan = diff.inspect(target['id'])
        action = next(a for a in plan['actions'] if a['resource_id'] == 'plugin:custom_nodes/LocalPlugin')
        self.assertEqual(action['status'], 'proposed')
        self.assertEqual(action['material']['kind'], 'local_zip')
        self.assertFalse(plan['blockers'])
        self.assertTrue(plan['execution_ready'])

    def test_changed_during_scan_rejected(self):
        target = self.save()
        original = diff.current_state
        calls = 0
        def scan():
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.local / '__init__.py').write_text('changed')
            return original()
        with patch.object(diff, 'current_state', side_effect=scan), self.assertRaisesRegex(ValueError, 'changed during'):
            diff.inspect(target['id'])

    def test_pure_diff_preserves_inputs(self):
        target = self.save()
        current, _ = diff.current_state()
        before = json.dumps([current, target], sort_keys=True)
        result = diff.compare(current, target)
        diff.recovery_plan(result, target, {})
        self.assertEqual(json.dumps([current, target], sort_keys=True), before)

    def test_inspection_does_not_write_environment_objects_or_records(self):
        self.wheel()
        target = self.save()
        def inventory():
            return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()}
        before = inventory()
        with (patch.object(state.files, 'capture', side_effect=AssertionError('No capture')),
              patch.object(records, 'create', side_effect=AssertionError('No save'))):
            diff.inspect(target['id'])
        self.assertEqual(inventory(), before)

    def test_sqlite_readonly_does_not_open_database(self):
        path = self.local / 'data.bin'
        with closing(sqlite3.connect(path)) as db:
            db.execute('create table test (x)')
            db.commit()
        with patch.object(state.files, 'sqlite_snapshot', side_effect=AssertionError('No SQLite connection')):
            current, _ = diff.current_state()
        row = self.resource(current, 'plugin:custom_nodes/LocalPlugin')
        self.assertEqual(row['recoverability']['reason'], 'source_scan_failed')

    def test_legacy_comparison_dispatches_same_engine_for_v4(self):
        target = self.save()
        result = legacy.compare(target['id'])
        self.assertEqual(result['direction'], 'current_to_target')

    def test_saved_comparison_direction_is_explicit(self):
        target = self.save()
        (self.local / '__init__.py').write_text('later')
        current = self.save()
        result = diff.inspect(target['id'], current['id'])
        self.assertEqual(result['diff']['target']['id'], target['id'])
        self.assertEqual(result['diff']['current']['id'], current['id'])
        self.assertEqual(result['evidence'], 'saved_current_manifest')

    def test_scope_mismatch_rejected(self):
        target = self.save()
        target['scope']['configuration_allowlist'].append('user')
        with self.assertRaisesRegex(ValueError, 'same V1 scope'):
            self.compare(target)

    def test_duplicate_identity_rejected(self):
        target = self.save()
        target['resources'].append(copy.deepcopy(target['resources'][0]))
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            self.compare(target)

    def test_path_traversal_rejected(self):
        target = self.save()
        row = self.resource(target, 'plugin:custom_nodes/LocalPlugin')
        row['relative_path'] = '../elsewhere'
        with self.assertRaises(ValueError):
            self.compare(target)

    def test_missing_scope_root_not_interpreted_as_delete(self):
        target = self.save()
        target['resources'] = [r for r in target['resources'] if r['id'] != 'core']
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            self.compare(target)

    def test_case_only_plugin_identity_not_missing_plus_extra(self):
        target = self.save()
        current, _ = diff.current_state()
        row = self.resource(current, 'plugin:custom_nodes/LocalPlugin')
        row['id'] = 'plugin:custom_nodes/localplugin'
        row['relative_path'] = 'custom_nodes/localplugin'
        result = diff.compare(current, target)
        self.assertEqual(result['state'], 'matched')
        self.assertEqual(len([r for r in result['resources'] if r['resource_id'].casefold() == row['id']]), 1)

    def test_extra_plugin_with_unmanaged_user_data_not_scheduled_for_whole_delete(self):
        target = self.save()
        extra = self.nodes / 'Extra'
        (extra / 'user').mkdir(parents=True)
        (extra / 'user' / 'private.txt').write_text('personal')
        (extra / '__init__.py').write_text('plugin')
        row = self.row(self.compare(target), 'plugin:custom_nodes/Extra')
        self.assertIn('remove_resource', row['proposals'])
        self.assertIn('removal_would_affect_unmanaged_content', row['blockers'])

    def test_snapshot_content_cannot_restore_manager_via_core(self):
        target = self.save()
        core = self.resource(target, 'core')
        core['content']['files'][0]['path'] = 'custom_nodes/renamed-manager/__init__.py'
        with self.assertRaisesRegex(ValueError, 'outside managed scope'):
            self.compare(target)

    def test_all_file_entries_compared_not_just_last(self):
        for name in ('a.py', 'b.py', 'z.py'):
            (self.local / name).write_text(name)
        target = self.save()
        (self.local / 'a.py').write_text('modified')
        (self.local / 'b.py').write_text('also modified')
        paths = {c.get('path') for c in self.row(self.compare(target))['differences']}
        self.assertEqual(paths, {'a.py', 'b.py'})

    def test_state_artifact_disagreement_rejected(self):
        target = self.save()
        self.resource(target, 'plugin:custom_nodes/LocalPlugin')['content']['files'][0]['sha256'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'disagree'):
            self.compare(target)

    def test_legacy_snapshot_not_upgraded_into_plan(self):
        target = self.save()
        with patch.object(records, 'read', return_value={'schema': 3}), self.assertRaisesRegex(ValueError, 'schema 4'):
            diff.inspect(target['id'])

    def test_lock_conflict_rejected_without_scan(self):
        target = self.save()
        with records.LOCK, patch.object(diff, 'current_state') as scan:
            with self.assertRaisesRegex(ValueError, 'running'):
                diff.inspect(target['id'])
            scan.assert_not_called()

    def test_git_inspection_disables_optional_writes(self):
        with patch.object(records.subprocess, 'run', return_value=type('Result', (), {'returncode': 0, 'stdout': ''})()) as run:
            records.git(self.local, 'status', '--porcelain')
        command = run.call_args.args[0]
        self.assertIn('--no-optional-locks', command)
        self.assertIn('core.fsmonitor=false', command)

    def test_recovery_plan_is_exactly_derived_from_diff_proposals(self):
        target = self.save()
        (self.local / '__init__.py').write_text('changed')
        result = self.compare(target)
        plan = diff.recovery_plan(result, target, diff.object_health(target, self.store))
        self.assertEqual([(a['resource_id'], a['operations']) for a in plan['actions']],
                         [(r['resource_id'], r['proposals']) for r in result['resources'] if r['proposals']])

    def test_stable_fingerprint_for_unchanged_environment(self):
        target = self.save()
        self.assertEqual(diff.inspect(target['id'])['fingerprint'], diff.inspect(target['id'])['fingerprint'])

    def test_real_git_changes_compare_content_without_checkout(self):
        def git(*args):
            run = subprocess.run(['git', '--no-optional-locks', '-c', 'safe.directory=' + str(self.local), '-C', str(self.local), *args], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            return run.stdout.strip()
        git('init'); git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.invalid')
        git('add', '.'); git('commit', '-m', 'target')
        first = git('rev-parse', 'HEAD')
        git('remote', 'add', 'origin', 'https://example.org/plugin.git')
        git('update-ref', 'refs/remotes/origin/main', first)
        original = records.git
        with patch.object(records, 'git', side_effect=lambda path, *args: git(*args) if Path(path) == self.local else original(path, *args)):
            target = self.save()
            (self.local / '__init__.py').write_text('next commit')
            git('add', '.'); git('commit', '-m', 'current')
            second = git('rev-parse', 'HEAD')
            row = self.row(self.compare(target))
            change = next(c for c in row['differences'] if c['field'] == 'content')
            self.assertNotEqual(change['current']['sha256'], change['target']['sha256'])
            self.assertIn('git_metadata_changed', row['warnings'])
            self.assertIn('restore_content', row['proposals'])
            self.assertEqual(git('rev-parse', 'HEAD'), second)


if __name__ == '__main__':
    unittest.main()
