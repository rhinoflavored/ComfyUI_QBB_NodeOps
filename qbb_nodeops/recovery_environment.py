"""Explicit runtime/package sources and hash-locked wheel recovery."""
import email.parser
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import uuid
import zipfile
from . import file_snapshots as files
try:
    from packaging.utils import canonicalize_name, parse_wheel_filename
    from packaging.version import Version
except ImportError:
    from pip._vendor.packaging.utils import canonicalize_name, parse_wheel_filename
    from pip._vendor.packaging.version import Version

PYPI = 'https://pypi.org/simple'
TORCH = 'https://download.pytorch.org/whl/'


def wheel_info(path):
    path = Path(path)
    files.no_links(path)
    name, version, _, _ = parse_wheel_filename(path.name)
    with zipfile.ZipFile(path) as archive:
        entries = [i for i in archive.infolist() if i.filename.endswith('.dist-info/METADATA')]
        if len(entries) != 1 or entries[0].file_size > 1024 * 1024:
            raise ValueError('wheel 元数据缺失或过大')
        data = email.parser.BytesParser().parsebytes(archive.read(entries[0]))
    if canonicalize_name(data.get('Name','')) != name or Version(data.get('Version','0')) != version:
        raise ValueError('wheel 文件名与元数据不一致')
    return {'name':str(name), 'version':str(version), 'sha256':files.hash_file(path), 'filename':path.name}


def build_plan(record, options=None):
    options = options or {}
    if not isinstance(options, dict) or not isinstance(options.get('dependencies', {}), dict):
        raise ValueError('Invalid environment options')
    blockers, warnings, packages = [], [], []
    py = record['python']
    version = py.get('version','')
    mode = options.get('python','auto')
    if mode not in ('auto','current','managed','offline'):
        raise ValueError('Invalid Python mode')
    if not re.fullmatch(r'3\.\d+\.\d+', version):
        blockers.append('只支持记录完整版本号的 CPython 3 正式版本。')
    arch = lambda value: {'amd64':'x86_64','x64':'x86_64','aarch64':'arm64'}.get(value.lower(),value.lower())
    if arch(py.get('architecture','')) != arch(platform.machine()) or py.get('platform','').split('-')[0] != platform.system():
        blockers.append('暂不支持跨操作系统或跨 CPU 架构恢复。')
    if py.get('implementation','CPython') != 'CPython':
        blockers.append('暂不支持此 Python 实现。')
    matching = platform.python_implementation() == 'CPython' and version == platform.python_version()
    method = ('current' if matching else 'managed') if mode == 'auto' else mode
    if method == 'current' and not matching:
        blockers.append('当前 Python 不匹配记录，请选择自动或下载同版本 Python。')
    runtime = {'version':version, 'architecture':py.get('architecture'), 'method':method, 'implementation':'CPython'}
    if mode=='offline' or (mode=='auto' and record.get('offline_environment')):
        from .offline_environment import payload
        from . import version_records
        data=payload(record)
        if not data:raise ValueError('记录没有离线环境资源')
        if platform.system()!='Windows':blockers.append('离线环境文件包仅支持 Windows。')
        if options.get('dependencies'):blockers.append('离线恢复使用包内锁定依赖，不接受来源替换。')
        if any(not r.get('snapshot') for r in [record['core'],*record['plugins']]):blockers.append('离线恢复缺少代码快照。')
        runtime.update(method='offline',offline=data,store=str(version_records.directory()))
        for entry in data['snapshot']['files']:
            try:
                obj=files.object_path(version_records.directory(),entry['sha256'])
                if obj.stat().st_size!=entry['size'] or files.hash_file(obj)!=entry['sha256']:
                    raise ValueError('哈希或长度不符')
            except (ValueError,OSError) as exc:
                blockers.append('离线资源缺失或损坏：'+entry['path']+' · '+str(exc));break
        return runtime,data['packages'],blockers,['使用已校验的离线 Python 和 wheel，不联网下载；不是原始二进制一致性证明，也不验证显卡驱动或工作流。']
    warnings.append('Python 下载使用 uv 的 python-build-standalone；版本相同不保证与原解释器的构建完全相同。' if method == 'managed' else '使用当前匹配解释器创建虚拟环境；基础 Python 仍须保留。')
    seen = set()
    for item in record['packages']:
        name, ver = item.get('name',''), item.get('version','')
        key = canonicalize_name(name)
        if key in seen:
            blockers.append('依赖名称重复：' + name)
            continue
        seen.add(key)
        valid = bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',name) and re.fullmatch(r'[A-Za-z0-9.!+_-]+',ver))
        try: Version(ver)
        except ValueError: valid = False
        supplied = options.get('dependencies',{}).get(key,{})
        if not isinstance(supplied,dict):raise ValueError('Invalid dependency choice')
        suffix = re.search(r'\+(cpu|cu\d+)$',ver)
        official = key in ('torch','torchvision','torchaudio') and suffix is not None
        unknown = item.get('source','').startswith('unknown') and not item.get('editable')
        default = ('pytorch' if official else 'pypi') if valid and unknown else 'pending'
        action = supplied.get('action',default)
        row = {'name':name,'key':key,'version':ver,'action':action,'recorded_source':item.get('source','unknown'),'source_substitution':action != 'skip'}
        if action not in ('pending','skip','pypi','pytorch','wheel'):
            raise ValueError('Invalid dependency action')
        if action == 'pending':blockers.append(name + ': 请提供 wheel、明确改用 PyPI，或跳过。')
        elif action != 'skip':
            if not valid:blockers.append(name + ': 依赖名称或版本无效。')
            elif action == 'pytorch':
                if not official:blockers.append(name + ': 没有可确定的 CPU/CUDA 构建后缀，请提供 wheel。')
                else:row['index'] = TORCH + suffix.group(1)
            elif action == 'pypi':row['index'] = PYPI
            elif action == 'wheel':
                try:
                    path = Path(supplied.get('path',''))
                    if not path.is_absolute():raise ValueError('请输入 wheel 绝对路径')
                    info = wheel_info(path)
                    if info['name'] != key or Version(info['version']) != Version(ver):raise ValueError('wheel 名称或版本与记录不匹配')
                    row.update(path=str(path),wheel=info)
                except (ValueError,OSError,zipfile.BadZipFile) as exc:blockers.append(name + ': ' + str(exc))
        packages.append(row)
    if set(options.get('dependencies',{})) - seen:raise ValueError('Unknown dependency choice')
    warnings.append('索引或补充 wheel 是明确的来源替代，不证明原始二进制一致。仅安装记录版本的 wheel，不编译源码；兼容性和依赖冲突在目标解释器中检查。')
    return runtime, packages, blockers, warnings


def prepare_python(runtime, environment, run, save):
    version = runtime['version']
    python = environment/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    probe = 'import json,platform; print(json.dumps([platform.python_version(),platform.machine(),platform.python_implementation()]))'
    def check(exe):
        actual = json.loads(run([exe,'-I','-S','-c',probe]))
        normalize = lambda a: {'amd64':'x86_64','aarch64':'arm64','x64':'x86_64'}.get(a.lower(),a.lower())
        if actual[0] != version or normalize(actual[1]) != normalize(runtime['architecture']) or actual[2] != 'CPython':
            raise ValueError('目标 Python 版本/架构/实现校验失败')
    files.no_links(python)
    offline_base=None
    if runtime['method']=='offline':
        from .rebuild_executor import verify, component
        assets=environment/'offline-assets';data=runtime['offline'];store=Path(runtime['store'])
        files.no_links(assets)
        meta=environment/'.nodeops-recovery'
        files.no_links(meta);meta.mkdir(exist_ok=True)
        # Older completed assets may have no receipt. Verify them, never overwrite.
        if not assets.exists() or (meta/'component-runtime-assets.json').exists():
            component({'id':'runtime-assets','name':'离线环境资源','target':'offline-assets',
                       'skip':'','core':False,'snapshot':data['snapshot']},environment,store,run)
        verify(assets,data['snapshot'],False,store)
        files.safe_parts(data['executable'])
        offline_base=assets.joinpath(*files.safe_parts(data['executable']))
        check(offline_base)
    if python.is_file():
        check(python)
        run([python,'-I','-m','ensurepip','--upgrade'])
        return python
    base = offline_base or Path(sys.executable)
    if runtime['method'] == 'managed':
        save(stage='下载 Python ' + version)
        tools = environment/'.runtime-tools'
        files.no_links(tools)
        run([sys.executable,'-m','pip','--isolated','install','--only-binary=:all:','--no-deps','--no-user','--index-url',PYPI,'--target',tools,'uv'])
        uv = tools/'bin'/('uv.exe' if os.name=='nt' else 'uv')
        files.no_links(uv)
        managed = environment/'.python'
        files.no_links(managed)
        files.no_links(environment/'.runtime-cache')
        env = {'UV_PYTHON_INSTALL_DIR':str(managed),'UV_CACHE_DIR':str(environment/'.runtime-cache'),'UV_PYTHON_INSTALL_BIN':'0','UV_PYTHON_PREFERENCE':'only-managed'}
        run([uv,'python','install',version],timeout=3600,extra_env=env)
        base = Path(run([uv,'python','find',version],extra_env=env).strip())
        if not base.resolve().is_relative_to(managed.resolve()):raise ValueError('下载的 Python 不在新实例内')
        files.no_links(base)
    check(base)
    run([base,'-I','-m','venv','--copies',environment/'.venv'])
    check(python)
    return python


def install(packages, environment, python, run, save):
    from .rebuild_executor import atomic_json
    cache = environment/'wheel-cache'
    files.no_links(cache)
    cache.mkdir(exist_ok=True)
    outcomes, locked = [], []
    for row in packages:
        if row['action'] == 'skip':
            outcomes.append({'name':row['name'],'status':'skipped'});save(dependency_items=outcomes);continue
        pin = row['name']+'=='+row['version']
        save(stage='准备依赖：' + pin)
        try:
            receipt = cache/(row['key']+'.json');files.no_links(receipt)
            if receipt.exists():
                data = json.loads(receipt.read_text(encoding='utf-8'))
                if data['request'] != row:raise ValueError('wheel 检查点与计划不匹配')
                relative = data['relative']
                files.safe_parts(relative)
                wheel = cache/relative
                info = wheel_info(wheel)
                if info != data['wheel']:raise ValueError('已缓存 wheel 已变化；拒绝安装')
            else:
                stage = cache/uuid.uuid4().hex;stage.mkdir()
                if row['action'] == 'offline':
                    files.safe_parts(row['relative'])
                    source=environment/'offline-assets'/row['relative']
                    if wheel_info(source)!=row['wheel']:raise ValueError('离线 wheel 哈希或元数据不匹配')
                    wheel=stage/row['wheel']['filename'];shutil.copyfile(source,wheel)
                    if wheel_info(wheel)!=row['wheel']:raise ValueError('离线 wheel 复制校验失败')
                elif row['action'] == 'wheel':
                    if wheel_info(row['path']) != row['wheel']:raise ValueError('补充的 wheel 已变化，请重新制定计划')
                    wheel = stage/row['wheel']['filename'];shutil.copyfile(row['path'],wheel)
                    if wheel_info(wheel) != row['wheel']:raise ValueError('复制 wheel 期间内容变化')
                else:
                    run([python,'-I','-m','pip','--isolated','download','--no-deps','--only-binary=:all:','--index-url',row['index'],'--dest',stage,pin],timeout=3600)
                    wheels = list(stage.glob('*.whl'))
                    if len(wheels)!=1:raise ValueError('未获取唯一 wheel')
                    wheel = wheels[0]
                info = wheel_info(wheel)
                if info['name'] != row['key'] or Version(info['version']) != Version(row['version']):raise ValueError('下载的 wheel 与记录版本不符')
                atomic_json(receipt,{'request':row,'relative':wheel.relative_to(cache).as_posix(),'wheel':info})
            # as_uri percent-encodes spaces and special characters.
            locked.append(wheel.resolve().as_uri()+' --hash=sha256:'+info['sha256'])
            outcomes.append({'name':row['name'],'status':'downloaded','sha256':info['sha256'],'source':row.get('index','local wheel')})
        except Exception as exc:
            outcomes.append({'name':row['name'],'status':'failed','error':str(exc)})
        save(dependency_items=outcomes)
    if any(i['status']=='failed' for i in outcomes):
        raise ValueError('部分依赖 wheel 准备失败，已保留成功缓存；查看逐项错误后重试。')
    lock = environment/'requirements-wheels.txt';files.no_links(lock)
    lock.write_text('\n'.join(locked)+'\n',encoding='utf-8')
    if locked:
        save(stage='按 SHA-256 安装缓存 wheel（禁止额外下载）')
        run([python,'-I','-m','pip','--isolated','install','--no-index','--no-deps','--only-binary=:all:','--require-hashes','-r',lock],timeout=3600)
    save(stage='检查依赖版本及一致性')
    installed = json.loads(run([python,'-I','-m','pip','--isolated','--disable-pip-version-check','list','--format=json']))
    versions = {canonicalize_name(p['name']):p['version'] for p in installed}
    for row in packages:
        if row['action']!='skip' and (row['key'] not in versions or Version(versions[row['key']])!=Version(row['version'])):
            raise ValueError('安装后的版本不匹配：'+row['name'])
    run([python,'-I','-m','pip','--isolated','check'])
    atomic_json(environment/'installed-packages.json',installed)
    for item in outcomes:
        if item['status']=='downloaded':item['status']='restored'
    save(dependency_items=outcomes)
