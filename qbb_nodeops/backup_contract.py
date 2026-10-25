"""Versioned backup inventory. Describes saved evidence without reading live files."""
import copy
import hashlib
from pathlib import Path

from .manifest import snapshot_inventory, exclusion_kind
from . import file_snapshots as files
from . import local_archives

VERSION = 2
SCOPE = {
    'included': ['core', 'plugin', 'package', 'configuration'],
    'configuration_allowlist': ['extra_model_paths.yaml'],
    'excluded_directories': ['models', 'input', 'output', 'workflow', 'workflows', 'user', 'temp'],
    'external_resources': 'record_only',
    'manager_policy': 'protected_resource',
    'interpreter_replacement': False,
    'package_backup_target': 'installed_files_and_metadata',
    'package_identity': 'normalized_name',
    'comparison_evidence': ['version', 'file_path', 'sha256', 'size'],
}


def inventory(record):
    """Normalize old schema-4 records without claiming missing content was saved."""
    components, objects = {}, {}
    version = VERSION if any('installed_inventory' in r or 'archive_ref' in r for r in record['resources']) else 1
    for row in record['resources']:
        ref = row.get('artifact_ref')
        snapshot = record['artifacts'].get(ref) if ref is not None else None
        source = row.get('source', {})
        health = row['recoverability']
        protected = row.get('protected') or health['status'] == 'protected_resource'
        coverage = snapshot_inventory(snapshot, ref, objects)
        gaps = copy.deepcopy(row.get('issues', []))
        if snapshot:
            for entry in snapshot.get('skipped', []):
                category = exclusion_kind(entry, row['kind'] == 'core')
                if entry.get('reason') in ('outside_scope_v1', 'configuration_not_allowlisted'):
                    category = 'outside_scope'
                if category not in ('regenerable', 'repository_metadata', 'separate_components', 'outside_scope'):
                    gaps.append(copy.deepcopy(entry))
        if protected:
            status = 'protected'
        elif not row.get('managed'):
            status = 'outside_scope'
        elif health['status'] == 'absent_recorded':
            status = 'absent'
        elif snapshot:
            status = 'local_saved' if coverage['status'] == 'complete_within_policy' and not gaps else 'incomplete'
        elif health['status'] == 'source_recorded':
            status = 'source_only'
        else:
            status = 'metadata_only'
        if row['kind'] == 'package':
            content = ('original_wheel' if source.get('kind') == 'local_wheel' else 'source_tree') if snapshot else 'none'
        else:
            content = 'managed_files' if snapshot else 'none'
        components[row['id']] = {
            'name': row['name'], 'kind': row['kind'], 'version': row.get('version'),
            'original_location': row.get('relative_path') or source.get('path_at_capture') or
                                 (record.get('instance', {}).get('root_at_capture') if row['kind'] == 'core' else None),
            'location_base': 'instance_root' if row.get('relative_path') else 'capture_path',
            'status': status, 'saved_content': content,
            'installed_files_saved': False if row['kind'] == 'package' else None,
            'storage': {'format': 'objects', 'artifact_ref': ref,
                        'object_path_template': 'objects/{sha256_first_2}/{sha256_rest}'} if snapshot else None,
            'files': [{k: entry[k] for k in ('path', 'sha256', 'size', 'mode') if k in entry}
                      for entry in snapshot.get('files', [])] if snapshot else [],
            'directories': copy.deepcopy(snapshot.get('directories', [])) if snapshot else [],
            'exclusions': copy.deepcopy(snapshot.get('skipped', [])) if snapshot else [],
            'gaps': gaps,
            'recovery_status': health['status'], 'recovery_reason': health.get('reason'),
        }
        if version == 2:
            component = components[row['id']]
            installed = row.get('installed_inventory')
            if installed is not None:
                if row['kind'] != 'package' or not row.get('managed') or protected or not Path(installed['root_at_capture']).is_absolute():
                    raise ValueError('Invalid installed package inventory')
                snapshot_inventory({'format': 2, 'files': installed['files']}, row['id'], objects)
                names = set()
                for entry in installed['files']:
                    files.safe_parts(entry['path'])
                    name = entry['path'].casefold()
                    if name in names:
                        raise ValueError('Duplicate installed file identity')
                    names.add(name)
                if any('/'.join(name.split('/')[:i]) in names for name in names for i in range(1, len(name.split('/')))):
                    raise ValueError('Installed file cannot be a parent directory')
                component['shared_files'] = copy.deepcopy(installed['shared_files'])
                component['gaps'] += copy.deepcopy(installed['gaps'])
                component['exclusions'] += copy.deepcopy(installed['exclusions'])
            archive_ref = row.get('archive_ref')
            if archive_ref is not None:
                archive = record['artifacts'][archive_ref]
                archive_file = archive['files'][0]
                content = row.get('archive_content')
                expected_content = ('installed_files_and_metadata' if installed and installed['files'] else
                                    'original_wheel' if row['kind'] == 'package' and source.get('kind') == 'local_wheel' else
                                    'source_tree' if row['kind'] == 'package' else 'managed_files')
                if content != expected_content or not snapshot and content != 'installed_files_and_metadata':
                    raise ValueError('Invalid ZIP content classification')
                component['storage'] = {'format': 'zip', 'artifact_ref': archive_ref,
                                        'sha256': archive_file['sha256'], 'size': archive_file['size'],
                                        'object_path': 'objects/' + archive_file['sha256'][:2] + '/' + archive_file['sha256'][2:],
                                        'index_member': '.nodeops-index.json'}
                component['saved_content'] = content
                if content == 'installed_files_and_metadata':
                    component['files'] = copy.deepcopy(installed['files'])
                    component['directories'] = []
                    component['original_location'] = installed['root_at_capture']
                    component['location_base'] = 'python_runtime_root'
                    component['installed_files_saved'] = not bool(installed['gaps'])
                    component['status'] = 'incomplete' if installed['gaps'] else 'local_saved'
                component['object_snapshot_ref'] = ref
                verification = row.get('archive_verification')
                if verification is not None:
                    index = local_archives.record_index(record, row)
                    expected_verification = {
                        'status': 'verified', 'sha256': archive_file['sha256'], 'size': archive_file['size'],
                        'files': len(index['files']),
                        'directories': sum(bool(d['path']) for d in index.get('directories', [])),
                        'content_bytes': sum(f['size'] for f in index['files']),
                        'index_sha256': hashlib.sha256(local_archives.index_bytes(local_archives.archive_index(row['id'], index))).hexdigest(),
                    }
                    if local_archives.index_bytes(verification) != local_archives.index_bytes(expected_verification):
                        raise ValueError('ZIP verification does not match saved inventory')
                    component['archive_verification'] = copy.deepcopy(verification)
            elif installed and installed['gaps'] and component['status'] == 'metadata_only':
                component['status'] = 'incomplete'
    return {'version': version, 'scope': copy.deepcopy(SCOPE), 'components': components}


def validate(record):
    expected = inventory(record)
    supplied = record.get('backup')
    if supplied is not None and supplied != expected:
        raise ValueError('Backup contract does not match saved resource evidence')
    return expected


def summary(record):
    components = record['backup']['components']
    counts = {'local_saved': 0, 'incomplete': 0, 'metadata_only': 0, 'source_only': 0,
              'protected': 0, 'outside_scope': 0, 'absent': 0}
    rows = []
    verified = 0
    for identity, component in components.items():
        counts[component['status']] += 1
        checked = component.get('archive_verification', {}).get('status') == 'verified'
        verified += checked
        rows.append({'id': identity, 'name': component['name'], 'status': component['status'],
                     'archive_verified': checked, 'files': len(component['files']),
                     'has_archive': (component.get('storage') or {}).get('format') == 'zip',
                     'gaps': copy.deepcopy(component['gaps'])})
    return {'verified_archives': verified, 'counts': counts, 'components': rows}
