"""Run Phase 3 isolated mutations plus the existing Phase 1/2 regression suite."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--tests-root', type=Path, default=Path(__file__).resolve().parent)
parser.add_argument('--output', type=Path, default=Path(__file__).parents[1] / 'phase3-evidence' / 'tests.json')
args = parser.parse_args()
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
args.output.parent.mkdir(parents=True, exist_ok=True)
env = {**os.environ, 'NODEOPS_TEST_EVIDENCE': str(args.output.parent), 'PYTHONIOENCODING': 'utf-8'}
results = []
for pattern in ('test_phase3_restore.py', 'test_phase3_routes.py', 'test_phase3_e2e.py'):
    command = [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', str(args.tests_root), '-p', pattern]
    run = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=360, env=env)
    output = run.stdout + run.stderr
    print(pattern + '\n' + output, flush=True)
    count = re.search(r'Ran (\d+) tests?', output)
    results.append({'group': pattern, 'exit_code': run.returncode, 'tests': int(count[1]) if count else 0, 'output': output})
regressions = args.output.parent / 'regressions.json'
run = subprocess.run([sys.executable, '-B', str(args.tests_root / 'run_phase2_acceptance.py'),
    '--tests-root', str(args.tests_root), '--output', str(regressions)], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=360, env=env)
print(run.stdout + run.stderr, flush=True)
regression_report = json.loads(regressions.read_text(encoding='utf-8'))
report = {'passed': all(r['exit_code'] == 0 for r in results) and run.returncode == 0 and regression_report['passed'],
    'phase3_tests': sum(r['tests'] for r in results), 'regression_tests': regression_report['python_tests'],
    'python_tests': sum(r['tests'] for r in results) + regression_report['python_tests'],
    'ui_groups': regression_report['ui_groups'], 'tests_root': str(args.tests_root), 'groups': results}
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in report.items() if k != 'groups'}), flush=True)
sys.exit(not report['passed'])
