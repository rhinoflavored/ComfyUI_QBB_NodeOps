"""Opt-in reconstruction into a NEW directory; never edits the source environment."""
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from . import version_records as records
from . import file_snapshots
from .manifest import repository_gap, package_gap, exclusion_kind
from .install_network import network_environment
from .git_tools import require_git
from .settings import load_settings
from .update_check import _network_fetch_url

LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
STATE = {'status': 'idle', 'log': []}

def jobs():
    return records.directory() / 'rebuild-jobs'

def status():
    with STATE_LOCK:
        return task_state(json.loads(json.dumps(STATE)))


def task_state(row):
    row.pop('prepared', None)  # Large frozen plan stays on disk, not in each poll.
    row['can_resume'] = row.get('executor_version') == 1 and (row.get('status') in ('failed', 'interrupted') or (row.get('status') == 'partial' and any(i.get('status') == 'failed' for i in row.get('items', []))))
    # Preserve legacy status fields for existing clients and job files.
    row['execution_state'] = {'idle':'not_started', 'running':'running',
        'rebuilt_unverified':'completed_unverified', 'partial':'partial_unverified',
        'failed':'failed', 'interrupted':'interrupted'}.get(row.get('status'), 'unknown')
    row['runtime_verified'] = False
    phases = row.get('phases', {})
    checks = {c['name']:c['status'] for c in row.get('verification', {}).get('checks', [])}
    phase_result = lambda name: {'completed':'passed','failed':'failed','running':'running'}.get(phases.get(name, {}).get('status'),'not_checked')
    file_state = checks.get('files', phase_result('files_verified'))
    dependencies = checks.get('dependencies', phase_result('dependencies'))
    row['integrity'] = {'files':file_state, 'dependencies':dependencies,
                        'startup':checks.get('startup', 'not_checked'), 'workflow':checks.get('workflow', 'not_checked'),
                        'scope':'restored_components_only',
                        'has_gaps':row.get('status') == 'partial' or any(i.get('status') in ('failed','skipped','substituted') for i in row.get('items', []))}
    verifying = row.get('verification',{}).get('status') == 'running'
    row['can_verify'] = row.get('executor_version') == 1 and row.get('status') in ('partial','rebuilt_unverified') and not verifying
    if verifying:row['can_resume'] = False
    return row

def save(**values):
    with STATE_LOCK:
        STATE.update(values)
        if STATE.get('id'):
            job_dir = Path(STATE.get('storage') or records.directory()) / 'rebuild-jobs'
            file_snapshots.no_links(job_dir)
            job_dir.mkdir(parents=True, exist_ok=True)
            path = job_dir / (STATE['id'] + '.json')
            tmp = path.with_suffix('.tmp')
            tmp.write_text(json.dumps(STATE, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(path)

def history():
    result = []
    for path in jobs().glob('*.json'):
        try:
            row = json.loads(path.read_text(encoding='utf-8'))
            if row.get('status') == 'running' and row.get('id') != status().get('id'):
                row['status'] = 'interrupted'
                for phase in row.get('phases',{}).values():
                    if phase.get('status')=='running':phase['status']='interrupted'
            if row.get('verification',{}).get('status') == 'running' and row.get('id') != status().get('id'):
                row['verification']['status'] = 'interrupted'
            result.append(task_state(row))
        except (ValueError, OSError):
            pass
    return sorted(result, key=lambda r:r.get('created', 0), reverse=True)[:20]

def plan(record_id, choices=None):
    record = records.read(record_id)
    if record.get('schema') == 4:
        raise ValueError('Phase 1 Snapshot: recovery is not implemented; the legacy new-instance executor cannot consume this format')
    blockers, warnings, repos, differences, unverified = [], [], [], [], []
    if record.get('kind') != 'version_record':
        raise ValueError('Unsupported record')
    if choices is not None and not isinstance(choices, dict):
        raise ValueError('Invalid recovery choices')
    choices = choices or {}
    from .recovery_environment import build_plan
    runtime, dependencies, env_blockers, env_warnings = build_plan(record, choices.get('_environment'))
    blockers.extend(env_blockers)
    warnings.extend(env_warnings)
    names = set()
    for index, repo in enumerate([record['core'], *record['plugins']]):
        name = repo['name']
        reason = repository_gap(repo)
        snapshot = repo.get('snapshot')
        if snapshot:
            for excluded in snapshot.get('skipped', []):
                warnings.append(name + ': excluded ' + excluded['path'] + ' (' + excluded['reason'] + ')')
        if index and (name.lower() in names or not re.fullmatch(r'[\w.-]+', name) or name in ('.', '..')):
            reason = 'Unsafe or duplicate plugin folder name.'
        names.add(name.lower())
        if reason:
            warnings.append(name + ': ' + reason)
        repos.append({'name':name, 'snapshot':snapshot, 'skip':reason, 'core':index == 0, 'commit':repo.get('commit',''), 'url':repo.get('url',''), 'target':'ComfyUI' if index == 0 else 'ComfyUI/custom_nodes/' + name})
        if index==0:repos[-1]['recorded_root']=repo.get('path','')
        current = {}  # Comparison is handled separately, never inspect stored source paths.
        recorded_commit, current_commit = repo.get('commit', ''), current.get('commit', '')
        comparable = bool(recorded_commit and current_commit)
        differences.append({'name':name, 'target':recorded_commit, 'current':current_commit,
                            'changed':comparable and current_commit != recorded_commit})
        if not comparable:
            unverified.append({'name':name, 'reason':'missing_version'})
        elif repo.get('dirty') is not False or current.get('dirty') is not False:
            # A dirty flag is not a content fingerprint. It cannot establish
            # whether local files changed since this record was saved.
            unverified.append({'name':name, 'reason':'local_contents'})
    pins = [p['name']+'=='+p['version'] for p in dependencies if p['action'] not in ('skip','pending')]
    from .recovery_choices import prepare, fingerprint
    component_choices = {k:v for k,v in choices.items() if k != '_environment'}
    current = records.inventory() if any(isinstance(v,dict) and v.get('action') == 'current' for v in component_choices.values()) else {'core':{},'plugins':[]}
    extra, notes = prepare(record, repos, component_choices, current, records.directory())
    blockers.extend(extra)
    warnings.extend(notes)
    recovery_state = 'blocked' if blockers else 'partial' if any(r['skip'] or r.get('substitution') or any(exclusion_kind(e,r['core']) in ('missing_content','unknown') for e in (r.get('snapshot') or {}).get('skipped',[])) for r in repos) or any(p['action']=='skip' for p in dependencies) else 'ready'
    result = {'recovery_state': recovery_state, 'execution_state': 'not_started', 'manifest': record.get('manifest'), 'record_id':record_id, 'blockers':blockers, 'warnings':warnings, 'repositories':repos, 'pins':pins, 'differences':differences, 'unverified':unverified, 'can_rebuild':not blockers}
    result.update(runtime=runtime, dependencies=dependencies)
    result['plan_id'] = fingerprint(record, result)
    return result

def validate_target(raw, existing=False):
    path = Path(raw).expanduser()
    if not path.is_absolute() or len(path.parts) < 3:
        raise ValueError('Choose an absolute, new directory, not a drive root.')
    file_snapshots.no_links(path)
    path = path.resolve()
    from .instances import CURRENT_COMFYUI_ROOT
    protected = [Path(CURRENT_COMFYUI_ROOT).resolve(), Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(), records.directory().resolve()]
    if any(path == p or path.is_relative_to(p) or p.is_relative_to(path) for p in protected):
        raise ValueError('Target overlaps the running environment or version records.')
    if existing and not path.is_dir():
        raise ValueError('恢复目标已不存在。')
    if path.exists() and not existing:
        raise ValueError('Target must not exist. Choose a new folder name; existing directories are never overwritten.')
    return path

def run(args, cwd=None, timeout=900, extra_env=None):
    settings = load_settings()
    env, proxy = network_environment(settings)
    for key in list(env):
        if key.upper().startswith(('PIP_', 'PYTHON', 'GIT_', 'UV_')):
            env.pop(key, None)
    env.pop('VIRTUAL_ENV', None)
    env.pop('CONDA_PREFIX', None)
    env.update(extra_env or {})
    env.update(PIP_CONFIG_FILE=os.devnull, GIT_TERMINAL_PROMPT='0', UV_NO_CONFIG='1',
               GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    command = [str(a) for a in args]
    if command[0] == 'git':
        command = [require_git(), '-c', 'http.proxy=' + proxy, '-c', 'https.proxy=' + proxy,
                   '-c', 'remote.origin.proxy=' + proxy, *command[1:]]
        if 'clone' in command:
            command = [_network_fetch_url(arg, settings) if arg.startswith(('https://', 'http://')) else arg for arg in command]
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                            text=True, errors='replace', timeout=timeout,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        raise ValueError((result.stderr or result.stdout or 'Command failed')[-8000:])
    return result.stdout

def start(record_id, target, confirmed=False, choices=None, plan_id=None):
    if confirmed is not True:
        raise ValueError('Confirm GitHub downloads and PyPI source substitution first.')
    if not LOCK.acquire(blocking=False):
        raise ValueError('Another reconstruction is running.')
    try:
        prepared = plan(record_id, choices)
        if any(not repo.get('skip') and not repo.get('snapshot') and not repo.get('substitution') for repo in prepared['repositories']):
            require_git()
        if not plan_id or plan_id != prepared['plan_id']:
            raise ValueError('恢复计划或来源已变化，请重新检查并确认。')
        if prepared['blockers']:
            raise ValueError('Record has blocking gaps. Run the feasibility check for details.')
        destination = validate_target(target)
        for repo in prepared['repositories']:
            if repo.get('substitution'):
                source = Path(repo['source']).resolve()
                if destination == source or destination.is_relative_to(source) or source.is_relative_to(destination):
                    raise ValueError('恢复目标不能与备份来源重叠。')
        destination.mkdir(parents=True, exist_ok=False)
        from .rebuild_executor import atomic_json
        job_id = uuid.uuid4().hex
        metadata = destination/'.nodeops-recovery'
        metadata.mkdir()
        atomic_json(metadata/'owner.json', {'job_id':job_id, 'plan_id':plan_id})
        with STATE_LOCK:
            STATE.clear()
        save(executor_version=1, prepared=prepared, storage=str(records.directory().resolve()), interpreter=[sys.executable, platform.python_version(), platform.machine()], id=job_id, created=time.time(), status='running', stage='Preparing', target=str(destination), record_id=record_id, log=[], items=[], notes=prepared['warnings'], error='', choices=choices or {}, plan_id=plan_id)
    except Exception:
        LOCK.release()
        raise
    launch(prepared, destination)
    return status()


def install_dependencies(prepared, destination, python):
    if 'dependencies' in prepared:
        from .recovery_environment import install
        return install(prepared['dependencies'], destination, python, run, save)
    for name in ('requirements-recorded.txt', 'wheels', 'wheel-hashes.json', 'installed-packages.json'):
        file_snapshots.no_links(destination/name)
    for wheel in (destination/'wheels').glob('*'):
        file_snapshots.no_links(wheel)
    pins = destination/'requirements-recorded.txt'
    pins.write_text('\n'.join(prepared['pins'])+'\n',encoding='utf-8')
    save(stage='Resolving and downloading exact-version wheels (PyPI)')
    # Wheels only: do not execute third-party build/install scripts.
    run([python,'-m','pip','download','--only-binary=:all:','--index-url','https://pypi.org/simple','--dest',destination/'wheels','-r',pins],timeout=3600)
    hashes = {}
    for wheel in (destination/'wheels').glob('*.whl'):
        with wheel.open('rb') as stream:
            digest = hashlib.sha256()
            for block in iter(lambda:stream.read(1024*1024), b''):
                digest.update(block)
            hashes[wheel.name] = digest.hexdigest()
    (destination/'wheel-hashes.json').write_text(json.dumps(hashes,indent=2),encoding='utf-8')
    save(stage='Installing into the NEW environment only')
    run([python,'-m','pip','install','--no-index','--find-links',destination/'wheels','--only-binary=:all:','-r',pins],timeout=3600)
    save(stage='Verifying dependencies and recorded versions')
    run([python,'-m','pip','check'])
    installed = json.loads(run([python,'-m','pip','--disable-pip-version-check','list','--format=json']))
    normalized = lambda s: re.sub(r'[-_.]+','-',s).lower()
    versions = {normalized(p['name']):p['version'] for p in installed}
    mismatches = [pin for pin in prepared['pins'] if versions.get(normalized(pin.split('==')[0])) != pin.split('==')[1]]
    if mismatches:
        raise ValueError('Version mismatch: '+', '.join(mismatches))
    (destination/'installed-packages.json').write_text(json.dumps(installed,indent=2),encoding='utf-8')



def launch(prepared, destination):
    store = Path(status()['storage'])
    def worker():
        try:
            from .rebuild_executor import execute
            execute(prepared, destination, store, run, save, install_dependencies)
        except Exception as exc:
            save(status='failed', error=str(exc), stage='恢复停止；原环境不变。可重试未完成项目。')
        finally:
            LOCK.release()
    try:
        threading.Thread(target=worker, daemon=True, name='QBB-rebuild').start()
    except Exception:
        LOCK.release()
        raise


def resume(job_id, confirmed=False):
    if confirmed is not True:
        raise ValueError('请确认按原计划继续恢复，可能继续联网下载。')
    if not re.fullmatch('[a-f0-9]{32}', str(job_id)):
        raise ValueError('Invalid job id')
    if not LOCK.acquire(blocking=False):
        raise ValueError('Another reconstruction is running.')
    try:
        path = jobs() / (job_id + '.json')
        file_snapshots.no_links(path)
        row = json.loads(path.read_text(encoding='utf-8'))
        if row.get('executor_version') != 1 or row.get('id') != job_id:
            raise ValueError('旧恢复任务没有续跑检查点，请选择新目标重新恢复。')
        if row.get('status') not in ('running', 'failed', 'partial', 'interrupted'):
            raise ValueError('任务无需继续。')
        if row.get('status') == 'partial' and not any(i.get('status') == 'failed' for i in row.get('items', [])):
            raise ValueError('没有失败项；跳过或替代内容需要新计划，不能靠重试补齐。')
        if row.get('interpreter') != [sys.executable, platform.python_version(), platform.machine()]:
            raise ValueError('Python 解释器已变化，不能继续此任务。')
        if row.get('storage') != str(records.directory().resolve()):
            raise ValueError('记录存储位置已变化，请切回原位置。')
        destination = validate_target(row['target'], existing=True)
        marker = destination/'.nodeops-recovery'/'owner.json'
        file_snapshots.no_links(marker)
        if json.loads(marker.read_text(encoding='utf-8')) != {'job_id':job_id, 'plan_id':row['plan_id']}:
            raise ValueError('目标目录不属于此恢复任务。')
        with STATE_LOCK:
            STATE.clear()
            STATE.update(row)
        save(status='running', stage='校验检查点并继续恢复', error='', verification={'status':'not_checked'})
        prepared = row['prepared']
    except Exception:
        LOCK.release()
        raise
    launch(prepared, destination)
    return status()


def verify_recovery(job_id, confirmed=False, startup=False, plugins=False, service=False, expected_nodes=None, workflow=None, workflow_confirmed=False):
    if confirmed is not True or not isinstance(startup,bool) or not isinstance(plugins,bool) or (plugins and not startup):
        raise ValueError('请明确确认验证及插件加载选项。')
    from .recovery_service import validate_options
    expected_nodes=[] if expected_nodes is None else expected_nodes
    validate_options(service,startup,expected_nodes,workflow,workflow_confirmed)
    if not re.fullmatch('[a-f0-9]{32}',str(job_id)):raise ValueError('Invalid job id')
    if not LOCK.acquire(blocking=False):raise ValueError('已有恢复或验证任务正在执行。')
    try:
        path=jobs()/(job_id+'.json');file_snapshots.no_links(path)
        row=json.loads(path.read_text(encoding='utf-8'))
        if row.get('id')!=job_id or row.get('executor_version')!=1 or row.get('status') not in ('partial','rebuilt_unverified'):
            raise ValueError('请先完成文件及依赖恢复。')
        destination=validate_target(row['target'],existing=True)
        marker=destination/'.nodeops-recovery/owner.json';file_snapshots.no_links(marker)
        if json.loads(marker.read_text(encoding='utf-8'))!={'job_id':job_id,'plan_id':row['plan_id']}:
            raise ValueError('目标目录不属于此任务。')
        if row.get('storage')!=str(records.directory().resolve()):raise ValueError('请切回原记录保存位置。')
        with STATE_LOCK:
            STATE.clear();STATE.update(row)
        save(stage='正在执行用户确认的验证', verification={'status':'running','checks':[],'startup_requested':startup,'plugins_requested':plugins})
        def worker():
            try:
                from .recovery_verification import verify_job
                result=verify_job(row,startup,plugins,progress=lambda checks:save(verification={'status':'running','checks':checks,'startup_requested':startup,'plugins_requested':plugins}),service=service,expected_nodes=expected_nodes,workflow=workflow)
                summary = '验证未通过，请查看逐项结果' if result['status']=='failed' else '指定工作流执行通过（不代表所有工作流）' if result.get('workflow_verified') else 'HTTP 服务检查通过；未验证工作流' if result.get('http_verified') else '已执行检查通过；启动初始化通过，工作流未验证' if result.get('startup_verified') else '已执行检查通过；未执行启动和工作流验证'
                save(stage=summary, verification=result)
            except Exception as exc:save(stage='验证未完成，请查看错误', verification={'status':'failed','error':str(exc),'startup_verified':False,'workflow_verified':False})
            finally:LOCK.release()
        threading.Thread(target=worker,daemon=True,name='QBB-recovery-verification').start()
    except Exception:
        LOCK.release();raise
    return status()
