"""Read-only comparison using temporary fixtures; no running environment access."""
import importlib.util
import copy
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

package=types.ModuleType('_compare_tests');package.__path__=[str(Path(__file__).parents[1]/'qbb_nodeops')];sys.modules[package.__name__]=package
def load(name):
    spec=importlib.util.spec_from_file_location(package.__name__+'.'+name,Path(package.__path__[0])/(name+'.py'))
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module);setattr(package,name,module);return module
files=load('file_snapshots');load('version_records');compare=load('record_compare')

class CompareTests(unittest.TestCase):
    def test_saved_type_and_permission_changes(self):
        after=copy.deepcopy(self.snapshot)
        after['files'][0]['mode'] ^= 0o200
        result=compare.compare_snapshots(self.snapshot,after)
        self.assertEqual([r['kind'] for r in result['changes']],['permissions'])
        after['files']=[];after['directories'].append({'path':'a.py','mode':0o755})
        result=compare.compare_snapshots(self.snapshot,after)
        self.assertEqual([r['kind'] for r in result['changes']],['type_changed'])

    def test_saved_large_policy_difference_is_unverified(self):
        before=copy.deepcopy(self.snapshot);before['policy']['large_files']='exclude'
        after=copy.deepcopy(self.snapshot);after['policy']['large_files']='include'
        after['files'].append({'path':'large.bin','sha256':'b'*64,'size':files.MAX_FILE+1,'mode':0o644})
        result=compare.compare_snapshots(before,after)
        self.assertEqual(result['changes'],[])
        self.assertTrue(any(r['path']=='large.bin' for r in result['unverified']))

    def test_saved_duplicate_paths_rejected(self):
        after=copy.deepcopy(self.snapshot);after['files'].append(copy.deepcopy(after['files'][0]))
        result=compare.compare_snapshots(self.snapshot,after)
        self.assertEqual(result['changes'],[]);self.assertTrue(result['unverified'])

    def test_saved_snapshots_changes_without_source_or_objects(self):
        before=copy.deepcopy(self.snapshot)
        (self.source/'a.py').write_bytes(b'changed')
        (self.source/'added.py').write_bytes(b'new')
        (self.source/'empty').mkdir()
        after=files.capture(self.source,self.store)
        with mock.patch.object(files,'scan',side_effect=AssertionError('Must not scan')),mock.patch.object(files,'object_path',side_effect=AssertionError('Must not read objects')):
            result=compare.compare_snapshots(before,after)
        self.assertEqual({(r['path'],r['kind']) for r in result['changes']},{('a.py','modified'),('added.py','added'),('empty','added')})
        self.assertEqual(result['unverified'],[])
        reverse=compare.compare_snapshots(after,before)
        self.assertIn(('added.py','deleted'),[(r['path'],r['kind']) for r in reverse['changes']])

    def test_saved_scope_change_is_not_file_addition(self):
        before=copy.deepcopy(self.snapshot)
        (self.source/'user').mkdir();(self.source/'user/workflow.json').write_text('{}')
        after=files.capture(self.source,self.store,core=True,core_dirs=['user'])
        result=compare.compare_snapshots(before,after,core=True)
        self.assertFalse(any(r['path'].startswith('user') for r in result['changes']))
        self.assertTrue(any(r['path']=='user' for r in result['unverified']))

    def test_saved_missing_and_old_inventory_are_not_deletions(self):
        self.assertEqual(compare.compare_snapshots(self.snapshot,None)['changes'],[])
        old=copy.deepcopy(self.snapshot);old['format']=1;old['files']=[]
        result=compare.compare_snapshots(self.snapshot,old)
        self.assertEqual(result['changes'],[]);self.assertTrue(result['unverified'])

    def test_saved_excluded_path_is_not_deleted(self):
        after=copy.deepcopy(self.snapshot);after['files']=[];after['skipped']=[{'path':'a.py','reason':'larger than 256 MiB'}]
        result=compare.compare_snapshots(self.snapshot,after)
        self.assertEqual(result['changes'],[]);self.assertTrue(result['unverified'])

    def test_saved_invalid_path_rejected(self):
        after=copy.deepcopy(self.snapshot);after['files'][0]['path']='../secret'
        result=compare.compare_snapshots(self.snapshot,after)
        self.assertEqual(result['changes'],[]);self.assertTrue(result['unverified'])

    def test_saved_api_never_reads_running_inventory(self):
        row={'id':'a'*32,'label':'Before','core':{'name':'ComfyUI','snapshot':self.snapshot},'plugins':[],'packages':[],'python':{'version':'3.11'}}
        with mock.patch.object(compare.records,'read',return_value=row),mock.patch.object(compare.records,'inventory',side_effect=AssertionError('live inventory')):
            result=compare.compare('a'*32,'b'*32)
        self.assertEqual(result['direction'],'record_to_record')
        self.assertEqual(result['change_count'],0)
        self.assertEqual(result['evidence'],'saved_manifests_not_object_health')

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.source.mkdir();self.store=self.root/'store'
        (self.source/'a.py').write_bytes(b'original')
        self.snapshot=files.capture(self.source,self.store)
    def run_compare(self):return compare.compare_files(self.snapshot,self.source,self.store)

    def test_permission_only_change_is_reported(self):
        path = self.source / 'a.py'
        original = path.stat().st_mode
        try:
            path.chmod(original & ~0o222)
            changes = self.run_compare()['changes']
            self.assertEqual([(row['path'], row['kind']) for row in changes], [('a.py', 'permissions')])
        finally:
            path.chmod(original)

    def test_root_directory_permissions_are_compared(self):
        self.snapshot['directories'][0]['mode'] ^= 0o200
        changes = self.run_compare()['changes']
        self.assertEqual([(row['path'], row['kind'], row['type']) for row in changes], [('.', 'permissions', 'directory')])

    def test_missing_permission_is_unknown(self):
        self.snapshot['files'][0].pop('mode')
        result = self.run_compare()
        self.assertEqual(result['changes'], [])
        self.assertTrue(any('权限' in row['reason'] for row in result['unverified']))

    def test_permission_masks_match_platform_capabilities(self):
        with mock.patch.object(files.os, 'name', 'nt'):
            self.assertEqual(files.permission_mode(0o755), files.permission_mode(0o644))
            self.assertNotEqual(files.permission_mode(0o444), files.permission_mode(0o644))
        with mock.patch.object(files.os, 'name', 'posix'):
            self.assertNotEqual(files.permission_mode(0o755), files.permission_mode(0o644))

    def test_unchanged_zero_differences(self):
        result=self.run_compare()
        self.assertEqual(result['changes'],[]);self.assertEqual(result['unverified'],[])
        self.assertEqual(result['checked_files'],1)

    def test_timestamp_only_not_content_change(self):
        p=self.source/'a.py';info=p.stat();os.utime(p,ns=(info.st_atime_ns,info.st_mtime_ns+1000000))
        self.assertEqual(self.run_compare()['changes'],[])

    def test_modify_add_delete(self):
        (self.source/'a.py').write_bytes(b'modified')
        self.assertEqual(self.run_compare()['changes'],[{'path':'a.py','kind':'modified','type':'file'}])
        (self.source/'a.py').unlink();(self.source/'b.py').write_bytes(b'new')
        changes=self.run_compare()['changes']
        self.assertEqual({(r['path'],r['kind']) for r in changes},{('a.py','deleted'),('b.py','added')})

    def test_empty_directory_added(self):
        (self.source/'empty').mkdir()
        self.assertEqual(self.run_compare()['changes'],[{'path':'empty','kind':'added','type':'directory'}])

    def test_exclusion_never_becomes_addition(self):
        (self.source/'__pycache__').mkdir();(self.source/'__pycache__/x').write_bytes(b'cache')
        self.snapshot=files.capture(self.source,self.store)
        (self.source/'__pycache__/x').write_bytes(b'changed cache')
        result=self.run_compare();self.assertEqual(result['changes'],[]);self.assertTrue(result['unverified'])

    def test_excluded_large_file_shrinking_stays_unknown(self):
        with mock.patch.object(files,'MAX_FILE',3):
            self.snapshot=files.capture(self.source,self.store)
            (self.source/'a.py').write_bytes(b'1')
            result=self.run_compare();self.assertEqual(result['changes'],[]);self.assertTrue(result['unverified'])

    def test_recorded_file_growing_still_compared(self):
        with mock.patch.object(files,'MAX_FILE',3):
            (self.source/'a.py').write_bytes(b'large and changed')
            self.assertEqual(self.run_compare()['changes'][0]['kind'],'modified')

    def test_unreadable_not_deleted(self):
        original=Path.open
        def denied(path,*args,**kwargs):
            if path==self.source/'a.py':raise PermissionError('denied')
            return original(path,*args,**kwargs)
        with mock.patch.object(Path,'open',denied):
            result=self.run_compare();self.assertEqual(result['changes'],[]);self.assertTrue(result['unverified'])

    def test_changed_during_scan_invalidates_result(self):
        original=compare.stable_hash
        def moving(path,signature):
            result=original(path,signature);(self.source/'new.py').write_bytes(b'new');return result
        with mock.patch.object(compare,'stable_hash',moving):
            result=self.run_compare();self.assertEqual(result['changes'],[]);self.assertEqual(result['checked_files'],0);self.assertTrue(result['unverified'])

    def test_no_snapshot_unknown(self):
        result=compare.compare_files(None,self.source,self.store)
        self.assertEqual(result['changes'],[]);self.assertTrue(result['unverified'])

    def test_packages_union_normalization_and_local(self):
        before=[{'name':'A_b','version':'1'},{'name':'gone','version':'1'},{'name':'local','version':'1','editable':True}]
        after=[{'name':'a-b','version':'1'},{'name':'new','version':'2'},{'name':'local','version':'2','editable':True}]
        changes,unknown=compare.compare_packages(before,after)
        self.assertEqual(len(unknown),2)  # Legacy package rows have no source metadata.
        self.assertEqual({(r['name'],r['kind']) for r in changes},{('gone','deleted'),('new','added'),('local','modified')})

    def test_duplicate_packages_unknown(self):
        changes,unknown=compare.compare_packages([{'name':'a-b','version':'1'},{'name':'a_b','version':'2'}],[])
        self.assertFalse(changes);self.assertTrue(unknown)

    def test_current_path_not_record_path(self):
        record={'core':{'name':'core','path':'DO_NOT_READ','snapshot':self.snapshot},'plugins':[],'packages':[]}
        current={'core':{'name':'core','path':str(self.source)},'plugins':[],'packages':[]}
        result=compare.compare_record(record,current,self.store)
        self.assertEqual(result['state'],'incomplete');self.assertEqual(result['change_count'],0)

    def test_same_dirty_commit_and_snapshot_not_difference(self):
        core={'name':'core','path':str(self.source),'snapshot':self.snapshot,'kind':'git','commit':'a'*40,'dirty':True}
        data={'core':core,'plugins':[],'packages':[]}
        result=compare.compare_record(data,data,self.store)
        self.assertEqual(result['state'],'incomplete')  # Fixture omits Python metadata.
        self.assertEqual(result['change_count'],0)

    def test_git_commit_change_separate_from_files(self):
        a={'name':'core','path':str(self.source),'snapshot':self.snapshot,'kind':'git','commit':'a'*40}
        b={**a,'commit':'b'*40}
        result=compare.compare_record({'core':a,'plugins':[],'packages':[]},{'core':b,'plugins':[],'packages':[]},self.store)
        self.assertEqual(result['versions'][0]['category'],'git')
        self.assertEqual(result['components'][0]['changes'],[])

    def test_missing_root_is_unknown_not_all_deleted(self):
        result=compare.compare_files(self.snapshot,self.root/'missing',self.store)
        self.assertEqual(result['changes'],[])
        self.assertTrue(result['unverified'])

    def test_new_and_removed_plugins(self):
        core={'name':'core','path':str(self.source),'snapshot':self.snapshot}
        result=compare.compare_record({'core':core,'plugins':[{'name':'old'}],'packages':[]},{'core':core,'plugins':[{'name':'new'}],'packages':[]},self.store)
        self.assertEqual({(v['name'],v['kind']) for v in result['versions']},{('old','deleted'),('new','added')})

    def test_compare_does_not_write_objects(self):
        before={p:p.stat().st_mtime_ns for p in self.store.rglob('*')}
        self.run_compare()
        self.assertEqual(before,{p:p.stat().st_mtime_ns for p in self.store.rglob('*')})

    def test_python_version_architecture_and_implementation(self):
        core={'name':'core','path':str(self.source),'snapshot':self.snapshot}
        old={'core':core,'plugins':[],'packages':[],'python':{'version':'3.11.7','architecture':'AMD64','implementation':'CPython','platform':'Windows'}}
        new={**old,'python':{**old['python'],'version':'3.12.0','architecture':'ARM64','implementation':'PyPy'}}
        result=compare.compare_record(old,new,self.store)
        self.assertEqual(result['state'],'differences');self.assertEqual(len(result['runtime']),3)
        self.assertEqual(compare.compare_record(old,old,self.store)['state'],'matched')

    def test_same_version_source_commit_and_editable_changes(self):
        old=[{'name':'demo','version':'1','source':'https://example.com/a','commit':'aaa','editable':False}]
        new=[{**old[0],'source':'https://example.com/b','commit':'bbb','editable':True}]
        changes,unknown=compare.compare_packages(old,new)
        self.assertEqual(len(changes),3);self.assertFalse(unknown)

    def test_unknown_sources_are_not_proven_equal(self):
        data=[{'name':'demo','version':'1','source':'unknown (index not recorded)'}]
        changes,unknown=compare.compare_packages(data,data)
        self.assertFalse(changes);self.assertTrue(unknown)

if __name__=='__main__':unittest.main()
