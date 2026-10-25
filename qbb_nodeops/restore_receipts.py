"""Verified install provenance. Never trust a receipt over changed pip metadata."""
import copy
import hashlib
import json
from pathlib import Path
from . import file_snapshots as files
from .rebuild_executor import atomic_json

FILENAME = 'qbb_nodeops_restore_sources.json'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load(manager):
    path = Path(manager) / FILENAME
    files.no_links(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def match(receipts, name, version, direct):
    receipt = receipts.get(name)
    if not receipt or receipt['version'] != version or receipt['observed'] != digest(direct):
        return None
    sha = direct.get('archive_info', {}).get('hashes', {}).get('sha256')
    if not sha:
        sha = direct.get('archive_info', {}).get('hash', '').removeprefix('sha256=')
    if receipt['sha256'] != sha or receipt['source']['kind'] not in ('local_wheel', 'remote_wheel'):
        return None
    return copy.deepcopy(receipt)


def save(manager, name, receipt):
    path = Path(manager) / FILENAME
    data = load(manager)
    if receipt is None:
        data.pop(name, None)
    else:
        data[name] = receipt
    atomic_json(path, data)
