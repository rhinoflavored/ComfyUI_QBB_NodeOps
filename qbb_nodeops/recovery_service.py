"""Temporary, authenticated loopback service checks for a recovered instance."""
import hashlib
import json
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from . import file_snapshots as files


def validate_options(service, startup, expected_nodes, workflow, workflow_confirmed):
    if type(service) is not bool or (service and not startup):
        raise ValueError('HTTP 验证必须同时选择启动验证')
    if not isinstance(expected_nodes,list) or len(expected_nodes)>2000 or any(not isinstance(n,str) or not n.strip() or len(n)>256 for n in expected_nodes):
        raise ValueError('节点列表格式错误')
    if expected_nodes and not service:raise ValueError('节点检查需要 HTTP 验证')
    if workflow is not None:
        if not service or workflow_confirmed is not True:raise ValueError('请单独确认执行工作流')
        if not isinstance(workflow,dict) or not workflow or len(workflow)>2000 or len(json.dumps(workflow).encode())>1024*1024:
            raise ValueError('请提供不超过 1 MiB 的 API 格式工作流')
        for key,node in workflow.items():
            if not isinstance(key,str) or not isinstance(node,dict) or not isinstance(node.get('class_type'),str) or not isinstance(node.get('inputs'),dict):
                raise ValueError('需要 API 格式节点映射，不支持画布 nodes/links 格式')


def stop(process):
    if os.name=='nt' and process.poll() is None:
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    elif os.name!='nt':
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    if process.poll() is None:process.kill()
    process.wait(timeout=15)


def verify_service(python, core, logs, cpu=False, plugins=False, expected_nodes=None, workflow=None, startup_timeout=180, workflow_timeout=600, submission_timeout=60):
    cli=core/'comfy/cli_args.py';files.no_links(cli)
    flags=['--listen','--port','--disable-auto-launch','--output-directory','--temp-directory']
    if not plugins:flags.append('--disable-all-custom-nodes')
    if any(flag not in cli.read_text(encoding='utf-8') for flag in flags):
        raise ValueError('目标版本缺少临时服务验证所需启动参数')
    files.no_links(logs);logs.mkdir(exist_ok=True)
    run_dir=logs/uuid.uuid4().hex;run_dir.mkdir()
    for name in ('output','temp'):(run_dir/name).mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    token=uuid.uuid4().hex
    args=['main.py','--listen','127.0.0.1','--port',str(port),'--disable-auto-launch',
          '--output-directory',str(run_dir/'output'),'--temp-directory',str(run_dir/'temp')]
    if not plugins:args.append('--disable-all-custom-nodes')
    if cpu:args.append('--cpu')
    # Authenticate every request; identity proves we did not contact another
    # process that won the ephemeral-port race. No target source file is edited.
    wrapper="""import os,sys,runpy
from aiohttp import web
token=TOKEN
original=web.Application.__init__
@web.middleware
async def guard(request,handler):
    if request.headers.get('X-NodeOps-Verification')!=token:raise web.HTTPForbidden()
    if request.path=='/__nodeops_identity':return web.json_response({'token':token,'pid':os.getpid()})
    return await handler(request)
def init(self,*a,**kw):
    original(self,*a,**kw)
    self.middlewares.insert(0,guard)
web.Application.__init__=init
sys.path.insert(0,os.getcwd())
sys.argv=ARGS
runpy.run_path('main.py',run_name='__main__')
""".replace('TOKEN',repr(token)).replace('ARGS',repr(args))
    env={k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','PIP_')) and k not in ('VIRTUAL_ENV','CONDA_PREFIX')}
    env.update(PYTHONNOUSERSITE='1',PYTHONUNBUFFERED='1',PYTHONIOENCODING='utf-8')
    env['QBB_NODEOPS_DISABLE_AUTOCHECKPOINT']='1'
    options={'creationflags':getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)} if os.name=='nt' else {'start_new_session':True}
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def request(path,payload=None,timeout=30):
        data=None if payload is None else json.dumps(payload).encode()
        req=urllib.request.Request('http://127.0.0.1:'+str(port)+path,data=data,headers={'X-NodeOps-Verification':token,'Content-Type':'application/json'})
        with opener.open(req,timeout=timeout) as response:
            raw=response.read(16*1024*1024+1)
            if len(raw)>16*1024*1024:raise ValueError('验证响应超过 16 MiB')
            return json.loads(raw)
    checks=[];report={'checks':checks,'http_verified':False,'nodes_verified':False,'workflow_verified':False,'port':port,'log':str(run_dir/'service.log')}
    stage='http'
    with (run_dir/'service.log').open('xb') as output:
        process=subprocess.Popen([str(python),'-I','-c',wrapper],cwd=core,env=env,stdout=output,stderr=subprocess.STDOUT,**options)
        def alive():
            if process.poll() is not None:raise ValueError('临时服务提前退出；请查看日志')
            if (run_dir/'service.log').stat().st_size>8*1024*1024:raise ValueError('服务日志超过 8 MiB')
        try:
            deadline=time.monotonic()+startup_timeout
            while True:
                alive()
                try:identity=request('/__nodeops_identity',timeout=2)
                except (urllib.error.URLError,TimeoutError,ConnectionError):
                    if time.monotonic()>=deadline:raise ValueError('等待 HTTP 服务超时')
                    time.sleep(.2);continue
                if identity!={'token':token,'pid':process.pid}:raise ValueError('服务身份不匹配；未提交工作流')
                break
            stats=request('/system_stats')
            if not isinstance(stats,dict) or not isinstance(stats.get('system'),dict):raise ValueError('system_stats 响应格式错误')
            checks.append({'name':'http','status':'passed','detail':'临时本机服务与进程身份验证通过'})
            report['http_verified']=True;stage='nodes'
            nodes=request('/object_info')
            if not isinstance(nodes,dict) or not nodes:raise ValueError('节点注册表为空或格式错误')
            expected=set(expected_nodes or [])|{n['class_type'] for n in (workflow or {}).values()}
            missing=sorted(expected-set(nodes))
            if missing:raise ValueError('未注册节点：'+', '.join(missing))
            report['nodes_verified']=bool(expected)
            checks.append({'name':'nodes','status':'passed' if expected else 'not_checked','detail':str(len(nodes))+' 个注册节点；'+(str(len(expected))+' 个指定节点均存在（不代表功能通过）' if expected else '未指定预期节点，不判断所有插件注册完整性')})
            stage='workflow'
            if workflow is not None:
                report['workflow_sha256']=hashlib.sha256(json.dumps(workflow,sort_keys=True).encode()).hexdigest()
                # Submission includes server-side validation, not just a health
                # check. Never retry this POST: it may already have queued work.
                try:response=request('/prompt',{'prompt':workflow,'client_id':'nodeops-verification'},timeout=submission_timeout)
                except urllib.error.HTTPError as exc:
                    raise ValueError('工作流被拒绝：'+exc.read(4000).decode('utf-8','replace')) from exc
                except (urllib.error.URLError,TimeoutError,ConnectionError) as exc:
                    raise ValueError('工作流提交未确认：等待响应超时或连接中断，无法确认是否已受理；未重复提交，将停止临时验证服务。这不代表恢复文件失败。') from exc
                prompt_id=response.get('prompt_id')
                if response.get('error') or response.get('node_errors') or not isinstance(prompt_id,str) or not prompt_id:
                    raise ValueError('工作流被拒绝：'+json.dumps(response,ensure_ascii=False)[:4000])
                uuid.UUID(prompt_id)
                report['prompt_id']=prompt_id;deadline=time.monotonic()+workflow_timeout
                while True:
                    alive()
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise ValueError('工作流执行验证超时')
                    try:history=request('/history/'+prompt_id,timeout=min(30,remaining))
                    except (urllib.error.URLError,TimeoutError,ConnectionError):
                        if time.monotonic()>=deadline:raise ValueError('工作流执行验证超时')
                        time.sleep(min(.2,max(0,deadline-time.monotonic())));continue
                    if prompt_id in history:
                        status=history[prompt_id].get('status',{})
                        messages=status.get('messages',[])
                        if status.get('status_str')!='success' or status.get('completed') is not True or any(m[0] in ('execution_error','execution_interrupted') for m in messages):
                            raise ValueError('工作流执行未成功：'+json.dumps(status,ensure_ascii=False)[:4000])
                        report['workflow_verified']=True
                        checks.append({'name':'workflow','status':'passed','detail':'指定 API 工作流执行成功；不验证生成质量或其他工作流。输出：'+str(run_dir/'output')})
                        break
                    if time.monotonic()>=deadline:raise ValueError('工作流执行验证超时')
                    time.sleep(.2)
            else:checks.append({'name':'workflow','status':'not_checked','detail':'未提交工作流'})
        except Exception as exc:
            checks.append({'name':stage,'status':'failed','detail':str(exc)+'；日志：'+report['log']})
        finally:
            try:stop(process)
            except (OSError,subprocess.TimeoutExpired) as exc:
                if process.poll() is None:process.kill();process.wait(timeout=15)
                checks.append({'name':'cleanup','status':'failed','detail':'进程树清理未确认：'+str(exc)})
        report['process_stopped']=process.poll() is not None
    return report
