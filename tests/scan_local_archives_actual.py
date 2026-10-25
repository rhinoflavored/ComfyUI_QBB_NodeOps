"""Read-only smoke check of small installed distributions; never writes a ZIP."""
import importlib.metadata
import json
import sys

from test_phase1_snapshot import state

names = ['six', 'packaging', 'typing_extensions']
distributions = []
for name in names:
    try:
        distributions.append(importlib.metadata.distribution(name))
    except importlib.metadata.PackageNotFoundError:
        continue
if not distributions:
    raise SystemExit('No smoke-check distributions installed')
result = state.local_archives.package_inventories(distributions, state.Path(state.__file__).parent.parent)
print(json.dumps({'python': sys.executable, 'packages': {
    key: {'files': len(value['files']), 'bytes': sum(f['size'] for f in value['files']),
          'gaps': value['gaps'], 'excluded': len(value['exclusions'])}
    for key, value in result.items()}}, ensure_ascii=False))
if any(value['gaps'] for value in result.values()):
    raise SystemExit(1)
