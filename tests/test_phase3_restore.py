"""All writes target temporary fixture instances, never the user's installation."""
import copy
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess
from unittest.mock import patch

from test_phase1_snapshot import CaptureFixture, Distribution, package, state, records

restore = importlib.import_module(package.__name__ + '.current_restore')
material = restore.material
diff = restore.diff


class RestoreTests(CaptureFixture):
    def scan(self, label='', read_only=False, **kwargs):
        return self.original_scan(self.core, [self.nodes], self.manager, self.distributions, self.store, label, read_only)

    def runtime(self):
        return {'core': str(self.core), 'manager': str(self.manager), 'plugin_roots': [str(self.nodes)],
                'store': str(self.store), 'python': sys.executable, 'cwd': str(self.core),
                'argv': [sys.executable, str(self.core / 'main.py')],
                'parent_pid': os.getpid(), 'parent_birth': restore.restore_process.identity(os.getpid())}

    def prepare(self, target):
        plan = diff.inspect(target['id'])
        return restore.prepare(target['id'], plan['fingerprint'], True, runtime=self.runtime())

    def run_offline(self, prepared):
        job = restore.job_path(prepared['id'], self.manager)
        value = restore.frozen(job, prepared['plan_hash'])
        with patch.object(restore.restore_process, 'identity', return_value=None):
            outcome = restore.execute(job, value)
        return job, value, outcome

    def finalize(self, job, outcome='applied'):
        restore.update(job, status='awaiting_verification', restarted_pid=os.getpid(),
                       restarted_birth=restore.restore_process.identity(os.getpid()), outcome=outcome)
        return restore.reconnect(runtime=self.runtime(), job_id=job.name, service_ready=True)

    def test_prepare_does_not_change_live_content(self):
        target = self.save()
        (self.local / '__init__.py').write_text('changed')
        p = self.prepare(target)
        self.assertEqual(p['status'], 'prepared')
        self.assertEqual((self.local / '__init__.py').read_text(), 'changed')

    def test_local_content_restore_and_final_cleanup(self):
        target = self.save()
        (self.local / '__init__.py').write_text('changed')
        job, value, outcome = self.run_offline(self.prepare(target))
        self.assertEqual(outcome, 'applied')
        self.assertEqual((self.local / '__init__.py').read_text(), 'original')
        self.assertTrue((job / 'backup').exists())
        self.assertEqual(self.finalize(job)['status'], 'completed')
        self.assertFalse((job / 'backup').exists())
        self.assertFalse((job / 'payload').exists())
        self.assertTrue((job / 'verification.json').exists())
        with patch.object(restore, 'manager_path', return_value=self.manager):
            details = restore.describe(job.name)
        self.assertEqual(details['plan']['status'], 'changes_proposed')
        self.assertEqual(details['verification']['status'], 'no_changes')
        self.assertEqual(details['action_results'][0]['status'], 'applied')
        self.assertEqual(details['action_results'][0]['operation'], 'restore')
        self.assertTrue(details['action_results'][0]['saved_artifact'])
        self.assertEqual(details['progress']['phase'], 'completed')
        self.assertEqual(details['task_directory'], str(job))

    def test_original_process_still_running_refuses_writes(self):
        target = self.save()
        (self.local / '__init__.py').write_text('changed')
        p = self.prepare(target)
        job = restore.job_path(p['id'], self.manager)
        with self.assertRaisesRegex(ValueError, 'still running'):
            restore.execute(job, restore.frozen(job, p['plan_hash']))
        self.assertEqual((self.local / '__init__.py').read_text(), 'changed')
        self.assertFalse((job / 'backup').exists())

    def test_stale_fingerprint_and_confirmation(self):
        target = self.save()
        with self.assertRaisesRegex(ValueError, 'Confirm'):
            restore.prepare(target['id'], 'stale', runtime=self.runtime())
        with self.assertRaisesRegex(ValueError, 'stale'):
            restore.prepare(target['id'], 'stale', True, runtime=self.runtime())
        self.assertFalse((self.manager / 'restore-tasks').exists())

    def test_changed_environment_after_preparation_refused(self):
        target = self.save()
        (self.local / '__init__.py').write_text('changed')
        p = self.prepare(target)
        (self.local / 'new.py').write_text('race')
        with self.assertRaisesRegex(ValueError, 'changed after shutdown'):
            self.run_offline(p)

    def test_payload_and_frozen_hash_tampering_rejected(self):
        target = self.save()
        (self.local / '__init__.py').write_text('changed')
        p = self.prepare(target)
        job = restore.job_path(p['id'], self.manager)
        (job / 'payload' / 'unexpected').write_text('tamper')
        with self.assertRaisesRegex(ValueError, 'payload changed'):
            restore.frozen(job, p['plan_hash'])
        with self.assertRaisesRegex(ValueError, 'plan was modified'):
            restore.frozen(job, '0' * 64)

    def test_unknown_package_blocks_before_task_creation(self):
        self.distributions = [Distribution()]
        target = self.save()
        (self.local / '__init__.py').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'comparison_incomplete'):
            self.prepare(target)
        self.assertFalse((self.manager / 'restore-tasks').exists())

    def test_missing_plugin_restored_and_extra_plugin_removed(self):
        target = self.save()
        shutil.rmtree(self.local)
        extra = self.nodes / 'Extra'
        extra.mkdir()
        (extra / '__init__.py').write_text('extra')
        job, _, _ = self.run_offline(self.prepare(target))
        self.assertFalse(extra.exists())
        self.assertEqual((self.local / '__init__.py').read_text(), 'original')
        self.assertEqual(self.finalize(job)['status'], 'completed')

    def test_configuration_and_core_restore_preserve_personal_content(self):
        config = self.core / 'extra_model_paths.yaml'
        config.write_text('target: config')
        target = self.save()
        config.write_text('current: config')
        (self.core / 'main.py').write_text('changed')
        for folder in state.SCOPE['excluded_directories']:
            (self.core / folder).mkdir()
            (self.core / folder / 'keep.txt').write_text('personal')
        job, _, _ = self.run_offline(self.prepare(target))
        self.assertEqual(config.read_text(), 'target: config')
        self.assertEqual((self.core / 'main.py').read_text(), 'pass')
        for folder in state.SCOPE['excluded_directories']:
            self.assertEqual((self.core / folder / 'keep.txt').read_text(), 'personal')
        self.assertEqual(self.finalize(job)['status'], 'completed')

    def test_extra_configuration_removed(self):
        target = self.save()
        config = self.core / 'extra_model_paths.yaml'
        config.write_text('new')
        job, _, _ = self.run_offline(self.prepare(target))
        self.assertFalse(config.exists())
        self.assertEqual(self.finalize(job)['status'], 'completed')

    def test_directory_file_type_changes(self):
        (self.local / 'dir').mkdir()
        (self.local / 'file').write_text('target')
        target = self.save()
        (self.local / 'dir').rmdir()
        (self.local / 'dir').write_text('wrong type')
        (self.local / 'file').unlink()
        (self.local / 'file').mkdir()
        (self.local / 'file' / 'old.py').write_text('extra')
        job, _, _ = self.run_offline(self.prepare(target))
        self.assertTrue((self.local / 'dir').is_dir())
        self.assertEqual((self.local / 'file').read_text(), 'target')
        self.assertEqual(self.finalize(job)['status'], 'completed')

    def test_failed_write_rolls_back_prior_actions(self):
        target = self.save()
        (self.local / '__init__.py').write_text('before')
        (self.core / 'main.py').write_text('before-core')
        p = self.prepare(target)
        original = material.apply_code
        calls = []
        def fail_second(*args):
            calls.append(1)
            if len(calls) == 2:
                raise OSError('injected failure')
            return original(*args)
        with patch.object(material, 'apply_code', side_effect=fail_second):
            job, _, outcome = self.run_offline(p)
        self.assertEqual(outcome, 'rolled_back')
        self.assertEqual((self.core / 'main.py').read_text(), 'before-core')
        self.assertEqual((self.local / '__init__.py').read_text(), 'before')
        self.assertEqual(self.finalize(job, outcome)['status'], 'failed_rolled_back')
        self.assertTrue((job / 'payload').exists())

    def test_residual_diff_never_success_or_cleanup(self):
        target = self.save()
        (self.local / '__init__.py').write_text('changed')
        job, _, _ = self.run_offline(self.prepare(target))
        (self.local / '__init__.py').write_text('new race')
        self.assertEqual(self.finalize(job)['status'], 'partial')
        self.assertTrue((job / 'backup').exists())

    def test_protected_and_external_paths_refused_by_journal(self):
        job = self.manager / 'restore-tasks' / ('a' * 32)
        job.mkdir(parents=True)
        journal = material.Journal(job, self.core, self.manager)
        for path in (self.manager, self.manager / '__init__.py', self.nodes, self.core / 'user' / 'file', self.root / 'outside'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                journal.replace(path)

    def test_extra_plugin_with_personal_content_is_not_removed(self):
        target = self.save()
        extra = self.nodes / 'Extra' / 'user'
        extra.mkdir(parents=True)
        (extra / 'keep.txt').write_text('personal')
        with self.assertRaisesRegex(ValueError, 'blocked'):
            self.prepare(target)
        self.assertEqual((extra / 'keep.txt').read_text(), 'personal')

    def test_corrupt_target_object_blocks_before_preparation(self):
        target = self.save()
        resource = self.resource(target, 'plugin:custom_nodes/LocalPlugin')
        sha = target['artifacts'][resource['artifact_ref']]['files'][0]['sha256']
        state.files.object_path(self.store, sha).write_text('corrupted')
        self.assertEqual(self.prepare(target)['status'], 'no_changes')
        (self.local / '__init__.py').write_text('changed')
        prepared = self.prepare(target)
        job = restore.job_path(prepared['id'], self.manager)
        payload = restore.frozen(job, prepared['plan_hash'])['actions'][0]['payload']
        self.assertTrue(payload['preserve_repository'])
        self.assertEqual((Path(payload['content']) / '__init__.py').read_text(), 'original')

    def test_cancel_prepared_cleans_only_task_payload(self):
        target = self.save()
        (self.local / '__init__.py').write_text('changed')
        p = self.prepare(target)
        with patch.object(restore, 'manager_path', return_value=self.manager):
            self.assertEqual(restore.cancel(p['id'], True)['status'], 'cancelled')
        self.assertEqual((self.local / '__init__.py').read_text(), 'changed')

    def test_cleanup_refuses_escape(self):
        with self.assertRaises(ValueError):
            material.remove_task_tree(self.manager, self.manager / '..' / 'LocalPlugin')
        self.assertTrue(self.local.exists())

    def test_task_lock_exclusive_and_pid_identity(self):
        path = self.root / 'worker.lock'
        with restore.restore_process.task_lock(path):
            with self.assertRaises(OSError):
                with restore.restore_process.task_lock(path):
                    pass
        self.assertIsNotNone(restore.restore_process.identity(os.getpid()))
        with self.assertRaises(ValueError):
            restore.restore_process.identity(-1)

    def test_git_exact_commit_and_dirty_content_restored(self):
        def git(*args):
            return material.git(self.local, *args)
        git('init')
        git('config', 'user.email', 'fixture@example.invalid')
        git('config', 'user.name', 'Fixture')
        git('add', '.')
        git('commit', '-m', 'one')
        first = git('rev-parse', 'HEAD')
        git('remote', 'add', 'origin', 'https://example.invalid/plugin.git')
        git('update-ref', 'refs/remotes/origin/main', first)
        (self.local / 'dirty.py').write_text('saved dirty content')
        target = self.save()
        (self.local / '__init__.py').write_text('second commit')
        git('add', '.')
        git('commit', '-m', 'two')
        (self.local / 'dirty.py').write_text('changed again')
        job, _, _ = self.run_offline(self.prepare(target))
        self.assertEqual(git('rev-parse', 'HEAD'), first)
        self.assertEqual((self.local / 'dirty.py').read_text(), 'saved dirty content')
        self.assertEqual(self.finalize(job)['status'], 'completed')

    def test_worker_times_out_without_parent_exit_and_never_writes(self):
        worker = importlib.import_module(package.__name__ + '.restore_worker')
        target = self.save()
        (self.local / '__init__.py').write_text('current')
        p = self.prepare(target)
        job = restore.job_path(p['id'], self.manager)
        restore.update(job, status='starting')
        restore.atomic_json(job / 'handoff.json', {'plan_hash': p['plan_hash'], 'parent_birth': self.runtime()['parent_birth']})
        with patch.object(worker, 'modules', return_value=restore), patch.object(worker, '__file__', str(self.manager / 'qbb_nodeops' / 'restore_worker.py')):
            self.assertEqual(worker.work(job, p['plan_hash'], exit_timeout=0), 2)
        self.assertEqual((self.local / '__init__.py').read_text(), 'current')
        self.assertFalse((job / 'backup').exists())
        self.assertEqual(restore.status(job.name, self.manager)['status'], 'handoff_failed')

    def test_worker_requires_handoff_after_readiness(self):
        worker = importlib.import_module(package.__name__ + '.restore_worker')
        target = self.save()
        (self.local / '__init__.py').write_text('current')
        p = self.prepare(target)
        job = restore.job_path(p['id'], self.manager)
        restore.update(job, status='starting')
        with patch.object(worker, 'modules', return_value=restore), patch.object(worker, '__file__', str(self.manager / 'qbb_nodeops' / 'restore_worker.py')):
            self.assertEqual(worker.work(job, p['plan_hash'], handoff_timeout=0), 2)
        self.assertFalse((job / 'backup').exists())

    def test_original_local_wheel_required_for_rollback(self):
        wheel = self.wheel()
        target = self.save()
        # Even target objects cannot invent the different current package's original wheel.
        self.distributions = [Distribution(version='2.0', direct={'url': (self.root / 'demo-2.0-py3-none-any.whl').as_uri(), 'archive_info': {}})]
        with self.assertRaisesRegex(ValueError, 'blocked|original_artifact_missing|comparison_incomplete'):
            self.prepare(target)

    def test_pip_changes_explicitly_blocked(self):
        with self.assertRaisesRegex(ValueError, 'protected'):
            material.prepare_wheel({'name': 'pip'}, {}, self.store, self.root / 'wheel', False)

    def test_preparation_disk_space_failure_does_not_modify_live(self):
        target = self.save()
        (self.local / '__init__.py').write_text('current')
        with patch.object(restore.shutil, 'disk_usage', return_value=__import__('types').SimpleNamespace(free=0)):
            with self.assertRaisesRegex(ValueError, 'Insufficient'):
                self.prepare(target)
        self.assertEqual((self.local / '__init__.py').read_text(), 'current')
        self.assertEqual(restore.status(manager=self.manager)['status'], 'preparation_failed')

    def test_start_refuses_stale_state_without_spawn(self):
        target = self.save()
        (self.local / '__init__.py').write_text('current')
        p = self.prepare(target)
        (self.local / 'race.py').write_text('changed after prepare')
        with patch.object(restore, 'context', return_value=self.runtime()), patch.object(restore, 'idle'), patch.object(restore.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(ValueError, 'changed since preparation'):
                restore.start(p['id'], p['plan_hash'], True)
        spawn.assert_not_called()

    def test_start_refuses_expired_plan_and_another_process(self):
        target = self.save()
        (self.local / '__init__.py').write_text('current')
        p = self.prepare(target)
        for runtime, now in [(self.runtime(), __import__('time').time() + 3600), ({**self.runtime(), 'parent_pid': 123}, __import__('time').time())]:
            with patch.object(restore, 'context', return_value=runtime), patch.object(restore, 'idle'), patch.object(restore.time, 'time', return_value=now), patch.object(restore.subprocess, 'Popen') as spawn:
                with self.assertRaisesRegex(ValueError, 'expired|another process'):
                    restore.start(p['id'], p['plan_hash'], True)
            spawn.assert_not_called()

    def test_cleanup_failure_never_success(self):
        target = self.save()
        (self.local / '__init__.py').write_text('current')
        job, _, _ = self.run_offline(self.prepare(target))
        with patch.object(material, 'remove_task_tree', side_effect=PermissionError('locked staging')):
            result = self.finalize(job)
        self.assertEqual(result['status'], 'partial')
        self.assertTrue((job / 'payload').exists())

    def test_rollback_refuses_to_overwrite_concurrent_user_change(self):
        job = self.manager / 'restore-tasks' / ('a' * 32)
        payload = job / 'payload'
        payload.mkdir(parents=True)
        (payload / 'new.py').write_text('worker contents')
        journal = material.Journal(job, self.core, self.manager)
        path = self.local / '__init__.py'
        journal.replace(path, payload / 'new.py')
        path.write_text('concurrent user change')
        with self.assertRaisesRegex(ValueError, 'destination changed'):
            journal.rollback()
        self.assertEqual(path.read_text(), 'concurrent user change')
        self.assertEqual((job / 'backup' / '0').read_text(), 'original')

    def test_wrong_restart_identity_retains_staging(self):
        target = self.save()
        (self.local / '__init__.py').write_text('current')
        job, _, _ = self.run_offline(self.prepare(target))
        restore.update(job, status='awaiting_verification', restarted_pid=os.getpid(), restarted_birth='wrong', outcome='applied')
        self.assertEqual(restore.reconnect(runtime=self.runtime(), service_ready=True)['status'], 'partial')
        self.assertTrue((job / 'backup').exists())

    def test_verified_receipt_invalidated_by_changed_install_metadata(self):
        direct = {'url': 'file:///prepared/demo.whl', 'archive_info': {'hashes': {'sha256': 'a' * 64}}}
        receipt = {'version': '1.0', 'observed': restore.restore_receipts.digest(direct), 'sha256': 'a' * 64,
                   'source': {'kind': 'remote_wheel', 'url': 'https://example.invalid/demo.whl', 'sha256': 'a' * 64}}
        self.assertEqual(restore.restore_receipts.match({'demo': receipt}, 'demo', '1.0', direct), receipt)
        changed = {**direct, 'url': 'file:///different/demo.whl'}
        self.assertIsNone(restore.restore_receipts.match({'demo': receipt}, 'demo', '1.0', changed))
        self.assertIsNone(restore.restore_receipts.match({'demo': receipt}, 'demo', '2.0', direct))

    def test_final_verification_requires_ready_service(self):
        with self.assertRaisesRegex(ValueError, 'service probe'):
            restore.reconnect(runtime=self.runtime())

    def test_uninstall_inventory_cannot_escape_interpreter(self):
        distribution = __import__('types').SimpleNamespace(files=['outside'], locate_file=lambda entry: self.root / 'outside')
        with patch.object(material.importlib.metadata, 'distribution', return_value=distribution):
            with self.assertRaisesRegex(ValueError, 'outside current interpreter'):
                material.package_scope('demo', self.manager)

    def test_remote_wheel_wrong_hash_never_falls_back_to_index(self):
        original = self.wheel()
        row = {'name': 'demo', 'version': '1.0', 'source': {'kind': 'remote_wheel',
               'url': 'https://example.invalid/demo-1.0-py3-none-any.whl', 'sha256': '0' * 64}}
        with patch.object(material, 'download', side_effect=lambda url, dest: shutil.copyfile(original, dest)) as download:
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                material.prepare_wheel(row, {}, self.store, self.root / 'downloads', True)
        download.assert_called_once()

    def test_missing_git_source_requires_explicit_network_preparation(self):
        row = {'source': {'kind': 'git', 'url': 'https://example.invalid/plugin.git', 'commit': 'a' * 40}}
        with self.assertRaisesRegex(ValueError, 'explicit network'):
            material.fetch_git(row, self.root / 'repo', None, False)
