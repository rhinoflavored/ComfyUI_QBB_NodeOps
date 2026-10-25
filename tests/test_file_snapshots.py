import importlib.util
from pathlib import Path
import tempfile
import sqlite3
import unittest
from unittest import mock
from contextlib import contextmanager

spec = importlib.util.spec_from_file_location('file_snapshots', Path(__file__).parents[1] / 'qbb_nodeops/file_snapshots.py')
snap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snap)

class SnapshotTests(unittest.TestCase):
    def nodeops_source(self, root):
        source = root/'renamed-nodeops'
        (source/'qbb_nodeops').mkdir(parents=True)
        for name in ('__init__.py', 'qbb_nodeops/health.py', 'qbb_nodeops/market_index.py', 'qbb_nodeops/file_snapshots.py'):
            (source/name).write_text('pass')
        return source

    def test_nodeops_runtime_databases_excluded_and_source_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=self.nodeops_source(root)
            for name in snap.NODEOPS_DATABASES:
                with sqlite3.connect(source/name) as db:
                    db.execute('create table data(value text)')
                    db.execute("insert into data values ('keep me')")
                db.close()
                for suffix in ('-wal','-shm','-journal'):
                    (source/(name+suffix)).write_bytes(b'runtime sidecar')
            before={p.name:p.read_bytes() for p in source.iterdir() if p.is_file()}
            baseline=snap.scan(source,root/'store')
            self.assertFalse(baseline['errors'])
            self.assertEqual({s['path'] for s in baseline['skipped']},set(snap.NODEOPS_DATABASES))
            record=snap.capture(source,root/'store',expected=baseline)
            snap.verify_source(source,root/'store',record,baseline)
            snap.restore(record,root/'store',root/'restored')
            self.assertEqual(before,{p.name:p.read_bytes() for p in source.iterdir() if p.is_file()})
            self.assertTrue((root/'restored/qbb_nodeops/health.py').is_file())
            self.assertFalse(list((root/'restored').glob('*.sqlite3*')))

    def test_nodeops_runtime_writes_do_not_invalidate_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=self.nodeops_source(root)
            baseline=snap.scan(source,root/'store')
            for name in snap.NODEOPS_DATABASES:
                (source/name).write_bytes(b'SQLite format 3\x00')
                (source/(name+'-wal')).write_bytes(b'new transaction')
            self.assertEqual(baseline,snap.scan(source,root/'store'))
            snap.capture(source,root/'store',expected=baseline)
            (source/'qbb_nodeops_usage.sqlite3-wal').unlink()
            snap.assert_same(source,root/'store',baseline)

    def test_sqlite_wal_backup_restores_committed_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); source=root/'source'; source.mkdir()
            db=sqlite3.connect(source/'data.db')
            try:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('CREATE TABLE data(value)'); db.execute('INSERT INTO data VALUES (42)'); db.commit()
                baseline=snap.scan(source,root/'store')
                self.assertFalse(baseline['errors'])
                record=snap.capture(source,root/'store',expected=baseline)
                snap.verify_source(source,root/'store',record,baseline)
                self.assertEqual([f['path'] for f in record['files']],['data.db'])
                snap.restore(record,root/'store',root/'restored')
                restored=sqlite3.connect(root/'restored/data.db')
                try:self.assertEqual(restored.execute('SELECT value FROM data').fetchone(),(42,))
                finally:restored.close()
                db.execute('INSERT INTO data VALUES (43)'); db.commit()
                with self.assertRaises(ValueError):snap.verify_source(source,root/'store',record,baseline)
            finally:db.close()

    def test_nodeops_exemption_is_not_a_general_database_exemption(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=self.nodeops_source(root)
            for path in (source/'other.sqlite3',source/'qbb_nodeops/qbb_nodeops_usage.sqlite3'):
                path.write_bytes(b'SQLite format 3\x00')
            self.assertEqual(len(snap.scan(source,root/'store')['errors']),2)
            other=root/'other-plugin';other.mkdir()
            (other/'qbb_nodeops_usage.sqlite3').write_bytes(b'SQLite format 3\x00')
            self.assertTrue(snap.scan(other,root/'store')['errors'])
            self.assertTrue(snap.scan(source,root/'store',runtime_files=True)['errors'])

    def test_empty_directories_and_old_format(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';(source/'empty/nested').mkdir(parents=True)
            (source/'node.py').write_bytes(b'pass')
            record=snap.capture(source,root/'store')
            self.assertEqual(record['format'],2)
            snap.restore(record,root/'store',root/'new')
            self.assertTrue((root/'new/empty/nested').is_dir())
            record['format']=1;record.pop('directories')
            snap.restore(record,root/'store',root/'old')
            self.assertEqual((root/'old/node.py').read_bytes(),b'pass')

    def test_large_file_policies(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(snap,'MAX_FILE',3):
            root=Path(temp);source=root/'node.py';source.write_bytes(b'1234')
            self.assertTrue(snap.scan(source,root/'store')['skipped'])
            self.assertTrue(snap.scan(source,root/'store',large_files='error')['errors'])
            with self.assertRaises(ValueError):snap.capture(source,root/'store',large_files='error')
            record=snap.capture(source,root/'store',large_files='include')
            snap.restore(record,root/'store',root/'restored.py')
            self.assertEqual((root/'restored.py').read_bytes(),b'1234')

    def test_add_delete_since_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';source.mkdir();(source/'a').write_text('a')
            baseline=snap.scan(source,root/'store')
            (source/'b').write_text('b')
            with self.assertRaises(ValueError):snap.capture(source,root/'store',expected=baseline)
            baseline=snap.scan(source,root/'store');(source/'a').unlink()
            with self.assertRaises(ValueError):snap.capture(source,root/'store',expected=baseline)
            record=snap.capture(source,root/'store')
            self.assertEqual([f['path'] for f in record['files']],['b'])

    def test_file_changes_during_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'node.py';source.write_bytes(b'abc')
            original=snap.tempfile.NamedTemporaryFile
            @contextmanager
            def changing(*args,**kwargs):
                with original(*args,**kwargs) as stream:
                    yield stream
                source.write_bytes(b'changed')
            with mock.patch.object(snap.tempfile,'NamedTemporaryFile',changing), self.assertRaises(ValueError):
                snap.capture(source,root/'store')
            self.assertFalse(list((root/'store').glob('tmp*')))

    def test_unreadable_file_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'node.py';source.write_bytes(b'abc')
            original=Path.open
            def deny(path,*args,**kwargs):
                if path==source:raise PermissionError('denied')
                return original(path,*args,**kwargs)
            with mock.patch.object(Path,'open',deny):
                self.assertTrue(snap.scan(source,root/'store')['errors'])
                with self.assertRaises(ValueError):snap.capture(source,root/'store')

    def test_roundtrip_and_dedup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source'
            source.mkdir()
            (source / 'node.py').write_bytes(b'print("hello")')
            (source / 'copy.py').write_bytes(b'print("hello")')
            store = root / 'store'
            first = snap.capture(source, store)
            self.assertEqual(first, snap.capture(source, store))
            self.assertEqual(len(list((store/'objects').glob('*/*'))), 1)
            snap.restore(first, store, root/'restored')
            self.assertEqual((root/'restored/node.py').read_bytes(), b'print("hello")')

    def test_corrupt_same_size_object_blocks_before_destination_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';source.mkdir()
            (source/'a').write_bytes(b'good');(source/'z').write_bytes(b'last')
            record=snap.capture(source,root/'store')
            snap.object_path(root/'store',record['files'][1]['sha256']).write_bytes(b'evil')
            with self.assertRaisesRegex(ValueError,'checksum'):snap.restore(record,root/'store',root/'target')
            self.assertFalse((root/'target').exists())

    def test_verify_restored_detects_disk_mutation_extra_file_and_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';(source/'empty').mkdir(parents=True)
            (source/'main.py').write_bytes(b'original');record=snap.capture(source,root/'store')
            target=root/'target';snap.restore(record,root/'store',target)
            (target/'main.py').write_bytes(b'modified')
            with self.assertRaisesRegex(ValueError,'checksum'):snap.verify_restored(record,target)
            (target/'main.py').write_bytes(b'original');(target/'extra').mkdir()
            with self.assertRaisesRegex(ValueError,'inventory'):snap.verify_restored(record,target)
            (target/'extra').rmdir();(target/'added.py').write_bytes(b'extra')
            with self.assertRaisesRegex(ValueError,'inventory'):snap.verify_restored(record,target)
            (target/'added.py').unlink();(target/'empty').rmdir()
            with self.assertRaisesRegex(ValueError,'inventory'):snap.verify_restored(record,target)

    def test_restore_reads_back_written_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'node.py';source.write_bytes(b'original')
            record=snap.capture(source,root/'store');target=root/'target.py'
            original=snap.os.chmod
            def corrupt(path,mode):
                original(path,mode)
                if path==target:target.write_bytes(b'modified')
            with mock.patch.object(snap.os,'chmod',side_effect=corrupt):
                with self.assertRaisesRegex(ValueError,'checksum'):snap.restore(record,root/'store',target)

    def test_unchanged_capture_never_opens_payload_temporary_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'node.py';source.write_bytes(b'original')
            first=snap.capture(source,root/'store')
            with mock.patch.object(snap.tempfile,'NamedTemporaryFile',side_effect=AssertionError('duplicate write')):
                second=snap.capture(source,root/'store')
            self.assertEqual(first,second)

    def test_new_capture_detects_same_size_content_change_during_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'node.py';source.write_bytes(b'original')
            original=snap.tempfile.NamedTemporaryFile
            @contextmanager
            def changing(*args,**kwargs):
                source.write_bytes(b'modified')
                with original(*args,**kwargs) as stream:yield stream
            signature=snap.signature(source.stat())
            with mock.patch.object(snap.tempfile,'NamedTemporaryFile',changing), mock.patch.object(snap,'signature',return_value=signature):
                with self.assertRaisesRegex(ValueError,'content changed'):snap.capture(source,root/'store')
            self.assertFalse(list((root/'store/objects').glob('*/*')))

    def test_preview_content_change_with_unchanged_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'node.py';source.write_bytes(b'original')
            baseline=snap.scan(source,root/'store');snap.plan_content(source,baseline)
            source.write_bytes(b'modified')
            with mock.patch.object(snap,'signature',return_value=baseline['files'][0]['signature']):
                with self.assertRaisesRegex(ValueError,'content changed'):snap.capture(source,root/'store',expected=baseline)

    def test_core_exclusion_not_recursive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'source'
            (source/'comfy/ldm/models').mkdir(parents=True)
            (source/'comfy/ldm/models/a.py').write_text('pass')
            (source/'models').mkdir()
            (source/'models/a.bin').write_bytes(b'model')
            result = snap.capture(source, root/'store', core=True)
            self.assertEqual([f['path'] for f in result['files']], ['comfy/ldm/models/a.py'])
            self.assertTrue(result['skipped'])

    def test_unsafe_names(self):
        for name in ['../bad', '/bad', 'C:/bad', 'a\\b', 'a/../b', 'NUL', 'a.', 'a//b']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                snap.safe_parts(name)

    def test_corruption_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'node.py'
            source.write_bytes(b'abc')
            record = snap.capture(source, root/'store')
            snap.object_path(root/'store', record['files'][0]['sha256']).write_bytes(b'xyz')
            with self.assertRaises(ValueError):
                snap.restore(record, root/'store', root/'output.py')

if __name__ == '__main__':
    unittest.main()
