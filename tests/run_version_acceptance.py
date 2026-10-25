"""Version-history acceptance suite; each test group uses isolated fixtures.

Run with Python from this directory or any working directory. --skip-git allows
restricted environments to defer the local Git fixture group to a normal shell.
No live instance is started or user backup store is accessed by these groups.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

PYTHON_GROUPS = (
    'manifest', 'file_snapshots', 'record_capture', 'record_compare',
    'checkpoint_policy', 'record_archives', 'offline_environment',
    'recovery_choices', 'recovery_environment', 'rebuild_executor',
    'recovery_verification', 'recovery_service', 'git_submodules',
    'record_history_routes', 'archive_routes', 'version_lifecycle',
)
UI_GROUPS = (
    'snapshot', 'record_archives', 'compare', 'history',
    'recovery_environment', 'recovery_plan', 'recovery_task', 'recovery_verification', 'grouped_recovery', 'recovery_picker',
)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-git',action='store_true')
    args=parser.parse_args()
    directory=Path(__file__).resolve().parent
    failures=[];count=0;ui_count=0
    for kind,groups in [('python',PYTHON_GROUPS),('ui',UI_GROUPS)]:
        for group in groups:
            if args.skip_git and group=='git_submodules':
                print('SKIPPED: local Git fixtures must be verified separately',flush=True)
                continue
            command=([sys.executable,'-B','-m','unittest','discover','-s',str(directory),'-p','test_'+group+'.py'] if kind=='python'
                     else ['node',str(directory/('test_'+group+'_ui.cjs'))])
            print('RUN '+kind+' '+group,flush=True)
            try:
                result=subprocess.run(command,capture_output=True,text=True,errors='replace',timeout=360)
                output=result.stdout+result.stderr
                print(output,flush=True)
                if result.returncode:failures.append(group)
                elif kind=='python':
                    match=re.search(r'Ran (\d+) tests?',output)
                    if not match:failures.append(group+' (no test count)')
                    else:count+=int(match[1])
                else:ui_count+=1
            except (OSError,subprocess.TimeoutExpired) as exc:
                failures.append(group);print(str(exc),flush=True)
    print(f'Passed: {count} Python tests, {ui_count} UI groups. Failures: {failures}',flush=True)
    return bool(failures)


if __name__=='__main__':
    sys.exit(main())
