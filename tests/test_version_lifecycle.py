"""Phase 1 lifecycle: independent records, export/import and reference-safe GC; no restore."""
import test_phase1_snapshot as fixture
from unittest.mock import patch
from pathlib import Path

class LifecycleTests(fixture.CaptureFixture):
    def test_two_records_and_reference_safe_cleanup(self):
        first = self.save()
        (self.local / '__init__.py').write_text('new content')
        second = self.save()
        first_refs = fixture.archives.references(first)[1]
        second_refs = fixture.archives.references(second)[1]
        self.assertTrue(set(first_refs) & set(second_refs))
        preview = fixture.archives.preview_remove(first['id'])
        with patch('send2trash.send2trash', side_effect=lambda path: Path(path).unlink()):
            fixture.archives.remove_record(first['id'], True, token=preview['token'])
            preview = fixture.archives.preview_gc()
            self.assertEqual(preview['objects'], len(set(first_refs) - set(second_refs)))
            fixture.archives.apply_gc(preview['token'], True)
        self.assertEqual(fixture.archives.check_record(second['id'])['status'], 'passed')
        archive = self.root / 'snapshot.zip'
        fixture.archives.export_bundle(second['id'], str(archive), True)
        other_store = self.root / 'imported-store'
        with patch.object(fixture.records, 'directory', return_value=other_store):
            preview = fixture.archives.preview_import(str(archive))
            result = fixture.archives.import_bundle(preview['token'], True)
            self.assertFalse(result['restoration_started'])
            self.assertEqual(fixture.archives.check_record(result['id'])['status'], 'passed')
            imported = fixture.records.read(result['id'])
            self.assertEqual(imported['artifacts'], second['artifacts'])
