"""Phase 4 persistent tasks/UI plus all Phase 1–3 isolated acceptance checks."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--tests-root', type=Path, default=Path(__file__).resolve().parent)
parser.add_argument('--output', type=Path, default=Path(__file__).parents[1] / 'phase4-evidence' / 'tests.json')
args = parser.parse_args()
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
args.output.parent.mkdir(parents=True, exist_ok=True)
env = {**os.environ, 'NODEOPS_TEST_EVIDENCE': str(args.output.parent), 'PYTHONIOENCODING': 'utf-8'}
groups = []
commands = [
    ('phase4_tasks', [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', str(args.tests_root), '-p', 'test_phase4_tasks.py']),
    ('phase4_ui', ['node', str(args.tests_root / 'test_phase4_ui.cjs')]),
]
for group, command in commands:
    run = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120, env=env)
    output = run.stdout + run.stderr
    print(group + '\n' + output, flush=True)
    count = re.search(r'Ran (\d+) tests?', output)
    groups.append({'group': group, 'exit_code': run.returncode, 'tests': int(count[1]) if count else 0, 'output': output})
regressions = args.output.parent / (args.output.stem + '-phase3.json')
run = subprocess.run([sys.executable, '-B', str(args.tests_root / 'run_phase3_acceptance.py'),
    '--tests-root', str(args.tests_root), '--output', str(regressions)], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600, env=env)
print(run.stdout + run.stderr, flush=True)
previous = json.loads(regressions.read_text(encoding='utf-8'))
report = {'passed': all(g['exit_code'] == 0 for g in groups) and run.returncode == 0 and previous['passed'],
    'phase4_tests': sum(g['tests'] for g in groups), 'python_tests': previous['python_tests'] + sum(g['tests'] for g in groups),
    'ui_groups': previous['ui_groups'] + 1, 'tests_root': str(args.tests_root), 'regressions': str(regressions), 'groups': groups}
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in report.items() if k != 'groups'}), flush=True)
sys.exit(not report['passed'])
