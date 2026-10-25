"""Opt-in partial captures. Source failures may be skipped; storage failures may not."""
import copy
import hashlib
import os
import re
import subprocess
import tempfile
from pathlib import Path
from . import file_snapshots as files
from .git_tools import require_git


class SourceFailure(Exception):
    pass


def source_call(fn):
    try:
        return fn()
    except (OSError, ValueError) as exc:
        # SQLite source backup can itself exhaust the temporary volume.
        if isinstance(exc, OSError) and exc.errno in (28, 122):
            raise
        raise SourceFailure(str(exc)) from exc


def allow_partial(scan):
    """Remove unreadable entries from the estimate, retaining explicit exclusions."""
    errors = scan['errors']
    bad = [e['path'] for e in errors]
    def excluded(path):
        return any(p in ('', '.') or path == p or path.startswith(p + '/') for p in bad)
    scan['files'] = [e for e in scan['files'] if not excluded(e['path'])]
    scan['directories'] = [e for e in scan['directories'] if not excluded(e['path'])]
    scan['skipped'].extend(dict(e, capture_error=True) for e in errors)
    scan['errors'] = []
    scan['content_hashes'] = {e['path']: scan['content_hashes'][e['path']] for e in scan['files']}
    scan['bytes'] = sum(e.get('sqlite_bytes', e['signature'][0]) for e in scan['files'])
    return scan


def capture(source, store, baseline):
    source, store = Path(source), Path(store)
    result = {'format': 2, 'single_file': baseline['single_file'], 'files': [],
              'directories': [{'path': d['path'], 'mode': d['mode']} for d in baseline['directories']],
              'skipped': copy.deepcopy(baseline['skipped']), 'policy': baseline['policy'],
              'inventory': 'complete_within_policy', 'consistency': 'per_file_checked_not_atomic'}
    for entry in baseline['files']:
        relative = entry['path']
        path = source if baseline['single_file'] else source.joinpath(*files.safe_parts(relative))
        try:
            source_call(lambda: files.no_links(path))
            # The staging destination is deliberately outside the source-error catch.
            with tempfile.TemporaryFile(dir=store) as out:
                if 'sqlite_sha256' in entry:
                    manager = files.sqlite_snapshot(path)
                    backup = source_call(manager.__enter__)
                    try:
                        stream = source_call(lambda: backup.open('rb'))
                        with stream:
                            digest, size = _copy(stream, out, entry['sqlite_bytes'])
                    finally:
                        manager.__exit__(None, None, None)
                else:
                    stream = source_call(lambda: path.open('rb'))
                    with stream:
                        if source_call(lambda: files.signature(os.fstat(stream.fileno()))) != entry['signature']:
                            raise SourceFailure('File changed before capture')
                        digest, size = _copy(stream, out, entry['signature'][0])
                        if source_call(lambda: files.signature(os.fstat(stream.fileno()))) != entry['signature']:
                            raise SourceFailure('File changed during capture')
                    source_call(lambda: files.no_links(path))
                    if source_call(lambda: files.signature(path.stat())) != entry['signature']:
                        raise SourceFailure('File changed after capture')
                if digest != baseline['content_hashes'][relative]:
                    raise SourceFailure('File content changed since preview')
                obj = files.object_path(store, digest)
                if obj.exists():
                    if obj.stat().st_size != size or files.hash_file(obj) != digest:
                        raise ValueError('Existing snapshot object is corrupt: ' + digest)
                else:
                    obj.parent.mkdir(parents=True, exist_ok=True)
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=obj.parent, delete=False) as target:
                            temporary = Path(target.name)
                            out.seek(0)
                            for block in iter(lambda: out.read(files.CHUNK), b''):
                                target.write(block)
                            target.flush(); os.fsync(target.fileno())
                        temporary.replace(obj)
                    finally:
                        if temporary is not None and temporary.exists():
                            temporary.unlink()
                saved = {'path': relative, 'sha256': digest, 'size': size,
                         'mode': entry['signature'][4], 'mtime_ns': entry['signature'][1]}
                if 'sqlite_sha256' in entry:
                    saved['capture_method'] = 'sqlite_backup'
                result['files'].append(saved)
        except SourceFailure as exc:
            result['skipped'].append({'path': relative, 'reason': str(exc), 'capture_error': True})
    result['bytes'] = sum(e['size'] for e in result['files'])
    return result


def storage_plan(scans, store):
    estimate = files.storage_plan(scans, store)
    # The source staging file coexists with the new immutable object, including
    # when all objects are reused. Do not underestimate a large reused file.
    staging = max((e.get('sqlite_bytes', e['signature'][0])
                   for scan in scans if scan for e in scan['files']), default=0)
    estimate['source_staging_bytes'] = staging
    estimate['required_bytes'] += staging
    return estimate


def _copy(stream, out, expected_size):
    digest, size = hashlib.sha256(), 0
    while True:
        block = source_call(lambda: stream.read(files.CHUNK))
        if not block:
            break
        if size + len(block) > expected_size:
            raise SourceFailure('File grew during capture')
        out.write(block)  # Disk errors must propagate, never be treated as source gaps.
        digest.update(block); size += len(block)
    return digest.hexdigest(), size


def github_source(row):
    url = row.get('url', '')
    commit = row.get('commit', '')
    if not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?', url):
        return None
    if not re.fullmatch(r'[0-9a-fA-F]{40,64}', commit):
        return None
    return {'url': url, 'commit': commit}


def supplement_github(row, store):
    snapshot = row['snapshot']
    errors = [e for e in snapshot['skipped'] if e.get('capture_error')]
    if not errors:
        return
    source = github_source(row)
    row['github_substitution'] = {'status': 'unavailable', 'original_local_changes_preserved': False}
    if not source:
        row['github_substitution']['reason'] = 'No verified GitHub URL and exact commit; name matching is not used.'
        return
    with tempfile.TemporaryDirectory(prefix='github-source-', dir=store) as temporary:
        checkout = Path(temporary) / 'source'
        env = dict(os.environ)
        for key in list(env):
            if key.startswith('GIT_'):
                env.pop(key)
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0', GIT_LFS_SKIP_SMUDGE='1')
        try:
            executable = require_git()
            def run(*args):
                subprocess.run([executable, '-c', 'core.hooksPath=', '-c', 'protocol.allow=never',
                                '-c', 'protocol.https.allow=always', *args], env=env,
                               capture_output=True, check=True, timeout=180,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            run('init', str(checkout))
            run('-C', str(checkout), 'fetch', '--depth=1', '--', source['url'], source['commit'])
            run('-C', str(checkout), 'checkout', '--detach', source['commit'])
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            row['github_substitution'].update(source, reason='GitHub source download failed: ' + type(exc).__name__)
            return
        remote = files.scan(checkout, store, core=True, large_files=snapshot['policy']['large_files'], core_dirs=[])
        files.plan_content(checkout, remote)
        allow_partial(remote)
        existing = {e['path'] for e in snapshot['files']}
        remote['files'] = [e for e in remote['files'] if e['path'] not in existing and any(
            error['path'] in ('', '.') or e['path'] == error['path'] or e['path'].startswith(error['path'] + '/') for error in errors)]
        downloaded = capture(checkout, store, remote)
        snapshot['files'].extend(downloaded['files'])
        directories = {d['path']: d for d in snapshot['directories']}
        for d in downloaded['directories']:
            if any(e['path'].startswith(d['path'] + '/') for e in downloaded['files']):
                directories.setdefault(d['path'], d)
        snapshot['directories'] = list(directories.values())
        snapshot['bytes'] = sum(e['size'] for e in snapshot['files'])
        # Keep original exclusions: downloading replacements never proves local identity.
        row['github_substitution'].update(source, status='partial_substitution', files=len(downloaded['files']))
