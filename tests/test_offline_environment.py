import copy
import importlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

package=types.ModuleType('_offline_tests');package.__path__=[str(Path(__file__).parents[1]/'qbb_nodeops')];sys.modules[package.__name__]=package
offline=importlib.import_module(package.__name__+'.offline_environment')
env=importlib.import_module(package.__name__+'.recovery_environment')
archives=importlib.import_module(package.__name__+'.record_archives')
records=importlib.import_module(package.__name__+'.version_records')
files=offline.files

class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.store=self.root/'store';self.assets=self.root/'assets';(self.assets/'python').mkdir(parents=True);(self.assets/'wheels').mkdir()
        (self.assets/'python/python.exe').write_bytes(b'fixture interpreter')
        self.wheel=self.assets/'wheels/demo-1.0-py3-none-any.whl'
        with zipfile.ZipFile(self.wheel,'w') as z:
            z.writestr('demo-1.0.dist-info/METADATA','Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n')
            z.writestr('demo-1.0.dist-info/WHEEL','Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            z.writestr('demo-1.0.dist-info/RECORD','')
        core=self.root/'core';core.mkdir();(core/'main.py').write_text('pass')
        self.record={'kind':'version_record','schema':3,'id':'a'*32,'label':'offline','created':1,'python':{'version':platform.python_version(),'architecture':platform.machine(),'platform':platform.platform(),'implementation':'CPython'},'packages':[{'name':'demo','version':'1.0','source':'unknown'}],'core':{'name':'ComfyUI','kind':'local','snapshot':files.capture(core,self.store)},'plugins':[]}
        self.data={'format':1,'status':'ready','python':self.record['python'],'executable':'python/python.exe','snapshot':files.capture(self.assets,self.store),'packages':[{'name':'demo','key':'demo','version':'1.0','action':'offline','relative':'wheels/'+self.wheel.name,'wheel':env.wheel_info(self.wheel),'source_substitution':True}]}
        self.record['offline_environment']=self.data
        p=patch.object(records,'directory',return_value=self.store);p.start();self.addCleanup(p.stop)
        (self.store/('a'*32+'.json')).write_text(json.dumps(self.record))
    def test_plan_offline_no_substitution(self):
        runtime,packages,blocked,_=env.build_plan(self.record)
        self.assertEqual(runtime['method'],'offline');self.assertFalse(blocked);self.assertEqual(packages[0]['action'],'offline')
    def test_missing_wheel_blocks(self):
        files.object_path(self.store,self.data['packages'][0]['wheel']['sha256']).unlink()
        self.assertTrue(env.build_plan(self.record)[2])
    def test_interrupted_asset_staging_can_retry(self):
        runtime=env.build_plan(self.record)[0];target=self.root/'retry';target.mkdir()
        def interrupted(snapshot,store,destination):
            destination.mkdir();(destination/'partial').write_text('partial')
            raise OSError('interrupted')
        def run(args,**kwargs):
            if '-c' in args:return json.dumps([platform.python_version(),platform.machine(),'CPython'])
            return ''
        with patch.object(files,'restore',side_effect=interrupted):
            with self.assertRaises(OSError):env.prepare_python(runtime,target,run,lambda **kw:None)
        self.assertFalse((target/'offline-assets').exists())
        env.prepare_python(runtime,target,run,lambda **kw:None)
        self.assertEqual((target/'offline-assets/python/python.exe').read_bytes(),b'fixture interpreter')
        (target/'offline-assets/python/python.exe').write_bytes(b'changed')
        with self.assertRaises(ValueError):env.prepare_python(runtime,target,run,lambda **kw:None)
    def test_incomplete_inventory_rejected(self):
        self.data['packages']=[]
        with self.assertRaises(ValueError):offline.payload(self.record)
    def test_unsafe_executable_rejected(self):
        self.data['executable']='../python.exe'
        with self.assertRaises(ValueError):offline.payload(self.record)
    def test_offline_payload_cannot_select_network_action(self):
        self.data['packages'][0]['action']='pypi'
        with self.assertRaises(ValueError):offline.payload(self.record)
    def test_architecture_mismatch_blocked(self):
        self.record['python']['architecture']='unsupported'
        self.assertTrue(env.build_plan(self.record)[2])
    def test_offline_never_downloads(self):
        calls=[];runtime=env.build_plan(self.record)[0];target=self.root/'target';target.mkdir()
        def run(args,**kwargs):
            args=[str(a) for a in args];calls.append(args)
            self.assertNotIn('download',args);self.assertNotIn('--index-url',args)
            if '-c' in args:return json.dumps([platform.python_version(),platform.machine(),'CPython'])
            if 'list' in args:return json.dumps([{'name':'demo','version':'1.0'}])
            return ''
        python=env.prepare_python(runtime,target,run,lambda **kw:None)
        env.install(self.data['packages'],target,python,run,lambda **kw:None)
        self.assertTrue(any('--no-index' in c and '--require-hashes' in c for c in calls))
    def test_roundtrip_and_gc_keep_runtime(self):
        archive=self.root/'offline.zip';archives.export_bundle('a'*32,str(archive),True)
        other=self.root/'other';other.mkdir()
        with patch.object(records,'directory',return_value=other):
            preview=archives.preview_import(str(archive));self.assertTrue(preview['offline_environment'])
            result=archives.import_bundle(preview['token'],True);imported=records.read(result['id'])
            self.assertIsNotNone(offline.payload(imported));self.assertEqual(archives.preview_gc()['objects'],0)
    def test_prepare_requires_confirmation(self):
        with self.assertRaises(ValueError):offline.start('a'*32)
    def test_prepare_missing_code_fails_before_runner(self):
        record=copy.deepcopy(self.record);record['core'].pop('snapshot')
        (self.store/('a'*32+'.json')).write_text(json.dumps(record))
        with self.assertRaises(ValueError):offline.prepare('a'*32,runner=lambda *a,**kw:self.fail('Executed before validation'))
    def test_prepare_failure_never_publishes_ready_record(self):
        before=set(self.store.glob('*.json'))
        with patch.object(env,'prepare_python',side_effect=ValueError('download failed')):
            with self.assertRaises(ValueError):offline.prepare('a'*32,save=lambda **kw:None)
        self.assertEqual(before,set(self.store.glob('*.json')))
        self.assertFalse(list(self.store.glob('.offline-build-*')))
    def test_offline_key_cannot_escape_cache(self):
        self.record['packages'][0]['name']='/escape'
        self.data['packages'][0].update(name='/escape',key='/escape')
        with self.assertRaises(ValueError):offline.payload(self.record)

    @unittest.skipUnless(os.name=='nt','Windows standalone runtime scope')
    def test_real_relocated_interpreter_and_wheel_without_downloads(self):
        # Only copies standard-library/runtime files into a disposable fixture.
        # No installation into the user's Python and no network command is allowed.
        base=Path(sys.base_prefix);python_dir=self.assets/'python'
        for pattern in ('python*.exe','python*.dll','vcruntime*.dll','python*.zip'):
            for source in base.glob(pattern):shutil.copyfile(source,python_dir/source.name)
        for name in ('Lib','DLLs'):
            if (base/name).is_dir():shutil.copytree(base/name,python_dir/name,ignore=shutil.ignore_patterns('site-packages','__pycache__','test','tests'))
        self.data['snapshot']=files.capture(self.assets,self.store,large_files='include',runtime_files=True)
        target=self.root/'relocated';target.mkdir()
        def run(args,**kwargs):
            args=[str(a) for a in args]
            self.assertNotIn('download',args);self.assertNotIn('--index-url',args)
            if 'install' in args:self.assertIn('--no-index',args)
            result=subprocess.run(args,capture_output=True,text=True,timeout=120,env={**os.environ,'PIP_NO_INDEX':'1'})
            if result.returncode:raise ValueError(result.stderr)
            return result.stdout
        runtime=env.build_plan(self.record)[0]
        python=env.prepare_python(runtime,target,run,lambda **kw:None)
        env.install(self.data['packages'],target,python,run,lambda **kw:None)
        installed=json.loads(run([python,'-I','-m','pip','--disable-pip-version-check','list','--format=json']))
        self.assertIn({'name':'demo','version':'1.0'},installed)
        # Exercise collection/publish as well, substituting a local runtime fixture
        # only for the download step. Installation and relocation checks stay real.
        original_prepare=env.prepare_python
        def local_managed(runtime,environment,runner,save):
            if runtime['method']!='managed':return original_prepare(runtime,environment,runner,save)
            managed=environment/'.python/cpython';managed.parent.mkdir()
            shutil.copytree(python_dir,managed,ignore=shutil.ignore_patterns('__pycache__'))
            runner([managed/'python.exe','-I','-m','venv','--copies',environment/'.venv'])
            return environment/'.venv/Scripts/python.exe'
        plain=copy.deepcopy(self.record);plain.pop('offline_environment')
        (self.store/('a'*32+'.json')).write_text(json.dumps(plain))
        with patch.object(env,'prepare_python',side_effect=local_managed):
            result=offline.prepare('a'*32,str(self.assets/'wheels'),save=lambda **kw:None,runner=run)
        prepared=records.read(result['id']);self.assertIsNotNone(offline.payload(prepared))
        self.assertNotEqual(result['id'],'a'*32)
        self.assertNotIn('offline_environment',records.read('a'*32))

if __name__=='__main__':unittest.main()
