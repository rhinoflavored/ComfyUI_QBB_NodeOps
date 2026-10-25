"""Checkpointed component recovery. Existing content is verified, never overwritten."""
import json
import hashlib
import copy
import os
import re
from pathlib import Path
import sys
import uuid
import time
from . import file_snapshots as files


def read_json(path):
    for attempt in range(20):
        files.no_links(path)
        try:
            return json.loads(Path(path).read_text(encoding='utf-8'))
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.025)


def atomic_json(path, data):
    files.no_links(path)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temp.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    # Windows readers can briefly hold the destination without FILE_SHARE_DELETE.
    # Keep publication atomic; never truncate a status file observed by the UI.
    for attempt in range(20):
        try:
            temp.replace(path)
            break
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.025)


def verify(folder, snapshot, core, store):
    from .record_compare import stable_hash
    scan = files.scan(folder, store, core=core, large_files='include',core_dirs=snapshot.get('core_dirs',[]),runtime_files=snapshot.get('policy',{}).get('runtime_files',False))
    if scan['errors']:
        raise ValueError('无法读取已恢复组件，请勿修改目标目录')
    expected = {f['path']: f['sha256'] for f in snapshot['files']}
    if scan['single_file'] != snapshot.get('single_file', False):
        raise ValueError('组件文件/目录类型发生变化；拒绝继续')
    actual = {('' if scan['single_file'] else f['path']): stable_hash(folder if scan['single_file'] else folder / f['path'], f['signature']) for f in scan['files']}
    if actual != expected or {d['path'] for d in scan['directories']} != {d['path'] for d in snapshot['directories']}:
        raise ValueError('已恢复组件发生变化；拒绝覆盖，请使用新目标目录')
    expected_modes = {f['path']: files.permission_mode(f.get('mode')) for f in snapshot['files']}
    actual_modes = {('' if scan['single_file'] else f['path']): files.permission_mode(f['signature'][4]) for f in scan['files']}
    expected_dirs = {d['path']: files.permission_mode(d.get('mode')) for d in snapshot['directories']}
    actual_dirs = {d['path']: files.permission_mode(d['mode']) for d in scan['directories']}
    if actual_modes != expected_modes or actual_dirs != expected_dirs:
        raise ValueError('已恢复组件权限发生变化；拒绝继续，请恢复权限或使用新目标目录')
    # New links must not hide changes during a resume.
    relevant = lambda rows: [r for r in rows if r.get('reason') != 'excluded cache/environment/assets']
    if relevant(scan['skipped']) != relevant(snapshot['skipped']):
        raise ValueError('组件排除项或链接发生变化；拒绝继续')
    files.assert_same(folder, store, scan)


def check_git_submodules(folder, run):
    files.no_links(folder)
    tree = run(['git','-c','core.hooksPath=','-C',folder,'ls-tree','-r','--full-tree','HEAD'])
    if any(line.startswith('160000 ') for line in tree.splitlines()):
        raise ValueError('此 Git 记录未保存子模块文件，不能确认恢复完整；请从包含已初始化子模块的源实例重新创建记录，或提供完整备份。')


def component(repo, destination, store, run):
    meta = destination / '.nodeops-recovery'
    files.no_links(meta)
    receipt = meta / ('component-' + repo['id'] + '.json')
    folder = destination / repo['target']
    files.no_links(folder)
    if not folder.resolve().is_relative_to(destination.resolve()):
        raise ValueError('恢复路径越界')
    if repo['skip']:
        return {'id':repo['id'], 'name':repo['name'], 'status':'skipped', 'error':repo['skip']}
    identity = hashlib.sha256(json.dumps({k:repo.get(k) for k in (
        'target','snapshot','substitution','source','source_scan','url','commit','recorded_root')},
        sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()
    if receipt.exists():
        files.no_links(receipt)
        saved = json.loads(receipt.read_text(encoding='utf-8'))
        if saved['target'] != repo['target']:
            raise ValueError('组件检查点不匹配')
        if saved.get('source_identity') and saved['source_identity'] != identity:
            raise ValueError('恢复计划与组件检查点不匹配；请使用新目录')
        if not re.fullmatch('stage-[a-f0-9]{32}', saved['stage']):
            raise ValueError('Invalid staging checkpoint')
        stage = meta / saved['stage']
        if not repo.get('snapshot') and not repo.get('substitution'):
            check_git_submodules(folder if folder.exists() else stage, run)
        # Receipt is published before rename, so a crash at rename is recoverable.
        if not folder.exists():
            files.no_links(stage)
            verify(stage, saved['snapshot'], repo['core'], store)
            folder.parent.mkdir(parents=True, exist_ok=True)
            stage.rename(folder)
        verify(folder, saved['snapshot'], repo['core'], store)
        return {'id':repo['id'], 'name':repo['name'], 'status':saved['status'], 'reused':True,'path_notes':saved.get('path_notes',[]),
                'checkpoint_binding':'matched' if saved.get('source_identity') else 'legacy_unbound'}
    if folder.exists():
        raise ValueError('目标组件已存在但没有检查点；拒绝覆盖')
    stage = meta / ('stage-' + uuid.uuid4().hex)
    restored_snapshot = None
    if repo.get('substitution'):
        snapshot = files.capture(Path(repo['source']), store, expected=repo['source_scan'], **repo['source_scan']['policy'])
        files.restore(snapshot, store, stage)
        restored_snapshot = snapshot
    elif repo['snapshot']:
        files.restore(repo['snapshot'], store, stage)
        restored_snapshot = repo['snapshot']
    else:
        run(['git','-c','core.hooksPath=','clone','--no-checkout','--',repo['url'],stage])
        run(['git','-c','core.hooksPath=','-C',stage,'checkout','--detach',repo['commit']])
        if run(['git','-c','core.hooksPath=','-C',stage,'rev-parse','HEAD']).strip() != repo['commit']:
            raise ValueError('Git commit 校验失败')
        check_git_submodules(stage, run)
    path_notes=[]
    if repo['core']:
        from .recovery_paths import relocate
        path_notes=relocate(stage,repo.get('recorded_root',''))
    if restored_snapshot is not None:
        expected = copy.deepcopy(restored_snapshot)
        if any(n['kind'] == 'relocated' for n in path_notes):
            # Only this known configuration file may intentionally differ.
            config = stage/'extra_model_paths.yaml'
            for entry in expected['files']:
                if entry['path'] == 'extra_model_paths.yaml':
                    info = config.stat()
                    entry.update(sha256=files.stable_hash(config, files.signature(info)), size=info.st_size)
    # Fingerprint the materialized tree, including deliberate path relocations.
    policy = repo['source_scan']['policy'] if repo.get('substitution') else (repo.get('snapshot') or {}).get('policy', {})
    scan = files.scan(stage, store, core=repo['core'], large_files='include',core_dirs=policy.get('core_dirs',[]),runtime_files=policy.get('runtime_files',False))
    if scan['errors']:
        raise ValueError('暂存文件无法校验')
    from .record_compare import stable_hash
    receipt_snapshot = {'policy':scan['policy'],'core_dirs':scan['policy']['core_dirs'],'single_file':scan['single_file'], 'files':[{'path':'' if scan['single_file'] else f['path'], 'mode':f['signature'][4], 'sha256':stable_hash(stage if scan['single_file'] else stage/f['path'],f['signature'])} for f in scan['files']], 'directories':scan['directories'], 'skipped':scan['skipped']}
    files.assert_same(stage, store, scan)
    if repo['core'] and not (stage/'main.py').is_file():
        raise ValueError('ComfyUI 核心缺少 main.py')
    outcome = 'substituted' if repo.get('substitution') else 'restored'
    if restored_snapshot is not None:
        files.verify_restored(expected, stage)
    verify(stage, receipt_snapshot, repo['core'], store)
    atomic_json(receipt, {'target':repo['target'], 'stage':stage.name, 'snapshot':receipt_snapshot, 'status':outcome,'path_notes':path_notes,'source_identity':identity})
    folder.parent.mkdir(parents=True, exist_ok=True)
    stage.rename(folder)
    verify(folder, receipt_snapshot, repo['core'], store)
    return {'id':repo['id'], 'name':repo['name'], 'status':outcome, 'reused':False,'path_notes':path_notes,'checkpoint_binding':'matched'}


def launcher(destination, cpu=False):
    # Fixed relative paths avoid encoding the user's (possibly non-ASCII) directory.
    path = destination / ('run_comfyui.bat' if os.name == 'nt' else 'run_comfyui.sh')
    flags = ' --cpu' if cpu else ''
    if os.name == 'nt':
        content = '@echo off\nsetlocal DisableDelayedExpansion\ncd /d "%~dp0ComfyUI" || exit /b 1\n"..\\python_env\\.venv\\Scripts\\python.exe" main.py' + flags + ' %*\nif errorlevel 1 pause\n'
    else:
        content = '#!/bin/sh\ncd "$(dirname "$0")/ComfyUI" || exit 1\nexec "../python_env/.venv/bin/python" main.py' + flags + ' "$@"\n'
    files.no_links(path)
    if path.exists():
        if path.read_text(encoding='utf-8') != content:
            raise ValueError('启动器已被修改，拒绝覆盖')
    else:
        temp=destination/('.launcher-'+uuid.uuid4().hex)
        try:
            with temp.open('x',encoding='utf-8',newline='') as stream:
                stream.write(content);stream.flush();os.fsync(stream.fileno())
            os.link(temp,path)  # Atomic publication, refusing raced user files.
        finally:
            if temp.exists():temp.unlink()
    if os.name != 'nt':path.chmod(path.stat().st_mode | 0o111)
    return path


def execute(prepared, destination, store, run, save, dependency_install):
    ledger=destination/'.nodeops-recovery/phases.json'
    files.no_links(ledger)
    phases=json.loads(ledger.read_text(encoding='utf-8')) if ledger.exists() else {}
    if not isinstance(phases,dict) or any(not isinstance(p,dict) or type(p.get('attempt',0)) is not int or p.get('attempt',0)<0 for p in phases.values()):
        raise ValueError('恢复阶段检查点损坏；拒绝继续')
    for item in phases.values():
        item['status']='pending'
    atomic_json(ledger,phases)
    save(phases=dict(phases),path_notes=[])
    def phase(name,fn):
        previous=phases.get(name,{})
        phases[name]={'status':'running','attempt':previous.get('attempt',0)+1,'started':time.time()}
        atomic_json(ledger,phases);save(phases=dict(phases))
        try:
            result=fn()
        except Exception as exc:
            phases[name].update(status='failed',error=str(exc),finished=time.time())
            atomic_json(ledger,phases);save(phases=dict(phases));raise
        phases[name].update(status='completed',finished=time.time())
        atomic_json(ledger,phases);save(phases=dict(phases));return result
    outcomes = []
    for repo in prepared['repositories']:
        save(stage='恢复组件：' + repo['name'])
        try:
            item = phase('component:'+repo['id'],lambda:component(repo, destination, store, run))
        except Exception as exc:
            item = {'id':repo['id'], 'name':repo['name'], 'status':'failed', 'error':str(exc)}
        outcomes.append(item)
        save(items=outcomes, processed=len(outcomes), completed=sum(i['status'] in ('restored', 'substituted') for i in outcomes), total=len(prepared['repositories']))
        if repo['core'] and item['status'] == 'failed':
            raise ValueError(item['error'])
    verification_receipts = []
    for repo, outcome in zip(prepared['repositories'], outcomes):
        if outcome['status'] in ('restored','substituted'):
            receipt = destination/'.nodeops-recovery'/('component-'+repo['id']+'.json')
            files.no_links(receipt)
            verification_receipts.append((repo, receipt, receipt.read_bytes()))
    environment = destination/'python_env'
    environment.mkdir(exist_ok=True)
    files.no_links(environment)
    python = environment/'.venv'/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    # venv creation is repeatable after interruption; use copies for link-free paths.
    files.no_links(python)
    save(stage='准备 Python 环境')
    if prepared.get('runtime'):
        from .recovery_environment import prepare_python
        python = phase('python',lambda:prepare_python(prepared['runtime'], environment, run, save))
    else:
        phase('python',lambda:run([sys.executable,'-m','venv','--copies',environment/'.venv']))
    save(stage='安装并检查依赖')
    phase('dependencies',lambda:dependency_install(prepared, environment, python))
    # Dependency installation must not silently change already restored components.
    def verify_components():
        for repo, receipt, raw in verification_receipts:
            files.no_links(receipt)
            if receipt.read_bytes() != raw:
                raise ValueError('恢复检查点在环境安装期间发生变化；拒绝确认完成')
            saved = json.loads(raw)
            verify(destination/repo['target'], saved['snapshot'], repo['core'], store)
    phase('files_verified', verify_components)
    cpu = any(pin.lower().startswith('torch==') and '+cpu' in pin.lower() for pin in prepared['pins'])
    path = phase('launcher',lambda:launcher(destination, cpu))
    # Registry membership is not a claim of runtime verification. Do not
    # publish instances with failed components; explicit skips/substitutions
    # remain usable partial restores and retain their existing result status.
    if not any(item['status'] == 'failed' for item in outcomes):
        from .instances import add_instance
        nodes = destination/'ComfyUI'/'custom_nodes'
        files.no_links(nodes)
        nodes.mkdir(exist_ok=True)
        instance = phase('registration',lambda:add_instance(str(destination/'ComfyUI'), destination.name))
        save(instance_id=instance['id'])
    path_notes=[n for item in outcomes for n in item.get('path_notes',[])]
    partial = prepared['recovery_state'] == 'partial' or any(i['status'] != 'restored' for i in outcomes) or any(n['kind']=='external' for n in path_notes)
    save(status='partial' if partial else 'rebuilt_unverified', stage='文件及依赖恢复完成；尚未验证启动和工作流', launcher=str(path), python=str(python), notes=prepared['warnings'], path_notes=path_notes, error='')
