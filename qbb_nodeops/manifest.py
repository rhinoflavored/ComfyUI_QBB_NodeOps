"""Versioned, derived recovery inventory. Does not download or restore files."""
import copy
import re
from pathlib import PurePosixPath

FORMAT_VERSION = 2
SUPPORTED_SCHEMAS = (1, 2, 3)


def exclusion_kind(entry, core=False):
    """Only known regenerable content is harmless; unknown exclusions stay visible."""
    path, reason = entry.get('path', ''), entry.get('reason', '')
    name = PurePosixPath(path).name
    if reason == 'excluded cache/environment/assets':
        if name in ('__pycache__', '.pytest_cache'):
            return 'regenerable'
        if name == '.git':
            return 'repository_metadata'
        if core and path == 'custom_nodes':
            return 'separate_components'
        if core and path in ('user', 'models', 'input', 'output', 'temp'):
            return 'outside_scope'
        return 'missing_content'  # venv and node_modules are not disposable caches.
    if reason in ('larger than 256 MiB', 'link/reparse point', 'linked source', 'special file', 'record storage'):
        return 'missing_content'
    return 'unknown'


def snapshot_inventory(snapshot, pointer, objects):
    """Describe recorded evidence only. Never read sources or the object store."""
    if not snapshot:
        return {'status': 'not_saved', 'snapshot_ref': None, 'files': 0,
                'directories': None, 'logical_bytes': 0, 'exclusions': []}
    if type(snapshot.get('format')) is not int or snapshot.get('format') not in (1, 2) or not isinstance(snapshot.get('files'), list):
        raise ValueError('Unsupported file snapshot format')
    logical = 0
    for entry in snapshot['files']:
        digest, size = entry.get('sha256'), entry.get('size')
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('Invalid object hash in file inventory')
        if type(size) is not int or size < 0:
            raise ValueError('Invalid object size in file inventory')
        if digest in objects and objects[digest] != size:
            raise ValueError('Conflicting sizes for shared object')
        objects[digest] = size
        logical += size
    directories = snapshot.get('directories') if snapshot['format'] == 2 else None
    complete = (isinstance(directories, list) and isinstance(snapshot.get('policy'), dict)
                and snapshot.get('inventory') == 'complete_within_policy')
    return {'status': 'complete_within_policy' if complete else 'files_only' if snapshot['format'] == 1 else 'unknown',
            'snapshot_ref': pointer, 'files': len(snapshot['files']),
            'directories': len(directories) if isinstance(directories, list) else None,
            'logical_bytes': logical,
            'exclusions': [{**copy.deepcopy(e), 'category': exclusion_kind(e, pointer == '/core/snapshot')}
                           for e in snapshot.get('skipped', [])]}


def content_contract(record):
    objects, inventories = {}, []
    for index, repo in enumerate([record['core'], *record['plugins']]):
        pointer = '/core/snapshot' if index == 0 else '/plugins/' + str(index-1) + '/snapshot'
        item = snapshot_inventory(repo.get('snapshot'), pointer, objects)
        item['component_id'] = 'core' if index == 0 else 'plugin:' + str(index)
        inventories.append(item)
    offline = record.get('offline_environment', {})
    runtime = snapshot_inventory(offline.get('snapshot'), '/offline_environment/snapshot', objects)
    all_inventories = [*inventories, runtime]
    snapshots = sum(i['status'] != 'not_saved' for i in inventories)
    gaps = any(e['category'] in ('missing_content', 'unknown') for i in inventories for e in i['exclusions'])
    complete = all(i['status'] == 'complete_within_policy' for i in inventories)
    code_status = ('metadata_only' if not snapshots else 'complete_within_policy' if complete and not gaps else 'partial')
    core = record['core'].get('snapshot') or {}
    policy = core.get('policy', {})
    scopes = {'code': {'status': code_status, 'components': len(inventories), 'snapshots': snapshots}}
    for name in ('user', 'models', 'input', 'output'):
        selected = name in policy.get('core_dirs', [])
        if 'core_dirs' not in policy:
            state = 'unknown'
        elif not selected:
            state = 'not_selected'
        else:
            excluded = any(e['category'] not in ('regenerable', 'repository_metadata')
                           and (e['path'] in ('', '.', name) or e['path'].startswith(name + '/'))
                           for e in inventories[0]['exclusions'])
            present = any(f['path'] == name or f['path'].startswith(name + '/') for f in core.get('files', []))
            present = present or any(d['path'] == name for d in core.get('directories', []))
            state = ('partial' if excluded else 'unknown' if inventories[0]['status'] != 'complete_within_policy'
                     else 'captured' if present else 'absent')
        scopes[name] = {'status': state, 'selected': selected if 'core_dirs' in policy else None}
    scopes['python_environment'] = {
        'status': ('not_saved' if runtime['status'] == 'not_saved' else 'offline_assets_recorded'
                   if offline.get('status') == 'ready' and runtime['status'] == 'complete_within_policy' and not runtime['exclusions'] else 'partial'),
        'original_binary_identity': False,
    }
    return {
        'storage': {'layout': 'objects/sha256-prefix/sha256-suffix', 'hash': 'sha256',
                    'encoding': 'raw', 'unit': 'whole_file', 'requires_parent_record': False,
                    'file_lists': 'full_within_policy'},
        'inventories': inventories, 'offline_inventory': runtime, 'scopes': scopes,
        'sizes': {'logical_bytes': sum(i['logical_bytes'] for i in all_inventories),
                  'unique_referenced_bytes': sum(objects.values()), 'unique_objects': len(objects),
                  'new_bytes': None, 'reclaimable_bytes': None, 'temporary_bytes': None},
        'verification': {'objects': 'not_checked', 'dependencies': 'not_checked',
                         'startup': 'not_checked', 'workflow': 'not_checked'},
        'external_resources': 'not_guaranteed',
    }

def repository_gap(repo):
    snapshot = repo.get('snapshot')
    if snapshot:
        if snapshot.get('format') not in (1, 2) or not isinstance(snapshot.get('files'), list):
            return 'Unsupported file snapshot format.'
        return ''
    if repo.get('kind') != 'git':
        return 'Local plugin contents were not saved.'
    if repo.get('has_submodules') or repo.get('submodule_error'):
        return 'Git 子模块内容没有可靠的文件快照；请提供包含子模块的备份或重新创建记录。'
    if repo.get('dirty') is not False:
        return 'Local modifications or incomplete Git inspection; recorded files cannot be reproduced.'
    if not re.fullmatch(r'[0-9a-fA-F]{40,64}', repo.get('commit', '')):
        return 'Missing exact commit.'
    if not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?', repo.get('url', '')):
        return 'Only public HTTPS GitHub repositories are supported.'
    return ''

def package_gap(package):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', package.get('name', '')) or not re.fullmatch(r'[A-Za-z0-9.!+_-]+', package.get('version', '')):
        return 'Invalid package pin.'
    if package.get('editable') or not package.get('source', '').startswith('unknown'):
        return 'Direct/local source requires an explicit wheel, index substitution, or skip choice.'
    return ''

def normalize(record):
    """Adapt old records in memory only; never invent missing historical data."""
    if isinstance(record, dict) and record.get('schema') == 4:
        from .snapshot_state import validate
        return validate(record)
    if not isinstance(record, dict) or record.get('kind') != 'version_record' or type(record.get('schema', 1)) is not int or record.get('schema', 1) not in SUPPORTED_SCHEMAS:
        raise ValueError('Unsupported version record format')
    existing = record.get('manifest', {})
    if not isinstance(existing, dict) or (existing and (type(existing.get('format_version')) is not int or existing.get('format_version') not in (1, FORMAT_VERSION))):
        raise ValueError('Unsupported manifest format')
    result = copy.deepcopy(record)
    components, names = [], set()
    for index, repo in enumerate([record['core'], *record['plugins']]):
        name = repo['name']
        gap = repository_gap(repo)
        if index and (name.lower() in names or not re.fullmatch(r'[\w.-]+', name) or name in ('.', '..')):
            gap = 'Unsafe or duplicate plugin folder name.'
        names.add(name.lower())
        snapshot = repo.get('snapshot')
        exclusions = snapshot.get('skipped', []) if snapshot else []
        components.append({
            'id': 'core' if index == 0 else 'plugin:' + str(index),
            'role': 'core' if index == 0 else 'plugin', 'name': name,
            'source_path': repo.get('path', ''),
            'repository': {k: repo.get(k) for k in ('kind', 'url', 'commit', 'branch', 'dirty')},
            'snapshot_ref': '/core/snapshot' if index == 0 else '/plugins/' + str(index-1) + '/snapshot',
            'capture': 'files' if snapshot else 'metadata_only',
            'excluded': exclusions,
            'recovery': {'method': 'skip' if gap else 'snapshot' if snapshot else 'git',
                         'status': 'needs_input' if gap else 'partial' if exclusions else 'ready',
                         'reason': gap, 'required': index == 0,
                         'verification': 'not_checked'},
        })
    dependencies = []
    for package in record['packages']:
        gap = package_gap(package)
        build = re.search(r'\+(cpu|cu\d+)$', package.get('version', ''))
        official = package['name'].lower() in ('torch', 'torchvision', 'torchaudio') and build and not gap
        dependencies.append({**copy.deepcopy(package), 'recovery': {
            'method': 'skip' if gap else 'pytorch_wheel' if official else 'pypi_wheel',
            'choices': ['wheel', 'pypi', 'skip'],
            'index_hint': 'https://download.pytorch.org/whl/' + build.group(1) if official else None,
            'status': 'needs_input' if gap else 'ready', 'reason': gap,
            'source_substitution': not bool(gap), 'verification': 'not_checked'}})
    blocked = any(c['recovery']['required'] and c['recovery']['status'] == 'needs_input' for c in components)
    partial = any(c['recovery']['status'] != 'ready' for c in components + dependencies)
    result['manifest'] = {
        'format_version': FORMAT_VERSION, 'source_schema': record.get('schema', 1),
        'content': content_contract(record),
        'consistency': copy.deepcopy(record.get('consistency', {'status':'not_recorded','requires_quiescent_source':True})),
        'instance': {'path': record['core'].get('path', ''), 'record_id': record.get('id'), 'captured_at': record.get('created')},
        'runtime': {'python': copy.deepcopy(record['python']),
                    'offline_assets': 'included' if record.get('offline_environment',{}).get('status')=='ready' else 'not_saved',
                    'torch': next((p['version'] for p in record['packages'] if p['name'].lower() == 'torch'), None),
                    'cuda': {'value': None, 'status': 'not_recorded'}},
        'components': components, 'dependencies': dependencies,
        'coverage': {'models': 'within_snapshot_policy' if 'models' in record.get('core',{}).get('snapshot',{}).get('policy',{}).get('core_dirs',[]) else 'not_guaranteed', 'configuration': 'only_if_in_snapshot',
                     'selected_core_dirs':record.get('core',{}).get('snapshot',{}).get('policy',{}).get('core_dirs',[]),
                     'python_environment_files': 'offline_runtime_and_wheels' if record.get('offline_environment',{}).get('status')=='ready' else 'not_saved', 'object_store': 'objects',
                     'json_export_contains_objects': False},
        'recovery': {'status': 'blocked' if blocked else 'partial' if partial else 'ready',
                     'scope': 'recorded_components_only', 'execution': 'not_started',
                     'availability_verified': False, 'runtime_verified': False},
    }
    return result
