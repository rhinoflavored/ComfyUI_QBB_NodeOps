import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

package=types.ModuleType('_environment_tests');package.__path__=[str(Path(__file__).parents[1]/'qbb_nodeops')];sys.modules[package.__name__]=package
def load(name):
    spec=importlib.util.spec_from_file_location(package.__name__+'.'+name,Path(package.__path__[0])/(name+'.py'))
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module);return module
env=load('recovery_environment')

class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.target=self.root/'environment';self.target.mkdir()
        self.record={'python':{'version':platform.python_version(),'architecture':platform.machine(),'platform':platform.platform()},'packages':[{'name':'demo','version':'1.0','source':'unknown'}]}
    def wheel(self,name='demo',version='1.0'):
        path=self.root/(name+'-'+version+'-py3-none-any.whl')
        prefix=name+'-'+version+'.dist-info/'
        with zipfile.ZipFile(path,'w') as z:
            z.writestr(prefix+'METADATA',f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n')
            z.writestr(prefix+'WHEEL','Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            z.writestr(prefix+'RECORD','')
        return path
    def local_plan(self):
        path=self.wheel()
        return env.build_plan(self.record,{'dependencies':{'demo':{'action':'wheel','path':str(path)}}})[1]
    def test_matching_python_current(self):
        runtime,rows,blocked,_=env.build_plan(self.record)
        self.assertEqual(runtime['method'],'current');self.assertFalse(blocked);self.assertEqual(rows[0]['index'],env.PYPI)
    def test_other_python_managed(self):
        self.record['python']['version']='3.10.11'
        self.assertEqual(env.build_plan(self.record)[0]['method'],'managed')
    def test_forced_current_blocks_mismatch(self):
        self.record['python']['version']='3.10.11'
        self.assertTrue(env.build_plan(self.record,{'python':'current'})[2])
    def test_architecture_blocked(self):
        self.record['python']['architecture']='wrong'
        self.assertTrue(env.build_plan(self.record)[2])
    def test_pytorch_build_source(self):
        self.record['packages']=[{'name':'torch','version':'2.5.1+cu124','source':'unknown'}]
        self.assertEqual(env.build_plan(self.record)[1][0]['index'],env.TORCH+'cu124')
    def test_local_source_needs_explicit_choice(self):
        self.record['packages'][0].update(source='local/private source',editable=True)
        self.assertTrue(env.build_plan(self.record)[2])
        self.assertFalse(env.build_plan(self.record,{'dependencies':{'demo':{'action':'skip'}}})[2])
        self.assertFalse(env.build_plan(self.record,{'dependencies':{'demo':{'action':'pypi'}}})[2])
    def test_duplicate_normalized_names(self):
        self.record['packages']*=2
        self.assertTrue(env.build_plan(self.record)[2])
    def test_wheel_metadata(self):
        self.assertEqual(env.wheel_info(self.wheel())['name'],'demo')
    def test_wheel_wrong_version_blocks(self):
        wheel=self.wheel(version='2.0')
        self.assertTrue(env.build_plan(self.record,{'dependencies':{'demo':{'action':'wheel','path':str(wheel)}}})[2])
    def test_wheel_changed_rejected(self):
        rows=self.local_plan();Path(rows[0]['path']).write_bytes(b'broken')
        with self.assertRaises(ValueError):env.install(rows,self.target,'unused',lambda *a,**kw:None,lambda **kw:None)
    def fake_run(self,args,**kw):
        if 'list' in args:return json.dumps([{'name':'demo','version':'1.0'}])
        return ''
    def test_cache_reused_without_original_wheel(self):
        rows=self.local_plan();env.install(rows,self.target,'unused',self.fake_run,lambda **kw:None)
        Path(rows[0]['path']).unlink()
        env.install(rows,self.target,'unused',self.fake_run,lambda **kw:None)
    def test_corrupt_cached_wheel_refused(self):
        rows=self.local_plan();env.install(rows,self.target,'unused',self.fake_run,lambda **kw:None)
        next((self.target/'wheel-cache').glob('*/*.whl')).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):env.install(rows,self.target,'unused',self.fake_run,lambda **kw:None)
    def test_download_failure_keeps_other_success(self):
        rows=self.local_plan();rows.append({'name':'other','key':'other','version':'1.0','action':'pypi','index':env.PYPI})
        def run(args,**kw):raise ValueError('offline')
        state={}
        with self.assertRaises(ValueError):env.install(rows,self.target,'unused',run,lambda **kw:state.update(kw))
        self.assertEqual([r['status'] for r in state['dependency_items']],['downloaded','failed'])
        self.assertTrue((self.target/'wheel-cache/demo.json').is_file())
    def test_install_flags_no_network_or_builds(self):
        calls=[]
        def run(args,**kw):calls.append(args);return self.fake_run(args)
        env.install(self.local_plan(),self.target,'unused',run,lambda **kw:None)
        command=next(a for a in calls if 'install' in a)
        for flag in ('--no-deps','--no-index','--require-hashes','--only-binary=:all:'):self.assertIn(flag,command)
    def test_managed_python_probe_must_match(self):
        runtime={'version':'3.10.11','architecture':platform.machine(),'method':'current'}
        with self.assertRaises(ValueError):env.prepare_python(runtime,self.target,lambda *a,**kw:json.dumps([platform.python_version(),platform.machine(),'CPython']),lambda **kw:None)
    def test_managed_download_is_scoped_and_exact(self):
        calls=[];base=self.target/'.python/python.exe'
        runtime={'version':'3.10.11','architecture':platform.machine(),'method':'managed'}
        def run(args,**kw):
            calls.append((args,kw))
            if 'find' in args:return str(base)
            if '-c' in args:return json.dumps(['3.10.11',platform.machine(),'CPython'])
            return ''
        env.prepare_python(runtime,self.target,run,lambda **kw:None)
        command,kw=next(c for c in calls if 'python' in c[0] and 'install' in c[0])
        self.assertEqual(command[-1],'3.10.11')
        self.assertEqual(kw['extra_env']['UV_PYTHON_INSTALL_DIR'],str(self.target/'.python'))
        self.assertTrue(all('--managed-python' not in cmd for cmd,_ in calls))
    def test_managed_find_outside_target_rejected(self):
        def run(args,**kw):return str(self.root/'outside') if 'find' in args else ''
        with self.assertRaises(ValueError):env.prepare_python({'version':'3.10.11','architecture':platform.machine(),'method':'managed'},self.target,run,lambda **kw:None)
    def test_real_offline_wheel_install_into_temporary_venv(self):
        # Only temporary files are installed; no download and no host pip mutation.
        def run(args,**kw):
            result=subprocess.run([str(a) for a in args],capture_output=True,text=True,timeout=120)
            if result.returncode:raise ValueError(result.stderr)
            return result.stdout
        python=self.target/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        runtime=env.build_plan(self.record)[0]
        self.assertEqual(env.prepare_python(runtime,self.target,run,lambda **kw:None),python)
        env.install(self.local_plan(),self.target,python,run,lambda **kw:None)
        self.assertIn('demo',run([python,'-m','pip','list','--format=json']))

if __name__=='__main__':unittest.main()
