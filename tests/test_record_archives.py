import hashlib
import io
import random
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

package=types.ModuleType('_archives_tests');package.__path__=[str(Path(__file__).parents[1]/'qbb_nodeops')];sys.modules[package.__name__]=package
def load(name):
    spec=importlib.util.spec_from_file_location(package.__name__+'.'+name,Path(package.__path__[0])/(name+'.py'))
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module);return module
a=load('record_archives')

class ArchivesTests(unittest.TestCase):
    def test_direct_trash_does_not_scan_or_parse_record_and_ignores_scan_lock(self):
        self.record_path.write_text('damaged metadata can still be recycled')
        a.records.LOCK.acquire()
        try:
            with (patch.object(a, 'gc_inventory', side_effect=AssertionError('No object scan')),
                  patch.object(a.records, 'read', side_effect=AssertionError('No JSON parsing')),
                  patch('send2trash.send2trash') as trash):
                result = a.remove_record(self.record['id'], True)
                trash.assert_called_once_with(str(self.record_path))
                self.assertEqual(result['objects_removed'], 0)
        finally:
            a.records.LOCK.release()

    def test_direct_trash_rejects_invalid_id_and_missing_confirmation(self):
        with patch('send2trash.send2trash') as trash:
            for identity, confirmed in [('../outside', True), (self.record['id'], False)]:
                with self.assertRaises(ValueError):
                    a.remove_record(identity, confirmed)
            trash.assert_not_called()

    def test_local_delete_accepts_record_over_bundle_metadata_limit(self):
        self.record_path.write_text(self.record_path.read_text() + '\n' + ' ' * 4096)
        self.assertGreater(self.record_path.stat().st_size, 1024)
        with patch.object(a, 'MAX_METADATA', 1024):
            preview = a.preview_remove(self.record['id'])
            with patch('send2trash.send2trash') as trash:
                a.remove_record(self.record['id'], True, token=preview['token'])
                trash.assert_called_once_with(str(self.record_path))

    def test_local_delete_still_rejects_record_over_local_limit(self):
        with patch.object(a, 'MAX_LOCAL_RECORD_METADATA', 64):
            with self.assertRaisesRegex(ValueError, '128 MiB'):
                a.preview_remove(self.record['id'])

    def test_adaptive_compression_bounded_sample_and_rewind(self):
        class Bounded(io.BytesIO):
            def read(self,size=-1):
                self.assert_size=size
                return super().read(size)
        for content,expected in [(b'code\n'*20000,zipfile.ZIP_DEFLATED),(random.Random(7).randbytes(100000),zipfile.ZIP_STORED),(b'',zipfile.ZIP_STORED)]:
            stream=Bounded(content)
            self.assertEqual(a.object_compression(stream),expected)
            self.assertEqual(stream.assert_size,a.COMPRESSION_SAMPLE)
            self.assertEqual(stream.tell(),0)

    def test_compressed_export_import_restore_preserves_hashes(self):
        text=b'print("configuration and workflow")\n'*120000
        opaque=random.Random(12).randbytes(256*1024)
        (self.source/'main.py').write_bytes(text);(self.source/'opaque.bin').write_bytes(opaque)
        self.record['core']['snapshot']=a.files.capture(self.source,self.store)
        self.record_path.write_text(json.dumps(self.record))
        result=self.export()
        self.assertLess(result['archive_bytes'],result['bytes']//4)
        self.assertEqual(result['archive_bytes'],self.archive.stat().st_size)
        print('Compression fixture: logical=%d archive=%d bytes (synthetic text + opaque binary)'%(result['bytes'],result['archive_bytes']))
        with zipfile.ZipFile(self.archive) as archive:
            self.assertEqual(archive.getinfo(a.obj_name(hashlib.sha256(text).hexdigest())).compress_type,zipfile.ZIP_DEFLATED)
            self.assertEqual(archive.getinfo(a.obj_name(hashlib.sha256(opaque).hexdigest())).compress_type,zipfile.ZIP_STORED)
        other=self.root/'other'
        with patch.object(a.records,'directory',return_value=other):
            preview=a.preview_import(str(self.archive));imported=a.import_bundle(preview['token'],True)
            record=a.records.read(imported['id'])
            a.files.restore(record['core']['snapshot'],other,self.root/'restored')
        self.assertEqual((self.root/'restored/main.py').read_bytes(),text)
        self.assertEqual((self.root/'restored/opaque.bin').read_bytes(),opaque)

    def test_old_stored_bundle_still_imports(self):
        self.export();legacy=self.root/'legacy.zip'
        with zipfile.ZipFile(self.archive) as source,zipfile.ZipFile(legacy,'w',compression=zipfile.ZIP_STORED) as target:
            for name in source.namelist():target.writestr(name,source.read(name))
        preview=a.preview_import(str(legacy));result=a.import_bundle(preview['token'],True)
        self.assertFalse(result['restoration_started'])
        self.assertEqual(result['space']['new_bytes'],0)

    def test_export_space_reserves_possible_expansion(self):
        size=100*1024**3
        self.assertGreater(a.export_required_bytes({'a'*64:size}),size+2*a.MAX_METADATA)

    def test_object_appearing_during_import_is_verified_not_trusted(self):
        self.export();other=self.root/'other';other.mkdir()
        original=a.inspect_archive
        def raced(path,stage=None,reused=None,**kwargs):
            result=original(path,stage,reused,**kwargs)
            if stage is not None:
                sha=next(iter(result[1]));target=a.files.object_path(other,sha)
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'xxxxxxxx')
            return result
        with patch.object(a.records,'directory',return_value=other):
            preview=a.preview_import(str(self.archive))
            with patch.object(a,'inspect_archive',side_effect=raced):
                with self.assertRaises(ValueError):a.import_bundle(preview['token'],True)
        self.assertEqual(list(other.glob('*.json')),[])

    def test_export_changed_destination_rejected(self):
        preview=a.preview_export(self.record['id'],str(self.archive))
        with self.assertRaises(ValueError):a.export_bundle(self.record['id'],str(self.root/'different.zip'),True,token=preview['token'])
        self.assertFalse((self.root/'different.zip').exists())

    def test_export_preview_binds_record_and_destination(self):
        preview=a.preview_export(self.record['id'],str(self.archive))
        self.assertEqual(preview['objects'],1);self.assertFalse(preview['runtime_verified'])
        self.assertEqual(preview['scopes']['python_environment']['status'],'not_saved')
        self.assertFalse(self.archive.exists())
        self.record['label']='changed';self.record_path.write_text(json.dumps(self.record))
        with self.assertRaises(ValueError):a.export_bundle(self.record['id'],str(self.archive),True,token=preview['token'])
        self.assertFalse(self.archive.exists())

    def test_export_preview_and_apply(self):
        preview=a.preview_export(self.record['id'],str(self.archive))
        a.export_bundle(self.record['id'],str(self.archive),True,token=preview['token'])
        self.assertTrue(self.archive.is_file())

    def test_import_reuses_without_staging_existing_objects(self):
        self.export();preview=a.preview_import(str(self.archive))
        self.assertEqual(preview['space']['new_bytes'],0)
        self.assertEqual(preview['space']['reused_bytes'],8)
        original=a.inspect_archive;staged=[]
        def inspect(path,stage=None,reused=None,**kwargs):
            result=original(path,stage,reused,**kwargs)
            if stage is not None:staged.extend(stage.iterdir())
            return result
        with patch.object(a,'inspect_archive',side_effect=inspect):
            result=a.import_bundle(preview['token'],True)
        self.assertEqual(staged,[]);self.assertEqual(result['space']['new_bytes'],0)

    def test_import_empty_store_estimate_counts_new_objects_once(self):
        self.export();other=self.root/'other'
        with patch.object(a.records,'directory',return_value=other):
            preview=a.preview_import(str(self.archive))
            self.assertEqual(preview['space']['new_bytes'],8)
            self.assertEqual(preview['space']['temporary_bytes'],8)
            self.assertEqual(preview['space']['required_bytes'],8+2*a.MAX_METADATA)
            self.assertEqual(preview['space']['reused_bytes'],0)

    def test_integrity_report_distinguishes_damage_and_metadata_only(self):
        self.assertEqual(a.check_record(self.record['id'])['status'],'passed')
        next((self.store/'objects').glob('*/*')).write_bytes(b'xxxxxxxx')
        report=a.check_record(self.record['id']);self.assertEqual(report['status'],'failed')
        self.assertEqual(len(report['errors']),1);self.assertFalse(report['runtime_verified'])
        self.record['core'].pop('snapshot');self.record_path.write_text(json.dumps(self.record))
        self.assertEqual(a.check_record(self.record['id'])['status'],'no_objects')

    def test_remove_preview_counts_only_new_orphans_and_preserves_shared(self):
        self.orphan()
        preview=a.preview_remove(self.record['id'])
        self.assertEqual(preview['newly_unreferenced_bytes'],8)
        other=dict(self.record,id='b'*32,operation_checkpoint={'before_record_id':self.record['id']})
        (self.store/('b'*32+'.json')).write_text(json.dumps(other))
        preview=a.preview_remove(self.record['id'])
        self.assertEqual(preview['newly_unreferenced_bytes'],0)
        self.assertEqual(preview['related_records'],['b'*32])
        self.assertEqual(preview['objects_removed'],0)

    def test_remove_preview_preserves_job_references(self):
        sha=self.record['core']['snapshot']['files'][0]['sha256']
        jobs=self.store/'rebuild-jobs';jobs.mkdir()
        (jobs/'job.json').write_text(json.dumps({'prepared':{'snapshot':{'files':[{'sha256':sha}]}}}))
        self.assertEqual(a.preview_remove(self.record['id'])['newly_unreferenced_bytes'],0)

    def test_remove_preview_stale_rejects_before_trash(self):
        preview=a.preview_remove(self.record['id']);self.orphan()
        with patch('send2trash.send2trash') as trash:
            with self.assertRaises(ValueError):a.remove_record(self.record['id'],True,token=preview['token'])
            trash.assert_not_called()

    def test_remove_preview_success_only_trashes_manifest(self):
        preview=a.preview_remove(self.record['id'])
        with patch('send2trash.send2trash') as trash:
            result=a.remove_record(self.record['id'],True,token=preview['token'])
            trash.assert_called_once_with(str(self.record_path))
        self.assertEqual(result['objects_removed'],0)

    def test_invalid_permissions_rejected_before_export(self):
        self.record['core']['snapshot']['files'][0]['mode']=True
        with self.assertRaises(ValueError):a.references(self.record)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=self.root/'store';self.store.mkdir()
        self.mock=patch.object(a.records,'directory',return_value=self.store);self.mock.start();self.addCleanup(self.mock.stop)
        self.source=self.root/'source';self.source.mkdir();(self.source/'main.py').write_bytes(b'original')
        snapshot=a.files.capture(self.source,self.store)
        self.record={'kind':'version_record','schema':3,'id':'a'*32,'created':1,'label':'test','python':{},'packages':[], 'core':{'name':'ComfyUI','kind':'local','snapshot':snapshot},'plugins':[]}
        self.record_path=self.store/(self.record['id']+'.json');self.record_path.write_text(json.dumps(self.record))
        self.archive=self.root/'backup.zip';a.TOKENS.clear()
    def export(self):return a.export_bundle(self.record['id'],str(self.archive),True)
    def test_roundtrip_new_id_dedup(self):
        self.export();p=a.preview_import(str(self.archive));r=a.import_bundle(p['token'],True)
        self.assertNotEqual(r['id'],self.record['id']);self.assertFalse(r['restoration_started'])
        imported=a.records.read(r['id']);self.assertEqual(imported['core']['snapshot'],self.record['core']['snapshot'])
        self.assertEqual(len(list((self.store/'objects').glob('*/*'))),1)
    def test_confirmation_and_no_overwrite(self):
        with self.assertRaises(ValueError):a.export_bundle(self.record['id'],str(self.archive))
        self.export()
        with self.assertRaises(ValueError):self.export()
    def test_missing_object_no_published_archive(self):
        next((self.store/'objects').glob('*/*')).unlink()
        with self.assertRaises(OSError):self.export()
        self.assertFalse(self.archive.exists())
    def test_corrupt_object_export_rejected(self):
        next((self.store/'objects').glob('*/*')).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):self.export()
        self.assertFalse(self.archive.exists())
    def test_invalid_extra_path(self):
        self.export()
        with zipfile.ZipFile(self.archive,'a') as z:z.writestr('../escape','bad')
        with self.assertRaises(ValueError):a.preview_import(str(self.archive))
    def test_duplicate_entry(self):
        self.export()
        with zipfile.ZipFile(self.archive,'a') as z:z.writestr('record.json','{}')
        with self.assertRaises(ValueError):a.preview_import(str(self.archive))
    def test_traversal_in_manifest(self):
        self.record['core']['snapshot']['files'][0]['path']='../escape'
        with self.assertRaises(ValueError):a.references(self.record)
    def test_archive_changes_after_preview(self):
        self.export();p=a.preview_import(str(self.archive))
        with self.archive.open('ab') as f:f.write(b'changed')
        with self.assertRaises(ValueError):a.import_bundle(p['token'],True)
        self.assertEqual(len(list(self.store.glob('*.json'))),1)
    def orphan(self):
        sha=hashlib.sha256(b'orphan').hexdigest();p=a.files.object_path(self.store,sha);p.parent.mkdir(exist_ok=True);p.write_bytes(b'orphan');return sha,p
    def test_gc_retains_record_and_job_references(self):
        sha,p=self.orphan();jobs=self.store/'rebuild-jobs';jobs.mkdir();(jobs/'job.json').write_text(json.dumps({'prepared':{'snapshot':{'files':[{'sha256':sha}]}}}))
        self.assertEqual(a.preview_gc()['objects'],0)
    def test_gc_orphan_and_stale_preview(self):
        self.orphan();preview=a.preview_gc();self.assertEqual(preview['objects'],1)
        self.record['label']='changed';self.record_path.write_text(json.dumps(self.record))
        with patch('send2trash.send2trash') as trash:
            with self.assertRaises(ValueError):a.apply_gc(preview['token'],True)
            trash.assert_not_called()
    def test_gc_only_orphan(self):
        sha,p=self.orphan();preview=a.preview_gc()
        with patch('send2trash.send2trash') as trash:
            result=a.apply_gc(preview['token'],True);trash.assert_called_once_with(str(p))
        self.assertEqual(result['moved'],1)
    def test_broken_record_blocks_gc(self):
        self.record_path.write_text('{bad')
        with self.assertRaises(ValueError):a.preview_gc()
    def test_legacy_job_blocks_gc(self):
        jobs=self.store/'rebuild-jobs';jobs.mkdir();(jobs/'old.json').write_text('{}')
        with self.assertRaises(ValueError):a.preview_gc()
    def test_remove_only_json(self):
        with patch('send2trash.send2trash') as trash:
            a.remove_record(self.record['id'],True);trash.assert_called_once_with(str(self.record_path))
        self.assertEqual(len(list((self.store/'objects').glob('*/*'))),1)
    def test_single_use_token(self):
        self.export();p=a.preview_import(str(self.archive));a.import_bundle(p['token'],True)
        with self.assertRaises(ValueError):a.import_bundle(p['token'],True)
    def test_import_into_empty_store_and_restore(self):
        self.export();other=self.root/'other';other.mkdir()
        with patch.object(a.records,'directory',return_value=other):
            p=a.preview_import(str(self.archive));result=a.import_bundle(p['token'],True)
            record=a.records.read(result['id']);a.files.restore(record['core']['snapshot'],other,self.root/'restored')
        self.assertEqual((self.root/'restored/main.py').read_bytes(),b'original')
    def test_single_plugin_export_import_restore(self):
        plugin=self.root/'single.py';plugin.write_bytes(b'plugin contents')
        self.record['plugins']=[{'name':'single.py','kind':'local','snapshot':a.files.capture(plugin,self.store)}]
        self.record_path.write_text(json.dumps(self.record));self.export()
        other=self.root/'other';other.mkdir()
        with patch.object(a.records,'directory',return_value=other):
            preview=a.preview_import(str(self.archive));result=a.import_bundle(preview['token'],True)
            record=a.records.read(result['id'])
            a.files.restore(record['plugins'][0]['snapshot'],other,self.root/'restored.py')
        self.assertEqual((self.root/'restored.py').read_bytes(),b'plugin contents')
    def test_store_change_requires_new_preview(self):
        self.export();p=a.preview_import(str(self.archive))
        with patch.object(a.records,'directory',return_value=self.root/'other'):
            with self.assertRaises(ValueError):a.import_bundle(p['token'],True)
    def test_capture_lock_prevents_maintenance(self):
        with a.records.LOCK:
            with self.assertRaises(ValueError):a.preview_gc()
    def test_corrupt_existing_object_prevents_import(self):
        self.export();p=a.preview_import(str(self.archive));next((self.store/'objects').glob('*/*')).write_bytes(b'wrong')
        with self.assertRaises(ValueError):a.import_bundle(p['token'],True)
        self.assertEqual(len(list(self.store.glob('*.json'))),1)

if __name__=='__main__':unittest.main()
