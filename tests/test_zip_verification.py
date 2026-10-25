"""Corrupt archives and publication failures in isolated fixtures; no extraction."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

from test_phase1_snapshot import CaptureFixture, state, records, archives

local = state.local_archives


class ZipVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'payload.bin'
        self.source.write_bytes(b'original bytes')
        self.index = {'files': [{'path': 'folder/payload.bin', 'sha256': hashlib.sha256(self.source.read_bytes()).hexdigest(),
                                 'size': self.source.stat().st_size, 'mode': 0o644}],
                      'directories': [{'path': '', 'mode': 0o755}, {'path': 'empty', 'mode': 0o755}],
                      'gaps': [], 'exclusions': []}
        self.path = self.root / 'test.zip'
        local.write_zip(self.path, 'demo', self.index['files'], lambda entry: self.source,
                        self.index['directories'])

    def rewrite(self, transform):
        with zipfile.ZipFile(self.path) as archive:
            entries = [(copy.copy(info), archive.read(info)) for info in archive.infolist()]
        entries = transform(entries)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(self.path, 'w') as archive:
                for info, content in entries:
                    archive.writestr(info, content)

    def verify(self):
        return local.verify_zip(self.path, 'demo', self.index)

    def test_all_files_index_and_empty_directories_verified(self):
        proof = self.verify()
        self.assertEqual(proof['status'], 'verified')
        self.assertEqual(proof['files'], 1)
        self.assertEqual(proof['directories'], 1)
        self.assertEqual(proof['content_bytes'], len(b'original bytes'))
        self.assertEqual(proof['sha256'], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertFalse((self.root / 'folder').exists(), 'Verification must not extract')

    def test_same_content_zip_independent_of_creation_clock(self):
        other = self.root / 'another.zip'
        with patch('zipfile.time.localtime', return_value=(2030, 12, 31, 23, 59, 58, 0, 0, 0)):
            local.write_zip(other, 'demo', self.index['files'], lambda entry: self.source, self.index['directories'])
        self.assertEqual(other.read_bytes(), self.path.read_bytes())

    def test_partial_inventory_can_verify_without_claiming_complete(self):
        self.path.unlink()
        self.index['gaps'] = [{'path': 'missing.bin', 'reason': 'missing'}]
        local.write_zip(self.path, 'demo', self.index['files'], lambda entry: self.source,
                        self.index['directories'], gaps=self.index['gaps'])
        self.assertEqual(self.verify()['status'], 'verified')

    def test_same_length_content_with_valid_crc_still_rejected(self):
        self.rewrite(lambda entries: [(info, b'changed! bytes' if info.filename.endswith('payload.bin') else data) for info, data in entries])
        with self.assertRaisesRegex(ValueError, 'content mismatch'):
            self.verify()

    def test_declared_size_mismatch(self):
        self.rewrite(lambda entries: [(info, b'short' if info.filename.endswith('payload.bin') else data) for info, data in entries])
        with self.assertRaisesRegex(ValueError, 'length mismatch'):
            self.verify()

    def test_index_mismatch(self):
        def transform(entries):
            for i, (info, data) in enumerate(entries):
                if info.filename == local.INDEX_NAME:
                    index = json.loads(data)
                    index['component_id'] = 'another-component'
                    entries[i] = (info, json.dumps(index).encode())
            return entries
        self.rewrite(transform)
        with self.assertRaisesRegex(ValueError, 'index does not match'):
            self.verify()

    def test_duplicate_index_key_rejected(self):
        self.rewrite(lambda entries: [(info, data.replace(b'"version": 1', b'"version": 1, "version": 1') if info.filename == local.INDEX_NAME else data) for info, data in entries])
        with self.assertRaisesRegex(ValueError, 'Duplicate ZIP index key'):
            self.verify()

    def test_boolean_index_version_is_not_integer_version(self):
        self.rewrite(lambda entries: [(info, data.replace(b'"version": 1', b'"version": true') if info.filename == local.INDEX_NAME else data) for info, data in entries])
        with self.assertRaisesRegex(ValueError, 'index does not match'):
            self.verify()

    def test_missing_extra_and_duplicate_members(self):
        original = self.path.read_bytes()
        transforms = [lambda entries: entries[:-1],
                      lambda entries: entries + [(zipfile.ZipInfo('unexpected.txt'), b'extra')],
                      lambda entries: entries + [entries[-1]]]
        for transform in transforms:
            self.path.write_bytes(original)
            self.rewrite(transform)
            with self.assertRaisesRegex(ValueError, 'members do not match'):
                self.verify()

    def test_directory_with_payload_rejected(self):
        self.rewrite(lambda entries: [(info, b'bad' if info.filename == 'empty/' else data) for info, data in entries])
        with self.assertRaisesRegex(ValueError, 'directory contains data'):
            self.verify()

    def test_symlink_member_rejected(self):
        def transform(entries):
            for info, _ in entries:
                if info.filename.endswith('payload.bin'):
                    info.external_attr = 0o120777 << 16
            return entries
        self.rewrite(transform)
        with self.assertRaisesRegex(ValueError, 'links or special'):
            self.verify()

    def test_permission_mismatch_rejected(self):
        def transform(entries):
            for info, _ in entries:
                if info.filename.endswith('payload.bin'):
                    info.external_attr = 0o100444 << 16
            return entries
        self.rewrite(transform)
        with self.assertRaisesRegex(ValueError, 'permission mismatch'):
            self.verify()

    def test_unsupported_compression_rejected(self):
        def transform(entries):
            for info, _ in entries:
                if info.filename.endswith('payload.bin'):
                    info.compress_type = zipfile.ZIP_BZIP2
            return entries
        self.rewrite(transform)
        with self.assertRaisesRegex(ValueError, 'unsupported ZIP member'):
            self.verify()

    def test_truncated_zip_rejected(self):
        self.path.write_bytes(self.path.read_bytes()[:-30])
        with self.assertRaisesRegex(ValueError, 'ZIP verification failed'):
            self.verify()

    def test_bad_crc_rejected(self):
        def transform(entries):
            for info, _ in entries:
                info.compress_type = zipfile.ZIP_STORED
            return entries
        self.rewrite(transform)
        with zipfile.ZipFile(self.path) as archive:
            info = archive.getinfo('folder/payload.bin')
            offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        data = bytearray(self.path.read_bytes());data[offset] ^= 1;self.path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'CRC'):
            self.verify()

    def test_stored_object_hash_binding(self):
        with self.assertRaisesRegex(ValueError, 'object hash or length mismatch'):
            local.verify_zip(self.path, 'demo', self.index, {'sha256': '0'*64, 'size': self.path.stat().st_size})

    def test_index_and_member_limits(self):
        with patch.object(local, 'MAX_INDEX_BYTES', 16):
            with self.assertRaisesRegex(ValueError, 'index exceeds'):
                self.verify()
        with patch.object(local, 'MAX_MEMBERS', 1):
            with self.assertRaisesRegex(ValueError, 'exceeds 100000 members'):
                self.verify()

    def test_expected_path_traversal_and_duplicate_paths_rejected(self):
        for path in ('../outside', '/absolute', 'C:/absolute'):
            bad = copy.deepcopy(self.index);bad['files'][0]['path'] = path
            with self.assertRaisesRegex(ValueError, 'Unsafe snapshot path'):
                local.verify_zip(self.path, 'demo', bad)
        bad = copy.deepcopy(self.index);bad['files'].append(copy.deepcopy(bad['files'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate ZIP inventory'):
            local.verify_zip(self.path, 'demo', bad)


class ZipPublicationTests(CaptureFixture):
    def test_bad_temporary_zip_does_not_publish_objects_or_record(self):
        preview = records.preview()
        original = local.write_zip
        def corrupt(destination, *args):
            original(destination, *args)
            destination.write_bytes(b'not a ZIP')
        with patch.object(local, 'write_zip', side_effect=corrupt):
            with self.assertRaisesRegex(ValueError, 'ZIP verification failed'):
                records.create('bad', preview_id=preview['preview_id'])
        self.assertEqual(list(self.store.glob('*.json')), [])
        self.assertEqual(list(self.store.glob('.qbb-zip-*')), [])
        self.assertFalse(any(path.is_file() and path.read_bytes() == b'not a ZIP' for path in self.store.rglob('*')))

    def test_final_stored_zip_failure_prevents_record_publication(self):
        preview = records.preview()
        with patch.object(local, 'verify_record_archive', side_effect=ValueError('stored ZIP changed')):
            with self.assertRaisesRegex(ValueError, 'stored ZIP changed'):
                records.create('bad stored', preview_id=preview['preview_id'])
        self.assertEqual(list(self.store.glob('*.json')), [])

    def test_proofs_bound_to_each_published_archive(self):
        record = self.save()
        for row in record['resources']:
            if row.get('archive_ref'):
                proof = local.verify_record_archive(record, row, self.store)
                self.assertEqual(row['archive_verification'], proof)
                self.assertEqual(record['backup']['components'][row['id']]['archive_verification'], proof)
        summary = state.backup_contract.summary(record)
        self.assertEqual(summary['verified_archives'], record['statistics']['zip_archives'])

    def test_forged_verification_counts_rejected(self):
        record = self.save();record.pop('backup')
        row = next(r for r in record['resources'] if r.get('archive_ref'))
        row['archive_verification']['files'] += 1
        with self.assertRaisesRegex(ValueError, 'verification does not match'):
            state.validate(record)

    def test_boolean_verification_count_is_not_numeric_count(self):
        record = self.save();record.pop('backup')
        row = next(r for r in record['resources'] if r.get('archive_ref') and r['archive_verification']['files'] == 1)
        row['archive_verification']['files'] = True
        with self.assertRaisesRegex(ValueError, 'verification does not match'):
            state.validate(record)

    def test_older_zip_record_reads_as_unverified(self):
        record = self.save();record.pop('backup')
        for row in record['resources']:
            row.pop('archive_verification', None)
        normalized = state.validate(record)
        summary = state.backup_contract.summary(normalized)
        self.assertEqual(summary['verified_archives'], 0)
        checked = state.local_archives.verify_record_archive(normalized, next(r for r in normalized['resources'] if r.get('archive_ref')), self.store)
        self.assertEqual(checked['status'], 'verified')

    def test_check_record_detects_internal_index_mismatch_with_valid_outer_hash(self):
        record = self.invalid_index_record()
        checked = archives.check_record(record['id'])
        self.assertEqual(checked['status'], 'failed')
        self.assertTrue(any('index does not match' in e['error'] for e in checked['errors']))

    def invalid_index_record(self):
        record = self.save();record.pop('backup')
        row = next(r for r in record['resources'] if r.get('archive_ref'))
        row.pop('archive_verification')
        artifact = record['artifacts'][row['archive_ref']]
        path = state.files.object_path(self.store, artifact['files'][0]['sha256'])
        changed = self.root / 'changed.zip'
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(changed, 'w') as output:
            for info in source.infolist():
                payload = source.read(info)
                if info.filename == local.INDEX_NAME:
                    index = json.loads(payload);index['component_id'] = 'wrong';payload = json.dumps(index).encode()
                output.writestr(info, payload)
        sha = hashlib.sha256(changed.read_bytes()).hexdigest()
        destination = state.files.object_path(self.store, sha)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(changed.read_bytes())
        artifact['files'][0].update(sha256=sha, size=changed.stat().st_size)
        record = state.validate(record)
        (self.store / (record['id'] + '.json')).write_text(json.dumps(record), encoding='utf-8')
        return record

    def test_import_cannot_publish_invalid_internal_zip(self):
        record = self.invalid_index_record()
        target = self.root / 'bundle.zip'
        preview = archives.preview_export(record['id'], str(target))
        archives.export_bundle(record['id'], str(target), True, token=preview['token'])
        destination = self.root / 'imported'
        with patch.object(records, 'directory', return_value=destination):
            preview = archives.preview_import(str(target))
            with self.assertRaisesRegex(ValueError, 'ZIP index does not match'):
                archives.import_bundle(preview['token'], True)
        self.assertEqual(list(destination.glob('*.json')), [])


if __name__ == '__main__':
    unittest.main()
