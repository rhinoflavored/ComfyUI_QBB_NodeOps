"""Version inventories with shared local-file snapshots (not environment images)."""
import importlib.metadata
import json
import platform
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from .git_tools import require_git

LOCK = threading.Lock()
SETTINGS = Path(__file__).resolve().parent.parent / 'qbb_nodeops_record_storage.json'

def directory():
    from .instances import CURRENT_COMFYUI_ROOT
    if SETTINGS.exists():
        data = json.loads(SETTINGS.read_text(encoding='utf-8'))
        configured = Path(data['directory'])
        if not configured.is_absolute():
            raise ValueError('Invalid version-record directory setting')
        return configured
    return Path(CURRENT_COMFYUI_ROOT).parent / 'QBB_version_records'

def set_directory(raw):
    from . import rebuild
    import tempfile
    import stat as stat_module
    if not isinstance(raw,str) or not raw.strip() or not Path(raw).is_absolute():
        raise ValueError('Enter an absolute folder path')
    target=Path(raw).resolve()
    if target==Path(target.anchor):
        raise ValueError('Choose a dedicated folder, not a drive root')
    try:
        info = target.lstat() if target.exists() else None
    except OSError as exc:
        raise ValueError('Cannot access storage folder') from exc
    if info is not None and (stat_module.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400):
        raise ValueError('Linked/shared paths cannot be used for version records')
    if not rebuild.LOCK.acquire(blocking=False):
        raise ValueError('Cannot change storage during reconstruction')
    try:
        if not LOCK.acquire(blocking=False):
            raise ValueError('Cannot change storage while recording')
        try:
            previous=str(directory())
            target.mkdir(parents=True,exist_ok=True)
            with tempfile.TemporaryFile(dir=target) as probe:
                probe.write(b'QBB storage check')
            temporary=SETTINGS.with_suffix('.tmp')
            temporary.write_text(json.dumps({'directory':str(target)},ensure_ascii=False),encoding='utf-8')
            temporary.replace(SETTINGS)
            return {'directory':str(target),'previous':previous}
        finally:
            LOCK.release()
    finally:
        rebuild.LOCK.release()

def safe_url(value):
    # Never persist embedded credentials, queries, or fragments.
    if not value:
        return ''
    if value.startswith('git@'):
        return value
    try:
        p = urlsplit(value)
        scheme = p.scheme
        host = (p.hostname or '').lower()
        if scheme == 'http' and host == 'github.com':
            scheme = 'https'
        if scheme not in ('https', 'ssh', 'git'):
            return ''
        if p.username or p.password:
            return ''
        netloc = host
        if p.port:
            netloc += ':' + str(p.port)
        return urlunsplit((scheme, netloc, p.path, '', ''))
    except ValueError:
        return ''

def git(path, *args):
    result = subprocess.run([require_git(), '--no-optional-locks', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=', '-C', str(path), *args], capture_output=True,
                            text=True, errors='replace', timeout=8,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('Git metadata unavailable')
    return result.stdout.strip()

def repository(path):
    row = {'name': path.name, 'path': str(path), 'kind': 'local', 'commit': '',
           'branch': '', 'url': '', 'dirty': None}
    if not path.is_dir() or not (path / '.git').exists():
        row['warning'] = 'Local files are not backed up; cannot recreate from this record.'
        return row
    row['kind'] = 'git'
    try:
        row['commit'] = git(path, 'rev-parse', 'HEAD')
        row['dirty'] = bool(git(path, 'status', '--porcelain', '--untracked-files=normal'))
        try:
            row['branch'] = git(path, 'symbolic-ref', '--short', '-q', 'HEAD')
        except ValueError:
            row['branch'] = '(detached)'
        try:
            row['url'] = safe_url(git(path, 'remote', 'get-url', 'origin'))
        except ValueError:
            pass
        if row['dirty']:
            row['warning'] = 'Uncommitted/untracked files detected; their contents are NOT saved.'
    except (OSError, ValueError, subprocess.TimeoutExpired):
        row['warning'] = 'Git inspection failed; this entry is incomplete.'
    try:
        tree = git(path, 'ls-files', '--stage')
        row['has_submodules'] = any(line.startswith('160000 ') for line in tree.splitlines())
        if row['has_submodules']:
            status = git(path, 'submodule', 'status', '--recursive')
            if not status or any(line.startswith(('-', 'U')) for line in status.splitlines()):
                row['submodule_error'] = 'Git 子模块未初始化或存在冲突；请完成子模块初始化后重新预览。'
    except (OSError, ValueError, subprocess.TimeoutExpired):
        row['submodule_error'] = '无法核实 Git 子模块内容；请修复仓库后重新预览。'
    return row

def packages():
    rows = []
    for dist in importlib.metadata.distributions():
        row = {'name': dist.metadata.get('Name', ''), 'version': dist.version,
               'source': 'unknown (package index is not recorded by installed metadata)'}
        try:
            direct = json.loads(dist.read_text('direct_url.json') or '{}')
            if direct:
                row['source'] = safe_url(direct.get('url', '')) or 'local/private source (not exported)'
                row['commit'] = direct.get('vcs_info', {}).get('commit_id', '')
                row['editable'] = bool(direct.get('dir_info', {}).get('editable'))
        except (ValueError, TypeError):
            row['source'] = 'unreadable metadata'
        rows.append(row)
    return sorted(rows, key=lambda row: row['name'].lower())

def inventory(label=''):
    from .instances import CURRENT_COMFYUI_ROOT
    from . import file_snapshots
    core = Path(CURRENT_COMFYUI_ROOT).resolve()
    import folder_paths
    roots = [Path(p) for p in folder_paths.get_folder_paths('custom_nodes')]
    plugins, seen = [], set()
    for root in roots:
        for path in sorted(root.iterdir()):
            if path.name.startswith('.') or path.name == '__pycache__':
                continue
            if not path.is_dir() and path.suffix != '.py':
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            row = repository(resolved)
            try:
                file_snapshots.no_links(path)
                is_linked = False
            except ValueError:
                is_linked = True
            if is_linked:
                row['linked_source'] = True
                row['warning'] = 'Linked plugin directory is not captured.'
            plugins.append(row)
    record = {'schema': 2, 'kind': 'version_record', 'id': uuid.uuid4().hex,
              'created': time.time(), 'label': str(label)[:120],
              'python': {'version': platform.python_version(), 'executable': sys.executable, 'implementation':platform.python_implementation(),
                         'platform': platform.platform(), 'architecture': platform.machine()},
              'core': repository(core), 'plugins': plugins, 'packages': packages(),
              'limitations': ['Local/modified repositories include file snapshots. Caches, links and files over 256 MiB are excluded; inspect snapshot.skipped.',
                              'Core user/models/input/output follow the selected scope. Temp is excluded; plugins are recorded separately. External/linked directories are not followed.',
                              'JSON export alone does not include file objects. Back up the entire record storage folder.',
                              'Not a resolved lockfile; original downloads may be unavailable.',
                              'Stop updates and dependency installs before recording. No automatic restore.']}

    record['schema'] = 3
    record['limitations'][0] = 'File capture follows the selected policy; exclusions are listed in snapshot.skipped.'
    return record


_PREVIEWS = {}
_PREVIEW_LOCK = threading.Lock()


def capture_options(options=None):
    from .snapshot_state import options as validate_options
    return validate_options(options)


def preview(options=None):
    from .snapshot_state import preview as capture_preview
    return capture_preview(options)


def checkpoint_context(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'operation', 'phase', 'before_record_id'}:
        raise ValueError('Invalid operation checkpoint')
    if value['operation'] not in ('update', 'dependencies', 'cleanup', 'other') or value['phase'] not in ('before', 'after'):
        raise ValueError('Invalid checkpoint operation or phase')
    if value['phase'] == 'before':
        if value['before_record_id'] is not None:
            raise ValueError('Before checkpoint must not reference another checkpoint')
    else:
        if not isinstance(value['before_record_id'], str):
            raise ValueError('Select a before-operation checkpoint')
        previous = read(value['before_record_id'])
        origin = previous.get('operation_checkpoint', {})
        if origin.get('phase') != 'before' or origin.get('operation') != value['operation']:
            raise ValueError('Related checkpoint must be before the same operation type')
    return dict(value, outcome='not_asserted')


def create(label='', options=None, preview_id=None, _automatic_guard=None, *, checkpoint=None):
    if _automatic_guard is not None or checkpoint is not None:
        raise ValueError('Phase 1 supports manual snapshots only')
    from .snapshot_state import create as capture
    return capture(label, options, preview_id)


def read(record_id):
    if not re.fullmatch('[a-f0-9]{32}', record_id):
        raise ValueError('Invalid record ID')
    from .manifest import normalize
    return normalize(json.loads((directory() / (record_id + '.json')).read_text(encoding='utf-8')))

def listing():
    rows = []
    for path in directory().glob('*.json'):
        try:
            data = read(path.stem)
            if data.get('schema') == 4:
                from .snapshot_state import summary
                rows.append({'id': path.stem, 'schema': 4, 'created': data['created'], 'label': data['label'],
                             'content_summary': summary(data), 'capabilities': data['capabilities'],
                             'bytes': path.stat().st_size, 'plugins': data['statistics']['plugins'],
                             'packages': data['statistics']['packages'], 'python': data['python']['version'],
                             'warnings': data['statistics']['unrestorable'] + len(data['issues'])})
                continue
            rows.append({'id': path.stem, 'created': data['created'], 'label': data['label'],
                         'operation_checkpoint': data.get('operation_checkpoint'),
                         'automatic_checkpoint': bool(data.get('automatic_checkpoint')),
                         'content_summary': {key: data['manifest']['content'][key] for key in ('scopes', 'sizes')},
                         'offline_environment':data.get('offline_environment',{}).get('status')=='ready',
                         'bytes': path.stat().st_size, 'plugins': len(data['plugins']),
                         'packages': len(data['packages']), 'python': data['python']['version'],
                         'warnings': sum(bool(r.get('warning')) for r in [data['core'], *data['plugins']])})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return {'items': sorted(rows, key=lambda r:r['created'], reverse=True), 'running': LOCK.locked()}
