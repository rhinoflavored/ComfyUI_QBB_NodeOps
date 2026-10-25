"""Read-only comparisons with the running instance or another saved inventory."""
import hashlib
import os
import re
import threading
from pathlib import Path
from . import file_snapshots as files
from . import version_records as records
from .manifest import exclusion_kind

LOCK = threading.Lock()


def covered(path, prefixes):
    return any(prefix in ('', '.') or path == prefix or path.startswith(prefix + '/') for prefix in prefixes)


def stable_hash(path, expected):
    files.no_links(path)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        if files.signature(os.fstat(stream.fileno())) != expected:
            raise ValueError('文件在扫描后发生变化')
        for block in iter(lambda: stream.read(files.CHUNK), b''):
            digest.update(block)
        if files.signature(os.fstat(stream.fileno())) != expected:
            raise ValueError('文件在读取时发生变化')
    files.no_links(path)
    if files.signature(path.stat()) != expected:
        raise ValueError('文件在读取后发生变化')
    return digest.hexdigest()


def compare_files(snapshot, source, store, core=False):
    result = {'changes': [], 'unverified': [], 'checked_files': 0}
    def compare_permissions(path, before, mode, kind):
        try:
            old, new = files.permission_mode(before.get('mode')), files.permission_mode(mode)
        except ValueError as exc:
            result['unverified'].append({'path': path or '.', 'reason': str(exc)})
            return
        if old != new:
            result['changes'].append({'path': path or '.', 'kind': 'permissions', 'type': kind,
                                      'target': oct(old), 'current': oct(new)})
    if not snapshot:
        result['unverified'].append({'path': '.', 'reason': '记录没有文件快照，无法核实本地内容。'})
        return result
    if snapshot.get('format') not in (1, 2):
        result['unverified'].append({'path': '.', 'reason': '不支持的文件快照格式。'})
        return result
    baseline = {}
    try:
        if snapshot.get('format') == 2:
            for directory in snapshot.get('directories', []):
                if directory['path']:
                    files.safe_parts(directory['path'])
        for entry in snapshot['files']:
            files.safe_parts(entry['path'])
            if entry['path'] in baseline or not re.fullmatch('[a-f0-9]{64}', entry['sha256']):
                raise ValueError('Invalid file manifest')
            baseline[entry['path']] = entry
    except (ValueError, TypeError, KeyError):
        result['unverified'].append({'path': '.', 'reason': '文件清单无效，不能可靠比较。'})
        return result
    source = Path(source)
    if snapshot['format'] == 1:
        result['unverified'].append({'path': '.', 'reason': '旧快照未记录空目录，仅核对文件内容。'})
    # Existing recorded files must still be hashed if they grew beyond the
    # capture threshold. Large unrecorded additions obey the saved policy.
    scan = files.scan(source, store, core=core, large_files='include',core_dirs=snapshot.get('policy',{}).get('core_dirs',[]))
    policy = snapshot.get('policy', {})
    old_excluded = [item['path'] for item in snapshot.get('skipped', [])]
    blocked = [item['path'] for item in scan['errors'] + scan['skipped']]
    for item in snapshot.get('skipped', []):
        result['unverified'].append({'path': item['path'], 'reason': '保存时已排除：' + item['reason']})
    for item in scan['errors'] + scan['skipped']:
        if not covered(item['path'], old_excluded):
            result['unverified'].append({'path': item['path'], 'reason': '当前无法核实：' + item['reason']})
    current = {entry['path']: entry for entry in scan['files']}
    for path in sorted(baseline.keys() | current.keys()):
        if covered(path, old_excluded + blocked):
            continue
        before, after = baseline.get(path), current.get(path)
        if after is None:
            result['changes'].append({'path': path, 'kind': 'deleted', 'type': 'file'})
            continue
        if before is None and policy.get('large_files', 'exclude') != 'include' and after['signature'][0] > files.MAX_FILE:
            result['unverified'].append({'path': path, 'reason': '新增大文件不在记录的保存策略范围内。'})
            continue
        target = source if scan['single_file'] else source.joinpath(*files.safe_parts(path))
        try:
            digest = after.get('sqlite_sha256') or stable_hash(target, after['signature'])
        except (OSError, ValueError) as exc:
            result['unverified'].append({'path': path, 'reason': str(exc)})
            continue
        result['checked_files'] += 1
        if before is None:
            result['changes'].append({'path': path, 'kind': 'added', 'type': 'file'})
        elif digest != before['sha256']:
            result['changes'].append({'path': path, 'kind': 'modified', 'type': 'file'})
        if before is not None:
            compare_permissions(path, before, after['signature'][4], 'file')
    if snapshot['format'] == 2:
        previous_dirs = {d['path'] for d in snapshot.get('directories', [])}
        current_dirs = {d['path'] for d in scan['directories']}
        old_modes = {d['path']: d for d in snapshot.get('directories', [])}
        for directory in scan['directories']:
            path = directory['path']
            if path in old_modes and not covered(path, old_excluded + blocked):
                compare_permissions(path, old_modes[path], directory['mode'], 'directory')
        for path in sorted(previous_dirs ^ current_dirs):
            if path and not covered(path, old_excluded + blocked):
                result['changes'].append({'path': path, 'kind': 'deleted' if path in previous_dirs else 'added', 'type': 'directory'})
    # Avoid reporting a mixture of states when files change during the scan.
    if files.scan(source, store, core=core, large_files='include',core_dirs=snapshot.get('policy',{}).get('core_dirs',[])) != scan:
        result['changes'] = []
        result['checked_files'] = 0
        result['unverified'].append({'path': '.', 'reason': '对比期间目录发生变化，请停止修改后重新对比。'})
    return result


def compare_packages(previous, current):
    def index(items):
        result = {}
        for item in items:
            name = re.sub(r'[-_.]+', '-', item['name']).lower()
            result.setdefault(name, []).append(item)
        return result
    before, after = index(previous), index(current)
    changes, unknown = [], []
    for name in sorted(before.keys() | after.keys()):
        a, b = before.get(name, []), after.get(name, [])
        if not name or len(a) > 1 or len(b) > 1:
            unknown.append({'name': name, 'reason': '依赖名称重复或缺失，不能可靠比较。'})
        elif not a or not b or a[0]['version'] != b[0]['version']:
            changes.append({'name': (b or a)[0]['name'], 'kind': 'added' if not a else 'deleted' if not b else 'modified',
                            'current': b[0]['version'] if b else None, 'target': a[0]['version'] if a else None})
        if len(a)==1 and len(b)==1:
            for field in ('source','commit','editable'):
                old,new=a[0].get(field),b[0].get(field)
                # Historical records omit editable for normal index installs.
                if field=='editable':old,new=bool(old),bool(new)
                if old!=new:
                    changes.append({'name':a[0]['name']+' · '+field,'kind':'modified','current':str(new),'target':str(old)})
            if any(not p.get('source') or p['source'].startswith(('unknown','local/private','unreadable')) for p in (a[0],b[0])):
                unknown.append({'name':name,'reason':'未保存可核实的完整依赖来源；同版本不证明二进制内容一致。'})
    return changes, unknown


def compare_snapshots(before, after, core=False):
    """Compare saved inventories only; never follow source paths or read objects."""
    result = {'changes': [], 'unverified': [], 'checked_files': 0}
    if not before or not after:
        result['unverified'].append({'path': '.', 'reason': '一侧没有文件快照，不能推断文件新增或删除。'})
        return result
    def inventory(snapshot):
        if type(snapshot.get('format')) is not int or snapshot['format'] not in (1, 2):
            raise ValueError('不支持的快照格式')
        entries = {}
        for kind, rows in [('file', snapshot['files']), ('directory', snapshot.get('directories', []))]:
            for row in rows:
                path = row['path']
                if path or kind == 'file':
                    files.safe_parts(path)
                if path in entries:
                    raise ValueError('清单包含重复路径')
                if kind == 'file' and (not re.fullmatch('[a-f0-9]{64}', row['sha256']) or type(row['size']) is not int or row['size'] < 0):
                    raise ValueError('文件摘要或长度无效')
                entries[path] = (kind, row)
        return entries
    try:
        a, b = inventory(before), inventory(after)
    except (KeyError, TypeError, ValueError) as exc:
        result['unverified'].append({'path': '.', 'reason': '清单无效：' + str(exc)})
        return result
    excluded = []
    for side, snapshot in [('起点', before), ('终点', after)]:
        for entry in snapshot.get('skipped', []):
            excluded.append(entry['path'])
            if exclusion_kind(entry, core) in ('missing_content', 'unknown'):
                result['unverified'].append({'path': entry['path'], 'reason': side + '记录排除：' + entry['reason']})
    if core:
        for scope in ('user', 'models', 'input', 'output'):
            selected = [scope in s.get('policy', {}).get('core_dirs', []) for s in (before, after)]
            if not all(selected):
                excluded.append(scope)
                if any(selected):
                    result['unverified'].append({'path': scope, 'reason': '两条记录保存范围不同，不作为文件新增或删除。'})
    full = all(s.get('format') == 2 and s.get('inventory') == 'complete_within_policy' and isinstance(s.get('policy'), dict) for s in (before, after))
    if not full:
        result['unverified'].append({'path': '.', 'reason': '一侧缺少完整范围清单，仅比较双方已记录文件，不推断缺失内容。'})
    for path in sorted(a.keys() | b.keys()):
        if covered(path, excluded):
            continue
        old, new = a.get(path), b.get(path)
        if old is None or new is None:
            entry = new or old
            if entry[0] == 'file' and entry[1]['size'] > files.MAX_FILE and any(s.get('policy', {}).get('large_files', 'exclude') != 'include' for s in (before, after)):
                result['unverified'].append({'path': path, 'reason': '大文件不在双方共同保存策略内，不推断新增或删除。'})
                continue
        if not full and (old is None or new is None or old[0] == 'directory' or new[0] == 'directory'):
            continue
        if old is None or new is None:
            result['changes'].append({'path': path or '.', 'kind': 'added' if old is None else 'deleted', 'type': (new or old)[0]})
            continue
        if old[0] != new[0]:
            result['changes'].append({'path': path or '.', 'kind': 'type_changed', 'type': new[0], 'target': old[0], 'current': new[0]})
            continue
        if old[0] == 'file':
            result['checked_files'] += 1
            if (old[1]['sha256'], old[1]['size']) != (new[1]['sha256'], new[1]['size']):
                result['changes'].append({'path': path, 'kind': 'modified', 'type': 'file'})
        try:
            old_mode, new_mode = (files.permission_mode(item[1].get('mode')) for item in (old, new))
            if old_mode != new_mode:
                result['changes'].append({'path': path or '.', 'kind': 'permissions', 'type': old[0], 'target': oct(old_mode), 'current': oct(new_mode)})
        except ValueError as exc:
            result['unverified'].append({'path': path or '.', 'reason': str(exc)})
    return result


def compare_record(record, current, store, *, saved=False):
    if record.get('schema') == 4 or current.get('schema') == 4:
        raise ValueError('Phase 1 Snapshot: unified Diff will be implemented in Phase 2')
    components, versions, unknown = [], [], []
    def group(rows):
        result = {}
        for row in rows:
            result.setdefault(row['name'].casefold(), []).append(row)
        return result
    before, after = group(record['plugins']), group(current['plugins'])
    pairs = [('ComfyUI', record['core'], current['core'], True)]
    for key in sorted(before.keys() | after.keys()):
        a, b = before.get(key, []), after.get(key, [])
        if len(a) > 1 or len(b) > 1:
            unknown.append({'name': key, 'reason': '存在同名插件，无法确定对应目录。'})
            continue
        pairs.append(((b or a)[0]['name'], a[0] if a else None, b[0] if b else None, False))
    for name, a, b, core in pairs:
        if not a or not b:
            versions.append({'name': name, 'kind': 'added' if not a else 'deleted', 'category': 'plugin',
                             'current': '存在' if b else None, 'target': '存在' if a else None})
            continue
        if a.get('commit') and b.get('commit'):
            if a['commit'] != b['commit']:
                versions.append({'name': name, 'kind': 'modified', 'category': 'git', 'current': b['commit'], 'target': a['commit']})
        elif a.get('kind') == 'git' or b.get('kind') == 'git':
            unknown.append({'name': name, 'reason': '缺少可比较的 Git 提交号。'})
        if saved:
            for field in ('kind', 'url', 'branch'):
                old, new = a.get(field), b.get(field)
                if old != new:
                    if old is None or new is None:
                        unknown.append({'name': name + ' · ' + field, 'reason': '一侧缺少来源元数据。'})
                    else:
                        versions.append({'name': name + ' · ' + field, 'kind': 'modified', 'category': 'source', 'target': old, 'current': new})
        if saved:
            detail = compare_snapshots(a.get('snapshot'), b.get('snapshot'), core)
        elif b.get('linked_source'):
            detail = {'changes': [], 'checked_files': 0, 'unverified': [{'path': '.', 'reason': '当前插件是链接，不跟随读取。'}]}
        else:
            detail = compare_files(a.get('snapshot'), b['path'], store, core)
        components.append({'name': name, **detail})
    dependencies, package_unknown = compare_packages(record['packages'], current['packages'])
    unknown.extend(package_unknown)
    runtime=[]
    for field in ('version','architecture','implementation','platform'):
        old,new=record.get('python',{}).get(field),current.get('python',{}).get(field)
        if not old or not new:
            unknown.append({'name':'Python · '+field,'reason':'缺少运行环境信息，无法核实。'})
        elif old!=new:
            runtime.append({'name':'Python · '+field,'kind':'modified','target':old,'current':new})
    unknown_count = len(unknown) + sum(len(c['unverified']) for c in components)
    changes = len(runtime) + len(versions) + len(dependencies) + sum(len(c['changes']) for c in components)
    return {'components': components, 'versions': versions, 'runtime':runtime, 'dependencies': dependencies, 'unverified': unknown,
            'change_count': changes, 'unverified_count': unknown_count,
            'checked_files': sum(c['checked_files'] for c in components),
            'state': 'differences' if changes else 'incomplete' if unknown_count else 'matched',
            'direction': 'record_to_record' if saved else 'record_to_current'}


def compare(record_id, other_record_id=None):
    if not LOCK.acquire(blocking=False):
        raise ValueError('已有文件对比正在执行，请稍后重试。')
    try:
        selected = records.read(record_id)
        if selected.get('schema') == 4:
            from .state_diff import inspect
            return inspect(record_id, other_record_id)['diff']
        if other_record_id is not None:
            before, after = records.read(record_id), records.read(other_record_id)
            result = compare_record(before, after, None, saved=True)
            result['endpoints'] = {'before': {'id': record_id, 'label': before.get('label', '')},
                                   'after': {'id': other_record_id, 'label': after.get('label', '')}}
            result['evidence'] = 'saved_manifests_not_object_health'
            return result
        # Inventory resolves the running instance, not arbitrary paths stored in JSON.
        return compare_record(records.read(record_id), records.inventory(), records.directory())
    finally:
        LOCK.release()
