"""Read a real instance; create evidence only in an explicitly separate store."""
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
parser.add_argument('--output', required=True, type=Path)
parser.add_argument('--capture', action='store_true')
args = parser.parse_args()
core, output = args.core.resolve(), args.output.resolve()
if output.is_relative_to(core):
    raise SystemExit('Evidence must be outside the scanned instance')
package = types.ModuleType('_phase1_evidence')
package.__path__ = [str(Path(__file__).parents[1] / 'qbb_nodeops')]
sys.modules[package.__name__] = package
state = importlib.import_module(package.__name__ + '.snapshot_state')
records = state.records
scan = state.scan_environment
store = output / 'store'

def git(path, *arguments):
    # Trust only this exact read target for this command, never global Git settings.
    result = subprocess.run([records.require_git(), '-c', 'safe.directory=' + str(Path(path).resolve()).replace('\\', '/'),
                             '-c', 'core.hooksPath=', '-C', str(path), *arguments],
                            capture_output=True, text=True, errors='replace', timeout=30,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('Git metadata unavailable')
    return result.stdout.strip()

def actual_scan(label=''):
    return scan(core, [core / 'custom_nodes'], core / 'custom_nodes' / 'ComfyUI_QBB_NodeOps',
                store=store, label=label)

output.mkdir(parents=True, exist_ok=True)
with patch.object(state, 'scan_environment', side_effect=actual_scan), patch.object(records, 'directory', return_value=store), patch.object(records, 'git', side_effect=git):
    record, captures = actual_scan('Real Phase 1 evidence')
    result = {'provenance': {'core': str(core), 'interpreter': sys.executable,
              'plugin_roots': [str(core / 'custom_nodes')],
              'scope_note': 'Explicit standard custom_nodes root; dynamic extra roots are not loaded. No network or installs.'},
              'resource_count': len(record['resources']),
              'kinds': dict(Counter(r['kind'] for r in record['resources'])),
              'statuses': dict(Counter(r['recoverability']['status'] for r in record['resources'])),
              'reasons': dict(Counter(r['recoverability']['reason'] for r in record['resources'] if r['recoverability']['reason'])),
              'space': state.files.storage_plan([s for _, s in captures.values()], store), 'issues': record['issues']}
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if args.capture:
        results = []
        for index in range(2):
            print('Capturing real snapshot ' + str(index + 1), flush=True)
            preview = records.preview()
            saved = records.create('Real Phase 1 evidence ' + str(index + 1), preview_id=preview['preview_id'])
            results.append({'id': saved['id'], 'statistics': saved['statistics']})
        result['snapshots'] = results
        final = records.read(results[-1]['id'])
        excerpt = {k: v for k, v in final.items() if k not in ('resources', 'artifacts')}
        selected = []
        seen = set()
        for row in final['resources']:
            group = (row['kind'], row['source']['kind'], row['recoverability']['status'], row['recoverability']['reason'])
            if group not in seen:
                seen.add(group)
                selected.append({k: v for k, v in row.items() if k not in ('files', 'directories')})
        excerpt['resources'] = selected
        excerpt['excerpt_note'] = 'Actual rows selected once per classification; file lists, repeated resources and artifacts omitted. Full snapshots are in store.'
        (output / 'snapshot-example.json').write_text(json.dumps(excerpt, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'scan-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
