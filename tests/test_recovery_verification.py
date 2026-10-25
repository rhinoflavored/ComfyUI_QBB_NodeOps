import importlib.util
import json
from pathlib import Path
import platform
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

package=types.ModuleType('_verification_tests');package.__path__=[str(Path(__file__).parents[1]/'qbb_nodeops')];sys.modules[package.__name__]=package
def load(name):
    spec=importlib.util.spec_from_file_location(package.__name__+'.'+name,Path(package.__path__[0])/(name+'.py'))
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module);return module
files=load('file_snapshots');verification=load('recovery_verification');rebuild=load('rebuild')

class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=self.root/'store';self.target=self.root/'target';self.core=self.target/'ComfyUI'
        (self.core/'comfy').mkdir(parents=True);(self.core/'main.py').write_text('test')
        (self.core/'comfy/cli_args.py').write_text('--quick-test-for-ci --disable-all-custom-nodes')
        meta=self.target/'.nodeops-recovery';meta.mkdir()
        snapshot=files.capture(self.core,self.store,core=True)
        (meta/'component-0.json').write_text(json.dumps({'target':'ComfyUI','snapshot':snapshot}))
        self.row={'target':str(self.target),'storage':str(self.store),'prepared':{'repositories':[{'id':'0','target':'ComfyUI','skip':None,'core':True}], 'pins':['torch==2.5.1+cpu'],'runtime':{'version':platform.python_version()}}}
        self.calls=[]
    def runner(self,args,*a,**kw):
        self.calls.append(args)
        if '-c' in args and 'NODEOPS_PROBE' in args[-1]:return 'NODEOPS_PROBE='+json.dumps({'python':platform.python_version(),'packages':{'torch':'2.5.1+cpu'}}),'probe.log'
        return 'success','probe.log'
    def test_default_no_startup(self):
        result=verification.verify_job(self.row,runner=self.runner)
        self.assertFalse(result['startup_verified']);self.assertFalse(result['workflow_verified'])
        self.assertFalse(any('main.py' in a for a in self.calls))

    def test_no_recorded_torch_allows_core_startup(self):
        self.row['prepared']['pins'] = []
        result = verification.verify_job(self.row, startup=True, runner=self.runner)
        self.assertTrue(result['startup_verified'])
        self.assertEqual(next(c['status'] for c in result['checks'] if c['name'] == 'torch'), 'not_checked')
        self.assertTrue(any('main.py' in args for args in self.calls))

    def test_no_torch_startup_failure_is_reported(self):
        self.row['prepared']['pins'] = []
        def runner(args, *a, **kw):
            if 'main.py' in args:
                raise ValueError('No module named torch')
            return self.runner(args, *a, **kw)
        result = verification.verify_job(self.row, startup=True, runner=runner)
        self.assertFalse(result['startup_verified'])
        self.assertEqual(result['status'], 'failed')
    def test_service_report_is_integrated(self):
        import importlib
        service=importlib.import_module(package.__name__+'.recovery_service')
        report={'checks':[{'name':'http','status':'passed','detail':'ok'},{'name':'workflow','status':'passed','detail':'ok'}],'http_verified':True,'nodes_verified':True,'workflow_verified':True}
        with patch.object(service,'verify_service',return_value=report) as check:
            result=verification.verify_job(self.row,True,False,self.runner,service=True,expected_nodes=['Test'],workflow={'1':{'class_type':'Test','inputs':{}}})
        self.assertTrue(result['workflow_verified']);self.assertTrue(result['http_verified'])
        self.assertEqual(check.call_args.kwargs['expected_nodes'],['Test'])
    def test_failed_dependencies_never_start_service(self):
        import importlib
        service=importlib.import_module(package.__name__+'.recovery_service')
        with patch.object(service,'verify_service') as check:
            result=verification.verify_job(self.row,True,False,lambda *a:(_ for _ in ()).throw(ValueError('failed')),service=True)
        check.assert_not_called();self.assertFalse(result['workflow_verified'])
    def test_opt_in_core_startup(self):
        result=verification.verify_job(self.row,True,False,self.runner)
        self.assertTrue(result['startup_verified'])
        command=next(a for a in self.calls if 'main.py' in a)
        self.assertIn('--quick-test-for-ci',command);self.assertIn('--disable-all-custom-nodes',command);self.assertIn('--cpu',command)
    def test_plugin_loading_requires_separate_option(self):
        result=verification.verify_job(self.row,True,True,self.runner)
        self.assertTrue(result['startup_verified']);self.assertFalse(result['workflow_verified'])
        self.assertNotIn('--disable-all-custom-nodes',next(a for a in self.calls if 'main.py' in a))
        self.assertEqual(result['checks'][-1]['status'],'not_checked')
    def test_changed_files_stop_before_executing_python(self):
        (self.core/'main.py').write_text('edited')
        result=verification.verify_job(self.row,True,True,self.runner)
        self.assertEqual(result['status'],'failed');self.assertEqual(self.calls,[])
    def test_import_failure_with_zero_exit_is_failure(self):
        def runner(args,*a,**kw):
            if 'main.py' in args:return 'IMPORT FAILED: plugin','log'
            return self.runner(args,*a,**kw)
        self.assertFalse(verification.verify_job(self.row,True,True,runner)['startup_verified'])
    def test_dependency_failure_prevents_startup(self):
        def runner(*a,**kw):raise ValueError('pip conflict')
        result=verification.verify_job(self.row,True,True,runner)
        self.assertEqual(result['status'],'failed');self.assertFalse(result['startup_verified'])
    def test_unsupported_startup_flag_does_not_launch(self):
        original_read=Path.read_text
        with patch.object(Path,'read_text',autospec=True,side_effect=lambda path,*a,**kw: '' if path.name=='cli_args.py' else original_read(path,*a,**kw)):
            result=verification.verify_job(self.row,True,False,self.runner)
        self.assertEqual(result['checks'][0]['status'],'passed')
        self.assertFalse(result['startup_verified']);self.assertFalse(any('main.py' in a for a in self.calls))
    def test_confirmation_required(self):
        with self.assertRaises(ValueError):rebuild.verify_recovery('a'*32)
        with self.assertRaises(ValueError):rebuild.verify_recovery('a'*32,True,False,True)
    def test_capabilities_and_no_full_runtime_claim(self):
        row={'executor_version':1,'status':'rebuilt_unverified','verification':{'status':'passed','startup_verified':True}}
        result=rebuild.task_state(row)
        self.assertTrue(result['can_verify']);self.assertFalse(result['runtime_verified'])
        row['verification']['status']='running'
        self.assertFalse(rebuild.task_state(row)['can_verify'])
    def test_real_bounded_probe(self):
        text,path=verification.probe([sys.executable,'-c','print("ok")'],self.root,self.root/'logs')
        self.assertIn('ok',text);self.assertTrue(Path(path).is_file())
    def test_real_probe_timeout(self):
        with self.assertRaises(ValueError):verification.probe([sys.executable,'-c','import time; time.sleep(30)'],self.root,self.root/'logs',timeout=.1)

if __name__=='__main__':unittest.main()
