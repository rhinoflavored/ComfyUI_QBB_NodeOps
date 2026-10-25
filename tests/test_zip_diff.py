"""Compare installed bytes and ZIP restore ability without modifying a live runtime."""
import copy
import importlib
import shutil
from unittest.mock import patch

from test_local_archives import InstalledDistribution
from test_phase1_snapshot import package, CaptureFixture

diff = importlib.import_module(package.__name__ + '.state_diff')
restore = importlib.import_module(package.__name__ + '.current_restore')


class ZipDiffTests(CaptureFixture):
    def test_content_only_compares_once_without_zip_verification_or_restore_authorization(self):
        target = self.setup_package()
        with (patch.object(diff, 'object_health', side_effect=AssertionError('No ZIP verification')),
              patch.object(diff, 'current_state', wraps=diff.current_state) as scanned):
            plan = diff.inspect(target['id'], content_only=True, progress_id='a' * 32)
        self.assertEqual(scanned.call_count, 1)
        self.assertEqual(self.row(plan)['status'], 'matched')
        self.assertFalse(plan['materials_checked'])
        self.assertFalse(plan['execution_ready'])
        self.assertFalse(plan['execution_allowed'])
        self.assertEqual(diff.comparison_progress('a' * 32)['status'], 'completed')

    def test_shared_file_is_confirmed_content_instead_of_unknown(self):
        self.distributions = [InstalledDistribution(self.library, 'one', ['shared.py']), InstalledDistribution(self.library, 'two', ['shared.py'])]
        target = self.save()
        plan = self.plan(target)
        self.assertEqual(plan['diff']['unverified_count'], 0)
        self.assertEqual(plan['status'], 'no_changes')
        self.assertEqual(self.row(plan, 'package:one')['status'], 'matched')
        # Historical gap labels do not invalidate saved bytes during comparison.
        old = copy.deepcopy(target)
        for row in old['resources']:
            if row['kind'] == 'package':
                row['installed_inventory']['gaps'].append({'path': 'Lib/site-packages/shared.py', 'reason': 'shared_installed_file'})
        result = diff.compare(self.scan()[0], old)
        self.assertEqual(result['unverified_count'], 0)

    def setUp(self):
        super().setUp()
        self.runtime = self.root / 'python'
        self.library = self.runtime / 'Lib/site-packages'
        self.library.mkdir(parents=True)
        self.scripts = self.runtime / 'Scripts'
        self.scripts.mkdir()
        original = diff.local_archives.package_inventories
        patched = patch.object(diff.local_archives, 'package_inventories', side_effect=lambda distributions, manager: original(distributions, manager, self.runtime, [self.library], self.scripts))
        patched.start()
        self.addCleanup(patched.stop)

    def scan(self, label='', read_only=False):
        return self.original_scan(self.core, [self.nodes], self.manager, self.distributions, self.store, label, read_only)

    def archive_path(self, record):
        return self.store / record['backup']['components']['package:demo']['storage']['object_path']
    def plan(self, target):
        return diff.inspect(target['id'])

    def row(self, plan, identity='package:demo'):
        return next(r for r in plan['diff']['resources'] if r['resource_id'] == identity)

    def action(self, plan, identity='package:demo'):
        return next(a for a in plan['actions'] if a['resource_id'] == identity)

    def setup_package(self):
        self.distributions = [InstalledDistribution(self.library)]
        return self.save()

    def test_unknown_origin_identical_installed_files_match(self):
        target = self.setup_package()
        plan = self.plan(target)
        self.assertEqual(self.row(plan)['status'], 'matched')
        self.assertEqual(plan['status'], 'no_changes')
        self.assertFalse(plan['blockers'])
        self.assertFalse(plan['actions'])
        self.assertEqual(self.row(plan)['comparison_basis'], 'installed_files')

    def test_same_name_and_version_modified_binary_uses_zip(self):
        target = self.setup_package()
        (self.library / 'demo/native.pyd').write_bytes(b'changed binary')
        before = (self.library / 'demo/native.pyd').read_bytes()
        plan = self.plan(target)
        self.assertTrue(any(c['status'] == 'content_mismatch' for c in self.row(plan)['differences']))
        self.assertEqual(self.action(plan)['material']['kind'], 'local_zip')
        self.assertEqual(plan['status'], 'changes_proposed')
        self.assertFalse(plan['blockers'])
        self.assertTrue(plan['execution_ready'])
        self.assertIsNone(plan['execution_notice'])
        self.assertEqual((self.library / 'demo/native.pyd').read_bytes(), before)

    def test_missing_package_restores_from_zip_without_source(self):
        target = self.setup_package()
        self.distributions = []
        plan = self.plan(target)
        self.assertEqual(self.row(plan)['differences'][0]['status'], 'missing')
        self.assertEqual(self.action(plan)['material']['kind'], 'local_zip')
        self.assertFalse(plan['blockers'])

    def test_new_package_is_removal_not_source_blocker(self):
        target = self.save()
        self.distributions = [InstalledDistribution(self.library)]
        plan = self.plan(target)
        self.assertEqual(self.row(plan)['differences'][0]['status'], 'extra')
        self.assertEqual(self.action(plan)['material']['kind'], 'installed_file_removal')
        self.assertFalse(plan['blockers'])
        self.assertTrue(plan['execution_ready'])

    def test_bad_zip_does_not_turn_matched_package_into_change(self):
        target = self.setup_package()
        self.archive_path(target).write_bytes(b'bad ZIP')
        plan = self.plan(target)
        self.assertEqual(self.row(plan)['status'], 'matched')
        self.assertEqual(plan['status'], 'no_changes')
        self.assertFalse(plan['blockers'])
        self.assertTrue(plan['backup_issues'])

    def test_bad_zip_blocks_only_needed_package_change(self):
        target = self.setup_package()
        self.archive_path(target).write_bytes(b'bad ZIP')
        (self.library / 'demo/native.pyd').write_bytes(b'changed')
        plan = self.plan(target)
        self.assertEqual(self.action(plan)['status'], 'blocked')
        self.assertIn('target_zip_unavailable_or_invalid', self.action(plan)['blockers'])
        self.assertEqual(len(plan['blockers']), 1)

    def test_incomplete_target_zip_is_not_full_restore_material(self):
        dist = InstalledDistribution(self.library)
        (self.library / 'demo/native.pyd').unlink()
        self.distributions = [dist]
        target = self.save()
        self.distributions = []
        plan = self.plan(target)
        self.assertIn('target_backup_incomplete', self.action(plan)['blockers'])

    def test_origin_change_with_identical_installed_bytes_is_warning_only(self):
        target = self.setup_package()
        current, _ = diff.current_state()
        row = next(r for r in current['resources'] if r['id'] == 'package:demo')
        row['source'] = {'kind': 'remote_wheel', 'url': 'https://example.invalid/demo.whl', 'sha256': 'a' * 64}
        result = diff.compare(current, target)
        self.assertEqual(next(r for r in result['resources'] if r['resource_id'] == 'package:demo')['status'], 'matched')

    def test_unretrievable_git_can_use_saved_local_archive(self):
        target = self.save()
        changed = copy.deepcopy(target)
        row = next(r for r in changed['resources'] if r['id'] == 'plugin:custom_nodes/LocalPlugin')
        row['source'] = {'kind': 'git', 'url': 'https://example.invalid/private.git', 'commit': 'a' * 40}
        row['recoverability'] = {'status': 'unrestorable', 'reason': 'git_commit_not_retrievable'}
        current = copy.deepcopy(changed)
        current['artifacts'][row['artifact_ref']]['files'][0]['sha256'] = 'b' * 64
        next(r for r in current['resources'] if r['id'] == row['id'])['content']['files'][0]['sha256'] = 'b' * 64
        result = diff.compare(current, changed)
        plan = diff.recovery_plan(result, changed, diff.object_health(changed, self.store))
        self.assertFalse(plan['blockers'])
        self.assertEqual(self.action(plan, row['id'])['material']['kind'], 'local_zip')

    def test_git_metadata_change_is_not_content_change_with_local_evidence(self):
        target = self.save()
        current, _ = diff.current_state()
        row = next(r for r in current['resources'] if r['id'] == 'plugin:custom_nodes/LocalPlugin')
        row['source'] = {'kind': 'git', 'url': 'https://example.invalid/private.git', 'commit': 'b' * 40}
        result = diff.compare(current, target)
        compared = next(r for r in result['resources'] if r['resource_id'] == row['id'])
        self.assertEqual(compared['status'], 'matched')
        self.assertFalse(compared['proposals'])
        self.assertIn('git_metadata_changed', compared['warnings'])

    def test_old_wheel_inventory_is_not_compared_as_installed_tree(self):
        target = self.setup_package()
        row = next(r for r in target['resources'] if r['id'] == 'package:demo')
        row.pop('installed_inventory')
        row.pop('archive_ref')
        row.pop('archive_content')
        row.pop('archive_verification')
        current, _ = diff.current_state()
        result = diff.compare(current, target)
        compared = next(r for r in result['resources'] if r['resource_id'] == row['id'])
        self.assertEqual(compared['status'], 'unknown')
        self.assertFalse(compared['proposals'])
        self.assertGreater(result['unverified_count'], 0)

    def test_git_package_without_installed_backup_cannot_claim_reproducible_build(self):
        target = self.setup_package()
        row = next(r for r in target['resources'] if r['id'] == 'package:demo')
        for field in ('installed_inventory', 'archive_ref', 'archive_content', 'archive_verification'):
            row.pop(field)
        row['source'] = {'kind': 'git', 'url': 'https://example.invalid/demo.git', 'commit': 'a' * 40}
        row['recoverability'] = {'status': 'unrestorable', 'reason': 'source_build_not_verified'}
        self.distributions = []
        current, _ = diff.current_state()
        result = diff.compare(current, target)
        plan = diff.recovery_plan(result, target, diff.object_health(target, self.store))
        self.assertIn('source_build_not_verified', self.action(plan)['blockers'])

    def test_both_local_materials_missing_block_changed_plugin(self):
        target = self.save()
        row = next(r for r in target['resources'] if r['id'] == 'plugin:custom_nodes/LocalPlugin')
        for ref in (row['artifact_ref'], row['archive_ref']):
            for entry in target['artifacts'][ref]['files']:
                diff.files.object_path(self.store, entry['sha256']).unlink(missing_ok=True)
        shutil.rmtree(self.local)
        plan = self.plan(target)
        self.assertEqual(self.action(plan, row['id'])['status'], 'blocked')

    def test_new_zip_plan_still_requires_explicit_preparation_confirmation(self):
        target = self.setup_package()
        (self.library / 'demo/native.pyd').write_bytes(b'changed')
        plan = self.plan(target)
        runtime = {'core': str(self.core), 'manager': str(self.manager), 'plugin_roots': [str(self.nodes)], 'store': str(self.store)}
        current, _ = diff.current_state()
        with patch.object(restore, 'idle'), patch.object(restore, 'scan', return_value=current):
            with self.assertRaisesRegex(ValueError, 'Confirm'):
                restore.prepare(target['id'], plan['fingerprint'], False, runtime=runtime)
        self.assertFalse((self.manager / 'restore-tasks').exists())
        self.assertFalse((self.manager / restore.ACTIVE).exists())
