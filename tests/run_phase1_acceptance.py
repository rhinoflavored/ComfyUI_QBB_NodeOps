"""Isolated Phase 1 regression suite; no live restore or package operations."""
import json
from pathlib import Path
import re
import subprocess
import sys

root = Path(__file__).resolve().parent
groups = ['phase1_snapshot', 'record_capture', 'capture_error_policy', 'checkpoint_policy',
          'version_lifecycle', 'manifest', 'file_snapshots', 'record_archives',
          'record_compare', 'record_history_routes', 'archive_routes']
results = []
for group in groups + ['snapshot_ui', 'record_archives_ui']:
    ui = group.endswith('_ui')
    command = ['node', str(root / ('test_' + group + '.cjs'))] if ui else [
        sys.executable, '-B', '-m', 'unittest', 'discover', '-s', str(root), '-p', 'test_' + group + '.py']
    print('RUN ' + group, flush=True)
    run = subprocess.run(command, capture_output=True, text=True, errors='replace', timeout=360)
    output = run.stdout + run.stderr
    print(output, flush=True)
    match = re.search(r'Ran (\d+) tests?', output)
    results.append({'group': group, 'exit_code': run.returncode, 'tests': int(match[1]) if match else None,
                    'output': output})
report = {'python_tests': sum(r['tests'] or 0 for r in results),
          'ui_groups': sum(r['group'].endswith('_ui') for r in results),
          'passed': all(r['exit_code'] == 0 for r in results), 'groups': results}
destination = root.parent / 'phase1-evidence' / 'tests.json'
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in report.items() if k != 'groups'}), flush=True)
sys.exit(not report['passed'])
