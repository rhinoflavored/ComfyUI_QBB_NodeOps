"""Opt-in Windows offline runtime preparation; immutable, content-addressed payload."""
import copy
import json
import os
from pathlib import Path
import platform
import re
import shutil
import tempfile
import threading
import time
import uuid
from . import file_snapshots as files

LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
STATE = {'status':'idle'}


def status():
    with STATE_LOCK:return copy.deepcopy(STATE)


def progress(**values):
    with STATE_LOCK:STATE.update(values)


def payload(record):
    data=record.get('offline_environment')
    if not data:return None
    if data.get('format')!=1 or data.get('status')!='ready':raise ValueError('Unsupported offline environment')
    snapshot=data['snapshot']
    if snapshot.get('format')!=2 or snapshot.get('single_file') or snapshot.get('skipped'):
        raise ValueError('Offline environment snapshot is incomplete')
    paths={f['path']:f for f in snapshot['files']}
    executable=data['executable'];files.safe_parts(executable)
    if not executable.startswith('python/') or executable not in paths:raise ValueError('Invalid offline Python path')
    if data['python']!=record['python']:raise ValueError('Offline Python metadata does not match record')
    from .recovery_environment import canonicalize_name, Version
    expected={canonicalize_name(p['name']):p['version'] for p in record['packages']}
    if len(expected)!=len(record['packages']):raise ValueError('Duplicate package names')
    found=set()
    for row in data['packages']:
        key=row['key'];relative=row['relative'];files.safe_parts(relative)
        if not re.fullmatch('[a-z0-9][a-z0-9-]*',key) or row.get('action')!='offline' or canonicalize_name(row['name'])!=key:raise ValueError('Invalid offline dependency action')
        if key in found or key not in expected or Version(row['version'])!=Version(expected[key]):raise ValueError('Offline package inventory differs')
        found.add(key)
        info=row['wheel'];entry=paths.get(relative)
        if not relative.startswith('wheels/') or not entry or entry['sha256']!=info['sha256'] or Path(relative).name!=info['filename'] or info['name']!=key or Version(info['version'])!=Version(row['version']):
            raise ValueError('Invalid offline wheel reference')
    if found!=set(expected):raise ValueError('Offline package inventory is incomplete')
    return data


def snapshot_components(record):
    data=payload(record)
    return [{'snapshot':data['snapshot']}] if data else []


def prepare(record_id, wheel_directory='', save=progress, runner=None):
    from . import record_archives as archives, version_records as records, recovery_environment as env
    from .rebuild import run
    from .rebuild_executor import atomic_json
    runner=runner or run
    if platform.system()!='Windows':raise ValueError('离线 Python 文件包目前仅支持 Windows；其他系统请使用联网重建。')
    with archives.exclusive() as store:
        record=records.read(record_id)
        if record.get('schema') == 4:
            raise ValueError('Phase 1 Snapshot does not support legacy offline environment reconstruction')
        if any(not r.get('snapshot') for r in [record['core'],*record['plugins']]):
            raise ValueError('代码尚未全部保存为快照。请先用“离线文件备份”创建记录，避免恢复时仍需 Git 下载。')
        # Verify saved objects before expensive downloads; never replace historical files.
        _,refs=archives.references(record)
        for sha,size in refs.items():
            with files.object_path(store,sha).open('rb') as stream:archives.copy_checked(stream,None,sha,size)
        options={'python':'managed','dependencies':{}}
        if wheel_directory:
            directory=Path(wheel_directory)
            if not directory.is_absolute() or not directory.is_dir():raise ValueError('请输入 wheel 文件夹绝对路径')
            files.no_links(directory)
            expected={env.canonicalize_name(p['name']):p['version'] for p in record['packages']}
            for wheel in directory.glob('*.whl'):
                info=env.wheel_info(wheel);key=info['name']
                if key not in expected or env.Version(info['version'])!=env.Version(expected[key]):continue
                if key in options['dependencies']:raise ValueError('同一依赖有多个匹配 wheel，请仅保留目标平台版本：'+key)
                options['dependencies'][key]={'action':'wheel','path':str(wheel)}
        original=copy.deepcopy(record);original.pop('offline_environment',None)
        runtime,packages,blockers,_=env.build_plan(original,options)
        if blockers:raise ValueError('\n'.join(blockers))
        store.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.offline-build-',dir=store) as tmp:
            workspace=Path(tmp);build=workspace/'build';build.mkdir()
            python=env.prepare_python(runtime,build,runner,save)
            env.install(packages,build,python,runner,save)
            assets=workspace/'assets';assets.mkdir()
            # Downloaded managed base stays separate from the temporary venv.
            base=build/'.python'
            if not base.is_dir():raise ValueError('独立 Python 下载目录缺失')
            inventory=files.scan(base,store,large_files='include',runtime_files=True)
            if inventory['errors'] or any(s['reason']!='excluded cache/environment/assets' or Path(s['path']).name!='__pycache__' for s in inventory['skipped']):
                raise ValueError('独立 Python 包含不支持的链接或排除内容')
            base_snapshot=files.capture(base,store,large_files='include',expected=inventory,runtime_files=True)
            files.restore(base_snapshot,store,assets/'python')
            # venv records its managed base path; no parent interpreter is bundled.
            cfg=(build/'.venv/pyvenv.cfg').read_text(encoding='utf-8')
            home=next((line.split('=',1)[1].strip() for line in cfg.splitlines() if line.strip().startswith('home =')),None)
            if not home:raise ValueError('无法定位独立 Python')
            executable=Path(home)/'python.exe';files.no_links(executable)
            relative=executable.resolve().relative_to(base.resolve())
            executable_rel='python/'+relative.as_posix()
            wheels=assets/'wheels';wheels.mkdir();packed=[]
            for row in packages:
                receipt=json.loads((build/'wheel-cache'/(row['key']+'.json')).read_text(encoding='utf-8'))
                files.safe_parts(receipt['relative']);source=build/'wheel-cache'/receipt['relative']
                info=env.wheel_info(source)
                if info!=receipt['wheel']:raise ValueError('收集期间 wheel 内容变化')
                target=wheels/info['filename'];shutil.copyfile(source,target)
                if env.wheel_info(target)!=info:raise ValueError('wheel 复制校验失败')
                packed.append({'name':row['name'],'key':row['key'],'version':row['version'],'action':'offline','relative':'wheels/'+info['filename'],'wheel':info,'source_substitution':True})
            snapshot=files.capture(assets,store,large_files='include',runtime_files=True)
            if snapshot['skipped']:raise ValueError('离线资源存在排除项，不能发布')
            files.verify_source(assets,store,snapshot,files.scan(assets,store,large_files='include',runtime_files=True))
            data={'format':1,'status':'ready','python':record['python'],'executable':executable_rel,'snapshot':snapshot,'packages':packed,'prepared_at':time.time(),'original_binary_identity':False}
            candidate=copy.deepcopy(record);candidate['offline_environment']=data;payload(candidate)
            # Prove relocation and installation from saved objects with no download path.
            test=workspace/'validation';test.mkdir()
            offline_runtime={**runtime,'method':'offline','offline':data,'store':str(store)}
            save(stage='从已保存对象离线重建并校验环境')
            offline_python=env.prepare_python(offline_runtime,test,runner,save)
            env.install(packed,test,offline_python,runner,save)
            candidate.update(id=uuid.uuid4().hex,label=(record.get('label','')+' [离线环境]')[:120],offline_prepared_from=record_id)
            # Validate archive limits before publishing an exportable record.
            candidate,_=archives.references(candidate)
            atomic_json(store/(candidate['id']+'.json'),candidate)
            return {'id':candidate['id'],'status':'ready','packages':len(packed),'original_binary_identity':False}


def start(record_id, wheel_directory='', confirmed=False):
    if confirmed is not True:raise ValueError('请确认下载并执行独立 Python、安装依赖以验证离线环境。')
    if not LOCK.acquire(blocking=False):raise ValueError('已有离线准备任务正在运行')
    with STATE_LOCK:
        STATE.clear();STATE.update(status='running',record_id=record_id,stage='准备离线环境',started=time.time())
    def worker():
        try:
            result=prepare(record_id,wheel_directory);progress(status='completed',result=result,stage='离线环境记录已创建')
        except Exception as exc:progress(status='failed',error=str(exc))
        finally:LOCK.release()
    try:threading.Thread(target=worker,daemon=True).start()
    except Exception:
        LOCK.release();progress(status='failed',error='无法启动准备任务');raise
    return status()
