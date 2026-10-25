"""Persistent workflow-reference counts, not execution or historical usage claims."""
import json
import sqlite3
import time
import subprocess
import hashlib
import os
import threading
import stat as stat_module
import sys
import logging
from pathlib import Path
from contextlib import closing
from .store import REPORT

DB = Path(__file__).resolve().parent.parent / 'qbb_nodeops_usage.sqlite3'
STARTED = time.time()
READY = None
_CLEAN_LOCK = threading.Lock()
_SIZE_CACHE = {}
_SIZE_LOCK = threading.Lock()

def _resource_size(path):
    """Logical bytes, never follow shared links or publish partial totals."""
    key = os.path.abspath(path)
    with _SIZE_LOCK:
        cached = _SIZE_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < 60:
        return cached[1]
    deadline = time.monotonic() + 1
    total, entries = 0, 0
    pending = [Path(path)]
    result = (None, 'unavailable')
    try:
        while pending:
            current = pending.pop()
            entries += 1
            if entries > 100000 or time.monotonic() > deadline:
                result = (None, 'scan_limit')
                break
            info = current.lstat()
            if stat_module.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                result = (None, 'linked')
                break
            if stat_module.S_ISDIR(info.st_mode):
                with os.scandir(current) as children:
                    for child in children:
                        if len(pending) + entries > 100000 or time.monotonic() > deadline:
                            raise TimeoutError('Directory scan limit')
                        pending.append(Path(child.path))
            elif stat_module.S_ISREG(info.st_mode):
                total += info.st_size
            else:
                raise OSError('Unsupported file type')
        else:
            result = (total, 'complete')
    except TimeoutError:
        result = (None, 'scan_limit')
    except OSError:
        result = (None, 'unavailable')
    with _SIZE_LOCK:
        if len(_SIZE_CACHE) >= 512:
            _SIZE_CACHE.clear()
        _SIZE_CACHE[key] = (time.monotonic(), result)
    return result

def _cleanup_path(path, recursive=False, allow_parent_links=False):
    """Fail closed on links/reparse points without resolving away their identity."""
    path = Path(os.path.abspath(path))
    if path == Path(path.anchor):
        raise ValueError('Cannot clean a filesystem root')
    def check(item):
        info = item.lstat()
        if stat_module.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked/shared paths cannot be cleaned automatically')
    check(path)
    if allow_parent_links and path.is_file():
        # Pin a model file to its actual location. Never recycle a junction or
        # recursively traverse one; later cleanup receives this canonical path.
        path = path.resolve(strict=True)
    for item in (path, *path.parents):
        check(item)
    def fail_scan(exc):
        raise exc
    if recursive and path.is_dir():
        for root, directories, files in os.walk(path, followlinks=False, onerror=fail_scan):
            for name in directories + files:
                check(Path(root) / name)
    return path

def connect():
    db=sqlite3.connect(str(DB),timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS usage(kind TEXT, name TEXT, count INTEGER, last REAL, PRIMARY KEY(kind,name))')
    db.execute('CREATE TABLE IF NOT EXISTS references_v2(kind TEXT, name TEXT, count INTEGER, last REAL, PRIMARY KEY(kind,name))')
    db.execute('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value REAL)')
    db.execute('CREATE TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY, first REAL)')
    db.execute("INSERT OR IGNORE INTO meta VALUES('since',?)",(time.time(),));db.commit()
    db.execute("INSERT OR IGNORE INTO meta VALUES('references_v2_since',?)",(time.time(),));db.commit()
    return db

def _resource_key(path):
    return os.path.normcase(os.path.realpath(path))

def _model_reference(key, value, folders):
    if not isinstance(value, str) or not value.lower().endswith(('.safetensors','.ckpt','.pt','.pth','.bin','.gguf')):
        return None
    categories = {'ckpt_name': ['checkpoints'], 'ckpt': ['checkpoints'],
                  'lora_name': ['loras'], 'lora': ['loras'], 'vae_name': ['vae'],
                  'unet_name': ['diffusion_models', 'unet'], 'control_net_name': ['controlnet'],
                  'clip_name': ['text_encoders', 'clip']}.get(key)
    if categories is None:
        categories = [category for category in folders.folder_names_and_paths if category != 'custom_nodes']
    matches = set()
    for category in categories:
        if category not in folders.folder_names_and_paths:
            continue
        full = folders.get_full_path(category, value.replace('\\', '/'))
        if full and Path(full).is_file():
            matches.add(_resource_key(full))
    return next(iter(matches)) if len(matches) == 1 else None

def _plugin_reference(cls, roots):
    module = sys.modules.get(getattr(cls, '__module__', ''))
    filename = getattr(module, '__file__', None)
    if not filename:
        return None
    for root in roots:
        try:
            relative = Path(_resource_key(filename)).relative_to(Path(_resource_key(root)))
        except ValueError:
            continue
        if relative.parts:
            return _resource_key(Path(root) / relative.parts[0])
    return None

def observe(payload):
    try:
        import nodes
        import folder_paths
        seen=set()
        for node in payload.get('prompt',{}).values():
            cls=nodes.NODE_CLASS_MAPPINGS.get(node.get('class_type'))
            plugin = _plugin_reference(cls, folder_paths.folder_names_and_paths.get('custom_nodes', ([], set()))[0])
            if plugin:
                seen.add(('plugin',plugin))
            inputs=node.get('inputs',{})
            for key,value in inputs.items():
                model = _model_reference(key, value, folder_paths)
                if model:
                    seen.add(('model',model))
        with closing(connect()) as db, db:
            for kind,name in seen:
                db.execute('INSERT INTO references_v2 VALUES(?,?,1,?) ON CONFLICT(kind,name) DO UPDATE SET count=count+1,last=excluded.last',(kind,name,time.time()))
    except Exception:
        logging.getLogger(__name__).warning('Workflow reference tracking failed', exc_info=True)
    return payload

def _model_classification(category, name, full, plugin_roots):
    """Separate plugin weights from model folders, including registered aliases."""
    relative = None
    for root in plugin_roots:
        try:
            relative = Path(os.path.abspath(full)).relative_to(Path(os.path.abspath(root)))
            break
        except ValueError:
            continue
    if relative is None and category == 'custom_nodes':
        relative = Path(name.replace('\\', '/'))
    if relative is None:
        return {'category': {'unet': 'diffusion_models'}.get(category, category)}
    parts = relative.parts
    folders = [part.casefold() for part in parts[:-1]]
    if len(parts) < 2 or any(
        part.startswith('.') or part.endswith('.disabled') or
        part in {'disabled', '__pycache__', 'cache', 'caches', 'tmp', 'temp', 'site-packages', 'node_modules'} or
        part.endswith('_cache') or part.startswith('cache_')
        for part in folders
    ):
        return None
    return {'category': 'plugin_models', 'plugin_name': parts[0]}

def report():
    import folder_paths
    with closing(connect()) as db:
        usage={(k,n):(c,last) for k,n,c,last in db.execute('SELECT * FROM references_v2')}
        since=db.execute("SELECT value FROM meta WHERE key='references_v2_since'").fetchone()[0]
    age=(time.time()-since)/86400
    rows=[]
    for rec in REPORT.records:
        name=Path(rec.path).stem
        count,last=usage.get(('plugin',_resource_key(rec.path)),(0,None))
        updated=None
        try:
            p=subprocess.run(['git','-c','core.hooksPath=','-C',rec.path,'log','-1','--format=%ct'],capture_output=True,text=True,timeout=2,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            updated=int(p.stdout.strip()) if p.returncode==0 else None
        except Exception: pass
        stale=updated is not None and time.time()-updated>180*86400
        suggestion='observe'
        if age>=30 and not count: suggestion='review_unused'
        if suggestion=='review_unused' and stale: suggestion='review_unused_old'
        if Path(rec.path).exists():
            rows.append(dict(kind='plugin',name=rec.name,path=rec.path,count=count,last=last,updated=updated,suggestion=suggestion))
    plugin_roots = folder_paths.folder_names_and_paths.get('custom_nodes', ([], set()))[0]
    for category in folder_paths.folder_names_and_paths:
        try: names=folder_paths.get_filename_list(category)
        except Exception: continue
        for name in names:
            if not name.lower().endswith(('.safetensors','.ckpt','.pt','.pth','.bin','.gguf')): continue
            full=folder_paths.get_full_path(category,name)
            if full and Path(full).is_file():
                count,last=usage.get(('model',_resource_key(full)),(0,None))
                classification = _model_classification(category, name, full, plugin_roots)
                if classification is None:
                    continue
                rows.append(dict(kind='model',**classification,name=f'{category}/{name}',path=full,count=count,last=last,updated=None,suggestion='review_unused' if age>=30 and not count else 'observe'))
    unique={}
    with closing(connect()) as db, db:
        for row in rows:
            path=Path(os.path.abspath(row['path']))
            protection_reason = None
            try:
                path = _cleanup_path(path, allow_parent_links=row['kind'] == 'model')
            except (OSError, ValueError) as exc:
                protection_reason = str(exc)
            row['path']=str(path)
            ident=hashlib.sha256(str(path).casefold().encode()).hexdigest()
            if ident in unique:continue
            try:
                stat=path.stat()
            except OSError:
                continue
            size, size_status = _resource_size(path)
            # File creation is an approximation, never a release or verified installation date.
            created=getattr(stat,'st_birthtime',stat.st_ctime if os.name=='nt' else None)
            row.update(local_created=created,local_modified=stat.st_mtime,published=None,
                       local_age_days=max(0,int((time.time()-(created or stat.st_mtime))/86400)),
                       age_basis='created' if created else 'modified')
            db.execute('INSERT OR IGNORE INTO seen VALUES(?,?)',(ident,time.time()))
            first=max(since,db.execute('SELECT first FROM seen WHERE id=?',(ident,)).fetchone()[0])
            row.update(id=ident,observed_days=int((time.time()-first)/86400),idle_days=int((time.time()-row['last'])/86400) if row['last'] else None,
                       size=size, size_status=size_status,
                       protected=bool(protection_reason) or path==Path(__file__).resolve().parent.parent,
                       protection_reason=protection_reason)
            unique[ident]=row
    return dict(started=STARTED,ready=READY,tracked_seconds=round(READY-STARTED,2) if READY else None,
                plugin_load_seconds=round(sum(r.elapsed_sec for r in REPORT.records),2),since=since,days=round(age,1),items=list(unique.values()))

def cleanup(ids):
    """Recycle only freshly inventoried files; client cannot supply arbitrary paths."""
    if os.name!='nt':raise ValueError('Recycle-bin cleanup currently supports Windows only')
    if not isinstance(ids,list) or not ids or len(ids)>50:raise ValueError('Select 1–50 items')
    with _CLEAN_LOCK:
        inventory={row['id']:row for row in report()['items']}
        selected=[]
        for ident in ids:
            row=inventory.get(ident)
            if not row or row['protected']:raise ValueError('Item unavailable or protected')
            _cleanup_path(row['path'], recursive=True)
            selected.append(row)
        results=[]
        for row in selected:
            env=os.environ.copy();env['QBB_CLEAN_TARGET']=row['path']
            script="Add-Type -AssemblyName Microsoft.VisualBasic; $p=$env:QBB_CLEAN_TARGET; try { if ([IO.Directory]::Exists($p)) { [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($p,'OnlyErrorDialogs','SendToRecycleBin','ThrowException') } else { [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($p,'OnlyErrorDialogs','SendToRecycleBin','ThrowException') }; exit 0 } catch { Write-Output $_.Exception.Message; exit 1 }"
            try:
                # Recheck immediately before recycling, not only during inventory.
                _cleanup_path(row['path'], recursive=True)
                proc=subprocess.run(['powershell','-NoProfile','-NoLogo','-NonInteractive','-Command',script],env=env,capture_output=True,text=True,errors='replace',timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                results.append({'name':row['name'],'ok':proc.returncode==0,'error':proc.stdout[-500:] if proc.returncode else ''})
            except Exception as exc:results.append({'name':row['name'],'ok':False,'error':str(exc)})
        return {'results':results}
