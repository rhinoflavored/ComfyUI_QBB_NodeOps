"""Relocate only known ComfyUI model-path configuration inside an unpublished stage."""
import os
from pathlib import Path
from . import file_snapshots as files


def relocate(stage, source_root):
    path=stage/'extra_model_paths.yaml'
    if not path.exists():return []
    files.no_links(path)
    if path.stat().st_size>2*1024*1024:raise ValueError('模型路径配置过大，请手动核对')
    import yaml
    data=yaml.safe_load(path.read_text(encoding='utf-8'))
    if data is None:return []
    if not isinstance(data,dict):raise ValueError('模型路径配置必须是映射，无法安全迁移')
    old=Path(source_root) if source_root else None
    if old is not None and (not old.is_absolute() or '..' in old.parts):old=None
    notes=[];changed=False
    def relative(value):
        # Do not expand environment variables or guess foreign paths.
        p=Path(value)
        if old is not None and p.is_absolute() and '..' not in p.parts:
            try:return p.relative_to(old).as_posix()
            except ValueError:pass
        return None
    for name,group in data.items():
        if group is None:continue
        if not isinstance(group,dict):raise ValueError('模型路径分组格式错误：'+str(name))
        base=group.get('base_path','')
        if not isinstance(base,str):raise ValueError('模型 base_path 必须是字符串')
        mapped=relative(base)
        if mapped is not None:
            group['base_path']=mapped;changed=True
            notes.append({'kind':'relocated','field':str(name)+'.base_path','from':base,'to':mapped})
        base=group.get('base_path','')
        base_external=bool(base and (Path(base).is_absolute() or '..' in Path(base).parts or '$' in base or '%' in base or base.startswith('~')))
        for key,value in list(group.items()):
            if key in ('base_path','is_default'):continue
            if not isinstance(value,str):raise ValueError('模型目录必须是字符串：'+str(key))
            paths=[]
            for original in value.split('\n'):
                if not original:paths.append(original);continue
                mapped=relative(original)
                # Absolute entries override base_path. Rewrite relative to the
                # new base only when it stays within the captured instance.
                if mapped is not None and not base_external:
                    mapped=os.path.relpath(mapped,base or '.')
                    paths.append(mapped);changed=True
                    notes.append({'kind':'relocated','field':str(name)+'.'+str(key),'from':original,'to':mapped})
                else:
                    paths.append(original)
                    if base_external or Path(original).is_absolute() or '..' in Path(original).parts or '$' in original or '%' in original or original.startswith('~'):
                        notes.append({'kind':'external','field':str(name)+'.'+str(key),'path':original,'base_path':base,'message':'外部模型引用未复制或改写，请在目标电脑核对。'})
            group[key]='\n'.join(paths)
    if changed:
        path.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False),encoding='utf-8')
    return notes
