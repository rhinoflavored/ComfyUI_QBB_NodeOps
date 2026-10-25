import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

package=types.ModuleType('_executor_tests');package.__path__=[str(Path(__file__).parents[1]/'qbb_nodeops')];sys.modules[package.__name__]=package
def load(name):
    spec=importlib.util.spec_from_file_location(package.__name__+'.'+name,Path(package.__path__[0])/(name+'.py'))
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module);return module
files=load('file_snapshots');load('version_records');executor=load('rebuild_executor')
rebuild=load('rebuild')
instances=load('instances')

class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=self.root/'store';self.source=self.root/'source';self.source.mkdir()
        registry_patch=patch.object(instances,'REGISTRY_PATH',self.root/'instances.json')
        registry_patch.start();self.addCleanup(registry_patch.stop)
        (self.source/'main.py').write_text('original')
        (self.source/'custom_nodes').mkdir()
        self.snapshot=files.capture(self.source,self.store)
        self.target=self.root/'target';(self.target/'.nodeops-recovery').mkdir(parents=True)
        self.repo={'id':'0','name':'ComfyUI','core':True,'skip':None,'snapshot':self.snapshot,'target':'ComfyUI','action':'recorded'}
    def component(self,repo=None):
        return executor.component(repo or self.repo,self.target,self.store,lambda args: self.fail('Unexpected external command'))
    def test_restore_and_resume(self):
        self.assertFalse(self.component()['reused'])
        self.assertTrue(self.component()['reused'])
        self.assertEqual((self.target/'ComfyUI/main.py').read_text(),'original')

    def test_resume_rejects_changed_component_plan(self):
        self.component()
        (self.source/'main.py').write_text('another version')
        self.repo['snapshot']=files.capture(self.source,self.store)
        with self.assertRaisesRegex(ValueError,'计划'):self.component()
        self.assertEqual((self.target/'ComfyUI/main.py').read_text(),'original')

    def test_snapshot_restore_does_not_require_original_source(self):
        self.source.rename(self.root/'source-unavailable')
        self.assertEqual(self.component()['status'],'restored')
        self.assertEqual((self.target/'ComfyUI/main.py').read_text(),'original')

    def test_legacy_receipt_remains_explicitly_unbound(self):
        self.component();receipt=self.target/'.nodeops-recovery/component-0.json'
        saved=json.loads(receipt.read_text());saved.pop('source_identity');receipt.write_text(json.dumps(saved))
        self.assertEqual(self.component()['checkpoint_binding'],'legacy_unbound')

    def test_unplanned_changes_during_relocation_are_not_blessed(self):
        import importlib
        paths=importlib.import_module(package.__name__+'.recovery_paths')
        def tamper(stage,source_root):
            (stage/'main.py').write_text('modified');return []
        with patch.object(paths,'relocate',side_effect=tamper):
            with self.assertRaisesRegex(ValueError,'checksum'):self.component()
        self.assertFalse((self.target/'.nodeops-recovery/component-0.json').exists())

    def test_modified_target_after_publication_is_not_reported_restored(self):
        original=Path.rename
        def tamper(path,target):
            result=original(path,target)
            if path.name.startswith('stage-'):(target/'main.py').write_text('modified')
            return result
        with patch.object(Path,'rename',tamper):
            with self.assertRaisesRegex(ValueError,'变化'):self.component()
        with self.assertRaises(ValueError):self.component()

    def test_dependency_side_effects_fail_final_file_verification(self):
        plan={'repositories':[self.repo],'pins':[],'warnings':[],'recovery_state':'ready'}
        state={}
        def install(*args):(self.target/'ComfyUI/main.py').write_text('modified by dependency')
        with self.assertRaisesRegex(ValueError,'变化'):
            executor.execute(plan,self.target,self.store,lambda args:None,lambda **v:state.update(v),install)
        self.assertEqual(state['phases']['files_verified']['status'],'failed')
        self.assertFalse(instances.REGISTRY_PATH.exists())

    def test_dependency_cannot_replace_receipt_to_hide_changes(self):
        plan={'repositories':[self.repo],'pins':[],'warnings':[],'recovery_state':'ready'}
        def install(*args):
            receipt=self.target/'.nodeops-recovery/component-0.json'
            value=json.loads(receipt.read_text());value['status']='substituted';receipt.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'检查点'):
            executor.execute(plan,self.target,self.store,lambda args:None,lambda **v:None,install)
        self.assertFalse(instances.REGISTRY_PATH.exists())

    def test_fingerprint_does_not_accept_changed_saved_content(self):
        original=files.scan
        changed=False
        def tamper(source,*args,**kwargs):
            nonlocal changed
            if not changed and Path(source).name.startswith('stage-'):
                (Path(source)/'main.py').write_text('modified');changed=True
            return original(source,*args,**kwargs)
        with patch.object(files,'scan',side_effect=tamper):
            with self.assertRaisesRegex(ValueError,'checksum'):self.component()
        self.assertFalse((self.target/'.nodeops-recovery/component-0.json').exists())

    def test_snapshot_plan_does_not_read_running_instance(self):
        import importlib
        env=importlib.import_module(package.__name__+'.recovery_environment')
        record={'kind':'version_record','core':{'name':'ComfyUI','snapshot':self.snapshot},'plugins':[]}
        with patch.object(rebuild.records,'read',return_value=record), patch.object(rebuild.records,'directory',return_value=self.store), patch.object(rebuild.records,'inventory',side_effect=AssertionError('source must not be read')), patch.object(env,'build_plan',return_value=({},[],[],[])):
            plan=rebuild.plan('a'*32)
        self.assertTrue(plan['can_rebuild'])
        self.assertEqual(plan['repositories'][0]['object_verification']['status'],'passed')

    def test_cache_exclusion_is_not_missing_application_content(self):
        import importlib
        env=importlib.import_module(package.__name__+'.recovery_environment')
        self.snapshot['skipped']=[{'path':'.git','reason':'excluded cache/environment/assets'}]
        record={'kind':'version_record','core':{'name':'ComfyUI','snapshot':self.snapshot},'plugins':[]}
        with patch.object(rebuild.records,'read',return_value=record), patch.object(rebuild.records,'directory',return_value=self.store), patch.object(env,'build_plan',return_value=({},[],[],[])):
            self.assertEqual(rebuild.plan('a'*32)['recovery_state'],'ready')
            self.snapshot['skipped'].append({'path':'required.bin','reason':'larger than 256 MiB'})
            self.assertEqual(rebuild.plan('a'*32)['recovery_state'],'partial')

    def test_task_integrity_distinguishes_files_dependencies_startup_workflow(self):
        row={'status':'partial','phases':{'files_verified':{'status':'completed'},'dependencies':{'status':'completed'}}}
        state=rebuild.task_state(row)
        self.assertEqual(state['integrity']['files'],'passed')
        self.assertEqual(state['integrity']['dependencies'],'passed')
        self.assertEqual(state['integrity']['startup'],'not_checked')
        self.assertEqual(state['integrity']['workflow'],'not_checked')
        self.assertTrue(state['integrity']['has_gaps'])
        self.assertFalse(state['runtime_verified'])
        row['verification']={'checks':[{'name':'files','status':'failed'}]}
        self.assertEqual(rebuild.task_state(row)['integrity']['files'],'failed')

    def test_resume_rejects_permission_only_change(self):
        self.component()
        path = self.target / 'ComfyUI/main.py'
        mode = path.stat().st_mode
        try:
            path.chmod(mode & ~0o222)
            with self.assertRaisesRegex(ValueError, '权限'):
                self.component()
        finally:
            path.chmod(mode)
        self.assertTrue(self.component()['reused'])

    def test_verify_checks_directory_modes_and_missing_file_modes(self):
        self.component()
        folder = self.target / 'ComfyUI'
        snapshot = files.capture(folder, self.store)
        snapshot['directories'][0]['mode'] ^= 0o200
        with self.assertRaisesRegex(ValueError, '权限'):
            executor.verify(folder, snapshot, False, self.store)
        snapshot['directories'][0]['mode'] ^= 0o200
        snapshot['files'][0].pop('mode')
        with self.assertRaisesRegex(ValueError, '权限'):
            executor.verify(folder, snapshot, False, self.store)
    def test_model_paths_relocated_and_external_preserved(self):
        import yaml
        config={'local':{'base_path':str(self.source),'checkpoints':'models/checkpoints'},
                'external':{'base_path':str(self.root/'shared'),'loras':'loras'}}
        path=self.source/'extra_model_paths.yaml';path.write_text(yaml.safe_dump(config))
        self.repo.update(snapshot=files.capture(self.source,self.store),recorded_root=str(self.source))
        result=self.component()
        restored=yaml.safe_load((self.target/'ComfyUI/extra_model_paths.yaml').read_text())
        self.assertEqual(restored['local']['base_path'],'.')
        self.assertEqual(restored['external'],config['external'])
        self.assertEqual(yaml.safe_load(path.read_text()),config)
        self.assertEqual([n['kind'] for n in result['path_notes']],['external','relocated'])
        self.assertEqual(self.component()['path_notes'],result['path_notes'])
    def test_model_path_traversal_not_relocated(self):
        import yaml
        from _executor_tests.recovery_paths import relocate
        path=self.source/'extra_model_paths.yaml'
        config={'x':{'base_path':str(self.source/'..'/'outside'),'loras':'loras'}}
        path.write_text(yaml.safe_dump(config))
        self.assertEqual(relocate(self.source,str(self.source))[0]['kind'],'external')
        self.assertEqual(yaml.safe_load(path.read_text()),config)
    def test_invalid_model_configuration_not_published(self):
        (self.source/'extra_model_paths.yaml').write_text('- invalid')
        self.repo['snapshot']=files.capture(self.source,self.store)
        with self.assertRaises(ValueError):self.component()
        self.assertFalse((self.target/'ComfyUI').exists())
    def test_phase_failure_and_retry(self):
        plan={'repositories':[self.repo],'pins':[],'warnings':[],'recovery_state':'ready'}
        state={}
        def fail(*args):raise ValueError('dependency failure')
        with self.assertRaises(ValueError):
            executor.execute(plan,self.target,self.store,lambda args:None,lambda **v:state.update(v),fail)
        self.assertEqual(state['phases']['dependencies']['status'],'failed')
        executor.execute(plan,self.target,self.store,lambda args:None,lambda **v:state.update(v),lambda *a:None)
        self.assertEqual(state['phases']['dependencies']['attempt'],2)
        self.assertEqual(state['phases']['launcher']['status'],'completed')
        self.assertTrue(state['items'][0]['reused'])
    def test_launcher_atomic_failure_retry(self):
        with patch.object(executor.os,'link',side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):executor.launcher(self.target)
        self.assertFalse(list(self.target.glob('run_comfyui.*')))
        self.assertTrue(executor.launcher(self.target).exists())
    def test_completed_restore_registered_once(self):
        plan={'repositories':[self.repo],'pins':[],'warnings':[],'recovery_state':'ready'}
        state={}
        for _ in range(2):
            executor.execute(plan,self.target,self.store,lambda args:None,lambda **v:state.update(v),lambda *a:None)
        entries=json.loads(instances.REGISTRY_PATH.read_text())
        self.assertEqual(len(entries),1)
        self.assertEqual(entries[0]['id'],state['instance_id'])
        self.assertEqual(Path(entries[0]['comfyui_root']),self.target/'ComfyUI')
        self.assertEqual(state['status'],'rebuilt_unverified')
    def test_dependency_failure_not_registered(self):
        plan={'repositories':[self.repo],'pins':[],'warnings':[],'recovery_state':'ready'}
        def fail(*args):raise ValueError('failed')
        with self.assertRaises(ValueError):
            executor.execute(plan,self.target,self.store,lambda args:None,lambda **v:None,fail)
        self.assertFalse(instances.REGISTRY_PATH.exists())
    def test_registration_failure_can_retry(self):
        plan={'repositories':[self.repo],'pins':[],'warnings':[],'recovery_state':'ready'}
        state={}
        with patch.object(instances,'add_instance',side_effect=OSError('registry unavailable')):
            with self.assertRaises(OSError):
                executor.execute(plan,self.target,self.store,lambda args:None,lambda **v:state.update(v),lambda *a:None)
        self.assertEqual(state['phases']['registration']['status'],'failed')
        executor.execute(plan,self.target,self.store,lambda args:None,lambda **v:state.update(v),lambda *a:None)
        self.assertTrue(state['instance_id'])
    def single_repo(self):
        path=self.root/'single.py';path.write_text('original')
        return {**self.repo,'id':'1','name':'single.py','core':False,'target':'ComfyUI/custom_nodes/single.py','snapshot':files.capture(path,self.store)}
    def test_single_file_restore_resume_and_changes(self):
        repo=self.single_repo();self.component(repo)
        self.assertTrue(self.component(repo)['reused'])
        path=self.target/repo['target'];self.assertEqual(path.read_text(),'original')
        path.write_text('changed')
        with self.assertRaises(ValueError):self.component(repo)
        self.assertEqual(path.read_text(),'changed')
    def test_single_file_interrupted_publication(self):
        repo=self.single_repo()
        with patch.object(Path,'rename',side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):self.component(repo)
        self.assertTrue(self.component(repo)['reused'])
        self.assertEqual((self.target/repo['target']).read_text(),'original')
    def test_changed_file_refuses_overwrite(self):
        self.component();(self.target/'ComfyUI/main.py').write_text('user edit')
        with self.assertRaises(ValueError):self.component()
        self.assertEqual((self.target/'ComfyUI/main.py').read_text(),'user edit')
    def test_added_plugin_does_not_invalidate_core(self):
        self.component();(self.target/'ComfyUI/custom_nodes/plug').mkdir(parents=True)
        (self.target/'ComfyUI/custom_nodes/plug/a.py').write_text('plugin')
        self.assertTrue(self.component()['reused'])
    def test_selected_user_data_verified_after_restore(self):
        (self.source/'user').mkdir();(self.source/'user/settings.json').write_text('{}')
        self.repo['snapshot']=files.capture(self.source,self.store,core=True,core_dirs=['user'])
        self.component();self.assertTrue(self.component()['reused'])
        (self.target/'ComfyUI/user/settings.json').write_text('changed')
        with self.assertRaises(ValueError):self.component()
    def test_publish_interruption(self):
        original=Path.rename
        def fail_stage(path,target):
            if path.name.startswith('stage-'):raise OSError('interrupted before rename')
            return original(path,target)
        with patch.object(Path,'rename',fail_stage):
            with self.assertRaises(OSError):self.component()
        self.assertFalse((self.target/'ComfyUI').exists())
        self.assertTrue(self.component()['reused'])
    def test_unknown_existing_folder(self):
        (self.target/'ComfyUI').mkdir()
        with self.assertRaises(ValueError):self.component()
    def test_corrupt_object_never_published(self):
        files.object_path(self.store,self.snapshot['files'][0]['sha256']).write_bytes(b'bad')
        with self.assertRaises(ValueError):self.component()
        self.assertFalse((self.target/'ComfyUI').exists())
    def test_core_requires_main(self):
        self.repo['snapshot']={'format':2,'files':[],'directories':[]}
        with self.assertRaises(ValueError):self.component()
    def test_skip_distinct_from_failure(self):
        self.repo.update(core=False,skip='user choice')
        self.assertEqual(self.component()['status'],'skipped')
    def test_substitution_recorded(self):
        self.repo.update(substitution=True,source=str(self.source),source_scan=files.scan(self.source,self.store,core=True,large_files='include'))
        self.assertEqual(self.component()['status'],'substituted')
    def test_missing_completed_component_is_not_silently_recreated(self):
        self.component();(self.target/'ComfyUI').rename(self.target/'moved')
        with self.assertRaises(ValueError):self.component()
    def test_stage_traversal_rejected(self):
        self.component();receipt=self.target/'.nodeops-recovery/component-0.json'
        data=json.loads(receipt.read_text());data['stage']='../outside';receipt.write_text(json.dumps(data))
        with self.assertRaises(ValueError):self.component()
    def test_launcher_relative_and_cpu(self):
        path=executor.launcher(self.target,True);text=path.read_text()
        self.assertNotIn(str(self.target),text);self.assertIn('python_env',text);self.assertIn('--cpu',text)
        self.assertEqual(executor.launcher(self.target,True),path)
    def test_launcher_preserves_user_edits(self):
        path=executor.launcher(self.target);path.write_text('edited')
        with self.assertRaises(ValueError):executor.launcher(self.target)
    def test_plugin_failure_continues_and_retries(self):
        bad=dict(self.repo,id='1',name='bad',core=False,target='ComfyUI/custom_nodes/bad',snapshot={'format':2,'files':[{'path':'x','sha256':'f'*64,'size':1}],'directories':[]})
        good=dict(self.repo,id='2',name='good',core=False,target='ComfyUI/custom_nodes/good')
        plan={'repositories':[self.repo,bad,good],'pins':[],'warnings':[],'recovery_state':'ready'}
        state={};save=lambda **values:state.update(values)
        executor.execute(plan,self.target,self.store,lambda args:None,save,lambda *args:None)
        self.assertEqual([i['status'] for i in state['items']],['restored','failed','restored'])
        self.assertEqual(state['status'],'partial')
        bad['snapshot']=self.snapshot
        executor.execute(plan,self.target,self.store,lambda args:None,save,lambda *args:None)
        self.assertEqual([i['status'] for i in state['items']],['restored','restored','restored'])
        self.assertTrue(state['items'][0]['reused']);self.assertTrue(state['items'][2]['reused'])
    def test_dependency_failure_preserves_components(self):
        def fail(*args):raise ValueError('offline')
        with self.assertRaises(ValueError):executor.execute({'repositories':[self.repo],'pins':[]},self.target,self.store,lambda args:None,lambda **kw:None,fail)
        self.assertTrue(self.component()['reused'])

    def test_resume_requires_confirmation(self):
        with self.assertRaises(ValueError):rebuild.resume('a'*32)

    def test_resume_restart_uses_persisted_plan(self):
        import platform
        job='b'*32
        prepared={'repositories':[self.repo], 'pins':[], 'warnings':[], 'recovery_state':'ready'}
        self.store.mkdir(exist_ok=True);(self.store/'rebuild-jobs').mkdir()
        row={'id':job, 'executor_version':1,'status':'running','plan_id':'plan','target':str(self.target),'storage':str(self.store.resolve()),'interpreter':[sys.executable,platform.python_version(),platform.machine()],'prepared':prepared}
        executor.atomic_json(self.store/'rebuild-jobs'/(job+'.json'),row)
        executor.atomic_json(self.target/'.nodeops-recovery/owner.json',{'job_id':job,'plan_id':'plan'})
        calls=[]
        with patch.object(rebuild.records,'directory',return_value=self.store),patch.object(rebuild,'validate_target',return_value=self.target),patch.object(rebuild,'launch',side_effect=lambda *args:calls.append(args)):
            try:rebuild.resume(job,True)
            finally:
                if rebuild.LOCK.locked():rebuild.LOCK.release()
        self.assertEqual(calls[0][0],prepared)
        self.assertNotIn('prepared',rebuild.status())

    def test_legacy_job_cannot_resume(self):
        job='c'*32;(self.store/'rebuild-jobs').mkdir(parents=True)
        executor.atomic_json(self.store/'rebuild-jobs'/(job+'.json'),{'id':job,'status':'failed'})
        with patch.object(rebuild.records,'directory',return_value=self.store):
            with self.assertRaises(ValueError):rebuild.resume(job,True)
        self.assertFalse(rebuild.LOCK.locked())

    def test_partial_skips_do_not_offer_retry(self):
        self.assertFalse(rebuild.task_state({'status':'partial','executor_version':1,'items':[{'status':'skipped'}]})['can_resume'])
        self.assertTrue(rebuild.task_state({'status':'partial','executor_version':1,'items':[{'status':'failed'}]})['can_resume'])

if __name__=='__main__':unittest.main()
