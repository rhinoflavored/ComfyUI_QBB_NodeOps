"""Explicit, read-only recovery choices. Never infer replacement from old paths."""
import hashlib
import json
import re
from pathlib import Path
from . import file_snapshots as files


def prepare(record, repos, choices, current, store):
    if not isinstance(choices, dict):
        raise ValueError('Invalid recovery choices')
    valid = {str(i) for i in range(len(repos))}
    if set(choices) - valid:
        raise ValueError('Unknown recovery component')
    blockers, notes = [], []
    seen = set()
    verified = {}
    for i, (original, repo) in enumerate(zip([record['core'], *record['plugins']], repos)):
        repo['id'] = str(i)
        if i and (repo['name'].casefold() in seen or not re.fullmatch(r'[\w.-]+', repo['name']) or repo['name'] in ('.', '..')):
            blockers.append('不安全或重复的插件名称：' + repo['name'])
            repo.update(gap='不安全或重复的插件名称', action='pending')
            continue
        seen.add(repo['name'].casefold())
        reason = repo['skip']
        snapshot = repo.get('snapshot')
        repo['object_verification'] = {'status':'not_checked'}
        if snapshot and not reason:
            try:
                repo['object_verification'] = files.validate_snapshot(snapshot, store, verified)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                reason = str(exc)
                repo['object_verification'] = {'status':'failed', 'error':reason}
        repo['gap'] = reason
        choice = choices.get(str(i), {})
        if not isinstance(choice, dict):
            raise ValueError('Invalid component choice')
        action = choice.get('action', 'pending' if reason else 'recorded')
        repo['action'] = action
        repo['substitution'] = False
        if action == 'recorded' and not reason:
            continue
        if action == 'pending':
            blockers.append(repo['name'] + ': 请明确选择跳过、提供备份或使用当前内容。')
            continue
        if action == 'skip' and not repo['core']:
            repo['skip'] = '用户选择跳过'
            notes.append(repo['name'] + ': 将跳过，不是完整恢复。')
            continue
        if action not in ('backup', 'current') or not reason:
            raise ValueError('Invalid recovery action: ' + action)
        try:
            if action == 'current':
                matches = [current['core']] if repo['core'] else [p for p in current['plugins'] if p['name'].casefold() == repo['name'].casefold()]
                if len(matches) != 1:
                    raise ValueError('当前实例没有唯一同名组件')
                source = Path(matches[0]['path'])
            else:
                source = Path(choice.get('path', ''))
            single = not repo['core'] and repo['name'].endswith('.py')
            if not source.is_absolute() or not (source.is_file() and source.suffix == '.py' if single else source.is_dir()):
                raise ValueError('请选择 .py 插件文件的绝对路径' if single else '请选择组件备份文件夹的绝对路径（不支持压缩包）')
            files.no_links(source)
            if repo['core'] and not (source / 'main.py').is_file():
                raise ValueError('ComfyUI 文件夹缺少 main.py')
            core_dirs = (original.get('snapshot') or {}).get('policy', {}).get(
                'core_dirs', record.get('capture_options', {}).get('core_dirs', [])) if repo['core'] else []
            scan = files.scan(source, store, core=repo['core'], large_files='include', core_dirs=core_dirs)
            files.plan_content(source, scan)
            if scan['errors'] or not scan['files']:
                raise ValueError('来源为空或有无法读取的文件')
            repo.update(skip=None, snapshot=None, substitution=True, source=str(source), source_scan=scan)
            notes.append(repo['name'] + ': 使用' + ('当前内容' if action == 'current' else '用户提供的备份') + '替代；不能证明与记录一致。')
            for item in scan['skipped']:
                notes.append(repo['name'] + ': 排除 ' + item['path'])
        except (OSError, ValueError) as exc:
            blockers.append(repo['name'] + ': ' + str(exc))
    return blockers, notes


def fingerprint(record, plan):
    payload = {'record': record, 'repositories': plan['repositories'], 'pins': plan['pins'], 'blockers': plan['blockers'], 'warnings': plan['warnings'], 'runtime':plan.get('runtime'), 'dependencies':plan.get('dependencies')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
