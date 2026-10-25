"""Opt-in bounded probes and optional temporary-service/workflow verification."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import uuid
from . import file_snapshots as files


def probe(args, cwd, log_dir, timeout=120):
    log_dir.mkdir(exist_ok=True)
    files.no_links(log_dir)
    path = log_dir/(uuid.uuid4().hex+'.log')
    env = {k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','PIP_')) and k not in ('VIRTUAL_ENV','CONDA_PREFIX')}
    env.update(PYTHONNOUSERSITE='1', PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')
    env['QBB_NODEOPS_DISABLE_AUTOCHECKPOINT']='1'
    options = {'creationflags':getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)} if os.name=='nt' else {'start_new_session':True}
    reason = None
    with path.open('xb') as output:
        process = subprocess.Popen([str(a) for a in args],cwd=cwd,env=env,stdout=output,stderr=subprocess.STDOUT,**options)
        try:
            deadline = time.monotonic()+timeout
            while process.poll() is None:
                if time.monotonic()>deadline:reason='验证超时';break
                if path.stat().st_size>8*1024*1024:reason='验证日志超过 8 MiB，已停止';break
                time.sleep(.1)
        finally:
            if os.name=='nt' and process.poll() is None:
                try:subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                except (OSError,subprocess.TimeoutExpired):pass
            elif os.name!='nt':
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            if process.poll() is None:process.kill()
            process.wait(timeout=15)
    if path.stat().st_size>8*1024*1024:reason='验证日志超过 8 MiB'
    with path.open('rb') as stream:
        text=stream.read(8*1024*1024).decode('utf-8','replace')
    if reason:raise ValueError(reason+'；日志：'+str(path))
    if process.returncode:raise ValueError('子进程退出 '+str(process.returncode)+'：'+text[-12000:])
    return text, str(path)


def verify_job(row, startup=False, plugins=False, runner=probe, progress=lambda value:None, service=False, expected_nodes=None, workflow=None):
    from .rebuild_executor import verify
    from .recovery_environment import Version, canonicalize_name
    target=Path(row['target']);prepared=row['prepared'];store=Path(row['storage'])
    python=target/'python_env/.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    files.no_links(python)
    logs=target/'.nodeops-recovery/verification'
    results=[]
    def perform(name,fn):
        try:detail=fn();item={'name':name,'status':'passed','detail':detail}
        except Exception as exc:item={'name':name,'status':'failed','detail':str(exc)}
        results.append(item);progress(list(results));return item['status']=='passed'
    def file_check():
        count=0
        for repo in prepared['repositories']:
            if repo['skip']:continue
            receipt=target/'.nodeops-recovery'/('component-'+repo['id']+'.json')
            files.no_links(receipt)
            saved=json.loads(receipt.read_text(encoding='utf-8'))
            if saved['target']!=repo['target']:raise ValueError('组件检查点不符')
            verify(target/repo['target'],saved['snapshot'],repo['core'],store);count+=1
        return str(count)+' 个组件内容与恢复检查点一致（非原始二进制证明）'
    if not perform('files',file_check):
        return {'status':'failed','checks':results,'startup_verified':False,'workflow_verified':False}
    def dependency_check():
        code="import json,platform,importlib.metadata as m; print('NODEOPS_PROBE='+json.dumps({'python':platform.python_version(),'packages':{d.metadata['Name']:d.version for d in m.distributions() if d.metadata['Name']}}))"
        text,log=runner([python,'-I','-c',code],target,logs)
        data=json.loads(next(s.split('=',1)[1] for s in reversed(text.splitlines()) if s.startswith('NODEOPS_PROBE=')))
        expected=prepared.get('runtime',{}).get('version')
        if expected and data['python']!=expected:raise ValueError('Python 版本已变化')
        versions={canonicalize_name(k):v for k,v in data['packages'].items()}
        for pin in prepared['pins']:
            name,version=pin.split('==',1)
            if canonicalize_name(name) not in versions or Version(versions[canonicalize_name(name)])!=Version(version):raise ValueError('依赖版本不符：'+pin)
        _,log=runner([python,'-I','-m','pip','--isolated','check'],target,logs)
        return 'Python/记录版本及 pip check 通过；日志：'+log
    deps=perform('dependencies',dependency_check)
    torch_pin=next((p.split('==',1)[1] for p in prepared['pins'] if p.lower().startswith('torch==')),None)
    if deps and torch_pin:
        def torch_check():
            cuda=bool(re.search(r'\+cu\d+$',torch_pin))
            code="import torch; print(torch.__version__, torch.version.cuda); assert str(torch.__version__).lower()=="+repr(torch_pin.lower())+", 'Imported torch version mismatch'; torch.ones(1"+(",device='cuda'" if cuda else '')+");"+(" torch.cuda.synchronize();" if cuda else '')
            _,log=runner([python,'-I','-c',code],target,logs)
            return ('CUDA 张量分配通过' if cuda else 'CPU 张量分配通过；未验证 GPU')+'；日志：'+log
        runtime=perform('torch',torch_check)
    else:
        runtime=False
        results.append({'name':'torch','status':'not_checked','detail':'没有可验证的 torch 记录，或依赖检查失败'});progress(list(results))
    startup_ok=False
    if startup and deps and (runtime or torch_pin is None):
        def startup_check():
            cli=target/'ComfyUI/comfy/cli_args.py';files.no_links(cli)
            source=cli.read_text(encoding='utf-8')
            flags=['--quick-test-for-ci']+([] if plugins else ['--disable-all-custom-nodes'])
            if any(flag not in source for flag in flags):raise ValueError('目标版本不支持安全的初始化测试参数；未启动')
            args=[python,'main.py',*flags]
            if '+cpu' in (torch_pin or ''):args.append('--cpu')
            text,log=runner(args,target/'ComfyUI',logs,timeout=180)
            # Quick CI may exit 0 even when individual nodes fail to import.
            if any(token in text for token in ('IMPORT FAILED','Traceback (most recent call last)','Cannot import ')):
                raise ValueError('初始化日志报告导入错误；日志：'+log+'；'+text[-12000:])
            return ('含第三方插件' if plugins else '仅核心')+'初始化正常退出；未验证 HTTP 服务和工作流。日志：'+log
        startup_ok=perform('startup',startup_check)
    else:
        results.append({'name':'startup','status':'not_checked','detail':'用户未选择启动，或前置验证未通过'});progress(list(results))
    # Even a clean log cannot prove every plugin/node registered successfully.
    results.append({'name':'plugins','status':'not_checked','detail':'已运行插件初始化，但未逐节点验证注册/功能' if startup_ok and plugins else '未加载第三方插件'})
    service_report={'http_verified':False,'nodes_verified':False,'workflow_verified':False}
    if service and startup_ok:
        from .recovery_service import verify_service
        service_report=verify_service(python,target/'ComfyUI',logs,cpu='+cpu' in (torch_pin or ''),plugins=plugins,expected_nodes=expected_nodes,workflow=workflow)
        results.extend(service_report.pop('checks'));progress(list(results))
    elif service:
        results.append({'name':'http','status':'not_checked','detail':'前置验证未通过，未启动服务或执行工作流'})
    return {'status':'failed' if any(c['status']=='failed' for c in results) else 'passed', 'checks':results,'startup_verified':startup_ok,'startup_scope':'with_plugins' if plugins else 'core_only',**service_report,'completed_at':time.time()}
