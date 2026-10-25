"""Local Git fixtures only: no network, production repositories or installs."""
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

package = types.ModuleType('_submodule_tests')
package.__path__ = [str(Path(__file__).parents[1]/'qbb_nodeops')]
sys.modules[package.__name__] = package
records = importlib.import_module(package.__name__+'.version_records')
files = importlib.import_module(package.__name__+'.file_snapshots')
executor = importlib.import_module(package.__name__+'.rebuild_executor')
manifest = importlib.import_module(package.__name__+'.manifest')


@unittest.skipUnless(shutil.which('git'), 'Git required')
class SubmoduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = os.environ.copy()
        for key in list(self.env):
            if key.startswith('GIT_'):
                self.env.pop(key)
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        self.store = self.root/'store'
        self.target = self.root/'target'
        (self.target/'.nodeops-recovery').mkdir(parents=True)
        self.core = self.root/'core'; self.core.mkdir()
        (self.core/'main.py').write_text('pass')
        self.leaf = self.init('leaf')
        self.middle = self.init('middle')
        self.git(self.middle, 'submodule', 'add', str(self.leaf), 'nested child')
        self.git(self.middle, 'commit', '-am', 'nested')
        self.source = self.init('source')
        self.git(self.source, 'submodule', 'add', str(self.middle), 'dependency')
        self.git(self.source, 'commit', '-am', 'dependency')

    def command(self, args):
        return subprocess.run([str(a) for a in args], env=self.env, check=True,
                              capture_output=True, text=True, encoding='utf-8', timeout=30).stdout.strip()

    def git(self, path, *args):
        return self.command(['git', '-c', 'core.hooksPath=', '-c', 'protocol.file.allow=always',
                             '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', '-C', path, *args])

    def init(self, name):
        path=self.root/name;path.mkdir()
        self.git(path, 'init')
        (path/'required.py').write_text(name)
        self.git(path, 'add', '.')
        self.git(path, 'commit', '-m', 'initial')
        return path

    def initialize_nested(self):
        self.git(self.source, 'submodule', 'update', '--init', '--recursive')

    def inventory(self, label=''):
        row=records.repository(self.source)
        row['url']='https://github.com/example/test'
        return {'schema':3,'kind':'version_record','id':'a'*32,'created':1,'label':label,
                'core':{'name':'ComfyUI','kind':'local','path':str(self.core)},
                'plugins':[row],'packages':[],'python':{'version':'3.11'}}

    def git_repo(self):
        return {'id':'1','name':'plugin','core':False,'skip':None,'snapshot':None,
                'target':'plugin','url':str(self.source), 'commit':self.git(self.source,'rev-parse','HEAD')}

    def test_clean_nested_submodules_snapshot_restore_and_resume(self):
        self.initialize_nested()
        with patch.object(records,'inventory',side_effect=self.inventory), patch.object(records,'directory',return_value=self.store):
            preview=records.preview()
            self.assertTrue(preview['can_save'])
            self.assertEqual(preview['items'][1]['method'],'snapshot')
            saved=records.read(records.create('',None,preview['preview_id'])['id'])
        row=saved['plugins'][0]
        self.assertFalse(row['dirty'])
        self.assertTrue(row['has_submodules'])
        repo={**self.git_repo(),'snapshot':row['snapshot']}
        result=executor.component(repo,self.target,self.store,self.command)
        self.assertEqual(result['status'],'restored')
        self.assertEqual((self.target/'plugin/dependency/required.py').read_text(),'middle')
        self.assertEqual((self.target/'plugin/dependency/nested child/required.py').read_text(),'leaf')
        self.assertTrue(executor.component(repo,self.target,self.store,self.command)['reused'])

    def test_uninitialized_nested_submodule_blocks_both_capture_modes(self):
        for mode in ('auto','offline'):
            with self.subTest(mode=mode), patch.object(records,'inventory',side_effect=self.inventory), patch.object(records,'directory',return_value=self.store):
                options={'mode':mode};preview=records.preview(options)
                self.assertFalse(preview['can_save'])
                self.assertIn('子模块',str(preview['errors']))
                with self.assertRaises(ValueError):
                    records.create('',options,preview['preview_id'])
        self.assertFalse(list(self.store.glob('*.json')))

    def test_legacy_git_record_never_reports_missing_submodule_as_restored(self):
        self.initialize_nested()
        with self.assertRaisesRegex(ValueError,'子模块'):
            executor.component(self.git_repo(),self.target,self.store,self.command)
        self.assertFalse((self.target/'plugin').exists())
        self.assertFalse((self.target/'.nodeops-recovery/component-1.json').exists())

    def test_old_incomplete_receipt_is_rejected_on_resume(self):
        self.initialize_nested()
        repo=self.git_repo()
        # Simulate the pre-fix receipt which fingerprinted empty submodule folders.
        with patch.object(executor,'check_git_submodules'):
            executor.component(repo,self.target,self.store,self.command)
        with self.assertRaisesRegex(ValueError,'子模块'):
            executor.component(repo,self.target,self.store,self.command)

    def test_manifest_requires_snapshot_when_submodules_are_known(self):
        row={'kind':'git','dirty':False,'commit':'a'*40,'url':'https://github.com/example/test','has_submodules':True}
        self.assertIn('子模块',manifest.repository_gap(row))

    def test_plain_git_record_still_restores(self):
        repo={**self.git_repo(),'url':str(self.leaf),'commit':self.git(self.leaf,'rev-parse','HEAD')}
        self.assertEqual(executor.component(repo,self.target,self.store,self.command)['status'],'restored')
        self.assertEqual((self.target/'plugin/required.py').read_text(),'leaf')


if __name__ == '__main__':
    unittest.main()
