import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

package=types.ModuleType('_conflict_scope_tests')
package.__path__=[str(Path(__file__).parents[1]/'qbb_nodeops')]
sys.modules[package.__name__]=package
conflicts=importlib.import_module(package.__name__+'.conflicts')
migration=importlib.import_module(package.__name__+'.migration')


class ConflictScopeTests(unittest.TestCase):
    def test_single_file_conflicts_with_directory_and_single_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            single = root / 'node.py'
            single.write_text('raise RuntimeError("must not execute")\nNODE_CLASS_MAPPINGS = {"Shared": Example}\n', encoding='utf-8')
            other = root / 'other.py'
            other.write_text('NODE_CLASS_MAPPINGS: dict = {"Shared": Other}\n', encoding='utf-8')
            directory = root / 'directory'
            directory.mkdir()
            (directory / '__init__.py').write_text('NODE_CLASS_MAPPINGS = {"Shared": Example}\n', encoding='utf-8')
            incoming = conflicts.inspect_plugin(single)
            self.assertEqual(incoming['nodes'], {'Shared': f'{single}:2'})
            for target in (other, directory):
                with self.subTest(target=target.name):
                    findings = conflicts.compare([incoming, conflicts.inspect_plugin(target)], {str(single)})
                    self.assertEqual(len(findings), 1)
                    self.assertEqual(findings[0]['kind'], 'duplicate_node')
                    self.assertEqual(findings[0]['message'], 'Shared')
                    self.assertIn(f'{single}:2', findings[0]['evidence'])

    def test_single_file_unknown_and_distinct_nodes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            a, b = root / 'a.py', root / 'b.py'
            a.write_text('NODE_CLASS_MAPPINGS = {"A": Example}\n', encoding='utf-8')
            b.write_text('NODE_CLASS_MAPPINGS = {"B": Example}\n', encoding='utf-8')
            self.assertEqual(conflicts.compare([conflicts.inspect_plugin(a), conflicts.inspect_plugin(b)]), [])
            b.write_text('NODE_CLASS_MAPPINGS = build_nodes()\n', encoding='utf-8')
            self.assertTrue(any('dynamic NODE_CLASS_MAPPINGS' in value for value in conflicts.inspect_plugin(b)['unknown']))
            b.write_text('invalid python syntax !\n', encoding='utf-8')
            self.assertTrue(any('could not statically parse' in value for value in conflicts.inspect_plugin(b)['unknown']))

    def test_preflight_detects_incoming_single_file_node_collision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, target = root / 'source', root / 'target'
            for base in (source, target):
                (base / 'custom_nodes').mkdir(parents=True)
            incoming = source / 'custom_nodes' / 'incoming.py'
            existing = target / 'custom_nodes' / 'existing.py'
            for file in (incoming, existing):
                file.write_text('NODE_CLASS_MAPPINGS = {"Shared": Example}\n', encoding='utf-8')
            with patch.object(conflicts, 'get_instance', side_effect=lambda key: {'comfyui_root': str(source if key == 'current' else target)}):
                report = conflicts.preflight({'mode': 'instance', 'target_instance_id': 'target', 'plugins': [str(incoming)]})
            duplicates = [item for item in report['findings'] if item['kind'] == 'duplicate_node']
            self.assertEqual(len(duplicates), 1)
            self.assertEqual(duplicates[0]['plugins'], ['incoming.py', 'existing.py'])

    def test_preflight_scopes_existing_conflicts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';target=root/'target'
            source.mkdir();target.mkdir()
            def plugin(base,name,requirement):
                path=base/'custom_nodes'/name;path.mkdir(parents=True)
                (path/'requirements.txt').write_text(requirement)
                return path
            incoming=plugin(source,'new','unrelated==1')
            old1=plugin(target,'old1','shared==1')
            old2=plugin(target,'old2','shared==2')
            def instance(key):
                return {'comfyui_root':str(source if key=='current' else target)}
            with patch.object(conflicts,'get_instance',side_effect=instance),patch.object(migration,'_resolve_selected',return_value=[types.SimpleNamespace(path=str(incoming))]),patch.object(migration,'scan_plugins',return_value={'plugins':[{'path':str(old1)},{'path':str(old2)}]}):
                payload={'mode':'instance','target_instance_id':'target','plugins':[str(incoming)]}
                report=conflicts.preflight(payload)
                self.assertEqual(report['confirmed'],0)
                self.assertEqual(report['findings'],[])
                self.assertEqual(len(report['existing_findings']),1)
                (incoming/'requirements.txt').write_text('shared==3')
                report=conflicts.preflight(payload)
                self.assertEqual(report['confirmed'],2)
                self.assertTrue(all('new' in f['plugins'] for f in report['findings']))
                (target/'custom_nodes'/'new').mkdir()
                self.assertEqual(conflicts.preflight(payload)['confirmed'],3)

    def test_selected_plugins_still_conflict_with_each_other(self):
        from packaging.requirements import Requirement
        groups=[{'name':name,'path':name,'requirements':[(Requirement('shared=='+version),'test')],'nodes':{}}
                for name,version in [('new1','1'),('new2','2')]]
        self.assertEqual(len(conflicts.compare(groups,{'new1','new2'})),1)


if __name__=='__main__':unittest.main()
