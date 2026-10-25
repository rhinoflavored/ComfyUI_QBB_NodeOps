"""Phase 2 + Phase 1/legacy regressions, isolated subprocess fixtures only."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--tests-root', type=Path, default=Path(__file__).resolve().parent)
parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'phase2-evidence' / 'tests.json')
args = parser.parse_args()
groups = ['phase2_diff', 'phase2_routes', 'phase1_snapshot', 'record_capture', 'capture_error_policy',
          'checkpoint_policy', 'version_lifecycle', 'manifest', 'file_snapshots', 'record_archives',
          'record_compare', 'record_history_routes', 'archive_routes']
results = []
for group in groups + ['state_plan_ui', 'snapshot_ui', 'record_archives_ui', 'compare_ui', 'history_ui']:
    ui = group.endswith('_ui')
    command = ['node', str(args.tests_root / ('test_' + group + '.cjs'))] if ui else [
        sys.executable, '-B', '-m', 'unittest', 'discover', '-s', str(args.tests_root), '-p', 'test_' + group + '.py']
    print('RUN ' + group, flush=True)
    run = subprocess.run(command, capture_output=True, text=True, errors='replace', timeout=360)
    output = run.stdout + run.stderr
    print(output, flush=True)
    match = re.search(r'Ran (\d+) tests?', output)
    results.append({'group': group, 'exit_code': run.returncode, 'tests': int(match[1]) if match else None, 'output': output})
report = {'tests_root': str(args.tests_root), 'python_tests': sum(r['tests'] or 0 for r in results),
          'ui_groups': sum(r['group'].endswith('_ui') for r in results),
          'passed': all(r['exit_code'] == 0 for r in results), 'groups': results}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in report.items() if k != 'groups'}), flush=True)
sys.exit(not report['passed'])
