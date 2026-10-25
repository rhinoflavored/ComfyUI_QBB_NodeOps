"""Backup contract regression: temporary fixtures, no live package changes."""
import copy
import unittest

from test_phase1_snapshot import CaptureFixture, Distribution, state


class BackupContractTests(CaptureFixture):
    def test_published_contract_and_unknown_package(self):
        self.distributions = [Distribution()]
        record = self.save()
        backup = record['backup']
        self.assertEqual(backup['version'], 2)
        self.assertEqual(set(backup['components']), {r['id'] for r in record['resources']})
        package = backup['components']['package:demo']
        self.assertEqual(package['status'], 'incomplete')
        self.assertFalse(package['installed_files_saved'])
        self.assertIsNone(package['storage'])
        self.assertEqual(package['files'], [])
        self.assertEqual(package['recovery_reason'], 'source_unknown')
        for row in record['resources']:
            descriptor = backup['components'][row['id']]
            if row.get('artifact_ref'):
                self.assertEqual(descriptor['object_snapshot_ref'], row['artifact_ref'])
                self.assertEqual(descriptor['storage']['format'], 'zip')
                self.assertTrue(descriptor['files'])
            if row['recoverability']['status'] == 'protected_resource':
                self.assertEqual(descriptor['status'], 'protected')
                self.assertIsNone(descriptor['storage'])

    def test_old_record_normalized_without_modifying_input(self):
        record = self.save()
        expected = record.pop('backup')
        original = copy.deepcopy(record)
        self.assertEqual(state.validate(record)['backup'], expected)
        self.assertEqual(record, original)

    def test_reject_fabricated_backup_and_inventory(self):
        original = self.save()
        saved_id = next(r['id'] for r in original['resources'] if r.get('artifact_ref'))
        for modify in (
            lambda b: b.update(version=3),
            lambda b: b['components'].pop(saved_id),
            lambda b: b['scope'].update(external_resources='capture'),
            lambda b: b['components'][saved_id]['files'][0].update(sha256='0'*64),
            lambda b: b['components'][saved_id]['storage'].update(artifact_ref='missing'),
        ):
            record = copy.deepcopy(original)
            modify(record['backup'])
            with self.assertRaisesRegex(ValueError, 'Backup contract'):
                state.validate(record)

    def test_local_files_independent_of_git_retrievability(self):
        record = self.save()
        record.pop('backup')
        row = next(r for r in record['resources'] if r['kind'] == 'plugin' and r.get('artifact_ref'))
        row['recoverability'] = state.recovery('unrestorable', 'git_commit_not_retrievable')
        descriptor = state.validate(record)['backup']['components'][row['id']]
        self.assertEqual(descriptor['status'], 'local_saved')
        self.assertEqual(descriptor['recovery_reason'], 'git_commit_not_retrievable')

    def test_saved_files_with_gap_are_incomplete(self):
        record = self.save()
        record.pop('backup')
        row = next(r for r in record['resources'] if r.get('artifact_ref'))
        record['artifacts'][row['artifact_ref']]['skipped'].append({'path': 'missing.bin', 'reason': 'unreadable'})
        with self.assertRaisesRegex(ValueError, 'ZIP verification does not match'):
            state.validate(record)
        row.pop('archive_verification')
        descriptor = state.validate(record)['backup']['components'][row['id']]
        self.assertEqual(descriptor['status'], 'incomplete')
        self.assertEqual(descriptor['gaps'][0]['path'], 'missing.bin')


if __name__ == '__main__':
    unittest.main()
