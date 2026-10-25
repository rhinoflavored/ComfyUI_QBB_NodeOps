"""Existing checkpoint settings remain readable; automatic capture is paused in Phase 1."""
import importlib
import json
from unittest.mock import patch
import test_phase1_snapshot as fixture

policy = importlib.import_module(fixture.package.__name__ + '.checkpoint_policy')

class CheckpointTests(fixture.CaptureFixture):
    def test_enabled_old_setting_cannot_capture_old_scope(self):
        setting = self.root / 'checkpoints.json'
        setting.write_text(json.dumps({**policy.DEFAULT, 'enabled': True}))
        before = setting.read_bytes()
        with patch.object(policy, 'SETTINGS', setting), patch.object(fixture.records, 'create', side_effect=AssertionError('automatic write')):
            policy.tick()
        self.assertEqual(policy.STATE['status'], 'paused')
        self.assertEqual(setting.read_bytes(), before)
        self.assertFalse(self.store.exists())

    def test_manual_api_rejects_old_checkpoint_mode(self):
        with self.assertRaises(ValueError):
            fixture.records.create(checkpoint={'operation': 'update', 'phase': 'before', 'before_record_id': None})

    def test_storage_usage_still_reads_new_snapshots(self):
        self.save()
        with patch.object(policy, 'SETTINGS', self.root / 'absent.json'):
            result = policy.storage_status()
        self.assertGreater(result['bytes'], 0)
        self.assertEqual(result['unreferenced_objects'], 0)
