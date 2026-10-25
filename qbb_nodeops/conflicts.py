"""Read-only plugin compatibility checks. Static findings are not load validation."""
import ast
import itertools
from pathlib import Path
from .instances import get_instance
from .store import REPORT


def inspect_plugin(path):
    from packaging.requirements import Requirement
    requirements, nodes, unknown = [], {}, []
    file = path / 'requirements.txt'
    if file.is_file():
        for number, line in enumerate(file.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            line = line.split(' #', 1)[0].strip()
            if not line or line.startswith('#'):
                continue
            try:
                req = Requirement(line)
                requirements.append((req, f'{file}:{number}'))
            except Exception:
                unknown.append(f'{file}:{number}: {line[:160]}')
    else:
        unknown.append(f'{path.name}: requirements.txt not declared')
    count = 0
    sources = [path] if path.is_file() and path.suffix.lower() == '.py' else path.rglob('*.py')
    for file in sources:
        if any(p in ('.git', 'venv', '.venv', '__pycache__') for p in file.relative_to(path).parts):
            continue
        if file.is_symlink() or file.stat().st_size > 1024 * 1024:
            continue
        count += 1
        if count > 300:
            unknown.append(f'{path.name}: Python scan truncated at 300 files')
            break
        try:
            tree = ast.parse(file.read_text(encoding='utf-8', errors='replace'))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    if any(isinstance(t, ast.Name) and t.id == 'NODE_CLASS_MAPPINGS' for t in targets):
                        if isinstance(node.value, ast.Dict):
                            for key in node.value.keys:
                                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                                    nodes[key.value] = f'{file}:{key.lineno}'
                        else:
                            unknown.append(f'{file}: dynamic NODE_CLASS_MAPPINGS')
        except (SyntaxError, OSError):
            unknown.append(f'{file}: could not statically parse')
    return {'name': path.name, 'path': str(path), 'requirements': requirements, 'nodes': nodes, 'unknown': unknown}


def disjoint(a, b):
    """Prove only exact pins and simple bounded intervals; complex forms stay unknown."""
    from packaging.version import Version
    specs = list(a) + list(b)
    pins = [s.version for s in specs if s.operator == '==' and '*' not in s.version]
    if pins:
        return not any(all(s.contains(pin, prereleases=True) for s in specs) for pin in pins)
    lower, upper = None, None
    for spec in specs:
        if spec.operator not in ('>', '>=', '<', '<='):
            return None
        version = Version(spec.version)
        inclusive = spec.operator.endswith('=')
        if spec.operator.startswith('>'):
            if lower is None or version > lower[0]: lower = (version, inclusive)
            elif version == lower[0]: lower = (version, lower[1] and inclusive)
        else:
            if upper is None or version < upper[0]: upper = (version, inclusive)
            elif version == upper[0]: upper = (version, upper[1] and inclusive)
    if lower and upper:
        return lower[0] > upper[0] or (lower[0] == upper[0] and not (lower[1] and upper[1]))
    return False


def compare(groups, incoming_paths=None):
    from packaging.utils import canonicalize_name
    findings = []
    for left, right in itertools.combinations(groups, 2):
        if incoming_paths is not None and left['path'] not in incoming_paths and right['path'] not in incoming_paths:
            continue
        for a, source_a in left['requirements']:
            for b, source_b in right['requirements']:
                if canonicalize_name(a.name) != canonicalize_name(b.name): continue
                # Target markers/extras may differ from the running environment.
                conditional = bool(a.marker or b.marker or a.extras or b.extras or a.url or b.url)
                conflict = disjoint(a.specifier, b.specifier)
                if conflict or conflict is None or conditional:
                    findings.append({'level': 'confirmed' if conflict and not conditional else 'unknown', 'kind': 'dependency',
                        'plugins': [left['name'],right['name']], 'message': f'{a} ↔ {b}', 'evidence': [source_a,source_b]})
        for key in left['nodes'].keys() & right['nodes'].keys():
            findings.append({'level':'suspected', 'kind':'duplicate_node', 'plugins':[left['name'],right['name']],
                             'message':key, 'evidence':[left['nodes'][key],right['nodes'][key]]})
    return findings


def preflight(payload):
    from .migration import _resolve_selected, scan_plugins, _existing_comfyui_target
    source = get_instance(str(payload.get('source_instance_id') or 'current'))
    root = Path(source['comfyui_root'])
    selected = payload.get('plugins')
    if not isinstance(selected, list) or not selected:
        raise ValueError('Select plugins first')
    plugins = _resolve_selected([str(p) for p in selected], root)
    paths = [Path(p.path) for p in plugins]
    incoming_paths = {str(p) for p in paths}
    mode = payload.get('mode', 'compare')
    target = None
    if mode == 'instance': target = Path(get_instance(str(payload.get('target_instance_id')))['comfyui_root'])
    elif mode == 'existing': target = _existing_comfyui_target(str(payload.get('destination_root', '')))
    elif mode not in ('new','compare'): raise ValueError('Invalid comparison mode')
    occupied = []
    if target:
        if target.resolve() == root.resolve(): raise ValueError('Source and target must differ')
        occupied = [p.name for p in paths if (target / 'custom_nodes' / p.name).exists()]
        paths += [Path(p['path']) for p in scan_plugins(target)['plugins']]
    groups = [inspect_plugin(p) for p in dict.fromkeys(paths)]
    findings = compare(groups, incoming_paths)
    existing_findings = compare([g for g in groups if g['path'] not in incoming_paths])
    for name in occupied:
        findings.append({'level':'confirmed','kind':'destination_exists','plugins':[name], 'message':name,'evidence':[str(target / 'custom_nodes' / name)]})
    logs = [{'name':r.name,'reason':r.reason} for r in REPORT.records if r.needs_attention and Path(r.path) in paths]
    return {'findings':findings, 'existing_findings':existing_findings, 'unknown':[x for g in groups for x in g['unknown']], 'logs':logs,
            'checked':len(groups), 'confirmed':sum(f['level']=='confirmed' for f in findings),
            'runtime_verified':False, 'target_environment_checked':False,
            'scope':'Static dependency declarations and node identifiers; target installed dependencies and runtime loading are not verified.'}
