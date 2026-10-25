"""Phase 1: scoped environment evidence and immutable local artifacts. No restore."""
import copy
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from . import file_snapshots as files
from . import version_records as records
from .recovery_environment import Version, wheel_info
from . import restore_receipts
from . import record_tasks
from . import backup_contract
from . import local_archives

SCHEMA = 4
CONFIGS = ['extra_model_paths.yaml']
SCOPE = {'version': 1, 'configuration_allowlist': CONFIGS,
         'excluded_directories': ['models', 'input', 'output', 'workflow', 'workflows', 'user', 'temp'],
         'external_resources': 'record_only', 'python': 'package_management',
         'interpreter_replacement': False, 'site_packages_integrity': 'not_checked',
         'manager_policy': 'protected_resource'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def recovery(status, reason=None):
    return {'status': status, 'reason': reason, 'remote_availability': 'not_checked'}


def key(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def local_path(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'file' or parsed.netloc not in ('', 'localhost'):
        return None
    return Path(url2pathname(parsed.path))


def scan_packages(core, distributions=None, manager=None):
    result, sources = [], {}
    distributions = list(importlib.metadata.distributions() if distributions is None else distributions)
    receipts = restore_receipts.load(manager) if manager is not None else {}
    for dist in distributions:
        name = dist.metadata.get('Name', '')
        record_tasks.emit('scan_packages', name)
        row = {'id': 'package:' + key(name), 'kind': 'package', 'name': name,
               'version': dist.version, 'managed': True, 'artifact_ref': None,
               'source': {'kind': 'unknown'}, 'recoverability': recovery('unrestorable', 'source_unknown')}
        try:
            direct = json.loads(dist.read_text('direct_url.json') or '{}')
            if not isinstance(direct, dict):
                raise ValueError('Invalid direct_url')
            url = direct.get('url', '')
            receipt = restore_receipts.match(receipts, key(name), dist.version, direct)
            if receipt:
                row['source'] = receipt['source']
                row['source_evidence'] = 'verified_restore_install_receipt'
                if row['source']['kind'] == 'remote_wheel':
                    row['recoverability'] = recovery('source_recorded')
                else:
                    obj = files.object_path(Path(receipt['store']), receipt['sha256'])
                    row['restore_artifact'] = {'store': receipt['store'], 'sha256': receipt['sha256']}
                    if not obj.is_file():
                        obj = Path(row['source']['path_at_capture'])
                    files.no_links(obj)
                    if obj.is_file() and files.hash_file(obj) == receipt['sha256']:
                        sources[row['id']] = obj
                        row['recoverability'] = recovery('artifact_pending')
                    else:
                        row['recoverability'] = recovery('unrestorable', 'original_artifact_missing')
                result.append(row)
                continue
            path = local_path(url) if url else None
            if path is not None:
                editable = bool(direct.get('dir_info', {}).get('editable'))
                row['source'] = {'kind': 'editable' if editable else 'local_directory' if 'dir_info' in direct else 'local_wheel',
                                 'path_at_capture': str(path), 'editable': editable}
                if row['source']['kind'] == 'local_wheel':
                    row['source']['filename'] = path.name
                    if not path.is_file():
                        row['recoverability'] = recovery('unrestorable', 'original_artifact_missing')
                    elif path.suffix.lower() != '.whl':
                        row['recoverability'] = recovery('unrestorable', 'unsupported_local_artifact')
                    else:
                        info = wheel_info(path)
                        if info['name'] != key(name) or Version(info['version']) != Version(dist.version):
                            raise ValueError('Local wheel identity differs from installed distribution')
                        row['source']['wheel'] = info
                        sources[row['id']] = path
                        row['recoverability'] = recovery('artifact_pending')
                elif manager is not None and (path.resolve().is_relative_to(manager) or manager.is_relative_to(path.resolve())):
                    row.update(managed=False, protected=True)
                    row['recoverability'] = recovery('protected_resource', 'current_environment_manager')
                elif not path.resolve().is_relative_to(core.resolve()):
                    row['managed'] = False
                    row['recoverability'] = recovery('unrestorable', 'external_source_record_only')
                elif not path.is_dir():
                    row['recoverability'] = recovery('unrestorable', 'original_artifact_missing')
                else:
                    files.no_links(path)
                    sources[row['id']] = path
                    # Saving source does not prove its build dependencies are reproducible.
                    row['recoverability'] = recovery('unrestorable', 'local_build_not_verified')
            elif direct:
                safe = records.safe_url(url) if not urlsplit(url).query else ''
                vcs = direct.get('vcs_info', {})
                hashes = direct.get('archive_info', {}).get('hashes', {})
                sha = hashes.get('sha256')
                old_hash = direct.get('archive_info', {}).get('hash', '')
                if not sha and old_hash.startswith('sha256='):
                    sha = old_hash[7:]
                if safe and vcs.get('vcs') == 'git' and re.fullmatch('[a-fA-F0-9]{40,64}', vcs.get('commit_id', '')):
                    row['source'] = {'kind': 'git', 'url': safe, 'commit': vcs['commit_id']}
                    row['recoverability'] = recovery('unrestorable', 'source_build_not_verified')
                elif safe and unquote(urlsplit(url).path).lower().endswith('.whl') and isinstance(sha, str) and re.fullmatch('[a-fA-F0-9]{64}', sha):
                    row['source'] = {'kind': 'remote_wheel', 'url': safe, 'sha256': sha.lower()}
                    row['recoverability'] = recovery('source_recorded')
                else:
                    row['source'] = {'kind': 'direct', 'url': safe or None}
                    row['recoverability'] = recovery('unrestorable', 'source_not_reproducible')
        except (OSError, ValueError, TypeError, KeyError, AttributeError, zipfile.BadZipFile) as exc:
            sources.pop(row['id'], None)
            row['recoverability'] = recovery('unrestorable', 'invalid_source_metadata_or_artifact')
            row['error'] = str(exc)
        result.append(row)
    counts = {}
    for row in result:
        counts[row['id']] = counts.get(row['id'], 0) + 1
    for row in result:
        if not row['name'] or counts[row['id']] > 1:
            row['recoverability'] = recovery('unrestorable', 'ambiguous_package_identity')
            sources.pop(row['id'], None)
    # Duplicate distribution metadata cannot be represented as a unique managed package.
    if any(count > 1 for count in counts.values()):
        raise ValueError('Duplicate installed package identities; resolve metadata before recording')
    managed = {row['id'] for row in result if row.get('managed') and not row.get('protected')}
    installed = local_archives.package_inventories(
        distributions,
        manager or Path(__file__).resolve().parent.parent)
    for row in result:
        if row['id'] in managed and row['id'] in installed:
            row['installed_inventory'] = installed[row['id']]
    return sorted(result, key=lambda r: r['id']), sources


def tree_evidence(scan):
    return {'files': [{'path': f['path'], 'sha256': scan['content_hashes'][f['path']],
                       'size': f.get('sqlite_bytes', f['signature'][0]), 'mode': f['signature'][4]}
                      for f in scan['files']],
            'directories': [{'path': d['path'], 'mode': d['mode']} for d in scan['directories']]}


def retrievable_git(path, metadata, scan):
    if metadata.get('kind') != 'git' or not metadata.get('url') or not re.fullmatch('[a-fA-F0-9]{40,64}', metadata.get('commit', '')):
        return False
    if metadata.get('dirty') is not False or metadata.get('has_submodules') or metadata.get('submodule_error'):
        return False
    try:
        remote_refs = records.git(path, 'for-each-ref', '--contains', metadata['commit'], '--format=%(refname)', 'refs/remotes/origin')
        tracked = set(records.git(path, '-c', 'core.quotePath=false', 'ls-files', '-z').split('\x00'))
        if not remote_refs or any(f['path'] not in tracked for f in scan['files']):
            return False
        # Pointer files alone cannot stand for fetched LFS content.
        for f in scan['files']:
            if f['signature'][0] < 1024:
                with (path / f['path']).open('rb') as stream:
                    if stream.read(43).startswith(b'version https://git-lfs.github.com/spec/v1'):
                        return False
        return True
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def scan_environment(core=None, plugin_roots=None, manager=None, distributions=None, store=None, label='', read_only=False):
    if core is None:
        from .instances import CURRENT_COMFYUI_ROOT
        core = Path(CURRENT_COMFYUI_ROOT)
    core = Path(core).resolve()
    if plugin_roots is None:
        import folder_paths
        plugin_roots = folder_paths.get_folder_paths('custom_nodes')
    manager = Path(manager or Path(__file__).resolve().parent.parent).resolve()
    store = Path(store or records.directory())
    resources, captures, issues = [], {}, []
    candidates = [('core', 'core', core)]
    seen = set()
    for raw in plugin_roots:
        root = Path(raw).absolute()
        try:
            files.no_links(root)
            entries = sorted(root.iterdir())
        except (OSError, ValueError) as exc:
            issues.append({'resource_id': 'plugin-root:' + str(root), 'reason': 'unreadable_or_linked_plugin_root', 'detail': str(exc)})
            continue
        for path in entries:
            if path.name.startswith('.') or path.name == '__pycache__' or (not path.is_dir() and path.suffix != '.py' and not path.is_symlink()):
                continue
            identity = str(path.absolute()).casefold()
            if identity in seen:
                continue
            seen.add(identity)
            relative = path.relative_to(core).as_posix() if path.is_relative_to(core) else str(path)
            candidates.append(('plugin:' + relative, 'plugin', path))
    for resource_id, kind, path in candidates:
        record_tasks.emit('scan_core' if kind == 'core' else 'scan_plugins', path.name)
        row = {'id': resource_id, 'kind': kind, 'name': path.name, 'managed': True,
               'path_at_capture': str(path), 'relative_path': '.' if kind == 'core' else path.relative_to(core).as_posix() if path.is_relative_to(core) else None,
               'artifact_ref': None, 'source': {'kind': 'local'}}
        resources.append(row)
        if path.resolve() == manager:
            row.update(managed=False, protected=True, recoverability=recovery('protected_resource', 'current_environment_manager'))
            row['source'] = records.repository(path)
            continue
        try:
            files.no_links(path)
            if not path.resolve().is_relative_to(core):
                raise ValueError('External resource')
        except ValueError:
            row.update(managed=False, recoverability=recovery('unrestorable', 'external_or_linked_resource_record_only'))
            continue
        metadata = records.repository(path)
        row['source'] = {k: metadata[k] for k in ('kind', 'url', 'commit', 'branch', 'dirty', 'has_submodules') if k in metadata}
        tracked_configs = []
        if metadata.get('kind') == 'git':
            try:
                tracked_configs = sorted(p for p in records.git(path, '-c', 'core.quotePath=false', 'ls-files', '-z').split('\x00') if p and p != 'extra_model_paths.yaml')
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        scan = files.scan(path, store, core=kind == 'core', large_files='include', managed_v1=True, tracked_configs=tracked_configs, read_only=read_only)
        files.plan_content(path, scan)
        if scan['errors']:
            row['recoverability'] = recovery('unrestorable', 'source_scan_failed')
            row['issues'] = scan['errors']
            continue
        row['content'] = tree_evidence(scan)
        row['content_sha256'] = digest(row['content'])
        row['excluded'] = scan['skipped']
        gaps = [s for s in scan['skipped'] if s['reason'] not in ('outside_scope_v1', 'separate_configuration', 'configuration_not_allowlisted') and not (
            s['reason'] == 'excluded cache/environment/assets' and Path(s['path']).name in ('.git', '__pycache__', '.pytest_cache', 'custom_nodes'))]
        if metadata.get('submodule_error'):
            gaps.append({'path': '.', 'reason': 'submodule_content_unverified'})
        lfs = False
        for entry in scan['files']:
            if entry['path'].endswith('.gitattributes'):
                lfs = lfs or 'filter=lfs' in (path / entry['path']).read_text(encoding='utf-8', errors='replace')
            if entry['signature'][0] < 1024:
                candidate = path if scan['single_file'] else path / entry['path']
                with candidate.open('rb') as stream:
                    if stream.read(43).startswith(b'version https://git-lfs.github.com/spec/v1'):
                        gaps.append({'path': entry['path'], 'reason': 'lfs_payload_missing'})
        if gaps:
            row['issues'] = gaps
        if not lfs and retrievable_git(path, metadata, scan) and not gaps:
            row['recoverability'] = recovery('source_recorded')
            row['source']['evidence'] = 'local_origin_tracking_ref_contains_commit'
        else:
            row['recoverability'] = recovery('unrestorable', 'incomplete_local_tree') if gaps else recovery('artifact_pending')
            if metadata.get('kind') == 'git' and not gaps:
                try:
                    reachable = bool(metadata.get('url') and metadata.get('commit') and records.git(path, 'for-each-ref', '--contains', metadata['commit'], '--format=%(refname)', 'refs/remotes/origin'))
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    reachable = False
                if not reachable:
                    row['recoverability'] = recovery('unrestorable', 'git_commit_not_retrievable')
            captures[resource_id] = (path, scan)
    packages, local_sources = scan_packages(core, distributions, manager)
    resources.extend(packages)
    for row in packages:
        path = local_sources.get(row['id'])
        if path is None:
            continue
        scan = files.scan(path, store, large_files='include', managed_v1=True, read_only=read_only)
        files.plan_content(path, scan)
        if scan['errors']:
            row['recoverability'] = recovery('unrestorable', 'artifact_scan_failed')
            row['issues'] = scan['errors']
        else:
            captures[row['id']] = (path, scan)
            row['content_sha256'] = digest(tree_evidence(scan))
    for name in CONFIGS:
        record_tasks.emit('scan_configuration', name)
        path = core / name
        row = {'id': 'config:' + name, 'kind': 'configuration', 'name': name,
               'relative_path': name, 'managed': True, 'present': path.exists() or path.is_symlink(),
               'source': {'kind': 'local'}, 'artifact_ref': None,
               'recoverability': recovery('absent_recorded')}
        resources.append(row)
        if row['present']:
            scan = files.scan(path, store, large_files='include', read_only=read_only)
            files.plan_content(path, scan)
            if not path.is_file() or scan['errors'] or scan['skipped']:
                row['recoverability'] = recovery('unrestorable', 'configuration_unreadable_or_linked')
            else:
                row['recoverability'] = recovery('artifact_pending')
                captures[row['id']] = (path, scan)
    return {'schema': SCHEMA, 'kind': 'version_record', 'id': uuid.uuid4().hex,
            'created': time.time(), 'label': str(label)[:120],
            'instance': {'root_at_capture': str(core)}, 'scope': copy.deepcopy(SCOPE),
            'python': {'implementation': platform.python_implementation(), 'version': platform.python_version(),
                       'architecture': platform.machine(), 'platform': platform.system(), 'executable_at_capture': sys.executable},
            'resources': resources, 'artifacts': {}, 'issues': issues,
            'consistency': {'status': 'checked_not_atomic'},
            'capabilities': {'restore': True, 'diff': True, 'recovery_plan': True, 'reason': 'phase_3_strict_preflight_required'}}, captures


def options(value=None):
    value = value or {}
    if not isinstance(value, dict) or set(value) - {'mode', 'large_files', 'core_dirs', 'on_error'}:
        raise ValueError('Invalid capture options')
    if value.get('core_dirs', []) or value.get('large_files', 'include') != 'include' or value.get('on_error', 'strict') != 'strict' or value.get('mode', 'auto') != 'auto':
        raise ValueError('Phase 1 uses the fixed V1 scope and source classification; legacy capture options are not supported')
    return {'mode': 'auto', 'large_files': 'include', 'core_dirs': [], 'on_error': 'strict'}


def identity(record, captures):
    value = {k: v for k, v in record.items() if k not in ('id', 'created', 'label', 'statistics', 'artifacts')}
    return digest({'record': value, 'scans': {k: v[1] for k, v in captures.items()}})


def preview(value=None):
    policy = options(value)
    if not records.LOCK.acquire(blocking=False):
        raise ValueError('A snapshot operation is running')
    try:
        record, captures = scan_environment()
        record_tasks.emit('check_storage')
        space = local_archives.reserve_space(files.storage_plan([scan for _, scan in captures.values()], records.directory()), record, captures)
        probe = records.directory()
        while not probe.exists():
            probe = probe.parent
        free = shutil.disk_usage(probe).free
        errors = [] if free >= space['required_bytes'] else ['Insufficient storage space']
        token = uuid.uuid4().hex
        with records._PREVIEW_LOCK:
            records._PREVIEWS.clear()
            records._PREVIEWS[token] = {'digest': identity(record, captures), 'options': policy, 'expires': time.monotonic() + 1800, 'errors': errors, 'record': record, 'captures': captures, 'store': str(records.directory().resolve())}
        items = []
        for row in record['resources']:
            scan = captures.get(row['id'], (None, {}))[1]
            installed = row.get('installed_inventory', {})
            items.append({'name': row['name'], 'method': 'snapshot' if scan or installed.get('files') else 'metadata',
                          'files': len(scan.get('files', [])) + len(installed.get('files', [])),
                          'bytes': scan.get('bytes', 0) + sum(f['size'] for f in installed.get('files', [])),
                          'skipped': scan.get('skipped', []) + installed.get('exclusions', []),
                          'errors': row.get('issues', []) + installed.get('gaps', []),
                          'recoverability': row['recoverability']})
        return {'preview_id': token, 'options': policy, 'items': items, 'scopes': [], 'errors': errors,
                'can_save': not errors, 'files': sum(i['files'] for i in items), 'space': space,
                'bytes_upper_bound': space['logical_bytes'], 'required_bytes': space['required_bytes'], 'free_bytes': free,
                'unrestorable': sum(r['recoverability']['status'] == 'unrestorable' for r in record['resources']),
                'scope': record['scope'], 'expires_in': 1800, 'expires_at': time.time() + 1800}
    finally:
        records.LOCK.release()


def create(label='', value=None, preview_id=None):
    policy = options(value)
    if not records.LOCK.acquire(blocking=False):
        raise ValueError('A snapshot operation is running')
    try:
        with records._PREVIEW_LOCK:
            reviewed = records._PREVIEWS.pop(preview_id, None)
        if not reviewed or reviewed['expires'] < time.monotonic() or reviewed['options'] != policy or reviewed['errors']:
            raise ValueError('Preview again before saving')
        if reviewed.get('store') != str(records.directory().resolve()):
            raise ValueError('Storage changed since preview; preview again')
        record, captures = reviewed['record'], reviewed['captures']
        record.update(id=uuid.uuid4().hex, created=time.time(), label=str(label)[:120])
        before = reviewed['digest']
        store = records.directory()
        files.no_links(store)
        store.mkdir(parents=True, exist_ok=True)
        space = local_archives.reserve_space(files.storage_plan([s for _, s in captures.values()], store), record, captures)
        if shutil.disk_usage(store).free < space['required_bytes']:
            raise ValueError('Insufficient storage space')
        reused_resources = sum(all(files.object_path(store, sha).exists() for sha in scan['content_hashes'].values())
                               for _, scan in captures.values())
        saved_count = 0
        for row in record['resources']:
            capture = captures.get(row['id'])
            if capture is None:
                continue
            path, scan = capture
            record_tasks.emit('save_objects', row['name'], saved_count, len(captures))
            snapshot = files.capture(path, store, expected=scan, **scan['policy'])
            saved_count += 1
            record_tasks.emit('save_objects', row['name'], saved_count, len(captures))
            record['artifacts'][row['id']] = snapshot
            row['artifact_ref'] = row['id']
            if row['recoverability']['status'] == 'artifact_pending':
                row['recoverability'] = recovery('artifact_saved')
        for number, (resource_id, (path, scan)) in enumerate(captures.items()):
            record_tasks.emit('verify_objects', resource_id, number, len(captures))
            files.verify_source(path, store, record['artifacts'][resource_id], scan)
        archive_count = zip_new_bytes = zip_reused_bytes = 0
        for row in record['resources']:
            installed = row.get('installed_inventory')
            snapshot = record['artifacts'].get(row.get('artifact_ref'))
            if installed and installed['files']:
                index = installed
                root = Path(installed['root_at_capture'])
                source_for = lambda entry, root=root: root / entry['path']
                row['archive_content'] = 'installed_files_and_metadata'
            elif snapshot:
                index = {'files': snapshot['files'], 'directories': snapshot.get('directories', []),
                         'exclusions': snapshot.get('skipped', []), 'gaps': row.get('issues', [])}
                source_for = lambda entry: files.object_path(store, entry['sha256'])
                row['archive_content'] = 'original_wheel' if row['kind'] == 'package' and row['source']['kind'] == 'local_wheel' else 'source_tree' if row['kind'] == 'package' else 'managed_files'
            else:
                continue
            record_tasks.emit('save_zip_archives', row['name'], archive_count, len(record['resources']))
            ref = row['id'] + ':zip'
            archive, reused = local_archives.save_zip(store, row['id'], index, source_for)
            record['artifacts'][ref] = archive
            if reused:
                zip_reused_bytes += archive['files'][0]['size']
            else:
                zip_new_bytes += archive['files'][0]['size']
            row['archive_ref'] = ref
            archive_count += 1
        archive_rows = [r for r in record['resources'] if r.get('archive_ref')]
        for number, row in enumerate(archive_rows):
            record_tasks.emit('verify_zip_archives', row['name'], number, len(archive_rows))
            row['archive_verification'] = local_archives.verify_record_archive(record, row, store)
        record_tasks.emit('verify_zip_archives', '', len(archive_rows), len(archive_rows))
        record_tasks.emit('verify_environment')
        with record_tasks.phase('verify_environment'):
            after_record, after_captures = scan_environment(label=label)
        if identity(after_record, after_captures) != before:
            raise ValueError('Environment changed during capture; snapshot not published')
        record['statistics'] = {**space, 'plugins': sum(r['kind'] == 'plugin' for r in record['resources']),
                                'packages': sum(r['kind'] == 'package' for r in record['resources']),
                                'saved_resources': len({r['id'] for r in record['resources'] if r.get('artifact_ref') or r.get('archive_ref')}),
                                'zip_archives': archive_count, 'reused_resources': reused_resources,
                                'unrestorable': sum(r['recoverability']['status'] == 'unrestorable' for r in record['resources'])}
        record['statistics'].update(new_bytes=space['new_bytes'] - space['zip_upper_bound_bytes'] + zip_new_bytes,
                                    reused_bytes=space['reused_bytes'] + zip_reused_bytes,
                                    zip_new_bytes=zip_new_bytes, zip_reused_bytes=zip_reused_bytes,
                                    unique_referenced_bytes=space['unique_referenced_bytes'] + zip_new_bytes + zip_reused_bytes,
                                    unique_objects=space['unique_objects'] + archive_count)
        record = validate(record)
        record_tasks.emit('publish')
        destination = store / (record['id'] + '.json')
        temporary = destination.with_suffix('.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return {'id': record['id'], 'schema': SCHEMA, 'statistics': record['statistics'], 'issues': record['issues'],
                'backup_summary': backup_contract.summary(record),
                'unrestorable': [r for r in record['resources'] if r['recoverability']['status'] == 'unrestorable'],
                'skipped_errors': record['statistics']['unrestorable'] + len(record['issues'])}
    finally:
        records.LOCK.release()


def validate(record):
    if record.get('schema') != SCHEMA or record.get('kind') != 'version_record' or record.get('scope') != SCOPE:
        raise ValueError('Unsupported Phase 1 snapshot contract')
    result = copy.deepcopy(record)
    ids, refs = set(), set()
    for row in result['resources']:
        if row['id'] in ids:
            raise ValueError('Duplicate resource identity')
        ids.add(row['id'])
        if row['kind'] not in ('core', 'plugin', 'package', 'configuration'):
            raise ValueError('Unknown resource kind')
        status = row['recoverability']['status']
        if status not in ('source_recorded', 'artifact_saved', 'unrestorable', 'protected_resource', 'absent_recorded'):
            raise ValueError('Unpublished resource status')
        ref = row.get('artifact_ref')
        if ref is not None:
            if ref not in result['artifacts']:
                raise ValueError('Missing artifact manifest')
            refs.add(ref)
        archive_ref = row.get('archive_ref')
        if row.get('archive_verification') is not None and archive_ref is None:
            raise ValueError('ZIP verification requires an archive reference')
        if archive_ref is not None:
            if not row.get('managed') or row.get('protected') or status == 'protected_resource':
                raise ValueError('Cannot archive protected or external resources')
            if archive_ref not in result['artifacts'] or archive_ref != row['id'] + ':zip':
                raise ValueError('Missing or invalid ZIP artifact reference')
            archive = result['artifacts'][archive_ref]
            if not archive.get('single_file') or len(archive.get('files', [])) != 1:
                raise ValueError('ZIP artifact must contain one archive object')
            refs.add(archive_ref)
        if status == 'artifact_saved' and ref is None:
            raise ValueError('Recoverable local resource needs saved content')
        if row.get('protected') and (row.get('managed') or status != 'protected_resource' or ref is not None or archive_ref is not None or row.get('installed_inventory')):
            raise ValueError('Protected manager cannot be modified')
    if refs != set(result['artifacts']):
        raise ValueError('Unreferenced artifact manifests')
    from .manifest import snapshot_inventory
    objects = {}
    for ref, snapshot in result['artifacts'].items():
        snapshot_inventory(snapshot, ref, objects)
        for entry in snapshot['files']:
            files.safe_parts(entry['path'])
        for entry in snapshot.get('directories', []):
            if entry['path']:
                files.safe_parts(entry['path'])
    result['backup'] = backup_contract.validate(result)
    result['capabilities'] = {'restore': True, 'diff': True, 'recovery_plan': True, 'reason': 'phase_3_strict_preflight_required'}
    return result


def summary(record):
    stats = record['statistics']
    components = record.get('backup', {}).get('components', {}).values()
    code_gaps = any(c['kind'] in ('core', 'plugin', 'configuration') and c['status'] in ('incomplete', 'metadata_only') for c in components)
    packages = [c for c in components if c['kind'] == 'package' and c['status'] not in ('protected', 'outside_scope')]
    package_state = ('installed_files_saved' if all(c.get('installed_files_saved') and c['status'] == 'local_saved' for c in packages)
                     else 'installed_files_partial') if record.get('backup', {}).get('version') == 2 else 'package_management_only'
    return {'scopes': {'code': {'status': 'partial' if code_gaps or record['issues'] else 'recorded'},
                       **{name: {'status': 'not_selected'} for name in ('user', 'models', 'input', 'output')},
                       'python_environment': {'status': package_state}}, 'sizes': stats}
