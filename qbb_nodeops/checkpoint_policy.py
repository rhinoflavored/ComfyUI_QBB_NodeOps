"""Opt-in periodic local checkpoints. Limits pause capture; never delete records."""
import json
import os
from pathlib import Path
import threading
import time
from . import version_records as records
from . import file_snapshots as files
from . import rebuild
from .rebuild_executor import atomic_json

SETTINGS=Path(__file__).resolve().parent.parent/'qbb_nodeops_checkpoints.json'
DEFAULT={'enabled':False,'interval_minutes':360,'max_records':20,'max_gib':20}
OPTIONS={'mode':'auto','large_files':'include','core_dirs':[],'on_error':'strict'}
LOCK=threading.Lock()
STOP=threading.Event()
THREAD=None
STATE={'status':'idle'}


def policy():
    files.no_links(SETTINGS)
    value=json.loads(SETTINGS.read_text(encoding='utf-8')) if SETTINGS.exists() else dict(DEFAULT)
    validate(value)
    return value


def validate(value):
    if not isinstance(value,dict) or set(value)!=set(DEFAULT) or type(value['enabled']) is not bool:
        raise ValueError('自动检查点设置格式错误')
    for key,low,high in [('interval_minutes',60,10080),('max_records',1,1000),('max_gib',1,10000)]:
        if type(value[key]) is not int or not low<=value[key]<=high:raise ValueError('设置超出范围：'+key)


def configure(value,confirmed=False):
    validate(value)
    if value['enabled'] and confirmed is not True:raise ValueError('请确认自动保存范围和空间限制')
    with LOCK:
        atomic_json(SETTINGS,value)
    return status()


def usage(store):
    files.no_links(store);size=0;count=0
    if not store.exists():return 0
    def unreadable(error):raise error
    for root,dirs,names in os.walk(store,followlinks=False,onerror=unreadable):
        for name in dirs:files.no_links(Path(root)/name)
        for name in names:
            path=Path(root)/name;files.no_links(path)
            size+=path.stat().st_size;count+=1
            if count>250000:raise ValueError('存储文件过多，请先检查存储目录')
    return size


def status():
    return {'policy':policy(),'state':dict(STATE),'scope':OPTIONS}


def storage_status():
    from .record_archives import exclusive, gc_inventory
    result={**status(),'bytes':None,'unreferenced_bytes':None,'unreferenced_objects':None}
    try:
        with exclusive() as store:
            result['bytes']=usage(store)
            candidates,_=gc_inventory(store)
            result.update(unreferenced_bytes=sum(i['bytes'] for i in candidates),unreferenced_objects=len(candidates))
    except (ValueError,OSError,KeyError,TypeError) as exc:
        result['usage_error']=str(exc)
    return result


def tick():
    STATE.update(status='paused', error='Phase 1: automatic checkpoints are paused; use manual snapshots')
    return


def start(idle):
    global THREAD
    if os.environ.get('QBB_NODEOPS_DISABLE_AUTOCHECKPOINT')=='1':return
    if THREAD is not None and THREAD.is_alive():return
    STOP.clear()
    def worker():
        previous=None;last_attempt=None
        # Delay until startup settles; do not scan while the queue is busy.
        while not STOP.wait(60):
            try:
                settings=policy()
                if settings!=previous:previous=dict(settings);last_attempt=None
                if settings['enabled'] and (last_attempt is None or time.monotonic()-last_attempt>=settings['interval_minutes']*60) and idle():
                    last_attempt=time.monotonic()
                    tick()
            except Exception as exc:STATE.update(status='paused',error=str(exc))
    THREAD=threading.Thread(target=worker,name='qbb-auto-checkpoints',daemon=True)
    THREAD.start()
