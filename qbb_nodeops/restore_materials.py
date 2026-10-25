"""Bounded preparation and journaled writes for the current-instance worker."""
import copy
import importlib.metadata
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import urllib.request
from urllib.parse import unquote, urlsplit
import zipfile

from . import file_snapshots as files, snapshot_state as state, state_diff as diff
from .git_tools import require_git
from .rebuild_executor import atomic_json
from .recovery_environment import wheel_info
try:
    from packaging.tags import sys_tags
    from packaging.utils import parse_wheel_filename
    from packaging.specifiers import SpecifierSet
except ImportError:
    from pip._vendor.packaging.tags import sys_tags
    from pip._vendor.packaging.utils import parse_wheel_filename
    from pip._vendor.packaging.specifiers import SpecifierSet


def package_scope(name, manager):
    """Check uninstall destinations, not package file-byte integrity."""
    distribution = importlib.metadata.distribution(name)
    if not distribution.files:
        raise ValueError('Package has no bounded uninstall inventory')
    paths = sysconfig.get_paths()
    roots = [Path(paths[key]).resolve() for key in ('purelib', 'platlib', 'scripts')]
    manager = Path(manager).resolve()
    for entry in distribution.files:
        path = Path(os.path.abspath(distribution.locate_file(entry)))
        files.no_links(path)
        if not any(path.is_relative_to(root) and path != root for root in roots):
            raise ValueError('Package uninstall path outside current interpreter install roots')
        if path == manager or path.is_relative_to(manager) or manager.is_relative_to(path):
            raise ValueError('Package inventory overlaps protected manager')
        if re.fullmatch(r'python[\d.]*w?(?:\.exe)?', path.name.lower()) or (
                path.parent == Path(paths['scripts']).resolve() and path.name.lower().startswith('pip')) or any(
                part.lower() == 'pip' or part.lower().startswith('pip-') and part.endswith('.dist-info') for part in path.parts):
            raise ValueError('Package inventory overlaps protected Python/pip tooling')


def inside(root, path, *, root_allowed=False):
    root, path = Path(os.path.abspath(root)), Path(os.path.abspath(path))
    files.no_links(root)
    files.no_links(path)
    if not path.is_relative_to(root) or path == root and not root_allowed:
        raise ValueError('Path outside the permitted task/resource boundary')
    return path


def inventory(path):
    path = Path(path)
    files.no_links(path)
    if not path.exists():
        return None
    if path.is_file():
        return {'file': files.stable_hash(path, files.signature(path.stat())), 'mode': stat.S_IMODE(path.stat().st_mode)}
    result = {}
    def fail(error):
        raise error
    for root, dirs, names in os.walk(path, followlinks=False, onerror=fail):
        for name in dirs + names:
            entry = Path(root) / name
            files.no_links(entry)
            if not entry.is_dir() and not entry.is_file():
                raise ValueError('Special files cannot be moved by restoration')
            result[entry.relative_to(path).as_posix()] = {
                'content': 'directory' if entry.is_dir() else files.stable_hash(entry, files.signature(entry.stat())),
                'mode': stat.S_IMODE(entry.stat().st_mode)}
    return {'tree': result, 'mode': stat.S_IMODE(path.stat().st_mode)}


def remove_task_tree(job, path):
    path = inside(job, path)
    inventory(path)  # Refuse links before any recursive cleanup.
    if path.is_dir():
        def writable(function, filename, error):
            os.chmod(filename, stat.S_IWRITE | stat.S_IREAD)
            function(filename)
        shutil.rmtree(path, onerror=writable)
    elif path.exists():
        path.chmod(stat.S_IWRITE | stat.S_IREAD)
        path.unlink()


def run(command, *, cwd=None):
    env = os.environ.copy()
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0',
               GIT_LFS_SKIP_SMUDGE='1', GIT_OPTIONAL_LOCKS='0', PIP_CONFIG_FILE=os.devnull,
               PYTHONDONTWRITEBYTECODE='1', PIP_DISABLE_PIP_VERSION_CHECK='1')
    result = subprocess.run([str(v) for v in command], cwd=cwd, env=env, capture_output=True,
                            text=True, errors='replace', timeout=600,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('Restore subprocess failed: ' + result.stdout[-2000:] + result.stderr[-2000:])
    return result.stdout.strip()


def git(folder, *args):
    return run([require_git(), '--no-optional-locks', '-c', 'core.hooksPath=', '-c', 'core.fsmonitor=false',
                '-c', 'safe.directory=' + str(Path(folder).resolve()).replace('\\', '/'), '-C', folder, *args])


def fetch_git(row, destination, current_path, network):
    source = row['source']
    if not re.fullmatch('[a-fA-F0-9]{40,64}', source.get('commit', '')) or not source.get('url'):
        raise ValueError('Exact Git source/commit required')
    destination.mkdir()
    git(destination, 'init')
    local = False
    if current_path and (Path(current_path) / '.git').is_dir():
        inventory(Path(current_path) / '.git')
        try:
            git(current_path, 'cat-file', '-e', source['commit'] + '^{commit}')
            git(destination, '-c', 'protocol.file.allow=always', 'fetch', '--no-tags', '--', str(current_path), source['commit'])
            local = True
        except ValueError:
            local = False
    if not local:
        if not network:
            raise ValueError('Git object is not local; explicit network preparation required')
        if urlsplit(source['url']).scheme not in ('https', 'http', 'ssh', 'git'):
            raise ValueError('Unsupported Git transport')
        git(destination, '-c', 'protocol.file.allow=never', '-c', 'protocol.ext.allow=never',
            'fetch', '--no-tags', '--', source['url'], source['commit'])
    git(destination, 'remote', 'add', 'origin', source['url'])
    git(destination, 'update-ref', 'refs/remotes/origin/nodeops-target', source['commit'])
    tree = git(destination, 'ls-tree', '-r', source['commit'])
    if any(line.startswith(('160000 ', '120000 ')) for line in tree.splitlines()):
        raise ValueError('Submodules and Git symlinks require separate support; no live writes performed')
    git(destination, 'checkout', '--detach', source['commit'])
    if git(destination, 'rev-parse', 'HEAD') != source['commit']:
        raise ValueError('Prepared Git commit mismatch')
    inventory(destination)
    for path in destination.rglob('.gitattributes'):
        if 'filter=' in path.read_text(encoding='utf-8', errors='replace'):
            raise ValueError('Git content filters/LFS are not executed by V1 restoration')


def download(url, destination):
    if urlsplit(url).scheme != 'https':
        raise ValueError('Pinned wheel downloads require HTTPS')
    with urllib.request.urlopen(url, timeout=60) as response, destination.open('xb') as out:
        if urlsplit(response.geturl()).scheme != 'https':
            raise ValueError('Insecure wheel redirect')
        count = 0
        while block := response.read(files.CHUNK):
            count += len(block)
            if count > 8 * 1024 ** 3:
                raise ValueError('Wheel download exceeds V1 limit')
            out.write(block)


def prepare_wheel(row, manifest, store, folder, network):
    if state.key(row['name']) == 'pip':
        raise ValueError('pip is protected execution tooling; V1 does not replace or uninstall it')
    src = row['source']
    kind = src['kind']
    if kind not in ('local_wheel', 'remote_wheel'):
        raise ValueError('Package mutation requires a verified original wheel; no source build or guessed index')
    filename = src.get('filename') if kind == 'local_wheel' else unquote(urlsplit(src['url']).path.rsplit('/', 1)[-1])
    files.safe_parts(filename)
    if len(PurePosixPath(filename).parts) != 1 or not filename.endswith('.whl'):
        raise ValueError('Invalid original wheel filename')
    folder.mkdir(parents=True)
    target = folder / filename
    sha = src.get('wheel', {}).get('sha256') if kind == 'local_wheel' else src.get('sha256')
    if row.get('artifact_ref'):
        artifact = manifest['artifacts'][row['artifact_ref']]
        files.validate_snapshot(artifact, store)
        if len(artifact['files']) != 1 or artifact['files'][0]['sha256'] != sha:
            raise ValueError('Wheel artifact does not match source evidence')
        shutil.copyfile(files.object_path(store, sha), target)
    elif row.get('restore_artifact') and files.object_path(Path(row['restore_artifact']['store']), sha).is_file():
        saved = row['restore_artifact']
        shutil.copyfile(files.object_path(Path(saved['store']), sha), target)
    elif kind == 'local_wheel':
        origin = Path(src['path_at_capture'])
        files.no_links(origin)
        if not origin.is_file():
            raise ValueError('original_artifact_missing: no rollback/install fallback')
        shutil.copyfile(origin, target)
    elif network:
        download(src['url'], target)
    else:
        raise ValueError('Explicit network preparation required for this wheel')
    info = wheel_info(target)
    if not sha or info['sha256'] != sha or info['name'] != state.key(row['name']) or info['version'] != row['version']:
        raise ValueError('Prepared wheel identity/hash mismatch')
    if not parse_wheel_filename(target.name)[3].intersection(sys_tags()):
        raise ValueError('Wheel tags are incompatible with the current interpreter')
    with zipfile.ZipFile(target) as archive:
        unpacked_bytes = sum(item.file_size for item in archive.infolist())
        if unpacked_bytes > 16 * 1024 ** 3:
            raise ValueError('Unpacked wheel exceeds V1 preparation limit')
        for item in archive.infolist():
            name = item.filename.rstrip('/')
            files.safe_parts(name)
            parts = PurePosixPath(name).parts
            installed_root = parts[2] if len(parts) > 2 and parts[0].endswith('.data') and parts[1] in ('purelib', 'platlib') else parts[0]
            if installed_root.lower() == 'pip' or installed_root.lower().startswith('pip-') and installed_root.endswith('.dist-info'):
                raise ValueError('Wheel cannot replace protected pip tooling')
            if 'scripts' in parts and (parts[-1].lower().startswith('pip') or re.fullmatch(r'python[\d.]*w?(?:\.exe)?', parts[-1].lower())):
                raise ValueError('Wheel cannot replace interpreter/pip launchers')
            if stat.S_ISLNK(item.external_attr >> 16) or any(p.endswith('.data') for p in parts[:-1]) and not any(p in ('purelib', 'platlib', 'scripts') for p in parts):
                raise ValueError('Unsupported wheel data path or link')
            if item.filename.endswith('.dist-info/METADATA'):
                from email.parser import BytesParser
                requires = BytesParser().parsebytes(archive.read(item)).get('Requires-Python')
                if requires and not SpecifierSet(requires).contains('.'.join(map(str, sys.version_info[:3]))):
                    raise ValueError('Wheel Requires-Python does not support this interpreter')
    provenance_store = str(store) if row.get('artifact_ref') else (row.get('restore_artifact') or {}).get('store', str(store))
    return {'path': str(target), 'sha256': sha, 'source': copy.deepcopy(src), 'name': row['name'],
            'version': row['version'], 'store': provenance_store, 'unpacked_bytes': unpacked_bytes}


def prepare_code(row, manifest, store, folder, current_path, network):
    folder.mkdir(parents=True)
    content = folder / 'content'
    artifact = manifest['artifacts'].get(row.get('artifact_ref'))
    repo = None
    if row['source']['kind'] == 'git':
        if row['source'].get('has_submodules'):
            raise ValueError('Submodule restoration is not supported by V1 executor')
        repo = folder / 'repository'
        fetch_git(row, repo, current_path, network)
    if artifact:
        files.restore(artifact, store, content)
    elif repo:
        expected = diff.tree(row, manifest)
        content.mkdir()
        for relative, entry in expected.items():
            destination = content.joinpath(*PurePosixPath(relative).parts)
            origin = repo.joinpath(*PurePosixPath(relative).parts)
            files.no_links(origin)
            if entry['type'] == 'directory':
                destination.mkdir(parents=True, exist_ok=True)
            else:
                if files.hash_file(origin) != entry['sha256']:
                    raise ValueError('Git source bytes do not match target snapshot')
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(origin, destination)
    else:
        raise ValueError('No reliable target content')
    return {'content': str(content), 'repository': str(repo) if repo else None}


class Journal:
    def __init__(self, job, core, manager):
        self.job, self.core, self.manager = Path(job), Path(core), Path(manager)
        self.path = self.job / 'journal.json'
        self.steps = []
        (self.job / 'backup').mkdir(exist_ok=True)

    def permitted(self, path):
        path = inside(self.core, path)
        if path == self.manager or path.is_relative_to(self.manager) or self.manager.is_relative_to(path):
            raise ValueError('Protected manager path cannot be modified')
        relative = path.relative_to(self.core)
        if any(p.casefold() in state.SCOPE['excluded_directories'] for p in relative.parts):
            raise ValueError('Unmanaged user content cannot be modified')
        return path

    def replace(self, path, source=None):
        path = self.permitted(path)
        if source is not None:
            source = inside(self.job / 'payload', source)
        old = inventory(path)
        if source is None and old is None:
            return
        backup = self.job / 'backup' / str(len(self.steps))
        step = {'path': str(path), 'backup': str(backup), 'old': old,
                'new': inventory(source) if source else None, 'done': False}
        self.steps.append(step)
        atomic_json(self.path, self.steps)
        if old is not None:
            path.rename(backup)
        if source is not None:
            if source.is_dir():
                shutil.copytree(source, path)
            else:
                shutil.copy2(source, path)
        step['done'] = True
        atomic_json(self.path, self.steps)

    def directory(self, path, mode=0o755):
        path = self.permitted(path)
        if not path.exists():
            self.replace(path, self.job / 'payload' / 'empty-directory')
        if not path.is_dir():
            raise ValueError('Expected directory')
        self.chmod(path, mode)

    def chmod(self, path, mode):
        path = self.permitted(path)
        old = stat.S_IMODE(path.stat().st_mode)
        if files.permission_mode(old) == files.permission_mode(mode):
            return
        self.steps.append({'path': str(path), 'chmod': old, 'done': False})
        atomic_json(self.path, self.steps)
        path.chmod(mode)
        self.steps[-1]['done'] = True
        atomic_json(self.path, self.steps)

    def rollback(self):
        for step in reversed(self.steps):
            self.rollback_step(step)

    def rollback_step(self, step):
        path = self.permitted(step['path'])
        if 'chmod' in step:
            if path.exists():
                path.chmod(step['chmod'])
            return
        backup = inside(self.job / 'backup', step['backup'])
        if not backup.exists() and step['old'] is not None:
            if inventory(path) == step['old']:
                return  # Write-ahead entry, move had not happened.
            raise ValueError('Rollback original missing; retained journal requires manual review')
        if path.exists():
            if inventory(path) != step['new']:
                raise ValueError('Rollback refused: destination changed after worker write')
            # Move worker-created data into task space, never recursively delete live directories.
            discard = self.job / 'backup' / ('discard-' + str(self.steps.index(step)))
            path.rename(discard)
        if backup.exists():
            backup.rename(path)


def apply_code(journal, action, current, target, payload):
    row = target or current
    relative = row['relative_path']
    root = journal.core if row['kind'] == 'core' else journal.permitted(journal.core / relative)
    if target is None or row['kind'] == 'configuration' and not target['present']:
        journal.replace(root)
        return
    prepared = Path(payload['content'])
    if row['kind'] == 'configuration' or prepared.is_file():
        journal.replace(root, prepared)
        return
    expected = diff.tree(target, action['target_manifest'])
    actual = diff.tree(current, action['current_manifest']) if current else {}
    if root.is_file():
        journal.replace(root)
        actual = {}
    # Type replacements/deletions, deepest first. Never remove a directory containing excluded contents.
    for rel, entry in sorted((actual or {}).items(), key=lambda item: len(PurePosixPath(item[0]).parts), reverse=True):
        if not rel:
            continue
        desired = expected.get(rel)
        path = root / rel
        if desired is None or desired['type'] != entry['type']:
            if path.is_dir() and any(path.iterdir()):
                raise ValueError('Unmanaged/residual directory content prevents removal')
            journal.replace(path)
    if root != journal.core:
        journal.directory(root)
    for rel, entry in sorted(expected.items(), key=lambda item: len(PurePosixPath(item[0]).parts)):
        destination = root / rel
        if entry['type'] == 'directory':
            if destination != journal.core:
                journal.directory(destination)
        elif (actual or {}).get(rel) != entry:
            journal.replace(destination, prepared / rel)
    for rel, entry in sorted(expected.items(), key=lambda item: len(PurePosixPath(item[0]).parts), reverse=True):
        if root / rel != journal.core:
            journal.chmod(root / rel, entry['mode'])
    if payload.get('repository'):
        journal.replace(root / '.git', Path(payload['repository']) / '.git')
    elif not payload.get('preserve_repository') and current and current['source']['kind'] == 'git' and (root / '.git').exists():
        journal.replace(root / '.git')
