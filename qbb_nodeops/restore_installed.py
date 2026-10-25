"""Local ZIP preparation and bounded installed-file transactions; no pip or builds."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import sysconfig
import uuid
import zipfile

from . import file_snapshots as files, local_archives, restore_materials as material
from .rebuild_executor import atomic_json


def layout():
    root = Path(sys.prefix).resolve()
    libraries = sorted({str(Path(sysconfig.get_path(k)).resolve()) for k in ('purelib', 'platlib')})
    scripts = Path(sysconfig.get_path('scripts')).resolve()
    for path in [root, scripts, *(Path(p) for p in libraries)]:
        files.no_links(path)
        if ((path == root or path.exists()) and not path.is_dir()) or not path.is_relative_to(root):
            raise ValueError('Interpreter install layout is outside the current runtime')
    return {'root': str(root), 'libraries': libraries, 'scripts': str(scripts)}


def destination(relative, runtime_layout, manager, *, directory=False):
    parts = files.safe_parts(relative)
    root = Path(runtime_layout['root'])
    path = material.inside(root, root.joinpath(*parts))
    libraries = [Path(p) for p in runtime_layout['libraries']] + [root / folder for folder in local_archives.PACKAGE_DATA_DIRS]
    scripts = Path(runtime_layout['scripts'])
    allowed = any(path != lib and path.is_relative_to(lib) for lib in libraries) or not directory and (path.parent == scripts or local_archives.runtime_package_dll(path, root))
    if directory:
        allowed |= any(path == lib or lib.is_relative_to(path) for lib in libraries) or path == scripts or scripts.is_relative_to(path)
    if not allowed:
        raise ValueError('Installed destination outside interpreter install roots: ' + relative)
    manager = Path(manager)
    if path == manager or path.is_relative_to(manager) or manager.is_relative_to(path):
        raise ValueError('Installed destination overlaps protected manager')
    for library in libraries:
        if path.is_relative_to(library):
            relative_parts = path.relative_to(library).parts
            first = relative_parts[0].casefold() if relative_parts else ''
            if first == 'pip' or first.startswith('pip-') and first.endswith('.dist-info'):
                raise ValueError('Installed destination overlaps protected pip tooling')
    name = path.name.casefold()
    if re.fullmatch(r'python[\d.]*w?(?:\.exe)?', name) or path.parent == scripts and name.startswith('pip'):
        raise ValueError('Installed destination overlaps protected interpreter launcher')
    return path


def file_state(path):
    path = Path(path)
    files.no_links(path)
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError('Installed destination is not a regular file: ' + str(path))
    signature = files.signature(path.stat())
    return {'sha256': files.stable_hash(path, signature), 'size': signature[0],
            'mode': files.permission_mode(signature[4])}


def entry_state(entry):
    return {'sha256': entry['sha256'], 'size': entry['size'], 'mode': files.permission_mode(entry['mode'])}


def copy_checked(source, target, expected):
    source, target = Path(source), Path(target)
    files.no_links(source)
    files.no_links(target)
    signature = files.signature(source.stat())
    digest, size = hashlib.sha256(), 0
    with source.open('rb') as src, target.open('xb') as dst:
        if files.signature(os.fstat(src.fileno())) != signature:
            raise ValueError('Source changed before preparing rollback material')
        while block := src.read(files.CHUNK):
            size += len(block)
            if size > expected['size']:
                raise ValueError('Source grew during preparation')
            digest.update(block)
            dst.write(block)
        dst.flush()
        os.fsync(dst.fileno())
        if files.signature(os.fstat(src.fileno())) != signature:
            raise ValueError('Source changed during preparation')
    if files.signature(source.stat()) != signature or size != expected['size'] or digest.hexdigest() != expected['sha256']:
        raise ValueError('Source content changed during preparation')
    target.chmod(signature[4])
    if file_state(target) != expected:
        raise ValueError('Prepared file does not match expected bytes and permissions')


def extract_archive(row, manifest, store, folder):
    """Copy and validate the ZIP in task space, then stream only expected members."""
    index = local_archives.record_index(manifest, row)
    archive_entry = manifest['artifacts'][row['archive_ref']]['files'][0]
    required = archive_entry['size'] + sum(e['size'] for e in index['files']) + 64 * files.CHUNK
    folder.mkdir(parents=True)
    if shutil.disk_usage(folder).free < required:
        raise ValueError('Insufficient free space for ZIP preparation')
    archive_path = folder / 'component.zip'
    copy_checked(files.object_path(store, archive_entry['sha256']), archive_path, entry_state(archive_entry))
    local_archives.verify_zip(archive_path, row['id'], index, archive_entry)
    archive_signature = files.signature(archive_path.stat())
    content = folder / 'content'
    content.mkdir()
    with zipfile.ZipFile(archive_path) as archive:
        for entry in index['files']:
            target = material.inside(content, content.joinpath(*files.safe_parts(entry['path'])))
            target.parent.mkdir(parents=True, exist_ok=True)
            digest, size = hashlib.sha256(), 0
            with archive.open(entry['path']) as src, target.open('xb') as dst:
                while block := src.read(files.CHUNK):
                    size += len(block)
                    if size > entry['size']:
                        raise ValueError('ZIP member grew during extraction')
                    digest.update(block)
                    dst.write(block)
                dst.flush()
                os.fsync(dst.fileno())
            target.chmod(entry['mode'])
            if size != entry['size'] or digest.hexdigest() != entry['sha256'] or file_state(target) != entry_state(entry):
                raise ValueError('Extracted member does not match saved inventory')
        for entry in index.get('directories', []):
            path = content if not entry['path'] else material.inside(content, content.joinpath(*files.safe_parts(entry['path'])))
            path.mkdir(parents=True, exist_ok=True)
        for entry in sorted(index.get('directories', []), key=lambda e: len(e['path']), reverse=True):
            (content / entry['path']).chmod(entry['mode'])
    if files.stable_hash(archive_path, archive_signature) != archive_entry['sha256']:
        raise ValueError('Prepared ZIP changed during extraction')
    return content


def prepare_code(row, manifest, store, folder, recovery_material):
    if recovery_material['kind'] == 'local_zip':
        content = extract_archive(row, manifest, store, folder)
        snapshot = manifest['artifacts'][row['artifact_ref']]
        if snapshot['single_file']:
            content = content / snapshot['files'][0]['path']
    else:
        folder.mkdir(parents=True)
        content = folder / 'content'
        files.restore(manifest['artifacts'][row['artifact_ref']], store, content)
    return {'content': str(content), 'repository': None, 'preserve_repository': True}


def package_specs(actions, current, target, runtime_layout, manager):
    now = {r['id'].casefold(): r for r in current['resources']}
    goals = {r['id'].casefold(): r for r in target['resources']}
    owners = {}
    for row in current['resources']:
        for entry in row.get('installed_inventory', {}).get('files', []):
            owners.setdefault(entry['path'].casefold(), set()).add(row['id'].casefold())
    specs, target_owners = {}, {}
    for action in actions:
        if action['kind'] != 'package' or action['material']['kind'] not in ('local_zip', 'installed_file_removal'):
            continue
        identity = action['resource_id'].casefold()
        a, b = now.get(identity), goals.get(identity)
        if a and (not a.get('installed_inventory') or a['installed_inventory']['root_at_capture'] != runtime_layout['root']):
            raise ValueError('Current installed inventory does not belong to this interpreter')
        if a and any(g.get('missing') is not True for g in a['installed_inventory']['gaps']):
            raise ValueError('Current installed inventory has unknown gaps; rollback cannot be prepared')
        if b and (b.get('archive_content') != 'installed_files_and_metadata' or b['installed_inventory']['gaps']):
            raise ValueError('Target ZIP does not contain complete installed files')
        old = {e['path']: e for e in a['installed_inventory']['files']} if a else {}
        new = {e['path']: e for e in b['installed_inventory']['files']} if b else {}
        before = {}
        for relative in old.keys() | new.keys():
            path = destination(relative, runtime_layout, manager)
            for parent in path.parents:
                if parent.exists() and not parent.is_dir():
                    parent_rel = parent.relative_to(runtime_layout['root']).as_posix()
                    if parent_rel not in old or parent_rel in new:
                        raise ValueError('Installed parent is not a managed replaceable directory: ' + parent_rel)
            actual = file_state(path)
            if relative in old and actual != entry_state(old[relative]):
                raise ValueError('Installed file changed since comparison: ' + relative)
            if relative in new:
                foreign = owners.get(relative.casefold(), set()) - {identity}
                if foreign or actual is not None and relative not in old:
                    raise ValueError('Target file is shared or belongs to unrecorded content: ' + relative)
                if relative.casefold() in target_owners:
                    raise ValueError('Multiple target packages write the same file: ' + relative)
                target_owners[relative.casefold()] = identity
            before[relative] = actual
        bytecode, cache_patterns = [], {}
        for relative in old.keys() | new.keys():
            if not relative.endswith('.py') or relative in old and relative in new and entry_state(old[relative]) == entry_state(new[relative]):
                continue
            module = destination(relative, runtime_layout, manager)
            cache = module.parent / '__pycache__'
            files.no_links(cache)
            cache_patterns[relative] = sorted(p.name for p in cache.glob(module.stem + '.*.pyc')) if cache.is_dir() else []
            if cache.is_dir():
                for path in sorted(cache.glob(module.stem + '.*.pyc')):
                    rel = path.relative_to(runtime_layout['root']).as_posix()
                    destination(rel, runtime_layout, manager)
                    before[rel] = file_state(path)
                    bytecode.append(rel)
        metadata_trees = {}
        new_metadata = {str(Path(r).parents[0]) for r in new if '.dist-info/' in r}
        for relative in old:
            parts = Path(relative).parts
            for offset, part in enumerate(parts[:-1]):
                if not part.casefold().endswith('.dist-info'):
                    continue
                root_rel = '/'.join(parts[:offset + 1])
                if root_rel in new_metadata or root_rel in metadata_trees:
                    continue
                path = destination(root_rel, runtime_layout, manager, directory=True)
                tree = material.inventory(path)
                if tree is not None:
                    for name, entry in tree['tree'].items():
                        rel = root_rel + '/' + name
                        owned = any(p.startswith(rel + '/') for p in before) if entry['content'] == 'directory' else rel in before
                        if not owned:
                            raise ValueError('Unrecorded metadata contents prevent bounded package removal: ' + rel)
                    metadata_trees[root_rel] = tree
        specs[identity] = {'current_files': list(old.values()), 'target_files': list(new.values()),
                           'before': before, 'bytecode': bytecode, 'metadata_trees': metadata_trees,
                           'cache_patterns': cache_patterns}
    names = set(target_owners)
    if any('/'.join(name.split('/')[:i]) in names for name in names for i in range(1, len(name.split('/')))):
        raise ValueError('Target package file cannot be another target file parent')
    return specs


def prepare_package(row, manifest, store, folder, spec, runtime_layout, manager):
    before = spec['before']
    required = sum(e['size'] for e in before.values() if e) + sum(e['size'] for e in spec['target_files']) + 64 * files.CHUNK
    folder.mkdir(parents=True)
    if shutil.disk_usage(folder).free < required:
        raise ValueError('Insufficient free space for installed-file rollback preparation')
    rollback = folder / 'rollback'
    rollback.mkdir()
    for relative, evidence in before.items():
        if evidence is not None:
            path = rollback.joinpath(*files.safe_parts(relative))
            path.parent.mkdir(parents=True, exist_ok=True)
            copy_checked(destination(relative, runtime_layout, manager), path, evidence)
    content = extract_archive(row, manifest, store, folder / 'target') if row else None
    return {**spec, 'content': str(content) if content else None, 'rollback': str(rollback),
            'bytes': required - 64 * files.CHUNK}


def verify_before(items, runtime_layout, manager):
    if runtime_layout != layout():
        raise ValueError('Interpreter install roots changed since preparation')
    for item in items:
        for relative, expected in item.get('file_restore', {}).get('cache_patterns', {}).items():
            module = destination(relative, runtime_layout, manager)
            cache = module.parent / '__pycache__'
            files.no_links(cache)
            actual = sorted(p.name for p in cache.glob(module.stem + '.*.pyc')) if cache.is_dir() else []
            if actual != expected:
                raise ValueError('Installed bytecode inventory changed since preparation')
        for relative, expected in item.get('file_restore', {}).get('metadata_trees', {}).items():
            if material.inventory(destination(relative, runtime_layout, manager, directory=True)) != expected:
                raise ValueError('Package metadata directory changed since preparation')
        for relative, expected in item.get('file_restore', {}).get('before', {}).items():
            if file_state(destination(relative, runtime_layout, manager)) != expected:
                raise ValueError('Installed file or bytecode changed since preparation: ' + relative)


def remove_expected(path, expected):
    if file_state(path) != expected:
        raise ValueError('Installed destination changed before removal')
    mode = path.stat().st_mode
    try:
        if os.name == 'nt':
            path.chmod(stat.S_IMODE(mode) | stat.S_IWRITE)
        path.unlink()
    except OSError:
        if path.exists() and file_state(path)['sha256'] == expected['sha256']:
            path.chmod(stat.S_IMODE(mode))
        raise


def atomic_copy(source, path, expected_old):
    expected_new = file_state(source)
    temporary = path.parent / ('.nodeops-restore-' + uuid.uuid4().hex)
    old_mode = path.stat().st_mode if path.exists() else None
    mode_changed = False
    try:
        copy_checked(source, temporary, expected_new)
        if file_state(path) != expected_old:
            raise ValueError('Installed destination changed before replacement')
        if os.name == 'nt' and old_mode is not None:
            path.chmod(stat.S_IMODE(old_mode) | stat.S_IWRITE)
            mode_changed = True
        os.replace(temporary, path)
        if file_state(path) != expected_new:
            raise ValueError('Installed replacement failed verification')
    except Exception:
        if mode_changed and expected_old is not None and path.exists() and file_state(path)['sha256'] == expected_old['sha256']:
            path.chmod(stat.S_IMODE(old_mode))
        raise
    finally:
        if temporary.exists():
            temporary.chmod(stat.S_IWRITE | stat.S_IREAD)
            temporary.unlink()


class Journal(material.Journal):
    def __init__(self, job, core, manager, runtime_layout=None, specs=()):
        super().__init__(job, core, manager)
        self.layout = runtime_layout
        self.package_files = set()
        self.package_directories = set()
        self.expected_files = {}
        for spec in specs:
            for relative in spec['before']:
                path = destination(relative, runtime_layout, manager)
                self.package_files.add(path)
                self.expected_files[path] = spec['before'][relative]
                for parent in path.parents:
                    if not parent.is_relative_to(runtime_layout['root']) or parent == Path(runtime_layout['root']):
                        continue
                    if any(parent != Path(lib) and parent.is_relative_to(lib) for lib in runtime_layout['libraries']) or not parent.exists() and (
                            parent == Path(runtime_layout['scripts']) or Path(runtime_layout['scripts']).is_relative_to(parent) or any(Path(lib).is_relative_to(parent) for lib in runtime_layout['libraries'])):
                        self.package_directories.add(parent)

    def permitted(self, path):
        path = Path(os.path.abspath(path))
        if path in self.package_files or path in self.package_directories:
            return destination(path.relative_to(self.layout['root']).as_posix(), self.layout, self.manager,
                               directory=path in self.package_directories)
        return super().permitted(path)

    def replace(self, path, source=None):
        path = self.permitted(path)
        if path in self.package_directories and path.is_dir() and source is None:
            old = material.inventory(path)
            if old is None:
                return
            if old['tree']:
                raise ValueError('Package directory still contains unmanaged/residual files')
            step = {'path': str(path), 'old': old, 'new': None, 'directory_removed': True, 'done': False}
            self.steps.append(step)
            atomic_json(self.path, self.steps)
            path.rmdir()
            step['done'] = True
            atomic_json(self.path, self.steps)
            return
        if path not in self.package_files:
            return super().replace(path, source)
        if path in self.package_directories and source is not None and Path(source).is_dir():
            return super().replace(path, source)
        if source is not None:
            source = material.inside(self.job / 'payload', source)
        old, new = file_state(path), file_state(source) if source else None
        if old != self.expected_files[path]:
            raise ValueError('Installed destination changed after preflight; refusing overwrite')
        if old is None and new is None:
            return
        backup = self.job / 'backup' / str(len(self.steps))
        step = {'path': str(path), 'backup': str(backup), 'old': old, 'new': new,
                'file_only': True, 'backup_ready': False, 'done': False}
        self.steps.append(step)
        atomic_json(self.path, self.steps)
        if old is not None:
            copy_checked(path, backup, old)
        step['backup_ready'] = True
        atomic_json(self.path, self.steps)
        if source is None:
            remove_expected(path, old)
        else:
            atomic_copy(source, path, old)
        step['done'] = True
        self.expected_files[path] = new
        atomic_json(self.path, self.steps)

    def rollback_step(self, step):
        path = self.permitted(step['path'])
        if step.get('directory_removed'):
            if not path.exists():
                path.mkdir()
                path.chmod(step['old']['mode'])
            elif material.inventory(path) != step['old']:
                raise ValueError('Rollback refused: removed package directory changed')
        elif step.get('file_only'):
            actual = file_state(path)
            if actual == step['old']:
                return
            if actual != step['new']:
                raise ValueError('Rollback refused: destination changed after worker write')
            if step['old'] is None:
                remove_expected(path, actual)
            else:
                backup = material.inside(self.job / 'backup', step['backup'])
                if not step['backup_ready'] or file_state(backup) != step['old']:
                    raise ValueError('Rollback original missing or corrupt; staging retained')
                atomic_copy(backup, path, actual)
            step['rolled_back'] = True
            atomic_json(self.path, self.steps)
        elif path in self.package_directories and step.get('old') is None and 'chmod' not in step:
            if path.exists():
                if material.inventory(path) != step['new']:
                    raise ValueError('Rollback refused: created package directory changed')
                path.rmdir()
        else:
            super().rollback_step(step)


def apply_package(journal, spec):
    old = {e['path']: e for e in spec['current_files']}
    new = {e['path']: e for e in spec['target_files']}
    for relative in [*spec['bytecode'], *(r for r in old if r not in new)]:
        journal.replace(destination(relative, journal.layout, journal.manager))
    for relative, entry in new.items():
        path = destination(relative, journal.layout, journal.manager)
        if relative in old and entry_state(old[relative]) == entry_state(entry):
            continue
        parents = []
        parent = path.parent
        while not parent.exists():
            parents.append(parent)
            parent = parent.parent
        if not parent.is_dir():
            raise ValueError('Installed parent is not a directory')
        for parent in reversed(parents):
            journal.directory(parent)
        journal.replace(path, Path(spec['content']).joinpath(*files.safe_parts(relative)))
    directories = {p for relative in [*old, *spec['bytecode']] for p in destination(relative, journal.layout, journal.manager).parents if p in journal.package_directories}
    for path in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        if path.exists() and not any(path.iterdir()):
            journal.replace(path)
