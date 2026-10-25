import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

package = types.ModuleType('_choice_tests')
package.__path__ = [str(Path(__file__).parents[1] / 'qbb_nodeops')]
sys.modules[package.__name__] = package
def load(name):
    spec = importlib.util.spec_from_file_location(package.__name__+'.'+name, Path(package.__path__[0])/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
files = load('file_snapshots')
choices = load('recovery_choices')

class ChoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'plugin'
        self.source.mkdir()
        (self.source/'main.py').write_text('test')
        self.store = self.root/'store'
        self.record = {'core':{}, 'plugins':[{}]}
        self.current = {'core':{'path':str(self.source)}, 'plugins':[{'name':'plug', 'path':str(self.source)}]}
        self.repos = [{'name':'ComfyUI','core':True,'skip':None,'snapshot':None}, {'name':'plug','core':False,'skip':'missing files','snapshot':None}]
    def prepare(self, value):
        return choices.prepare(self.record,self.repos,value,self.current,self.store)
    def test_requires_explicit_choice(self):
        self.assertTrue(self.prepare({})[0])

    def test_core_substitution_preserves_recorded_scopes_and_resume_checks(self):
        executor = load('rebuild_executor')
        for name in ('user', 'models', 'input', 'output'):
            folder = self.source / name
            folder.mkdir()
            (folder / 'example.txt').write_text(name)
        for action in ('backup', 'current'):
            for origin in ('snapshot', 'capture_options'):
                with self.subTest(action=action, origin=origin):
                    scopes = ['user', 'models', 'input']
                    self.record['core'] = {'snapshot': {'policy': {'core_dirs': scopes}}} if origin == 'snapshot' else {}
                    self.record['capture_options'] = {'core_dirs': scopes if origin == 'capture_options' else ['output']}
                    self.repos[0].update(skip='missing files', snapshot=None, target='ComfyUI')
                    choice = {'action': action, 'path': str(self.source)}
                    self.assertFalse(self.prepare({'0': choice, '1': {'action': 'skip'}})[0])
                    repo = self.repos[0]
                    self.assertEqual(repo['source_scan']['policy']['core_dirs'], sorted(scopes))
                    destination = self.root / (action + origin)
                    (destination / '.nodeops-recovery').mkdir(parents=True)
                    def no_git(args): raise AssertionError('must not use Git')
                    executor.component(repo, destination, self.store, no_git)
                    for name in scopes:
                        self.assertEqual((destination / 'ComfyUI' / name / 'example.txt').read_text(), name)
                    self.assertFalse((destination / 'ComfyUI/output').exists())
                    self.assertTrue(executor.component(repo, destination, self.store, no_git)['reused'])
                    (destination / 'ComfyUI/user/example.txt').write_text('changed')
                    with self.assertRaises(ValueError):
                        executor.component(repo, destination, self.store, no_git)
    def test_skip(self):
        self.assertFalse(self.prepare({'1':{'action':'skip'}})[0])
        self.assertTrue(self.repos[1]['skip'])
    def test_core_cannot_skip(self):
        self.repos[0]['skip']='missing'
        with self.assertRaises(ValueError):self.prepare({'0':{'action':'skip'}})
    def test_current_trusted_inventory(self):
        self.record['plugins'][0]['path']='nonexistent'
        self.assertFalse(self.prepare({'1':{'action':'current'}})[0])
        self.assertEqual(self.repos[1]['source'],str(self.source))
        self.assertTrue(self.repos[1]['substitution'])
    def test_backup_and_capture(self):
        self.assertFalse(self.prepare({'1':{'action':'backup','path':str(self.source)}})[0])
        repo=self.repos[1]
        snapshot=files.capture(self.source,self.store,large_files='include',expected=repo['source_scan'])
        files.restore(snapshot,self.store,self.root/'restored')
        self.assertEqual((self.root/'restored'/'main.py').read_text(),'test')
    def test_stale_source(self):
        self.prepare({'1':{'action':'current'}})
        (self.source/'new.py').write_text('new')
        with self.assertRaises(ValueError):files.capture(self.source,self.store,large_files='include',expected=self.repos[1]['source_scan'])
    def test_missing_backup(self):
        self.assertTrue(self.prepare({'1':{'action':'backup','path':str(self.root/'missing')}})[0])
    def test_duplicate_current(self):
        self.current['plugins']*=2
        self.assertTrue(self.prepare({'1':{'action':'current'}})[0])
    def test_bad_id(self):
        with self.assertRaises(ValueError):self.prepare({'99':{}})
    def test_bad_name_cannot_be_substituted(self):
        self.repos[1]['name']='../escape'
        self.assertTrue(self.prepare({'1':{'action':'current'}})[0])
    def test_missing_object_is_gap(self):
        self.repos[1].update(skip=None,snapshot={'files':[{'sha256':'a'*64}]})
        self.assertTrue(self.prepare({})[0])

    def test_existing_corrupt_object_requires_explicit_resolution(self):
        snapshot=files.capture(self.source,self.store)
        self.repos[1].update(skip=None,snapshot=snapshot)
        obj=files.object_path(self.store,snapshot['files'][0]['sha256']);obj.write_bytes(b'evil')
        self.assertTrue(self.prepare({})[0])
        self.assertEqual(self.repos[1]['object_verification']['status'],'failed')
        self.assertFalse(self.prepare({'1':{'action':'skip'}})[0])

    def test_preflight_hashes_shared_objects_once(self):
        snapshot=files.capture(self.source,self.store)
        self.repos[0].update(skip=None,snapshot=snapshot)
        self.repos[1].update(skip=None,snapshot=snapshot)
        with patch.object(files,'stable_hash',wraps=files.stable_hash) as hashed:
            self.assertFalse(self.prepare({})[0])
        self.assertEqual(hashed.call_count,1)
        self.assertEqual(self.repos[0]['object_verification']['status'],'passed')
        self.assertEqual(self.repos[1]['object_verification']['status'],'passed')

    def test_preflight_rejects_unsafe_snapshot_before_recovery(self):
        snapshot=files.capture(self.source,self.store);snapshot['files'][0]['path']='../outside'
        self.repos[1].update(skip=None,snapshot=snapshot)
        self.assertTrue(self.prepare({})[0])

    def test_substitution_freezes_content_not_only_metadata(self):
        self.prepare({'1':{'action':'current'}})
        scan=self.repos[1]['source_scan'];signature=scan['files'][0]['signature']
        (self.source/'main.py').write_text('evil')
        with patch.object(files,'signature',return_value=signature):
            with self.assertRaisesRegex(ValueError,'content changed'):
                files.capture(self.source,self.store,expected=scan,**scan['policy'])
    def test_plan_fingerprint(self):
        plan={'repositories':self.repos,'pins':[],'blockers':[],'warnings':[]}
        old=choices.fingerprint(self.record,plan)
        self.repos[1]['action']='skip'
        self.assertNotEqual(old,choices.fingerprint(self.record,plan))

    def test_single_file_current_materializes_and_resumes(self):
        source=self.root/'node.py';source.write_text('NODE_CLASS_MAPPINGS = {}')
        self.repos[1].update(name='node.py',target='ComfyUI/custom_nodes/node.py')
        self.current['plugins']=[{'name':'node.py','path':str(source)}]
        self.assertFalse(self.prepare({'1':{'action':'current'}})[0])
        executor=load('rebuild_executor')
        destination=self.root/'destination';(destination/'.nodeops-recovery').mkdir(parents=True)
        def no_git(args):raise AssertionError('must not use Git')
        outcome=executor.component(self.repos[1],destination,self.store,no_git)
        self.assertEqual(outcome['status'],'substituted')
        self.assertEqual((destination/self.repos[1]['target']).read_bytes(),source.read_bytes())
        self.assertTrue(executor.component(self.repos[1],destination,self.store,no_git)['reused'])

    def test_single_file_backup_requires_python_file(self):
        source=self.root/'backup.py';source.write_text('pass')
        self.repos[1]['name']='node.py'
        self.assertFalse(self.prepare({'1':{'action':'backup','path':str(source)}})[0])
        self.assertTrue(self.repos[1]['source_scan']['single_file'])
        self.repos[1]['skip']='missing'
        self.assertTrue(self.prepare({'1':{'action':'backup','path':str(self.source)}})[0])

    def test_single_file_migration_and_count(self):
        migration=load('migration')
        instances=sys.modules[package.__name__+'.instances']
        source=self.root/'instance';nodes=source/'custom_nodes';nodes.mkdir(parents=True)
        (nodes/'node.py').write_text('pass')
        (nodes/'__init__.py').write_text('pass')
        (nodes/'disabled.py.disabled').write_text('pass')
        (nodes/'folder').mkdir()
        found=migration.scan_plugins(source)['plugins']
        self.assertEqual({p['name'] for p in found},{'node.py','folder'})
        description=instances.describe_instance(source,instance_id='test',name='test')
        self.assertEqual(description['plugin_count'],2)
        target=self.root/'target';target.mkdir();created=[]
        plugin=migration.MigrationPlugin(**next(p for p in found if p['name']=='node.py'))
        migration._copy_selected_plugins(target,[plugin],delete_source=False,created=created)
        self.assertEqual((target/'node.py').read_bytes(),(nodes/'node.py').read_bytes())
        self.assertEqual(created,[target/'node.py'])
        with self.assertRaises(ValueError):
            migration._copy_selected_plugins(target,[plugin],delete_source=False)

if __name__ == '__main__':unittest.main()
