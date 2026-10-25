"""Read real state and target objects; write only the requested evidence output."""
import argparse
from collections import Counter
import importlib
import json
from pathlib import Path
import subprocess
import sys
import types
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument('--core', required=True, type=Path)
parser.add_argument('--store', required=True, type=Path)
parser.add_argument('--record-id', required=True)
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
core, store, output = args.core.resolve(), args.store.resolve(), args.output.resolve()
if output.is_relative_to(core) or output.is_relative_to(store):
    raise SystemExit('Output must be outside the real instance and target store')
package = types.ModuleType('_phase2_evidence')
package.__path__ = [str(Path(__file__).parents[1] / 'qbb_nodeops')]
sys.modules[package.__name__] = package
diff = importlib.import_module(package.__name__ + '.state_diff')
state, records = diff.state, diff.records
original_scan = state.scan_environment

def git(path, *arguments):
    run = subprocess.run([records.require_git(), '--no-optional-locks', '-c', 'core.fsmonitor=false',
                          '-c', 'safe.directory=' + str(Path(path).resolve()).replace('\\', '/'),
                          '-c', 'core.hooksPath=', '-C', str(path), *arguments],
                         capture_output=True, text=True, errors='replace', timeout=30,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if run.returncode:
        raise ValueError('Git metadata unavailable')
    return run.stdout.strip()

def scan(label='', read_only=False):
    print('Read-only scan of actual instance', flush=True)
    return original_scan(core, [core / 'custom_nodes'], core / 'custom_nodes' / 'ComfyUI_QBB_NodeOps',
                         store=store, label=label, read_only=read_only)

with patch.object(state, 'scan_environment', side_effect=scan), patch.object(records, 'directory', return_value=store), patch.object(records, 'git', side_effect=git):
    plan = diff.inspect(args.record_id)
summary = {'provenance': {'core': str(core), 'interpreter': sys.executable, 'target_store': str(store),
            'target_id': args.record_id, 'plugin_roots': [str(core / 'custom_nodes')],
            'scope_note': 'Explicit standard custom_nodes root. No dynamic extra roots, network, downloads, installs or restore.'},
           'status': plan['status'], 'execution_allowed': plan['execution_allowed'], 'fingerprint': plan['fingerprint'],
           'summary': plan['summary'], 'action_count': len(plan['actions']), 'blocker_count': len(plan['blockers']),
           'blocker_reasons': dict(Counter(b['reason'] for b in plan['blockers'])),
           'artifact_health': dict(Counter(h['status'] for h in plan['artifact_health'].values())),
           'diff_state': plan['diff']['state'], 'change_count': plan['diff']['change_count']}
output.mkdir(parents=True, exist_ok=True)
(output / 'actual-plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
(output / 'actual-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
