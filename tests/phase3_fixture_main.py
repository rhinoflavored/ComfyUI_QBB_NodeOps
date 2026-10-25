"""Copied to a TEMPORARY fake ComfyUI main.py by test_phase3_e2e. No GPU/server."""
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sys
import types
from unittest.mock import patch

core = Path(__file__).resolve().parent
fixture = core.parent
if not (fixture / 'ISOLATED_NODEOPS_TEST').is_file() or not Path(sys.prefix).resolve().is_relative_to(fixture):
    raise SystemExit('Fixture refuses non-isolated interpreter')
manager = core / 'custom_nodes' / 'NodeOps'
package = types.ModuleType('_fixture_nodeops')
package.__path__ = [str(manager / 'qbb_nodeops')]
sys.modules[package.__name__] = package
restore = importlib.import_module(package.__name__ + '.current_restore')
state, records, diff = restore.state, restore.records, restore.diff
runtime = {'core': str(core), 'manager': str(manager), 'plugin_roots': [str(core / 'custom_nodes')],
    'store': str(fixture / 'store'), 'python': sys.executable, 'cwd': str(core),
    'argv': [sys.executable, '-B', str(core / 'main.py')], 'parent_pid': os.getpid(),
    'parent_birth': restore.restore_process.identity(os.getpid())}
scenario = json.loads((fixture / 'scenario.json').read_text())

if restore.status(manager=manager)['status'] != 'idle':
    result = restore.reconnect(runtime=runtime, service_ready=True)
    (fixture / 'result.json').write_text(json.dumps(result), encoding='utf-8')
    with patch.object(records, 'directory', return_value=fixture / 'store'):
        current, captures = state.scan_environment(core, runtime['plugin_roots'], manager, store=fixture / 'store', read_only=True)
        for row in current['resources']:
            if row['id'] in captures:
                row['content'] = state.tree_evidence(captures[row['id']][1])
        target = records.read(result['record_id'])
        (fixture / 'independent-diff.json').write_text(json.dumps(diff.compare(current, target)), encoding='utf-8')
    expected_status = 'failed_rolled_back' if scenario.get('rollback') else 'completed'
    raise SystemExit(0 if result['status'] == expected_status else 1)

original_scan = state.scan_environment
def scan(label='', read_only=False, **kwargs):
    return original_scan(core, runtime['plugin_roots'], manager, store=fixture / 'store', label=label, read_only=read_only)

with patch.object(records, 'directory', return_value=fixture / 'store'), patch.object(state, 'scan_environment', side_effect=scan):
    preview = records.preview()
    target = records.read(records.create('isolated Phase 3 end-to-end', preview_id=preview['preview_id'])['id'])
    (fixture / 'target-summary.json').write_text(json.dumps(target['statistics']), encoding='utf-8')
    # Saved original wheel objects must work even after original external paths disappear.
    (fixture / 'wheels' / ('demo-' + scenario['target'] + '-py3-none-any.whl')).unlink()
    (fixture / 'wheels' / 'missing-1.0-py3-none-any.whl').unlink()
    local = core / 'custom_nodes' / 'Local'
    (local / '__init__.py').write_text('current = 2')
    shutil.rmtree(core / 'custom_nodes' / 'Missing')
    extra = core / 'custom_nodes' / 'Extra'
    extra.mkdir()
    (extra / '__init__.py').write_text('extra = True')
    (core / 'extra_model_paths.yaml').write_text('current: state')
    # Upgrade one package, remove one and add another using real pip in the fixture venv.
    wheels = fixture / 'wheels'
    restore.material.run([sys.executable, '-B', '-m', 'pip', '--isolated', 'install', '--no-index', '--no-deps',
        '--force-reinstall', str(wheels / 'current' / ('demo-' + scenario['current'] + '-py3-none-any.whl')),
        str(wheels / 'extra-1.0-py3-none-any.whl')])
    restore.material.run([sys.executable, '-B', '-m', 'pip', '--isolated', 'uninstall', '--yes', 'missing'])
    importlib.invalidate_caches()
    plan = diff.inspect(target['id'])
    (fixture / 'plan.json').write_text(json.dumps(plan), encoding='utf-8')
    prepared = restore.prepare(target['id'], plan['fingerprint'], True, runtime=runtime)
    (fixture / 'prepared.json').write_text(json.dumps(prepared), encoding='utf-8')
    # Exercise the real spawn/handshake path; only the fake queue/context are substituted.
    original_popen = restore.subprocess.Popen
    def launch_worker(command, **kwargs):
        # Keep the real worker/handshake but avoid an interactive console in tests.
        command = [arg for arg in command if arg != '--manual-lifecycle']
        kwargs.pop('creationflags', None)
        if os.name == 'nt':
            kwargs['creationflags'] = restore.subprocess.CREATE_NO_WINDOW
        return original_popen(command, **kwargs)
    with patch.object(restore, 'idle'), patch.object(restore, 'context', return_value=runtime), patch.object(restore.subprocess, 'Popen', side_effect=launch_worker):
        started = restore.start(prepared['id'], prepared['plan_hash'], True)
    (fixture / 'started.json').write_text(json.dumps(started), encoding='utf-8')
# Normal process exit. The worker must observe this before touching the fixture.
