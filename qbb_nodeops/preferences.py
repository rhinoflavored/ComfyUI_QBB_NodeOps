"""User-controlled automatic popup preferences, persisted across restarts."""
import json
import threading
from pathlib import Path

PATH=Path(__file__).resolve().parent.parent/'qbb_nodeops_preferences.json'
LOCK=threading.RLock()
DEFAULTS={'popup_errors':True,'popup_updates':True}

def load():
    with LOCK:
        try: data=json.loads(PATH.read_text(encoding='utf-8'))
        except (OSError,ValueError):data={}
        if not isinstance(data,dict):data={}
        return {key:data.get(key,value) if isinstance(data.get(key,value),bool) else value for key,value in DEFAULTS.items()}

def save(data):
    with LOCK:
        current=load()
        for key in DEFAULTS:
            if key in data:
                if not isinstance(data[key],bool):raise ValueError('Expected boolean preference')
                current[key]=data[key]
        temp=PATH.with_suffix('.tmp')
        temp.write_text(json.dumps(current,indent=2),encoding='utf-8');temp.replace(PATH)
        return current
