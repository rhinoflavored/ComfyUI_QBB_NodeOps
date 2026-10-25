"""Streaming immutable file snapshots; format 2 adds directories and capture policy."""
import hashlib
import os
import re
import stat
import tempfile
import sqlite3
import time
from contextlib import contextmanager, closing
from pathlib import Path, PurePosixPath

CHUNK = 1024 * 1024
MAX_FILE = 256 * CHUNK
EXCLUDED = {'.git', '__pycache__', '.venv', 'venv', 'node_modules', '.pytest_cache'}
CORE_EXCLUDED = {'models', 'input', 'output', 'temp', 'user', 'custom_nodes'}


@contextmanager
def sqlite_snapshot(path):
    """Bounded SQLite online backup, including committed WAL transactions."""
    no_links(path)
    with tempfile.TemporaryDirectory(prefix='nodeops-sqlite-') as temporary:
        target = Path(temporary) / 'backup.db'
        deadline = time.monotonic() + 30
        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise ValueError('数据库备份超时，请暂停写入后重试：' + str(path))
        try:
            with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as source:
                with closing(sqlite3.connect(target)) as destination:
                    source.backup(destination, pages=256, progress=progress, sleep=0.05)
                    if destination.execute('PRAGMA quick_check').fetchone() != ('ok',):
                        raise ValueError('数据库完整性检查失败：' + str(path))
            yield target
        except sqlite3.Error as exc:
            raise ValueError('数据库一致性备份失败：' + str(path) + '：' + str(exc)) from exc
NODEOPS_DATABASES = {
    'qbb_nodeops_market.sqlite3': 'NodeOps 市场缓存：不纳入环境版本，恢复后可重新生成',
    'qbb_nodeops_usage.sqlite3': 'NodeOps 当前路径的运行统计：不纳入环境版本，原实例数据保留，新实例重新统计',
}


def linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def no_links(path):
    for item in [Path(path), *Path(path).parents]:
        if item.is_symlink() or (item.exists() and linked(item)):
            raise ValueError('Linked path is not allowed: ' + str(item))


def safe_parts(value):
    parts = PurePosixPath(value).parts
    if not parts or '/'.join(parts) != value or any(
        p in ('.', '..') or re.search(r'[\\\\:<>"|?*\x00-\x1f]', p)
        or p.endswith((' ', '.')) or re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p)
        for p in parts
    ) or value.startswith('/'):
        raise ValueError('Unsafe snapshot path: ' + value)
    return parts


def object_path(store, digest):
    if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
        raise ValueError('Invalid object hash')
    result = Path(store) / 'objects' / digest[:2] / digest[2:]
    no_links(result)
    return result


def permission_mode(mode):
    """Compare only chmod permissions supported by the destination platform."""
    if not isinstance(mode, int) or isinstance(mode, bool) or not 0 <= mode <= 0o7777:
        raise ValueError('记录缺少有效权限信息，无法核实权限')
    return mode & (stat.S_IWRITE if os.name == 'nt' else 0o777)


def signature(info):
    # Windows stat infers executable bits from the filename, fstat cannot.
    # They are not real Windows permission changes; retain read/write bits.
    mode=stat.S_IMODE(info.st_mode)
    if os.name=='nt':mode &= ~0o111
    return [info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino, mode]


def scan(source, store, core=False, large_files='exclude', core_dirs=None, runtime_files=False, managed_v1=False, tracked_configs=None, read_only=False):
    """Metadata-only estimate. Read errors block publication, never silently skip."""
    if large_files not in ('exclude', 'include', 'error'):
        raise ValueError('Invalid large-file policy')
    if type(runtime_files)!=bool:raise ValueError('Invalid runtime capture policy')
    core_dirs = [] if core_dirs is None else core_dirs
    if not isinstance(core_dirs,list) or any(d not in ('user','models','input','output') for d in core_dirs):
        raise ValueError('Invalid capture scope')
    source, store = Path(source), Path(store)
    files, directories, skipped, errors = [], [], [], []
    seen = set()
    single = source.is_file()
    # Recognize the plugin layout, including renamed/restored copies. Never exempt
    # another plugin's database merely because it has the same filename.
    nodeops = not core and not runtime_files and all((source / name).is_file() for name in (
        '__init__.py', 'qbb_nodeops/health.py', 'qbb_nodeops/market_index.py', 'qbb_nodeops/file_snapshots.py'))
    runtime_excluded = set()
    if nodeops:
        for name, reason in NODEOPS_DATABASES.items():
            runtime_excluded.update(name + suffix for suffix in ('', '-wal', '-shm', '-journal'))
            # Declare the policy even when absent: a live WAL appearing/disappearing
            # must not invalidate a preview of the unchanged environment files.
            skipped.append({'path': name, 'reason': reason + '（含 -wal/-shm/-journal 事务文件）'})
    def visit(path, relative):
        try:
            info = path.lstat()
            if linked(path):
                skipped.append({'path': relative or '.', 'reason': 'link/reparse point'})
                return
            if relative:
                safe_parts(relative)
                key = relative.casefold()
                if key in seen:
                    raise ValueError('Case-insensitive duplicate path: ' + relative)
                seen.add(key)
            if stat.S_ISDIR(info.st_mode):
                if path.resolve() == store.resolve():
                    skipped.append({'path': relative or '.', 'reason': 'record storage'})
                    return
                directories.append({'path': relative, 'mode': stat.S_IMODE(info.st_mode), 'identity': info.st_ino})
                for child in sorted(path.iterdir()):
                    rel = child.name if not relative else relative + '/' + child.name
                    if managed_v1 and child.suffix.lower() in {'.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.db', '.sqlite', '.sqlite3'} and rel not in (tracked_configs or []) and child.name not in {'pyproject.toml', 'package.json', 'package-lock.json', 'setup.cfg'}:
                        skipped.append({'path': rel, 'reason': 'configuration_not_allowlisted'})
                        continue
                    if managed_v1 and (child.name.casefold() in {'models', 'input', 'output', 'workflow', 'workflows', 'user', 'temp'} or child.suffix.lower() in {'.safetensors', '.ckpt', '.pt', '.pth', '.gguf'}):
                        skipped.append({'path': rel, 'reason': 'outside_scope_v1'})
                        continue
                    if managed_v1 and core and not relative and child.name == 'extra_model_paths.yaml':
                        skipped.append({'path': rel, 'reason': 'separate_configuration'})
                        continue
                    if not relative and child.name in runtime_excluded and not child.is_dir():
                        continue
                    if child.name in ({'__pycache__'} if runtime_files else EXCLUDED) or (core and not relative and child.name in CORE_EXCLUDED and child.name not in core_dirs):
                        skipped.append({'path': rel, 'reason': 'excluded cache/environment/assets'})
                    else:
                        visit(child, rel)
                return
            if not stat.S_ISREG(info.st_mode):
                skipped.append({'path': relative, 'reason': 'special file'})
                return
            if info.st_size > MAX_FILE and large_files != 'include':
                if large_files == 'error':
                    raise ValueError('File exceeds 256 MiB; change large-file policy: ' + relative)
                skipped.append({'path': relative, 'reason': 'larger than 256 MiB', 'size': info.st_size})
                return
            # A raw copy cannot establish a transaction-consistent live database.
            # Refuse rather than silently publish only a main file or WAL fragment.
            with path.open('rb') as probe:
                header = probe.read(16)
            if path.name.lower().endswith(('-wal','-shm','-journal')):
                main = path.with_name(path.name.rsplit('-', 1)[0])
                if main.is_file() and not linked(main):
                    with main.open('rb') as stream:
                        if stream.read(16) == b'SQLite format 3\x00':
                            return  # SQLite backup incorporates committed transactions.
                raise ValueError('数据库或事务日志不能直接快照；请使用数据库工具制作离线备份并单独保管，或取消包含此文件的目录：' + relative)
            entry = {'path': relative, 'signature': signature(info)}
            if header == b'SQLite format 3\x00':
                if read_only:
                    raise ValueError('SQLite transaction state is unknown in read-only comparison; database not opened')
                with sqlite_snapshot(path) as backup:
                    entry['sqlite_sha256'] = hash_file(backup)
                    entry['sqlite_bytes'] = backup.stat().st_size
            files.append(entry)
        except (OSError, ValueError) as exc:
            errors.append({'path': relative or '.', 'reason': str(exc)})
    try:
        no_links(source.parent)
        visit(source, source.name if single else '')
    except (OSError, ValueError) as exc:
        errors.append({'path': '.', 'reason': str(exc)})
    return {'single_file': single, 'files': files, 'directories': directories,
            'skipped': skipped, 'errors': errors, 'bytes': sum(f.get('sqlite_bytes', f['signature'][0]) for f in files),
            'policy': dict({'core': core, 'large_files': large_files, 'core_dirs':sorted(set(core_dirs)), 'runtime_files':runtime_files}, **({'managed_v1': True, 'tracked_configs': tracked_configs or []} if managed_v1 else {}))}


def assert_same(source, store, baseline):
    policy = baseline['policy']
    current = scan(source, store, **policy)
    if current != {k:v for k,v in baseline.items() if k != 'content_hashes'}:
        raise ValueError('Files changed since preview/capture. Stop updates and preview again: ' + str(source))


def stable_hash(path, expected):
    no_links(path)
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        if signature(os.fstat(stream.fileno())) != expected:
            raise ValueError('File changed before hashing: ' + str(path))
        for block in iter(lambda: stream.read(CHUNK), b''):
            digest.update(block)
        if signature(os.fstat(stream.fileno())) != expected:
            raise ValueError('File changed during hashing: ' + str(path))
    no_links(path)
    if signature(Path(path).stat()) != expected:
        raise ValueError('File changed after hashing: ' + str(path))
    return digest.hexdigest()


def plan_content(source, baseline):
    """Hash sources without publishing objects; storage state is not source identity."""
    source = Path(source)
    hashes = {}
    for entry in baseline['files']:
        path = source if baseline['single_file'] else source.joinpath(*safe_parts(entry['path']))
        try:
            hashes[entry['path']] = entry.get('sqlite_sha256') or stable_hash(path, entry['signature'])
        except (OSError, ValueError) as exc:
            baseline['errors'].append({'path':entry['path'], 'reason':str(exc)})
    baseline['content_hashes'] = hashes


def storage_plan(scans, store):
    """Deduplicate across components and verify reusable objects before estimating."""
    refs = {}; logical = 0; sqlite_temporary = 0
    for scan in scans:
        if scan is None:
            continue
        if scan['errors']:
            raise ValueError('Resolve file scan errors before saving')
        for entry in scan['files']:
            size = entry.get('sqlite_bytes', entry['signature'][0])
            digest = scan['content_hashes'][entry['path']]
            if digest in refs and refs[digest] != size:
                raise ValueError('Conflicting object sizes')
            refs[digest] = size; logical += size
            sqlite_temporary = max(sqlite_temporary, entry.get('sqlite_bytes', 0))
    added = 0; reused = 0; largest = 0
    for digest, size in refs.items():
        obj = object_path(store, digest)
        if obj.exists():
            if obj.stat().st_size != size or hash_file(obj) != digest:
                raise ValueError('Existing snapshot object is corrupt: ' + digest)
            reused += size
        else:
            added += size; largest = max(largest, size)
    return {'logical_bytes':logical, 'unique_referenced_bytes':sum(refs.values()),
            'unique_objects':len(refs), 'new_bytes':added, 'reused_bytes':reused,
            'temporary_bytes':largest, 'sqlite_temporary_bytes':sqlite_temporary,
            'metadata_reserve_bytes':16*CHUNK, 'required_bytes':added + 16*CHUNK + sqlite_temporary}


def capture(source, store, core=False, large_files='exclude', expected=None, core_dirs=None, runtime_files=False, managed_v1=False, tracked_configs=None):
    source, store = Path(source), Path(store)
    baseline = scan(source, store, core, large_files, core_dirs, runtime_files, managed_v1, tracked_configs)
    if baseline['errors']:
        raise ValueError('Snapshot scan failed: ' + str(baseline['errors'][:5]))
    if expected is not None and baseline != {k:v for k,v in expected.items() if k != 'content_hashes'}:
        raise ValueError('Files changed since preview; preview again: ' + str(source))
    no_links(store)
    store.mkdir(parents=True, exist_ok=True)
    files = []
    for entry in baseline['files']:
        relative = entry['path']
        path = source if baseline['single_file'] else source.joinpath(*safe_parts(relative))
        no_links(path)
        temporary = None
        try:
            if 'sqlite_sha256' in entry:
                with sqlite_snapshot(path) as backup:
                    sha = hash_file(backup)
                    if sha != entry['sqlite_sha256']:
                        raise ValueError('数据库在预览后发生变化，请重新预览：' + relative)
                    obj = object_path(store, sha)
                    obj.parent.mkdir(parents=True, exist_ok=True)
                    count = backup.stat().st_size
                    if obj.exists():
                        if hash_file(obj) != sha:
                            raise ValueError('Existing snapshot object is corrupt: ' + sha)
                    else:
                        with tempfile.NamedTemporaryFile(dir=obj.parent, delete=False) as out, backup.open('rb') as src:
                            temporary = Path(out.name)
                            for block in iter(lambda: src.read(CHUNK), b''):
                                out.write(block)
                            out.flush(); os.fsync(out.fileno())
                        temporary.replace(obj)
                    files.append({'path': relative, 'sha256': sha, 'size': count, 'mode': entry['signature'][4],
                                  'mtime_ns': entry['signature'][1], 'capture_method': 'sqlite_backup'})
                continue
            sha = stable_hash(path, entry['signature'])
            if expected is not None and 'content_hashes' in expected and sha != expected['content_hashes'].get(relative):
                raise ValueError('File content changed since preview: ' + relative)
            obj = object_path(store, sha)
            count = entry['signature'][0]
            if obj.exists():
                if obj.stat().st_size != count or hash_file(obj) != sha:
                    raise ValueError('Existing snapshot object is corrupt: ' + sha)
                files.append({'path':relative, 'sha256':sha, 'size':count,
                              'mode':entry['signature'][4], 'mtime_ns':entry['signature'][1]})
                continue
            digest = hashlib.sha256()
            with path.open('rb') as src, tempfile.NamedTemporaryFile(dir=store, delete=False) as out:
                temporary = Path(out.name)
                if signature(os.fstat(src.fileno())) != entry['signature']:
                    raise ValueError('File changed before capture: ' + relative)
                count = 0
                for block in iter(lambda: src.read(CHUNK), b''):
                    count += len(block)
                    if count > entry['signature'][0]:
                        raise ValueError('File grew during capture: ' + relative)
                    digest.update(block)
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
                if signature(os.fstat(src.fileno())) != entry['signature']:
                    raise ValueError('File changed during capture: ' + relative)
            if signature(path.stat()) != entry['signature'] or linked(path) or count != entry['signature'][0]:
                raise ValueError('File changed during capture: ' + relative)
            if digest.hexdigest() != sha:
                raise ValueError('File content changed during capture: ' + relative)
            obj.parent.mkdir(parents=True, exist_ok=True)
            if obj.exists():
                if obj.stat().st_size != count or hash_file(obj) != sha:
                    raise ValueError('Existing snapshot object is corrupt: ' + sha)
            else:
                temporary.replace(obj)
            files.append({'path': relative, 'sha256': sha, 'size': count,
                          'mode': entry['signature'][4], 'mtime_ns': entry['signature'][1]})
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    assert_same(source, store, baseline)
    return {'format': 2, 'single_file': baseline['single_file'], 'files': files,
            'directories': [{'path': d['path'], 'mode': d['mode']} for d in baseline['directories']],
            'skipped': baseline['skipped'], 'bytes': baseline['bytes'],
            'policy': baseline['policy'], 'inventory': 'complete_within_policy',
            'consistency': 'metadata_checked_before_and_after_not_atomic'}


def hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            digest.update(block)
    return digest.hexdigest()


def verify_source(source, store, snapshot, baseline):
    """Second full content pass. Still not an atomic multi-file filesystem snapshot."""
    source = Path(source)
    assert_same(source, store, baseline)
    signatures = {entry['path']: entry['signature'] for entry in baseline['files']}
    for entry in snapshot['files']:
        path = source if snapshot.get('single_file') else source.joinpath(*safe_parts(entry['path']))
        no_links(path)
        expected = signatures[entry['path']]
        if entry.get('capture_method') == 'sqlite_backup':
            with sqlite_snapshot(path) as backup:
                if hash_file(backup) != entry['sha256']:
                    raise ValueError('数据库在保存期间发生变化，请暂停写入后重试：' + entry['path'])
            continue
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            if signature(os.fstat(stream.fileno())) != expected:
                raise ValueError('文件在最终校验前变化：' + entry['path'])
            for block in iter(lambda: stream.read(CHUNK), b''):
                digest.update(block)
            if signature(os.fstat(stream.fileno())) != expected:
                raise ValueError('文件在最终校验中变化：' + entry['path'])
        no_links(path)
        if signature(path.stat()) != expected or digest.hexdigest() != entry['sha256']:
            raise ValueError('文件内容在保存期间变化；未发布记录：' + entry['path'])
    assert_same(source, store, baseline)


def validate_snapshot(snapshot, store, verified=None):
    """Validate structure and all referenced bytes before materializing anything."""
    if type(snapshot.get('format')) is not int or snapshot.get('format') not in (1, 2):
        raise ValueError('Unsupported snapshot format')
    verified = {} if verified is None else verified
    entries = snapshot['files']
    directories = snapshot.get('directories', []) if snapshot['format'] == 2 else []
    names, folder_names = set(), set()
    for entry in entries:
        safe_parts(entry['path'])
        name = entry['path'].casefold()
        if name in names:
            raise ValueError('Duplicate snapshot paths')
        names.add(name)
        if type(entry['size']) is not int or entry['size'] < 0:
            raise ValueError('Invalid snapshot object size')
        permission_mode(entry.get('mode', 0o644))
        obj = object_path(store, entry['sha256'])
        if not obj.is_file() or obj.stat().st_size != entry['size']:
            raise ValueError('Missing or invalid snapshot object: ' + entry['path'])
        if entry['sha256'] in verified:
            if verified[entry['sha256']] != entry['size']:
                raise ValueError('Conflicting snapshot object sizes')
        else:
            if stable_hash(obj, signature(obj.stat())) != entry['sha256']:
                raise ValueError('Snapshot checksum mismatch: ' + entry['path'])
            verified[entry['sha256']] = entry['size']
    for directory in directories:
        rel = directory['path']
        if rel:
            safe_parts(rel)
        name = rel.casefold()
        if name in folder_names or name in names:
            raise ValueError('Conflicting snapshot directory')
        folder_names.add(name)
        permission_mode(directory.get('mode', 0o755))
    for name in names | folder_names:
        parts = name.split('/')
        if any('/'.join(parts[:i]) in names for i in range(1, len(parts))):
            raise ValueError('Snapshot file used as directory')
    if snapshot.get('single_file') and (len(entries) != 1 or directories):
        raise ValueError('Incomplete single-file snapshot')
    return {'status':'passed', 'files':len(entries), 'directories':len(directories),
            'logical_bytes':sum(e['size'] for e in entries)}


def verify_restored(snapshot, destination):
    """Read the materialized tree, not the bytes that were sent to the writer."""
    destination = Path(destination)
    no_links(destination)
    single = snapshot.get('single_file', False)
    if single != destination.is_file() or (not single and not destination.is_dir()):
        raise ValueError('Restored file/directory type mismatch')
    expected_files = {e['path'] for e in snapshot['files']}
    directories = snapshot.get('directories', []) if snapshot['format'] == 2 else []
    expected_dirs = {d['path'] for d in directories}
    if not single:
        expected_dirs.add('')
        for relative in expected_files | expected_dirs.copy():
            expected_dirs.update('' if str(p) == '.' else p.as_posix() for p in PurePosixPath(relative).parents)
        actual_files, actual_dirs = set(), {''}
        def unreadable(error):raise error
        for root, dirs, names in os.walk(destination, followlinks=False, onerror=unreadable):
            for name in dirs + names:
                path = Path(root)/name;no_links(path)
                relative = path.relative_to(destination).as_posix()
                (actual_dirs if name in dirs else actual_files).add(relative)
        if expected_files != actual_files or expected_dirs != actual_dirs:
            raise ValueError('Restored file/directory inventory mismatch')
    for entry in snapshot['files']:
        target = destination if single else destination.joinpath(*safe_parts(entry['path']))
        no_links(target)
        info = target.stat()
        if info.st_size != entry['size'] or stable_hash(target, signature(info)) != entry['sha256']:
            raise ValueError('Restored content checksum mismatch: ' + entry['path'])
        if permission_mode(stat.S_IMODE(info.st_mode)) != permission_mode(entry.get('mode', 0o644)):
            raise ValueError('文件权限恢复失败：' + str(target))
    for directory in directories:
        target = destination.joinpath(*PurePosixPath(directory['path']).parts)
        if permission_mode(stat.S_IMODE(target.stat().st_mode)) != permission_mode(directory.get('mode', 0o755)):
            raise ValueError('目录权限恢复失败：' + str(target))


def restore(snapshot, store, destination):
    destination = Path(destination)
    no_links(destination)
    if destination.exists():
        raise ValueError('Snapshot destination must not exist')
    validate_snapshot(snapshot, store)
    entries = snapshot['files']
    directories = snapshot.get('directories', []) if snapshot['format'] == 2 else []
    if not snapshot.get('single_file'):
        destination.mkdir(parents=True)
        for directory in directories:
            destination.joinpath(*PurePosixPath(directory['path']).parts).mkdir(parents=True, exist_ok=True)
    for entry in entries:
        target = destination if snapshot.get('single_file') else destination.joinpath(*safe_parts(entry['path']))
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with object_path(store, entry['sha256']).open('rb') as src, target.open('xb') as out:
            for block in iter(lambda: src.read(CHUNK), b''):
                digest.update(block)
                out.write(block)
        if digest.hexdigest() != entry['sha256']:
            raise ValueError('Snapshot checksum mismatch: ' + entry['path'])
        if 'mtime_ns' in entry:
            os.utime(target, ns=(entry['mtime_ns'], entry['mtime_ns']))
        os.chmod(target, entry.get('mode', 0o644) & 0o777)
        if permission_mode(target.stat().st_mode & 0o7777) != permission_mode(entry.get('mode', 0o644)):
            raise ValueError('文件权限恢复失败：' + str(target))
    for directory in sorted(directories, key=lambda d: len(PurePosixPath(d['path']).parts), reverse=True):
        os.chmod(destination.joinpath(*PurePosixPath(directory['path']).parts), directory.get('mode', 0o755) & 0o777)
        target = destination.joinpath(*PurePosixPath(directory['path']).parts)
        if permission_mode(target.stat().st_mode & 0o7777) != permission_mode(directory.get('mode', 0o755)):
            raise ValueError('目录权限恢复失败：' + str(target))
    verify_restored(snapshot, destination)
