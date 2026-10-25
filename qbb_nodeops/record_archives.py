"""Portable record objects, validated imports, and conservative shared-object GC."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
import uuid
import zipfile
import zlib
from . import file_snapshots as files
from . import version_records as records
from .manifest import normalize
from . import local_archives

MAX_METADATA = 32 * 1024 * 1024
MAX_LOCAL_RECORD_METADATA = 128 * 1024 * 1024
MAX_ENTRIES = 100000
MAX_BYTES = 100 * 1024**3
TOKENS = {}
COMPRESSION_SAMPLE = 64 * 1024


def object_compression(stream):
    sample=stream.read(COMPRESSION_SAMPLE)
    stream.seek(0)
    return zipfile.ZIP_DEFLATED if sample and len(zlib.compress(sample,1)) < len(sample)*0.9 else zipfile.ZIP_STORED


def export_required_bytes(refs):
    size=sum(refs.values())
    # Reserve metadata, ZIP headers and expansion if the sample is misleading.
    return size+size//100+len(refs)*512+2*MAX_METADATA


@contextmanager
def exclusive():
    from . import rebuild
    if not rebuild.LOCK.acquire(blocking=False):raise ValueError('恢复/验证正在运行，请稍后维护备份。')
    try:
        if not records.LOCK.acquire(blocking=False):raise ValueError('记录或备份操作正在运行。')
        try:
            files.no_links(records.directory())
            yield records.directory()
        finally:records.LOCK.release()
    finally:rebuild.LOCK.release()


def json_bytes(data):
    value = json.dumps(data,ensure_ascii=False,sort_keys=True).encode('utf-8')
    if len(value)>MAX_METADATA:raise ValueError('备份元数据超过 32 MiB 限制')
    return value


def references(record):
    """Validate snapshot paths without ever extracting them into the filesystem."""
    if not isinstance(record,dict) or not isinstance(record.get('label',''),str):raise ValueError('Invalid record metadata')
    if type(record.get('created')) not in (int,float) or not math.isfinite(record['created']) or not 0<=record['created']<=253402300799:raise ValueError('Invalid record timestamp')
    record=normalize(record)
    refs={}
    from .offline_environment import snapshot_components
    components = ([{'snapshot': value} for value in record['artifacts'].values()] if record.get('schema') == 4
                  else [record['core'], *record['plugins'], *snapshot_components(record)])
    for repo in components:
        snapshot=repo.get('snapshot')
        if not snapshot:continue
        if type(snapshot.get('format')) is not int or snapshot.get('format') not in (1,2):raise ValueError('Unsupported snapshot format')
        if type(snapshot.get('single_file',False)) is not bool:raise ValueError('Invalid single file flag')
        names=set();directories=set()
        for entry in snapshot['files']:
            files.permission_mode(entry.get('mode',0o644))
            files.safe_parts(entry['path']);name=entry['path'].casefold()
            if name in names:raise ValueError('重复的快照路径')
            names.add(name)
            digest=entry['sha256'];size=entry['size']
            if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Invalid object hash')
            if type(size)!=int or size<0 or size>MAX_BYTES:raise ValueError('Invalid object size')
            if digest in refs and refs[digest]!=size:raise ValueError('对象长度冲突')
            refs[digest]=size
        for entry in snapshot.get('directories',[]):
            files.permission_mode(entry.get('mode',0o755))
            path=entry['path']
            if path:files.safe_parts(path)
            name=path.casefold()
            if name in names or name in directories:raise ValueError('目录与文件路径冲突')
            directories.add(name)
        for name in names|directories:
            parts=name.split('/')
            if any('/'.join(parts[:i]) in names for i in range(1,len(parts))):raise ValueError('文件不能作为父目录')
        if snapshot.get('single_file') and (len(names)!=1 or directories):raise ValueError('Invalid single file snapshot')
    if len(refs)+2>MAX_ENTRIES or sum(refs.values())>MAX_BYTES:raise ValueError('备份超过对象数量或 100 GiB 内容限制')
    return record,refs


def scopes(record):
    if record.get('schema') == 4:
        from .snapshot_state import summary
        return summary(record)['scopes']
    return record['manifest']['content']['scopes']


def obj_name(digest):return 'objects/'+digest[:2]+'/'+digest[2:]


def copy_checked(source, destination, digest, size):
    hasher=hashlib.sha256();count=0
    while True:
        block=source.read(files.CHUNK)
        if not block:break
        count+=len(block)
        if count>size:raise ValueError('对象超出声明长度')
        hasher.update(block)
        if destination is not None:destination.write(block)
    if count!=size or hasher.hexdigest()!=digest:raise ValueError('对象内容校验失败：'+digest)


def verify_objects(store, refs):
    for digest,size in refs.items():
        with files.object_path(store,digest).open('rb') as stream:
            copy_checked(stream,None,digest,size)


def record_inventory(store, record_id):
    if not isinstance(record_id,str) or not re.fullmatch('[a-f0-9]{32}',record_id):raise ValueError('Invalid record ID')
    path=store/(record_id+'.json');files.no_links(path)
    if path.stat().st_size>MAX_LOCAL_RECORD_METADATA:raise ValueError('本地记录元数据超过 128 MiB 限制')
    return references(records.read(record_id))


def import_space(store, refs):
    reused={}
    for digest,size in refs.items():
        path=files.object_path(store,digest)
        if path.exists():
            with path.open('rb') as stream:copy_checked(stream,None,digest,size)
            reused[digest]=size
    new_bytes=sum(refs.values())-sum(reused.values())
    return reused,{'new_bytes':new_bytes,'reused_bytes':sum(reused.values()),
                   'temporary_bytes':new_bytes,'required_bytes':new_bytes+2*MAX_METADATA}


def preview_export(record_id, raw):
    target=Path(raw)
    if not target.is_absolute() or target.suffix.lower()!='.zip':raise ValueError('请输入新 ZIP 文件的绝对路径')
    files.no_links(target)
    if target.exists() or not target.parent.is_dir():raise ValueError('目标必须是现有文件夹内尚不存在的 ZIP 文件')
    with exclusive() as store:
        if target.resolve().is_relative_to(store.resolve()):raise ValueError('请把导出文件放在记录保存目录之外')
        record,refs=record_inventory(store,record_id);verify_objects(store,refs)
        payload=json_bytes(record);required=export_required_bytes(refs)
        free=shutil.disk_usage(target.parent).free
        if free<required:raise ValueError('导出位置可用空间不足')
        token=issue('export',{'record_id':record_id,'record_hash':hashlib.sha256(payload).hexdigest(),'path':str(target),'store':str(store.resolve())})
        return {'token':token,'objects':len(refs),'bytes':sum(refs.values()),'required_bytes':required,'free_bytes':free,
                'scopes':scopes(record),'expires_in':600,'runtime_verified':False}


def export_bundle(record_id, raw, confirmed=False, *, token=None):
    if confirmed is not True:raise ValueError('请确认备份文件可能含配置和密钥。')
    target=Path(raw)
    if not target.is_absolute() or target.suffix.lower()!='.zip':raise ValueError('请输入新 ZIP 文件的绝对路径')
    files.no_links(target)
    if target.exists() or not target.parent.is_dir():raise ValueError('目标必须是现有文件夹内尚不存在的 ZIP 文件')
    with exclusive() as store:
        if target.resolve().is_relative_to(store.resolve()):raise ValueError('请把导出文件放在记录保存目录之外')
        record,refs=record_inventory(store,record_id)
        if token is not None:
            reviewed=consume(token,'export')
            if reviewed!={'record_id':record_id,'record_hash':hashlib.sha256(json_bytes(record)).hexdigest(),'path':str(target),'store':str(store.resolve())}:
                raise ValueError('导出记录、路径或保存位置已变化，请重新预览')
        if shutil.disk_usage(target.parent).free < export_required_bytes(refs):raise ValueError('导出位置可用空间不足')
        payload=json_bytes(record)
        with tempfile.TemporaryDirectory(prefix='.qbb-export-',dir=target.parent) as temp:
            archive=Path(temp)/'bundle.zip'
            with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as out:
                out.writestr('bundle.json',json_bytes({'kind':'qbb_record_bundle','version':1,'record_sha256':hashlib.sha256(payload).hexdigest(),'scope':'recorded_files_only','objects':len(refs)}))
                out.writestr('record.json',payload)
                for digest,size in refs.items():
                    source=files.object_path(store,digest)
                    with source.open('rb') as src:
                        out.compression=object_compression(src)
                        with out.open(obj_name(digest),'w',force_zip64=True) as dst:
                            copy_checked(src,dst,digest,size)
            # Exclusive creation: never replace an existing user archive, including races.
            with archive.open('r+b') as stream:os.fsync(stream.fileno())
            os.link(archive,target)
        return {'path':str(target),'objects':len(refs),'bytes':sum(refs.values()),'archive_bytes':target.stat().st_size,'compression':'adaptive_deflate','scope':'recorded_files_only'}


def inspect_archive(path, stage=None, reused=None, *, verify=True):
    path=Path(path)
    if not path.is_absolute():raise ValueError('请输入本机 ZIP 文件的绝对路径')
    files.no_links(path)
    before=files.signature(path.stat())
    with zipfile.ZipFile(path) as archive:
        entries=archive.infolist()
        if len(entries)>MAX_ENTRIES:raise ValueError('备份条目过多')
        names=set()
        for entry in entries:
            name=entry.filename
            mode=entry.external_attr>>16
            if name in names or entry.is_dir() or entry.flag_bits&1 or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0,stat.S_IFREG)):
                raise ValueError('备份包含重复、加密、目录或特殊文件条目')
            if name not in ('bundle.json','record.json') and not re.fullmatch(r'objects/[a-f0-9]{2}/[a-f0-9]{62}',name):raise ValueError('备份包含非法路径或未知条目')
            names.add(name)
        for name in ('bundle.json','record.json'):
            if name not in names or archive.getinfo(name).file_size>MAX_METADATA:raise ValueError('备份元数据缺失或过大')
        metadata=json.loads(archive.read('bundle.json'));payload=archive.read('record.json')
        if metadata.get('kind')!='qbb_record_bundle' or type(metadata.get('version')) is not int or metadata['version']!=1:raise ValueError('Unsupported archive version')
        if hashlib.sha256(payload).hexdigest()!=metadata.get('record_sha256'):raise ValueError('记录清单校验失败')
        record,refs=references(json.loads(payload))
        if names != {'bundle.json','record.json',*(obj_name(d) for d in refs)} or type(metadata.get('objects')) is not int or metadata['objects']!=len(refs):raise ValueError('备份对象集合不完整或有额外对象')
        reused=reused or {}
        if stage is not None and shutil.disk_usage(stage).free<sum(size for sha,size in refs.items() if sha not in reused)+2*MAX_METADATA:raise ValueError('导入位置可用空间不足')
        for digest,size in refs.items():
            entry=archive.getinfo(obj_name(digest))
            if entry.file_size!=size:raise ValueError('备份对象长度不符')
            if not verify and stage is None:continue
            with archive.open(entry) as source:
                if stage is None or digest in reused:copy_checked(source,None,digest,size)
                else:
                    with (stage/digest).open('xb') as destination:
                        copy_checked(source,destination,digest,size)
                        destination.flush();os.fsync(destination.fileno())
    if before!=files.signature(path.stat()):raise ValueError('备份包在校验期间发生变化')
    return record,refs,hashlib.sha256(payload).hexdigest(),before


def issue(kind, data):
    now=time.monotonic()
    for key in list(TOKENS):
        if TOKENS[key]['expires']<now:del TOKENS[key]
    if len(TOKENS)>=32:TOKENS.clear()
    token=uuid.uuid4().hex;TOKENS[token]={'kind':kind,'data':data,'expires':now+600}
    return token


def consume(token,kind):
    value=TOKENS.pop(token,None)
    if not value or value['kind']!=kind or value['expires']<time.monotonic():raise ValueError('预览已失效，请重新预览')
    return value['data']


def preview_import(raw):
    with exclusive() as store:
        record,refs,digest,signature=inspect_archive(raw)
        _,space=import_space(store,refs)
        token=issue('import',{'path':raw,'record_hash':digest,'signature':signature,'store':str(store.resolve())})
        components = record['resources'] if record.get('schema') == 4 else [record['core'], *record['plugins']]
        return {'token':token,'space':space,'scopes':scopes(record),'label':record.get('label',''),'offline_environment':record.get('offline_environment',{}).get('status')=='ready','objects':len(refs),'bytes':sum(refs.values()),
                'plugins': sum(r.get('kind') == 'plugin' for r in components) if record.get('schema') == 4 else len(record['plugins']),
                'metadata_only_components':sum(not (r.get('snapshot') or r.get('artifact_ref')) for r in components), 'expires_in':600,'scope':'recorded_files_only'}


def import_bundle(token, confirmed=False):
    if confirmed is not True:raise ValueError('请确认导入仅登记记录，不执行恢复。')
    with exclusive() as store:
        reviewed=consume(token,'import')
        if reviewed['store']!=str(store.resolve()):raise ValueError('保存位置已变化，请重新预览')
        store.mkdir(parents=True,exist_ok=True)
        record,refs,digest,signature=inspect_archive(reviewed['path'],verify=False)
        if digest!=reviewed['record_hash'] or signature!=reviewed['signature']:raise ValueError('备份包已变化，请重新预览')
        reused,space=import_space(store,refs)
        if shutil.disk_usage(store).free<space['required_bytes']:raise ValueError('导入位置可用空间不足')
        with tempfile.TemporaryDirectory(prefix='.import-',dir=store) as temp:
            stage=Path(temp);record,refs,digest,signature=inspect_archive(reviewed['path'],stage,reused)
            if digest!=reviewed['record_hash'] or signature!=reviewed['signature']:raise ValueError('备份包已变化，请重新预览')
            # Recheck reused/raced objects after validating the archive, before publication.
            for sha in refs:
                destination=files.object_path(store,sha)
                if destination.exists():
                    with destination.open('rb') as stream:copy_checked(stream,None,sha,refs[sha])
                else:
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    # Same-volume hard link is atomic and refuses to overwrite a raced file.
                    os.link(stage/sha,destination)
            original=record.get('id');record.update(id=uuid.uuid4().hex, imported_at=time.time(), imported_from_id=original)
            for row in record.get('resources', []):
                if row.get('archive_ref'):
                    row['archive_verification'] = local_archives.verify_record_archive(record, row, store)
            if record.get('schema') == 4:
                record.pop('backup', None)  # Rebuild after independent imported-ZIP verification.
            record=normalize(record)
            pending=stage/'record.json'
            with pending.open('xb') as stream:
                stream.write(json_bytes(record));stream.flush();os.fsync(stream.fileno())
            os.link(pending,store/(record['id']+'.json'))
        return {'id':record['id'],'objects':len(refs),'space':space,'restoration_started':False}


def gc_inventory(store, excluding_record=None):
    used=set();identity=[]
    def consume_json(path,record=False):
        files.no_links(path)
        limit = MAX_LOCAL_RECORD_METADATA if record else MAX_METADATA
        if path.stat().st_size>limit:raise ValueError('清单过大，停止清理：'+path.name)
        raw=path.read_bytes()
        if len(raw)>limit:raise ValueError('清单过大，停止清理：'+path.name)
        data=json.loads(raw)
        if record:
            _,refs=references(data)
            if path.stem!=excluding_record:used.update(refs)
        else:
            if not isinstance(data.get('prepared'),dict):raise ValueError('恢复任务缺少引用清单，无法安全清理：'+path.name)
            def visit(value):
                if isinstance(value,dict):
                    if 'sha256' in value:
                        sha=value['sha256']
                        if not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha):raise ValueError('任务包含非法对象引用')
                        used.add(sha)
                    for v in value.values():visit(v)
                elif isinstance(value,list):
                    for v in value:visit(v)
            visit(data['prepared'])
        identity.append([str(path.relative_to(store)),hashlib.sha256(raw).hexdigest()])
    for path in sorted(store.glob('*.json')):
        if not re.fullmatch('[a-f0-9]{32}',path.stem):raise ValueError('保存目录有未知 JSON，停止清理：'+path.name)
        consume_json(path,True)
    jobs=store/'rebuild-jobs';files.no_links(jobs)
    for path in sorted(jobs.glob('*.json')):consume_json(path)
    candidates=[]
    objects=store/'objects';files.no_links(objects)
    if objects.exists():
        for prefix in sorted(objects.iterdir()):
            files.no_links(prefix)
            if not re.fullmatch('[a-f0-9]{2}',prefix.name) or not prefix.is_dir():raise ValueError('对象目录含未知内容，停止清理')
            for path in sorted(prefix.iterdir()):
                files.no_links(path);sha=prefix.name+path.name
                if not re.fullmatch('[a-f0-9]{64}',sha) or not path.is_file():raise ValueError('对象目录含未知内容，停止清理')
                sig=files.signature(path.stat());identity.append([sha,sig])
                if sha not in used:candidates.append({'sha256':sha,'bytes':sig[0]})
    digest=hashlib.sha256(json_bytes(identity)).hexdigest()
    return candidates,digest


def preview_gc():
    with exclusive() as store:
        candidates,digest=gc_inventory(store)
        token=issue('gc',{'digest':digest,'store':str(store.resolve())})
        return {'token':token,'objects':len(candidates),'bytes':sum(c['bytes'] for c in candidates),'expires_in':600}


def apply_gc(token, confirmed=False):
    if confirmed is not True:raise ValueError('请确认将未引用对象移入系统回收站')
    from send2trash import send2trash
    with exclusive() as store:
        reviewed=consume(token,'gc');candidates,digest=gc_inventory(store)
        if reviewed!={'digest':digest,'store':str(store.resolve())}:raise ValueError('引用或对象已变化，请重新预览')
        moved=0;errors=[]
        for entry in candidates:
            try:send2trash(str(files.object_path(store,entry['sha256'])));moved+=1
            except OSError as exc:errors.append(str(exc));break
        return {'moved':moved,'errors':errors,'recoverable':'system_trash'}


def check_record(record_id):
    with exclusive() as store:
        record,refs=record_inventory(store,record_id);errors=[];checked=0
        for sha,size in refs.items():
            try:
                verify_objects(store,{sha:size});checked+=1
            except (OSError,ValueError) as exc:
                errors.append({'sha256':sha,'error':str(exc)})
        archive_rows = [r for r in record.get('resources', []) if r.get('archive_ref')]
        checked_archives = 0
        for row in archive_rows:
            try:
                local_archives.verify_record_archive(record, row, store)
                checked_archives += 1
            except (OSError, ValueError) as exc:
                errors.append({'resource_id': row['id'], 'error': str(exc)})
        return {'record_id':record_id,'objects':len(refs),'checked_objects':checked,'errors':errors,
                'archives': len(archive_rows), 'checked_archives': checked_archives,
                'status':'failed' if errors else 'passed' if refs else 'no_objects',
                'scopes':scopes(record),'runtime_verified':False}


def preview_remove(record_id):
    with exclusive() as store:
        record,refs=record_inventory(store,record_id)
        before,digest=gc_inventory(store)
        after,_=gc_inventory(store,excluding_record=record_id)
        existing={r['sha256'] for r in before}
        released=[r for r in after if r['sha256'] not in existing]
        related=[]
        for path in sorted(store.glob('*.json')):
            other=records.read(path.stem)
            checkpoint=other.get('operation_checkpoint')
            if (isinstance(checkpoint,dict) and checkpoint.get('before_record_id')==record_id) or other.get('offline_prepared_from')==record_id:
                related.append(path.stem)
        token=issue('remove',{'record_id':record_id,'digest':digest,'store':str(store.resolve())})
        return {'token':token,'record_id':record_id,'referenced_bytes':sum(refs.values()),
                'newly_unreferenced_objects':len(released),'newly_unreferenced_bytes':sum(r['bytes'] for r in released),
                'related_records':related,'objects_removed':0,'expires_in':600}


def remove_record(record_id, confirmed=False, *, token=None):
    if confirmed is not True:raise ValueError('请确认移除这条记录')
    from send2trash import send2trash
    if token is None:
        if not isinstance(record_id, str) or not re.fullmatch('[a-f0-9]{32}', record_id):
            raise ValueError('Invalid record ID')
        store = records.directory()
        path = store / (record_id + '.json')
        files.no_links(store)
        files.no_links(path)
        if not path.is_file():
            raise ValueError('记录文件不存在')
        send2trash(str(path))
        return {'removed': record_id, 'objects_removed': 0, 'recoverable': 'system_trash'}
    with exclusive() as store:
        records.read(record_id)
        if token is not None:
            reviewed=consume(token,'remove');_,digest=gc_inventory(store)
            if reviewed!={'record_id':record_id,'digest':digest,'store':str(store.resolve())}:
                raise ValueError('记录或对象引用已变化，请重新预览移除')
        path=store/(record_id+'.json');files.no_links(path);send2trash(str(path))
        return {'removed':record_id,'objects_removed':0,'recoverable':'system_trash'}
