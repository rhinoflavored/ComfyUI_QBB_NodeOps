"""Capture installed distributions and local component files as indexed ZIP objects."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import re
import stat
from pathlib import Path
import sys
import sysconfig
import tempfile
import zipfile
import zlib

from . import file_snapshots as files, record_tasks

INDEX_NAME = '.nodeops-index.json'
MAX_INDEX_BYTES = 32 * 1024 * 1024
PACKAGE_DATA_DIRS = ('share', 'etc', 'include', 'Include', 'bin', 'tests', 'SPIRV-Tools', 'SPIRV-Tools-diff', 'SPIRV-Tools-link', 'SPIRV-Tools-lint', 'SPIRV-Tools-opt', 'SPIRV-Tools-reduce')


def runtime_package_dll(path, root):
    return path.parent == root and bool(re.fullmatch(r'(?:concrt|msvcp|vcamp|vccorlib|vcomp|vcruntime)140(?:_[a-z0-9_]+)?[.]dll', path.name.lower()))
MAX_MEMBERS = 100000


def archive_index(component_id, index):
    return {'version': 1, 'component_id': component_id, 'files': index['files'],
            'directories': list(index.get('directories', [])),
            'exclusions': list(index.get('exclusions', [])), 'gaps': list(index.get('gaps', []))}


def index_bytes(index):
    encoded = json.dumps(index, sort_keys=True, ensure_ascii=False).encode('utf-8')
    if len(encoded) > MAX_INDEX_BYTES:
        raise ValueError('ZIP index exceeds 32 MiB')
    return encoded


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate ZIP index key')
        result[key] = value
    return result


def verify_zip(path, component_id, index, expected_object=None):
    """Validate the whole ZIP against an independent manifest without extracting files."""
    path = Path(path)
    expected = archive_index(component_id, index)
    encoded = index_bytes(expected)
    expected_files, expected_directories, names = {}, {}, {INDEX_NAME.casefold()}
    for entry in expected['files']:
        files.safe_parts(entry['path'])
        if entry['path'].casefold() in names:
            raise ValueError('Duplicate ZIP inventory path')
        names.add(entry['path'].casefold())
        if type(entry['size']) is not int or entry['size'] < 0 or not re.fullmatch('[a-f0-9]{64}', entry['sha256']):
            raise ValueError('Invalid ZIP file inventory')
        files.permission_mode(entry.get('mode', 0o644))
        expected_files[entry['path']] = entry
    for entry in expected['directories']:
        if not entry['path']:
            continue
        files.safe_parts(entry['path'])
        if entry['path'].casefold() in names:
            raise ValueError('Conflicting ZIP directory path')
        names.add(entry['path'].casefold())
        files.permission_mode(entry.get('mode', 0o755))
        expected_directories[entry['path'] + '/'] = entry
    file_names = set(expected_files)
    for name in file_names | {name.rstrip('/') for name in expected_directories}:
        if any('/'.join(name.split('/')[:i]) in file_names for i in range(1, len(name.split('/')))):
            raise ValueError('ZIP file cannot be a parent directory')
    if len(names) > MAX_MEMBERS:
        raise ValueError('ZIP exceeds 100000 members')
    try:
        files.no_links(path)
        signature = files.signature(path.stat())
        sha = hashlib.sha256()
        with path.open('rb') as stream:
            if files.signature(os.fstat(stream.fileno())) != signature:
                raise ValueError('ZIP changed before verification')
            for block in iter(lambda: stream.read(files.CHUNK), b''):
                sha.update(block)
            digest = sha.hexdigest()
            if expected_object and (signature[0] != expected_object['size'] or digest != expected_object['sha256']):
                raise ValueError('Stored ZIP object hash or length mismatch')
            stream.seek(0)
            with zipfile.ZipFile(stream) as archive:
                members = archive.infolist()
                expected_names = {INDEX_NAME, *expected_files, *expected_directories}
                if len(members) != len(expected_names) or {m.filename for m in members} != expected_names:
                    raise ValueError('ZIP members do not match component inventory')
                seen = set()
                for member in members:
                    name = member.filename
                    files.safe_parts(name[:-1] if member.is_dir() else name)
                    if name.casefold() in seen or member.orig_filename != name:
                        raise ValueError('Duplicate or invalid ZIP member name')
                    seen.add(name.casefold())
                    mode = member.external_attr >> 16
                    if stat.S_IFMT(mode) not in (0, stat.S_IFDIR if member.is_dir() else stat.S_IFREG):
                        raise ValueError('ZIP links or special members are not supported')
                    if member.flag_bits & 1 or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                        raise ValueError('Encrypted or unsupported ZIP member')
                    if name == INDEX_NAME:
                        if member.file_size > MAX_INDEX_BYTES:
                            raise ValueError('ZIP index exceeds 32 MiB')
                        raw = archive.read(member)
                        actual = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
                        if index_bytes(actual) != encoded:
                            raise ValueError('ZIP index does not match component inventory')
                        continue
                    if member.is_dir():
                        entry = expected_directories[name]
                        if member.file_size or archive.read(member):
                            raise ValueError('ZIP directory contains data')
                    else:
                        entry = expected_files[name]
                        if member.file_size != entry['size']:
                            raise ValueError('ZIP member length mismatch: ' + name)
                        content_sha, size = hashlib.sha256(), 0
                        with archive.open(member) as content:
                            while block := content.read(files.CHUNK):
                                size += len(block)
                                if size > entry['size']:
                                    raise ValueError('ZIP member exceeds declared length: ' + name)
                                content_sha.update(block)
                        if size != entry['size'] or content_sha.hexdigest() != entry['sha256']:
                            raise ValueError('ZIP member content mismatch: ' + name)
                    if files.permission_mode(mode & 0o7777) != files.permission_mode(entry.get('mode', 0o755 if member.is_dir() else 0o644)):
                        raise ValueError('ZIP member permission mismatch: ' + name)
            files.no_links(path)
            if files.signature(os.fstat(stream.fileno())) != signature or files.signature(path.stat()) != signature:
                raise ValueError('ZIP changed during verification')
        return {'status': 'verified', 'sha256': digest, 'size': signature[0],
                'files': len(expected_files), 'directories': len(expected_directories),
                'content_bytes': sum(e['size'] for e in expected_files.values()),
                'index_sha256': hashlib.sha256(encoded).hexdigest()}
    except (OSError, ValueError, zipfile.BadZipFile, zlib.error, EOFError) as exc:
        raise ValueError('ZIP verification failed for ' + component_id + ': ' + str(exc)) from exc


def record_index(record, row):
    if row['archive_content'] == 'installed_files_and_metadata':
        return row['installed_inventory']
    snapshot = record['artifacts'][row['artifact_ref']]
    return {'files': snapshot['files'], 'directories': snapshot.get('directories', []),
            'exclusions': snapshot.get('skipped', []), 'gaps': row.get('issues', [])}


def verify_record_archive(record, row, store):
    entry = record['artifacts'][row['archive_ref']]['files'][0]
    return verify_zip(files.object_path(store, entry['sha256']), row['id'], record_index(record, row), entry)


def package_inventories(distributions, manager, runtime_root=None, library_roots=None, scripts_root=None):
    with ThreadPoolExecutor(max_workers=4) as pool:
        return _package_inventories(distributions, manager, runtime_root, library_roots, scripts_root, pool)


def _package_inventories(distributions, manager, runtime_root, library_roots, scripts_root, pool):
    root = Path(runtime_root or sys.prefix).resolve()
    libraries = [Path(p).resolve() for p in (library_roots or
                 {sysconfig.get_path('purelib'), sysconfig.get_path('platlib')})]
    scripts = Path(scripts_root or sysconfig.get_path('scripts')).resolve()
    manager = Path(manager).resolve()
    result, owners = {}, {}
    for dist in distributions:
        name = dist.metadata.get('Name', '')
        # Match snapshot_state.key without importing its scanner.
        identity = 'package:' + re.sub(r'[-_.]+', '-', name).lower()
        index = {'root_at_capture': str(root), 'files': [], 'gaps': [], 'exclusions': [], 'shared_files': []}
        result[identity] = index
        record_tasks.emit('scan_installed_files', name)
        if name.lower() == 'pip':
            index['gaps'].append({'path': '.', 'reason': 'protected_runtime_package'})
            continue
        try:
            declared = getattr(dist, 'files', None)
            entries = list(declared) if declared else None
        except (OSError, ValueError, TypeError) as exc:
            index['gaps'].append({'path': '.', 'reason': 'invalid_installed_file_manifest', 'detail': str(exc)})
            continue
        if not entries:
            index['gaps'].append({'path': '.', 'reason': 'installed_file_manifest_missing'})
            continue
        paths = set()
        pending = []
        def collect():
            for future, relative, signature, raw in pending:
                try:
                    sha = future.result()
                    index['files'].append({'path': relative, 'sha256': sha, 'size': signature[0], 'mode': signature[4]})
                    owners.setdefault(relative.casefold(), []).append(identity)
                except (OSError, ValueError) as exc:
                    index['gaps'].append({'path': raw, 'reason': str(exc)})
            pending.clear()
        for entry in entries:
            raw = str(entry)
            relative = None
            try:
                path = Path(dist.locate_file(entry)).absolute()
                files.no_links(path)
                resolved = path.resolve()
                if resolved.is_relative_to(root) and ('__pycache__' in resolved.parts or resolved.suffix == '.pyc'):
                    index['exclusions'].append({'path': resolved.relative_to(root).as_posix(), 'reason': 'regenerable_bytecode'})
                    continue
                if not resolved.is_relative_to(root) or not (
                        any(resolved.is_relative_to(lib) for lib in libraries) or
                        any(resolved.is_relative_to(root / folder) for folder in PACKAGE_DATA_DIRS) or
                        runtime_package_dll(resolved, root) or
                        resolved.parent == scripts):
                    raise ValueError('installed_file_outside_runtime_scope')
                if resolved == manager or resolved.is_relative_to(manager) or manager.is_relative_to(resolved):
                    raise ValueError('protected_manager_file')
                for library in libraries:
                    if resolved.is_relative_to(library):
                        relative_parts = resolved.relative_to(library).parts
                        first = relative_parts[0].lower() if relative_parts else ''
                        if first == 'pip' or first.startswith('pip-') and first.endswith('.dist-info'):
                            raise ValueError('protected_runtime_package')
                if name.lower() == 'pip' or re.fullmatch(r'(pythonw?|pip)[\d.]*([.]exe)?', resolved.name.lower()):
                    raise ValueError('protected_runtime_file')
                relative = resolved.relative_to(root).as_posix()
                files.safe_parts(relative)
                if relative.casefold() in paths:
                    continue
                paths.add(relative.casefold())
                if '__pycache__' in resolved.parts or resolved.suffix == '.pyc':
                    index['exclusions'].append({'path': relative, 'reason': 'regenerable_bytecode'})
                    continue
                if not path.is_file():
                    raise ValueError('installed_file_missing_or_not_regular')
                signature = files.signature(path.stat())
                pending.append((pool.submit(files.stable_hash, path, signature), relative, signature, raw))
                if len(pending) >= 64:
                    collect()
            except (OSError, ValueError) as exc:
                gap = {'path': raw, 'reason': str(exc)}
                if relative and str(exc) == 'installed_file_missing_or_not_regular' and not path.exists():
                    gap.update(resolved_path=relative, missing=True)
                index['gaps'].append(gap)
        collect()
        index['files'].sort(key=lambda f: f['path'])
        metadata = {Path(f['path']).name.upper() for f in index['files'] if '.dist-info/' in f['path'].lower()}
        if not {'METADATA', 'RECORD'}.issubset(metadata):
            index['gaps'].append({'path': '.', 'reason': 'installed_metadata_incomplete'})
    for identity, index in result.items():
        for entry in index['files']:
            shared = sorted(set(owners[entry['path'].casefold()]))
            if len(shared) > 1:
                index['shared_files'].append({'path': entry['path'], 'owners': shared})

    return result


def write_zip(destination, component_id, entries, source_for, directories=(), exclusions=(), gaps=()):
    """Stream and hash every source; the archive never contains absolute paths."""
    index = archive_index(component_id, {'files': entries, 'directories': directories,
                                        'exclusions': exclusions, 'gaps': gaps})
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
        index_info = zipfile.ZipInfo(INDEX_NAME, (1980, 1, 1, 0, 0, 0))
        index_info.compress_type = zipfile.ZIP_DEFLATED
        index_info.external_attr = 0o100644 << 16
        archive.writestr(index_info, index_bytes(index))
        names = {INDEX_NAME.casefold()}
        for directory in directories:
            if not directory['path']:
                continue
            name = '/'.join(files.safe_parts(directory['path'])) + '/'
            if name.casefold() in names:
                raise ValueError('Duplicate ZIP directory')
            names.add(name.casefold())
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.external_attr = (0o040000 | directory.get('mode', 0o755)) << 16
            archive.writestr(info, b'')
        for entry in entries:
            parts = files.safe_parts(entry['path'])
            name = '/'.join(parts)
            if name.casefold() in names:
                raise ValueError('Duplicate ZIP member')
            names.add(name.casefold())
            source = Path(source_for(entry))
            files.no_links(source)
            signature = files.signature(source.stat())
            sha, size = hashlib.sha256(), 0
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100000 | entry.get('mode', 0o644)) << 16
            with source.open('rb') as src, archive.open(info, 'w', force_zip64=True) as dst:
                while block := src.read(files.CHUNK):
                    size += len(block)
                    if size > entry['size']:
                        raise ValueError('Source grew during ZIP capture')
                    sha.update(block)
                    dst.write(block)
            if size != entry['size'] or sha.hexdigest() != entry['sha256'] or files.signature(source.stat()) != signature:
                raise ValueError('Source changed during ZIP capture')
    with destination.open('r+b') as stream:
        os.fsync(stream.fileno())


def save_zip(store, component_id, index, source_for):
    with tempfile.TemporaryDirectory(prefix='.qbb-zip-', dir=store) as temporary:
        path = Path(temporary) / 'component.zip'
        write_zip(path, component_id, index['files'], source_for, index.get('directories', []),
                  index.get('exclusions', []), index.get('gaps', []))
        record_tasks.emit('verify_zip_archives', component_id)
        verify_zip(path, component_id, index)
        scan = files.scan(path, store, large_files='include')
        files.plan_content(path, scan)
        reused = all(files.object_path(store, sha).exists() for sha in scan['content_hashes'].values())
        snapshot = files.capture(path, store, expected=scan, **scan['policy'])
        files.verify_source(path, store, snapshot, scan)
        return snapshot, reused


def reserve_space(space, record, captures):
    # Account for an uncompressed upper bound, headers and a simultaneous temporary ZIP.
    indexes = [r['installed_inventory'] for r in record['resources'] if r.get('installed_inventory')]
    logical = sum(sum(f['size'] for f in index['files']) for index in indexes)
    archive_content = logical + sum(scan['bytes'] for _, scan in captures.values())
    count = sum(len(index['files']) for index in indexes) + sum(len(s['files']) + len(s['directories']) for _, s in captures.values())
    path_bytes = sum(len(f['path'].encode('utf-8')) for i in indexes for f in i['files'])
    path_bytes += sum(len(f['path'].encode('utf-8')) for _, s in captures.values() for f in [*s['files'], *s['directories']])
    upper = archive_content + archive_content // 100 + count * 2048 + path_bytes * 4 + sum(len(json.dumps(i).encode('utf-8')) for i in indexes) + 1048576
    return {**space, 'logical_bytes': space['logical_bytes'] + logical,
            'zip_upper_bound_bytes': upper, 'installed_file_bytes': logical,
            'new_bytes': space['new_bytes'] + upper,
            'required_bytes': space['required_bytes'] + 2 * upper}
