"""Current capture contract; legacy formats are covered by test_manifest/archives."""
import copy
from unittest.mock import patch
import test_phase1_snapshot as fixture

class RecordCaptureTests(fixture.CaptureFixture):
    def test_unreadable_component_is_explicit_gap(self):
        original = fixture.state.files.scan
        def unreadable(source, *args, **kwargs):
            result = original(source, *args, **kwargs)
            if source == self.local:
                result['errors'].append({'path': '.', 'reason': 'fixture permission denied'})
            return result
        with patch.object(fixture.state.files, 'scan', side_effect=unreadable):
            row = self.resource(self.save(), 'plugin:custom_nodes/LocalPlugin')
        self.assertEqual(row['recoverability']['reason'], 'source_scan_failed')
        self.assertIsNone(row['artifact_ref'])

    def test_preview_token_is_single_use(self):
        preview = fixture.records.preview()
        fixture.records.create(preview_id=preview['preview_id'])
        with self.assertRaises(ValueError):
            fixture.records.create(preview_id=preview['preview_id'])

    def test_listing_contains_new_schema_and_gap_count(self):
        self.distributions = [fixture.Distribution()]
        record = self.save()
        item = fixture.records.listing()['items'][0]
        self.assertEqual(item['id'], record['id'])
        self.assertEqual(item['schema'], 4)
        self.assertEqual(item['warnings'], 1)
        self.assertTrue(item['capabilities']['restore'])
        self.assertEqual(item['capabilities']['reason'], 'phase_3_strict_preflight_required')

    def test_source_changes_during_save_do_not_publish(self):
        original = fixture.state.files.capture
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.local / '__init__.py').write_text('changed after copying')
            return result
        preview = fixture.records.preview()
        with patch.object(fixture.state.files, 'capture', side_effect=changed):
            with self.assertRaises(ValueError):
                fixture.records.create(preview_id=preview['preview_id'])
        self.assertEqual(list(self.store.glob('*.json')), [])

    def test_missing_submodule_not_reported_as_complete(self):
        original = fixture.records.repository
        def metadata(path):
            row = original(path)
            if path == self.local:
                row.update(kind='git', submodule_error='uninitialized')
            return row
        with patch.object(fixture.records, 'repository', side_effect=metadata):
            row = self.resource(self.save(), 'plugin:custom_nodes/LocalPlugin')
        self.assertEqual(row['recoverability']['reason'], 'incomplete_local_tree')

    def test_lfs_pointer_is_missing_payload(self):
        (self.local / 'payload.txt').write_text('version https://git-lfs.github.com/spec/v1\noid sha256:' + 'a'*64)
        row = self.resource(self.save(), 'plugin:custom_nodes/LocalPlugin')
        self.assertEqual(row['recoverability']['reason'], 'incomplete_local_tree')

    def test_manager_editable_alias_is_also_protected(self):
        self.distributions = [fixture.Distribution(direct={'url': self.manager.as_uri(), 'dir_info': {'editable': True}})]
        row = self.resource(self.save(), 'package:demo')
        self.assertEqual(row['recoverability']['status'], 'protected_resource')
        self.assertIsNone(row['artifact_ref'])
