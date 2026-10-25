"""Supersedes the removed skip/GitHub-substitution/metadata-only creation policies."""
from unittest.mock import patch
import test_phase1_snapshot as fixture

class CaptureErrorPolicyTests(fixture.CaptureFixture):
    def test_legacy_fallback_options_rejected_before_scan(self):
        for mode in ('skip', 'github', 'metadata'):
            with patch.object(fixture.state, 'scan_environment', side_effect=AssertionError('must not scan')):
                with self.assertRaises(ValueError):
                    fixture.records.preview({'on_error': mode})

    def test_storage_write_failure_does_not_publish(self):
        preview = fixture.records.preview()
        with patch.object(fixture.state.files, 'capture', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                fixture.records.create(preview_id=preview['preview_id'])
        self.assertEqual(list(self.store.glob('*.json')), [])

    def test_invalid_wheel_is_unrestorable_not_a_public_substitution(self):
        wheel = self.wheel()
        wheel.write_bytes(b'not a wheel')
        row = self.resource(self.save(), 'package:demo')
        self.assertEqual(row['recoverability']['status'], 'unrestorable')
        self.assertIsNone(row['artifact_ref'])
