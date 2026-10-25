"""One Current -> Target comparison contract. Proposals only; no executor."""
from collections import Counter
import copy
from pathlib import Path, PurePosixPath
import re
import threading
import time
import uuid

from . import snapshot_state as state
from . import file_snapshots as files
from . import version_records as records
from . import local_archives
from . import backup_contract


def tree(resource, manifest):
    if resource['kind'] == 'package' and resource.get('installed_inventory') is not None:
        inventory = resource['installed_inventory']
        if not inventory['files']:
            return None
        evidence = {'files': inventory['files'], 'directories': []}
        # Validate with the same path/hash/type rules as ordinary snapshots.
        proxy = {k: v for k, v in resource.items() if k not in ('installed_inventory', 'artifact_ref')}
        proxy['content'] = evidence
        return tree(proxy, manifest)
    value = resource.get('content')
    if value is None:
        value = manifest.get('artifacts', {}).get(resource.get('artifact_ref'))
    if value is None:
        return None
    result, seen = {}, set()
    for kind, entries in [('file', value['files']), ('directory', value['directories'])]:
        for entry in entries:
            path = entry['path']
            if path:
                files.safe_parts(path)
            elif kind == 'file':
                raise ValueError('Empty file path')
            if path.casefold() in seen:
                raise ValueError('Ambiguous content path')
            seen.add(path.casefold())
            parts = PurePosixPath(path).parts
            if resource['kind'] in ('core', 'plugin') and (any(p.casefold() in state.SCOPE['excluded_directories'] for p in parts)
                    or resource['kind'] == 'core' and parts and parts[0] == 'custom_nodes'):
                raise ValueError('Content evidence outside managed scope')
            row = {'type': kind, 'mode': files.permission_mode(entry['mode'])}
            if kind == 'file':
                if not re.fullmatch('[a-f0-9]{64}', entry['sha256']) or type(entry['size']) is not int or entry['size'] < 0:
                    raise ValueError('Invalid content evidence')
                row.update(sha256=entry['sha256'], size=entry['size'])
            result[path] = row
    if resource['kind'] == 'configuration' and set(result) != {resource['name']}:
        raise ValueError('Configuration content does not match its allowlisted identity')
    artifact = manifest.get('artifacts', {}).get(resource.get('artifact_ref'))
    if resource.get('content') is not None and artifact is not None:
        artifact_row = {k: v for k, v in resource.items() if k != 'content'}
        if result != tree(artifact_row, manifest):
            raise ValueError('Recorded state and saved artifact disagree')
    return result


def index(manifest):
    if manifest.get('schema') != 4 or manifest.get('scope') != state.SCOPE:
        raise ValueError('Unified Diff requires schema 4 with the same V1 scope')
    result, seen = {}, set()
    for row in manifest['resources']:
        identity = row['id']
        if identity.casefold() in seen:
            raise ValueError('Ambiguous resource identity')
        seen.add(identity.casefold())
        kind = row['kind']
        if type(row.get('managed')) is not bool:
            raise ValueError('Invalid resource management flag')
        if kind == 'plugin' and row.get('managed'):
            relative = row.get('relative_path')
            files.safe_parts(relative or '')
            if identity != 'plugin:' + relative or relative.split('/')[0].casefold() in state.SCOPE['excluded_directories']:
                raise ValueError('Invalid managed plugin path')
        elif kind == 'package':
            if not row.get('name') or identity != 'package:' + state.key(row['name']):
                raise ValueError('Invalid package identity')
        elif kind == 'configuration':
            if row['name'] not in state.CONFIGS or identity != 'config:' + row['name'] or type(row.get('present')) is not bool:
                raise ValueError('Configuration outside V1 allowlist')
        elif kind == 'core' and identity != 'core':
            raise ValueError('Invalid core identity')
        elif kind not in ('core', 'plugin', 'package', 'configuration'):
            raise ValueError('Unknown resource kind')
        result[identity.casefold()] = row
    if 'core' not in result or any('config:' + name not in result for name in state.CONFIGS):
        raise ValueError('Incomplete managed scope inventory')
    return result


def source(resource, manifest):
    value = resource['source']
    kind = value.get('kind', 'unknown')
    fields = ('url', 'commit') if kind == 'git' else ('url', 'sha256') if kind == 'remote_wheel' else ()
    result = {'kind': kind, **{key: value.get(key) for key in fields}}
    if kind in ('editable', 'local_directory', 'local_wheel'):
        raw = value.get('path_at_capture', '')
        path, root = Path(raw), Path(manifest['instance']['root_at_capture'])
        result['path'] = ('instance:' + path.relative_to(root).as_posix()) if path.is_relative_to(root) else str(path)
        if kind == 'local_wheel':
            result['sha256'] = value.get('wheel', {}).get('sha256')
        else:
            result['editable'] = value.get('editable', False)
    if kind == 'git' and resource['kind'] != 'package':
        result.pop('commit', None)  # Commit has its own diff field.
    return result


def name_inventory():
    """List component identities without reading or hashing their contents."""
    import importlib.metadata
    import folder_paths
    from .instances import CURRENT_COMFYUI_ROOT
    core = Path(CURRENT_COMFYUI_ROOT)
    rows = [{'id': 'core', 'kind': 'core', 'name': core.name}]
    issues, seen = [], set()
    for raw in folder_paths.get_folder_paths('custom_nodes'):
        try:
            for path in sorted(Path(raw).iterdir()):
                if path.name.startswith('.') or path.name == '__pycache__':
                    continue
                if not path.is_dir() and path.suffix != '.py' and not path.is_symlink():
                    continue
                identity = str(path.absolute()).casefold()
                if identity in seen:
                    continue
                seen.add(identity)
                rows.append({'id': 'plugin:' + identity, 'kind': 'plugin', 'name': path.name})
        except OSError as exc:
            issues.append({'reason': 'plugin_names_unavailable', 'detail': str(exc)})
    for dist in importlib.metadata.distributions():
        name = dist.metadata.get('Name', '')
        rows.append({'id': 'package:' + state.key(name), 'kind': 'package', 'name': name})
    for name in state.CONFIGS:
        rows.append({'id': 'config:' + name, 'kind': 'configuration', 'name': name,
                     'present': (core / name).exists()})
    return {'id': None, 'instance': {'root_at_capture': str(core)}, 'resources': rows, 'issues': issues}


def compare_names(current, target):
    """Explicitly requested name equality; never produces restoration actions."""
    def names(manifest):
        result = {}
        for row in manifest['resources']:
            if row['kind'] == 'configuration' and not row.get('present'):
                continue
            name = state.key(row['name']) if row['kind'] == 'package' else row['name'].casefold()
            identity = (row['kind'], 'core' if row['kind'] == 'core' else name)
            result.setdefault(identity, row)
        return result
    now, goal = names(current), names(target)
    rows = []
    for identity in sorted(now.keys() | goal.keys()):
        a, b = now.get(identity), goal.get(identity)
        resource = b or a
        changes = [] if a and b else [{'status': 'missing' if b else 'extra',
                   'field': 'name', 'current': a['name'] if a else None, 'target': b['name'] if b else None}]
        rows.append({'resource_id': resource['id'], 'kind': resource['kind'], 'name': resource['name'],
                     'managed': False, 'comparison_basis': 'names_only', 'differences': changes,
                     'proposals': [], 'blockers': [], 'warnings': [], 'status': 'differences' if changes else 'matched'})
    issues = [{'status': 'unknown', 'reason': 'name_inventory_incomplete', 'detail': issue}
              for issue in current.get('issues', []) if issue.get('reason') == 'plugin_names_unavailable']
    count = sum(bool(r['differences']) for r in rows)
    return {'engine_version': 2, 'comparison_mode': 'names_only', 'direction': 'current_to_target',
            'read_only': True, 'resources': rows, 'runtime': [], 'issues': issues,
            'change_count': count, 'unverified_count': len(issues), 'blocked_resources': 0,
            'state': 'differences' if count else 'unknown' if issues else 'matched'}


def current_state():
    manifest, captures = state.scan_environment(read_only=True)
    manifest['id'] = None
    # Same scanner as capture; expose ephemeral evidence without publishing artifacts.
    for row in manifest['resources']:
        if row['id'] in captures:
            row['content'] = state.tree_evidence(captures[row['id']][1])
    return manifest, captures


def comparison_trees(a, b, current, target):
    if a['kind'] == 'package' and not all(r.get('installed_inventory', {}).get('files') for r in (a, b)):
        if not all(r['source']['kind'] in ('editable', 'local_directory') for r in (a, b)):
            return None, None
        a, b = ({k: v for k, v in r.items() if k != 'installed_inventory'} for r in (a, b))
    return tree(a, current), tree(b, target)


def inventory_gaps(resource, manifest):
    """Normalize declared package paths without touching the runtime filesystem."""
    import posixpath
    inventory = resource.get('installed_inventory', {})
    roots = {entry['path'].split('/site-packages/')[0] + '/site-packages'
             for entry in inventory.get('files', []) if '/site-packages/' in entry['path']}
    gaps = []
    for gap in inventory.get('gaps', []):
        if gap.get('reason') in ('shared_installed_file', 'duplicate_installed_file_path'):
            continue
        raw = gap.get('resolved_path') or gap.get('path', '.')
        paths = [raw] if gap.get('resolved_path') or raw == '.' or any(raw.startswith(root + '/') for root in roots) else [posixpath.normpath(root + '/' + raw.replace('\\', '/')) for root in roots]
        if not paths:
            paths = [raw]
        if all('__pycache__' in path.split('/') or path.endswith('.pyc') for path in paths):
            continue
        gaps.append({**gap, 'coverage_paths': paths})
    content = resource.get('content') or manifest.get('artifacts', {}).get(resource.get('artifact_ref')) or {}
    for gap in content.get('skipped', []):
        if gap.get('reason') not in ('outside_scope_v1', 'configuration_not_allowlisted', 'regenerable_bytecode'):
            gaps.append({**gap, 'coverage_paths': [gap['path']]})
    return gaps


def gap_covers(path, gaps):
    return any(prefix == '.' or path.casefold() == prefix.casefold() or path.casefold().startswith(prefix.casefold().rstrip('/') + '/')
               for gap in gaps for prefix in gap['coverage_paths'])


def compare(current, target, comparison_mode='strict'):
    """Pure comparison. Never inspect object storage or mutate either manifest."""
    if comparison_mode not in ('strict', 'names_only'):
        raise ValueError('Invalid comparison mode')
    now, goal = index(current), index(target)
    def identity_name(row):
        name = state.key(row['name']) if row['kind'] == 'package' else row['name'].casefold()
        return row['kind'], name
    current_names = {identity_name(r) for r in now.values() if r['kind'] != 'configuration' or r.get('present')}
    target_names = {identity_name(r) for r in goal.values() if r['kind'] != 'configuration' or r.get('present')}
    global_issues = [{'status': 'unknown', 'reason': 'scope_incomplete', 'side': side, 'detail': issue}
                     for side, record in [('current', current), ('target', target)] for issue in record.get('issues', [])]
    runtime = []
    for field in ('implementation', 'version', 'architecture', 'platform'):
        a, b = current.get('python', {}).get(field), target.get('python', {}).get(field)
        if not a or not b or a != b:
            runtime.append({'field': field, 'status': 'unknown' if not a or not b else 'version_mismatch',
                            'current': a, 'target': b, 'reason': 'interpreter_replacement_out_of_scope'})
    protected_paths = [PurePosixPath(r['relative_path']) for r in current['resources']
                       if r.get('protected') and r.get('relative_path')]
    rows = []
    for identity in sorted(now.keys() | goal.keys()):
        a, b = now.get(identity), goal.get(identity)
        resource = b or a
        kind = resource['kind']
        row = {'resource_id': resource['id'], 'kind': kind, 'name': resource['name'], 'differences': [],
               'proposals': [], 'blockers': [], 'warnings': [], 'historical_gaps': []}
        changes, proposals, blockers = row['differences'], row['proposals'], row['blockers']
        def change(status, field, old, new, path=None):
            item = {'status': status, 'field': field, 'current': old, 'target': new}
            if path is not None:
                item['path'] = path
            changes.append(item)
        def propose(operation):
            if operation not in proposals:
                proposals.append(operation)
        def block(reason):
            if reason not in blockers:
                blockers.append(reason)
        protected = any(r and (r.get('protected') or r['recoverability']['status'] == 'protected_resource') for r in (a, b))
        protected |= kind == 'package' and state.key(resource['name']) == 'pip'
        if kind == 'plugin':
            for candidate in (a, b):
                if candidate and candidate.get('relative_path'):
                    path = PurePosixPath(candidate['relative_path'])
                    protected |= any(path == p or path in p.parents or p in path.parents for p in protected_paths)
        if protected:
            row.update(status='protected_resource', reason='protected_python_package' if kind == 'package' else 'current_environment_manager', managed=False)
            rows.append(row)
            continue
        if any(r is not None and not r.get('managed') for r in (a, b)):
            row.update(status='external_resource', reason='outside_managed_scope', managed=False)
            rows.append(row)
            continue
        row['managed'] = True
        if comparison_mode == 'names_only' and identity_name(resource) in current_names & target_names:
            row.update(status='matched', comparison_basis='names_only', skipped_by_name=True)
            rows.append(row)
            continue
        present_a = a is not None and (kind != 'configuration' or a.get('present') is True)
        present_b = b is not None and (kind != 'configuration' or b.get('present') is True)
        if present_a != present_b:
            change('missing' if present_b else 'extra', 'presence', present_a, present_b)
            propose(('install_package' if present_b else 'uninstall_package') if kind == 'package' else
                    ('restore_configuration' if present_b else 'remove_configuration') if kind == 'configuration' else
                    ('restore_resource' if present_b else 'remove_resource'))
            if not present_b and kind == 'plugin' and any(e['reason'] in ('outside_scope_v1', 'configuration_not_allowlisted') for e in a.get('excluded', [])):
                block('removal_would_affect_unmanaged_content')
        if b and present_b:
            tree(b, target)  # Validate recorded content even when the resource is missing now.
            health = b['recoverability']
            if health['status'] == 'source_recorded':
                row['warnings'].append('remote_availability_not_checked')
        if a and present_a and a.get('issues'):
            change('unknown', 'current_evidence', a['issues'], None)
        if present_a and present_b:
            target_gaps = inventory_gaps(b, target)
            current_gaps = inventory_gaps(a, current)
            row['historical_gaps'] = target_gaps
            if kind == 'package':
                if not a.get('version') or not b.get('version'):
                    change('unknown', 'version', a.get('version'), b.get('version'))
                elif a['version'] != b['version']:
                    change('version_mismatch', 'version', a['version'], b['version'])
                    propose('set_package_version')
            sa, sb = source(a, current), source(b, target)
            installed = kind == 'package' and all(r.get('installed_inventory', {}).get('files') for r in (a, b))
            ta, tb = comparison_trees(a, b, current, target)
            content_known = ta is not None and tb is not None
            row['comparison_basis'] = 'installed_files' if installed else 'saved_content' if content_known else 'metadata_only'
            if kind == 'package' and not installed:
                row['warnings'].append('installed_content_not_recorded')
                if not content_known:
                    change('unknown', 'installed_content_evidence', False, False)
            source_unknown = sa['kind'] in ('unknown', 'direct') or sb['kind'] in ('unknown', 'direct') or any(
                    v is None or v == '' for s in (sa, sb) for v in s.values())
            if sa != sb and content_known:
                row['warnings'].append('source_metadata_changed')
            if sa != sb and not source_unknown and not content_known:
                change('source_mismatch', 'source', sa, sb)
                propose('set_package_source' if kind == 'package' else 'set_repository_source')
            if source_unknown:
                row['warnings'].append('source_unknown')
                if not content_known:
                    change('unknown', 'source_evidence', sa, sb)
            if kind in ('plugin', 'core') and (a['source']['kind'] == 'git' or b['source']['kind'] == 'git'):
                ca, cb = a['source'].get('commit'), b['source'].get('commit')
                if a['source']['kind'] == 'git' and not ca or b['source']['kind'] == 'git' and not cb:
                    if not content_known:
                        change('unknown', 'commit', ca, cb)
                elif ca != cb:
                    if content_known:
                        row['warnings'].append('git_metadata_changed')
                    else:
                        change('commit_mismatch', 'commit', ca, cb)
                        propose('checkout_commit' if cb else 'restore_content')
            if kind != 'package' or installed or b['source']['kind'] in ('editable', 'local_directory'):
                if ta is None or tb is None:
                    change('unknown', 'content_evidence', ta is not None, tb is not None)
                else:
                    for path in sorted(ta.keys() | tb.keys()):
                        old, new = ta.get(path), tb.get(path)
                        if old == new:
                            continue
                        if new is None and gap_covers(path, target_gaps):
                            continue
                        if old is None and gap_covers(path, [g for g in current_gaps if g.get('missing') is not True]):
                            continue
                        status = ('missing' if old is None else 'extra' if new is None else
                                  'type_mismatch' if old['type'] != new['type'] else
                                  'content_mismatch' if old.get('sha256') != new.get('sha256') or old.get('size') != new.get('size') else 'permissions_mismatch')
                        change(status, 'content', old, new, path)
                        propose(('restore_installed_files' if installed else 'restore_local_source') if kind == 'package' else 'restore_content')
            if installed:
                gaps = [g for g in current_gaps if not gap_covers(g['coverage_paths'][0], target_gaps)]
                gaps = [g for g in gaps if not (g.get('missing') is True and g.get('resolved_path') in tb)]
                if gaps:
                    change('unknown', 'current_installed_inventory', gaps, None)
        if proposals and global_issues:
            block('scope_incomplete')
        if proposals and a and a.get('issues'):
            block('current_content_unverified')
        if proposals and kind == 'package' and any((r or {}).get('installed_inventory', {}).get('shared_files') for r in (a, b)):
            block('shared_installed_file')
        if proposals and kind == 'package' and not present_b and any(g.get('missing') is not True for g in a.get('installed_inventory', {}).get('gaps', [])):
            block('current_installed_inventory_incomplete')
        row['installed_file_operation'] = kind == 'package' and bool((a or {}).get('installed_inventory', {}).get('files')) and (not present_b or 'restore_installed_files' in proposals)
        if proposals and kind == 'package' and runtime:
            block('python_interpreter_incompatible_or_unknown')
        if proposals and kind == 'package' and (present_a and not a.get('version') or present_b and not b.get('version')):
            block('package_version_unknown')
        row['status'] = 'blocked' if blockers else 'differences' if proposals else 'unknown' if changes else 'historical_gap' if row['historical_gaps'] else 'matched'
        rows.append(row)
    differences = sum(c['status'] not in ('unknown', 'unrestorable') for r in rows for c in r['differences'])
    unverified = sum(c['status'] in ('unknown', 'unrestorable') for r in rows for c in r['differences'])
    historical = sum(len(r.get('historical_gaps', [])) for r in rows)
    blocked = sum(bool(r['blockers']) for r in rows)
    return {'engine_version': 2, 'comparison_mode': comparison_mode, 'direction': 'current_to_target', 'read_only': True,
            'current': {'id': current.get('id'), 'root': current['instance']['root_at_capture']},
            'target': {'id': target.get('id'), 'root_at_capture': target['instance']['root_at_capture']},
            'runtime': runtime, 'issues': global_issues, 'resources': rows,
            'change_count': differences, 'unverified_count': unverified + historical + len(global_issues) + len(runtime),
            'historical_gap_count': historical,
            'blocked_resources': blocked,
            'state': 'blocked' if blocked else 'differences' if differences else 'unknown' if unverified or global_issues or runtime else 'historical_gap' if historical else 'matched',
            'scope': copy.deepcopy(state.SCOPE)}


def object_health(target, store):
    """Read and hash target objects once per digest. No downloads or repairs."""
    results, checked = {}, {}
    for row in target['resources']:
        ref = row.get('artifact_ref')
        failures = []
        for entry in target['artifacts'][ref]['files'] if ref is not None else []:
            sha, size = entry['sha256'], entry['size']
            identity = (sha, size)
            if identity not in checked:
                try:
                    path = files.object_path(store, sha)
                    info = path.stat()
                    checked[identity] = None if info.st_size == size and files.stable_hash(path, files.signature(info)) == sha else 'artifact_corrupt'
                except (OSError, ValueError):
                    checked[identity] = 'artifact_missing_or_unreadable'
            if checked[identity]:
                failures.append({'sha256': sha, 'reason': checked[identity]})
        evidence = {'status': 'failed' if failures else 'verified' if ref else 'unavailable', 'failures': failures}
        if row.get('archive_ref'):
            try:
                proof = local_archives.verify_record_archive(target, row, store)
                evidence['archive'] = {'status': 'verified', **proof}
            except (OSError, ValueError, KeyError) as exc:
                evidence['archive'] = {'status': 'failed', 'reason': 'target_zip_unavailable_or_invalid', 'detail': str(exc)}
        if ref or row.get('archive_ref'):
            results[row['id']] = evidence
    return results


def recovery_plan(diff, target, health):
    """Derive a read-only proposal from Diff, never a second comparison engine."""
    actions, blockers, backup_issues = [], [], []
    goals = index(target)
    coverage = backup_contract.inventory(target)['components']
    for row in diff['resources']:
        evidence = health.get(row['resource_id'])
        goal = goals.get(row['resource_id'].casefold())
        if evidence and (evidence['status'] == 'failed' or evidence.get('archive', {}).get('status') == 'failed'):
            backup_issues.append({'resource_id': row['resource_id'], 'reason': 'backup_material_invalid', 'detail': evidence})
        if row['proposals']:
            reasons = list(row['blockers'])
            material = {'kind': 'removal'}
            if row.get('installed_file_operation'):
                material = {'kind': 'installed_file_removal'}
            present = goal is not None and (goal['kind'] != 'configuration' or goal.get('present'))
            if present:
                installed = goal.get('archive_content') == 'installed_files_and_metadata'
                inventory = goal.get('installed_inventory', {})
                complete = coverage[goal['id']]['status'] == 'local_saved'
                zip_ok = evidence and evidence.get('archive', {}).get('status') == 'verified'
                legacy_snapshot = not installed and goal['recoverability']['status'] == 'artifact_saved' and evidence and evidence['status'] == 'verified' and complete
                if legacy_snapshot:
                    material = {'kind': 'local_snapshot', 'artifact_ref': goal['artifact_ref']}
                elif goal.get('archive_ref') and zip_ok and complete and (goal['kind'] != 'package' or installed):
                    material = {'kind': 'local_zip', 'archive_ref': goal['archive_ref'], 'content': goal.get('archive_content')}
                elif goal.get('artifact_ref') and evidence and evidence['status'] == 'verified' and not installed and complete and goal['kind'] != 'package':
                    material = {'kind': 'local_snapshot', 'artifact_ref': goal['artifact_ref'], 'requires_file_executor': True}
                else:
                    src = goal['source']
                    exact_source = bool(src.get('url')) and ((src.get('kind') == 'git' and bool(re.fullmatch('[a-fA-F0-9]{40,64}', src.get('commit', '')))) or (src.get('kind') == 'remote_wheel' and bool(re.fullmatch('[a-fA-F0-9]{64}', src.get('sha256', '')))))
                    if exact_source and goal['recoverability']['status'] == 'source_recorded' and 'restore_installed_files' not in row['proposals'] and not goal.get('artifact_ref') and not goal.get('archive_ref'):
                        material = {'kind': 'remote_source', 'availability': 'unchecked'}
                    else:
                        material = {'kind': 'unavailable'}
                        incomplete = coverage[goal['id']]['status'] == 'incomplete'
                        if goal.get('archive_ref'):
                            reason = 'target_installed_files_not_saved' if goal['kind'] == 'package' and not installed else 'target_backup_incomplete' if incomplete else 'target_zip_unavailable_or_invalid'
                        else:
                            reason = goal['recoverability'].get('reason') or ('target_backup_incomplete' if incomplete else 'target_artifact_unavailable_or_unchecked')
                        reasons.append(reason)
            blockers.extend({'resource_id': row['resource_id'], 'reason': reason} for reason in dict.fromkeys(reasons))
            actions.append({'resource_id': row['resource_id'], 'kind': row['kind'], 'operations': row['proposals'],
                            'material': material,
                            'scope': 'managed_content_only',
                            'status': 'blocked' if reasons else 'proposed', 'blockers': reasons,
                            'target': None if goal is None else {k: goal[k] for k in ('version', 'source', 'artifact_ref', 'archive_ref', 'relative_path') if k in goal},
                            'requires': ['explicit_phase_3_preparation_and_confirmation'] + row['warnings']})
    execution_ready = not blockers and not diff['unverified_count']
    return {'plan_version': 2, 'direction': 'current_to_target', 'read_only': True, 'execution_allowed': False,
            'execution_ready': execution_ready,
            'execution_notice': 'comparison_incomplete' if diff['unverified_count'] else None,
            'status': 'blocked' if blockers else 'changes_proposed' if actions else 'comparison_incomplete' if diff['unverified_count'] else 'no_changes',
            'backup_issues': backup_issues,
            'actions': actions, 'blockers': blockers, 'artifact_health': health, 'diff': diff,
            'summary': dict(Counter(r['status'] for r in diff['resources'])),
            'notice': 'Read-only proposal, not execution authorization. Re-scan and preflight are required before any future execution.'}


_comparison_cache = {}
COMPARISON_TTL = 300

_comparison_progress = {}
_progress_lock = threading.Lock()


def comparison_progress(identity):
    with _progress_lock:
        return dict(_comparison_progress.get(identity, {'status': 'queued', 'phase': 'queued'}))


def inspect(record_id, current_record_id=None, *, content_only=False, progress_id=None, comparison_mode='strict', comparison_token=None):
    from . import record_tasks
    if progress_id is None:
        return _inspect(record_id, current_record_id, content_only=content_only, comparison_mode=comparison_mode, comparison_token=comparison_token)
    if not isinstance(progress_id, str) or not re.fullmatch('[a-f0-9]{32}', progress_id):
        raise ValueError('Invalid comparison progress ID')
    def update(fields):
        with _progress_lock:
            if progress_id not in _comparison_progress and len(_comparison_progress) >= 8:
                del _comparison_progress[next(iter(_comparison_progress))]
            _comparison_progress[progress_id] = dict(fields)
    previous = getattr(record_tasks.LOCAL, 'callback', None)
    record_tasks.LOCAL.callback = lambda fields: update(dict(fields, status='running'))
    update({'status': 'running', 'phase': 'read_record'})
    try:
        result = _inspect(record_id, current_record_id, content_only=content_only, comparison_mode=comparison_mode, comparison_token=comparison_token)
        update({'status': 'completed', 'phase': 'completed'})
        return result
    except Exception:
        update({'status': 'failed', 'phase': 'failed'})
        raise
    finally:
        record_tasks.LOCAL.callback = previous


def _inspect(record_id, current_record_id=None, *, content_only=False, comparison_mode='strict', comparison_token=None):
    if comparison_mode not in ('strict', 'names_only'):
        raise ValueError('Invalid comparison mode')
    if not records.LOCK.acquire(blocking=False):
        raise ValueError('A snapshot/storage operation is running; retry later')
    try:
        store = records.directory()
        files.no_links(store)
        for identity in (record_id, current_record_id):
            if identity is not None:
                if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{32}', identity):
                    raise ValueError('Invalid record ID')
                files.no_links(store / (identity + '.json'))
        target = records.read(record_id)
        if target.get('schema') != 4:
            raise ValueError('Read-only Recovery Plan requires schema 4; legacy records are not reinterpreted')
        lightweight = comparison_mode == 'names_only' and current_record_id is None
        reused = comparison_token is not None
        if reused:
            cached = _comparison_cache.get(comparison_token) if isinstance(comparison_token, str) else None
            if not cached or cached['expires'] < time.monotonic():
                raise ValueError('Comparison expired; compare again')
            if cached['record_id'] != record_id or cached['mode'] != comparison_mode or cached['store'] != str(store) or cached['target_digest'] != state.digest(target) or current_record_id is not None:
                raise ValueError('Comparison no longer matches the selected record or mode; compare again')
            current, captures = cached['current'], None
            lightweight = cached.get('lightweight', False)
        elif current_record_id is not None:
            current = records.read(current_record_id)
            captures = None
        elif lightweight:
            current, captures = name_inventory(), None
        else:
            current, captures = current_state()
        if not reused and current_record_id is None:
            for token in list(_comparison_cache):
                if _comparison_cache[token]['expires'] < time.monotonic():
                    del _comparison_cache[token]
            while len(_comparison_cache) >= 4:
                del _comparison_cache[next(iter(_comparison_cache))]
            comparison_token = uuid.uuid4().hex
            _comparison_cache[comparison_token] = {'record_id': record_id, 'mode': comparison_mode,
                'store': str(store), 'target_digest': state.digest(target), 'current': current, 'lightweight': lightweight,
                'expires': time.monotonic() + COMPARISON_TTL}
        from . import record_tasks
        record_tasks.emit('compare_contents')
        result = compare_names(current, target) if lightweight else compare(current, target, comparison_mode)
        if lightweight and not content_only:
            if result['change_count'] == 0:
                # No actions: names alone suffice, with no content scan or archive checks.
                return {'comparison_token': comparison_token, 'comparison_ttl': COMPARISON_TTL,
                    'diff': result, 'actions': [], 'blockers': [], 'backup_issues': [],
                    'materials_checked': False, 'execution_allowed': False,
                    'execution_ready': result['unverified_count'] == 0,
                    'status': 'comparison_incomplete' if result['unverified_count'] else 'no_changes',
                    'evidence': 'reused_comparison_preflight_required' if reused else 'names_only_inventory'}
            # Changed names require file evidence for safe removal/restore preparation.
            # Unchanged-name comparison never pays for this scan. Cache it once.
            current, captures = current_state()
            result = compare(current, target, comparison_mode)
            if comparison_token in _comparison_cache:
                _comparison_cache[comparison_token].update(current=current, lightweight=False)
            captures = None  # Live preparation will revalidate before any mutation.
        if content_only:
            coverage = backup_contract.inventory(target)['components']
            materials = [{'resource_id': key, 'name': value['name'], 'status': value['status'],
                          'reason': value.get('recovery_reason')}
                         for key, value in coverage.items()
                         if value['status'] in ('incomplete', 'metadata_only', 'source_only')]
            # No restoration authorization is issued from this content-only result.
            return {'comparison_token': comparison_token, 'comparison_ttl': COMPARISON_TTL, 'diff': result, 'actions': [], 'blockers': [], 'backup_issues': [], 'material_notices': materials, 'read_only': True,
                    'materials_checked': False, 'execution_allowed': False, 'execution_ready': False,
                    'status': 'comparison_only', 'evidence': 'live_scan_not_atomic' if captures is not None else 'saved_current_manifest'}
        health = object_health(target, store)
        if captures is not None:
            after, after_captures = current_state()
            if state.identity(current, captures) != state.identity(after, after_captures):
                raise ValueError('Environment changed during read-only scan; no plan published, retry')
        plan = recovery_plan(result, target, health)
        plan['comparison_token'] = comparison_token
        plan['comparison_ttl'] = COMPARISON_TTL
        plan['evidence'] = 'reused_comparison_preflight_required' if reused else 'saved_current_manifest' if captures is None else 'live_scan_checked_not_atomic'
        plan['fingerprint'] = state.digest({'diff': result, 'target': target, 'health': health})
        if comparison_token in _comparison_cache:
            _comparison_cache[comparison_token]['plan'] = copy.deepcopy(plan)
        return plan
    finally:
        records.LOCK.release()


def reviewed_restore(record_id, fingerprint, comparison_mode, store, comparison_token=None):
    """Consume server-held reviewed evidence; no live scan or plan regeneration."""
    candidates = [(comparison_token, _comparison_cache.get(comparison_token))] if comparison_token else list(_comparison_cache.items())
    cached = next((item for token, item in candidates if item and item['record_id'] == record_id
                   and item['mode'] == comparison_mode and item['store'] == str(store)
                   and item.get('plan', {}).get('fingerprint') == fingerprint), None)
    if not cached or cached['expires'] < time.monotonic():
        raise ValueError('Reviewed plan is stale or expired; compare again explicitly')
    target = records.read(record_id)
    if cached['target_digest'] != state.digest(target):
        raise ValueError('Reviewed target record changed; compare again explicitly')
    return cached['current'], target, copy.deepcopy(cached['plan'])
