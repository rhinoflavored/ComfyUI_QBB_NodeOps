"""Check declarations using the target interpreter, without loading plugin code."""
import json
import subprocess

PROBE = r'''
import json, sys
from importlib.metadata import distribution, PackageNotFoundError
try:
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
except ImportError:
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.utils import canonicalize_name
issues, unknown = [], []
def check(plugin, req, seen):
    # Check every version constraint, even when this package was visited before.
    if req.url:
        unknown.append(plugin + ': ' + str(req))
        return
    try:
        dist = distribution(req.name)
    except PackageNotFoundError:
        issues.append(plugin + ': missing ' + str(req))
        return
    if not req.specifier.contains(dist.version, prereleases=True):
        issues.append(plugin + ': requires ' + str(req) + ', installed ' + dist.version)
    if not req.extras:
        return
    key = (canonicalize_name(req.name), tuple(sorted(canonicalize_name(e) for e in req.extras)))
    if key in seen:
        return
    seen.add(key)
    provided = {canonicalize_name(e) for e in dist.metadata.get_all('Provides-Extra', [])}
    requested = {canonicalize_name(e) for e in req.extras}
    for extra in sorted(requested - provided):
        unknown.append(plugin + ': ' + req.name + ' does not declare extra [' + extra + ']')
    for declaration in dist.requires or []:
        try:
            dependency = Requirement(declaration)
            if dependency.marker and not any(dependency.marker.evaluate({'extra': extra}) for extra in requested & provided):
                continue
            check(plugin, dependency, seen)
        except Exception:
            unknown.append(plugin + ': unsupported extra dependency ' + declaration)

for plugin, lines in json.load(sys.stdin):
    if lines is None:
        unknown.append(plugin + ': requirements.txt not declared')
        continue
    for line in lines:
        line = line.split(' #', 1)[0].strip()
        if not line or line.startswith('#'): continue
        try:
            req = Requirement(line)
            if req.marker and not req.marker.evaluate(): continue
            check(plugin, req, set())
        except Exception:
            unknown.append(plugin + ': unsupported requirement ' + line)
print(json.dumps({'issues': issues, 'unknown': unknown}))
'''


def validate(python, target, plugins):
    result = {'runtime_verified': False, 'issues': [], 'unknown': []}
    if not python or not python.is_file():
        result['unknown'].append('Target Python could not be identified; dependencies were not checked.')
        return result
    try:
        declarations = []
        for plugin in plugins:
            file = target / 'custom_nodes' / plugin.name / 'requirements.txt'
            declarations.append((plugin.name, file.read_text(encoding='utf-8-sig').splitlines() if file.is_file() else None))
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        proc = subprocess.run([str(python), '-I', '-c', PROBE], input=json.dumps(declarations), cwd=str(python.parent), capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60, creationflags=flags)
        if proc.returncode:
            result['unknown'].append('Target dependency probe failed: ' + proc.stderr[-1000:])
        else:
            result.update(json.loads(proc.stdout))
        check = subprocess.run([str(python), '-I', '-m', 'pip', 'check'], cwd=str(python.parent), capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60, creationflags=flags)
        if check.returncode:
            result['unknown'].append('Target pip check did not pass: ' + (check.stdout + check.stderr)[-2000:])
    except Exception as exc:
        result['unknown'].append('Dependency verification incomplete: ' + str(exc))
    return result
