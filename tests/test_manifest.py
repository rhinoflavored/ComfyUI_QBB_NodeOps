import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('manifest', Path(__file__).parents[1]/'qbb_nodeops/manifest.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class ManifestTests(unittest.TestCase):
    def record(self):
        return {'schema':1, 'kind':'version_record', 'python':{'version':'3.11.7'},
                'core':{'name':'ComfyUI', 'kind':'git', 'dirty':False, 'commit':'a'*40, 'url':'https://github.com/Comfy-Org/ComfyUI'},
                'plugins':[], 'packages':[]}

    def test_legacy_no_mutation(self):
        record=self.record(); before=copy.deepcopy(record)
        result=module.normalize(record)
        self.assertEqual(record,before)
        self.assertEqual(result['manifest']['recovery']['status'],'ready')
        self.assertFalse(result['manifest']['recovery']['runtime_verified'])
        self.assertEqual(result['manifest']['runtime']['cuda']['status'],'not_recorded')

    def test_missing_plugin_partial_core_blocked(self):
        record=self.record(); record['plugins']=[{'name':'local','kind':'local'}]
        self.assertEqual(module.normalize(record)['manifest']['recovery']['status'],'partial')
        record['core']['kind']='local'
        self.assertEqual(module.normalize(record)['manifest']['recovery']['status'],'blocked')

    def test_snapshot_and_dependency(self):
        record=self.record();record['schema']=2
        record['plugins']=[{'name':'local','snapshot':{'format':1,'files':[], 'skipped':[{'path':'large','reason':'size'}]}}]
        record['packages']=[{'name':'torch','version':'2.0+cu118','source':'unknown'}, {'name':'custom','version':'1','source':'local','editable':True}]
        manifest=module.normalize(record)['manifest']
        self.assertEqual(manifest['components'][1]['recovery']['method'],'snapshot')
        self.assertEqual(manifest['dependencies'][1]['recovery']['method'],'skip')
        self.assertEqual(manifest['runtime']['torch'],'2.0+cu118')
        self.assertEqual(manifest['recovery']['status'],'partial')

    def test_future_rejected(self):
        record=self.record();record['schema']=99
        with self.assertRaises(ValueError):module.normalize(record)

    def test_idempotent(self):
        record=module.normalize(self.record())
        self.assertEqual(record,module.normalize(record))

    def snapshot(self, paths=('main.py',), skipped=None, core_dirs=None):
        return {'format':2, 'files':[{'path':p, 'sha256':'b'*64, 'size':12, 'mode':0o644} for p in paths],
                'directories':[{'path':'', 'mode':0o755}], 'inventory':'complete_within_policy',
                'policy':{'core_dirs':core_dirs or []}, 'skipped':skipped or []}

    def content(self, record):
        return module.normalize(record)['manifest']['content']

    def test_all_legacy_schemas_and_v1_manifest_read_only(self):
        for schema in (1, 2, 3):
            with self.subTest(schema=schema):
                record=self.record();record['schema']=schema
                record['manifest']={'format_version':1,'coverage':{'invented':True}}
                before=copy.deepcopy(record);result=module.normalize(record)
                self.assertEqual(record,before)
                self.assertEqual(result['manifest']['format_version'],2)
                self.assertEqual(result['manifest']['source_schema'],schema)
                self.assertEqual(result['manifest']['content']['scopes']['code']['status'],'metadata_only')
                self.assertEqual(result,module.normalize(result))

    def test_unknown_versions_and_boolean_versions_rejected(self):
        for version in (0, 3, 99, True, '2'):
            record=self.record();record['manifest']={'format_version':version}
            with self.subTest(version=version), self.assertRaises(ValueError):module.normalize(record)
        record=self.record();record['schema']=True
        with self.assertRaises(ValueError):module.normalize(record)

    def test_forged_derived_capabilities_are_not_trusted(self):
        record=self.record()
        record['manifest']={'format_version':2,'content':{'verification':{'objects':'passed'},'scopes':{'code':{'status':'complete_within_policy'}}}}
        content=self.content(record)
        self.assertEqual(content['scopes']['code']['status'],'metadata_only')
        self.assertEqual(content['verification']['objects'],'not_checked')
        self.assertEqual(content['scopes']['user']['status'],'unknown')

    def test_shared_content_counts_once_across_components(self):
        record=self.record();record['core']['snapshot']=self.snapshot(('main.py','copy.py'))
        record['plugins']=[{'name':'local','snapshot':self.snapshot()}]
        content=self.content(record)
        self.assertEqual(content['sizes']['logical_bytes'],36)
        self.assertEqual(content['sizes']['unique_referenced_bytes'],12)
        self.assertEqual(content['sizes']['unique_objects'],1)
        self.assertIsNone(content['sizes']['new_bytes'])
        self.assertIsNone(content['sizes']['reclaimable_bytes'])
        self.assertEqual(content['inventories'][1]['snapshot_ref'],'/plugins/0/snapshot')
        self.assertFalse(content['storage']['requires_parent_record'])
        self.assertEqual(content['scopes']['code']['status'],'complete_within_policy')

    def test_deleted_file_does_not_depend_on_previous_record(self):
        old=self.record();old['core']['snapshot']=self.snapshot(('main.py','deleted.py'))
        new=copy.deepcopy(old);new['core']['snapshot']['files'].pop()
        self.assertEqual(self.content(new)['inventories'][0]['files'],1)
        self.assertEqual(self.content(old)['inventories'][0]['files'],2)

    def test_conflicting_object_sizes_and_invalid_references_rejected(self):
        record=self.record();record['core']['snapshot']=self.snapshot(('a','b'))
        record['core']['snapshot']['files'][1]['size']=13
        with self.assertRaises(ValueError):self.content(record)
        for field,value in [('sha256','bad'),('size',-1),('size',True),('size','12')]:
            record['core']['snapshot']=self.snapshot()
            record['core']['snapshot']['files'][0][field]=value
            with self.subTest(field=field,value=value), self.assertRaises(ValueError):self.content(record)

    def test_legacy_files_do_not_invent_directory_completeness(self):
        record=self.record();record['core']['snapshot']={'format':1,'files':[]}
        content=self.content(record)
        self.assertEqual(content['inventories'][0]['status'],'files_only')
        self.assertIsNone(content['inventories'][0]['directories'])
        self.assertEqual(content['scopes']['code']['status'],'partial')
        record['core']['snapshot']={'format':2,'files':[],'directories':[]}
        self.assertEqual(self.content(record)['inventories'][0]['status'],'unknown')

    def test_empty_directory_snapshot_is_saved_not_metadata_only(self):
        record=self.record();record['core']['snapshot']=self.snapshot(())
        content=self.content(record)
        self.assertEqual(content['scopes']['code']['status'],'complete_within_policy')
        self.assertEqual(content['inventories'][0]['directories'],1)
        self.assertEqual(content['sizes']['unique_referenced_bytes'],0)

    def test_expected_exclusions_differ_from_missing_content(self):
        record=self.record();snapshot=self.snapshot(skipped=[
            {'path':'.git','reason':'excluded cache/environment/assets'},
            {'path':'sub/__pycache__','reason':'excluded cache/environment/assets'},
            {'path':'custom_nodes','reason':'excluded cache/environment/assets'},
            {'path':'models','reason':'excluded cache/environment/assets'}])
        record['core']['snapshot']=snapshot;content=self.content(record)
        self.assertEqual([e['category'] for e in content['inventories'][0]['exclusions']],
                         ['repository_metadata','regenerable','separate_components','outside_scope'])
        self.assertEqual(content['scopes']['code']['status'],'complete_within_policy')
        self.assertEqual(content['scopes']['models']['status'],'not_selected')
        for path,reason in [('weights.bin','larger than 256 MiB'),('linked','link/reparse point'),
                            ('node_modules','excluded cache/environment/assets'),('secret','unknown rule')]:
            with self.subTest(path=path):
                snapshot['skipped']=[{'path':path,'reason':reason}]
                self.assertEqual(self.content(record)['scopes']['code']['status'],'partial')

    def test_requested_scope_is_not_proof_of_captured_scope(self):
        record=self.record();record['capture_options']={'core_dirs':['user','models']}
        self.assertEqual(self.content(record)['scopes']['user']['status'],'unknown')
        record['core']['snapshot']=self.snapshot()
        self.assertEqual(self.content(record)['scopes']['user']['status'],'not_selected')

    def test_selected_scopes_distinguish_captured_absent_and_excluded(self):
        record=self.record();record['core']['snapshot']=self.snapshot(('user/workflow.json',),core_dirs=['user','models','input'])
        record['core']['snapshot']['directories'].append({'path':'input','mode':0o755})
        content=self.content(record)
        self.assertEqual(content['scopes']['user']['status'],'captured')
        self.assertEqual(content['scopes']['input']['status'],'captured')
        self.assertEqual(content['scopes']['models']['status'],'absent')
        self.assertEqual(content['scopes']['output']['status'],'not_selected')
        record['core']['snapshot']['skipped']=[{'path':'models/large.bin','reason':'larger than 256 MiB'}]
        self.assertEqual(self.content(record)['scopes']['models']['status'],'partial')

    def test_offline_objects_counted_without_claiming_runtime_verification(self):
        record=self.record();record['core']['snapshot']=self.snapshot()
        record['offline_environment']={'status':'ready','snapshot':self.snapshot(('python/python.exe',)),
                                       'original_binary_identity':True}
        content=self.content(record)
        self.assertEqual(content['sizes']['logical_bytes'],24)
        self.assertEqual(content['sizes']['unique_referenced_bytes'],12)
        self.assertEqual(content['offline_inventory']['snapshot_ref'],'/offline_environment/snapshot')
        self.assertEqual(content['scopes']['python_environment']['status'],'offline_assets_recorded')
        self.assertFalse(content['scopes']['python_environment']['original_binary_identity'])
        self.assertEqual(set(content['verification'].values()),{'not_checked'})

    def test_returned_contract_does_not_alias_source(self):
        record=self.record();record['core']['snapshot']=self.snapshot(skipped=[{'path':'x','reason':'unknown'}])
        result=module.normalize(record)
        result['manifest']['content']['inventories'][0]['exclusions'][0]['path']='changed'
        self.assertEqual(record['core']['snapshot']['skipped'][0]['path'],'x')

    def test_incomplete_offline_assets_are_partial_not_ready(self):
        record=self.record();record['offline_environment']={'status':'ready','snapshot':{'format':1,'files':[]}}
        self.assertEqual(self.content(record)['scopes']['python_environment']['status'],'partial')

    def test_future_or_boolean_snapshot_format_rejected(self):
        for version in (3, True):
            record=self.record();record['core']['snapshot']=self.snapshot()
            record['core']['snapshot']['format']=version
            with self.subTest(version=version), self.assertRaises(ValueError):self.content(record)

    def test_known_cache_does_not_make_user_scope_partial(self):
        record=self.record();record['core']['snapshot']=self.snapshot(('user/config.json',),core_dirs=['user'],
            skipped=[{'path':'user/__pycache__','reason':'excluded cache/environment/assets'}])
        self.assertEqual(self.content(record)['scopes']['user']['status'],'captured')
