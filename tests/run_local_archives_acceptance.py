"""Isolated ZIP capture acceptance; no running ComfyUI or pip modifications."""
from pathlib import Path
import re
import subprocess
import sys

root = Path(__file__).resolve().parent
groups = ['zip_verification', 'local_archives', 'backup_contract', 'phase1_snapshot', 'phase2_diff', 'zip_diff', 'zip_restore', 'phase3_restore',
          'record_archives', 'version_lifecycle', 'manifest', 'file_snapshots',
          'capture_error_policy', 'record_capture']
total = 0
for group in groups:
    run = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', str(root),
                          '-p', 'test_' + group + '.py'], capture_output=True, text=True,
                         errors='replace', timeout=360)
    output = run.stdout + run.stderr
    count = re.search(r'Ran (\d+) tests?', output)
    if run.returncode or count is None or int(count[1]) == 0:
        print(group + ': FAILED\n' + output, flush=True)
        sys.exit(1)
    total += int(count[1])
    print(group + ': ' + count[1] + ' passed', flush=True)
print('Total: ' + str(total) + ' passed', flush=True)
