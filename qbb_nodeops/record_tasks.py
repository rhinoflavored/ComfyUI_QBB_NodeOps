"""Persistent UI status for one manual preview/capture, not an environment executor."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time

from . import version_records as records, file_snapshots as files, restore_process
from .rebuild_executor import atomic_json, read_json

ROOT = Path(__file__).resolve().parent.parent / 'record-tasks'
LOCK = threading.Lock()
LOCAL = threading.local()
RUNNING = {'queued', 'running'}


def emit(phase, current='', done=None, total=None):
    callback = getattr(LOCAL, 'callback', None)
    if callback:
        callback({'phase': getattr(LOCAL, 'phase', None) or phase, 'current': current, 'done': done, 'total': total})


@contextmanager
def phase(name):
    previous = getattr(LOCAL, 'phase', None)
    LOCAL.phase = name
    try:
        yield
    finally:
        LOCAL.phase = previous


def path(task_id):
    if not isinstance(task_id, str) or not re.fullmatch('[a-f0-9]{32}', task_id):
        raise ValueError('Invalid capture task ID')
    result = ROOT / (task_id + '.json')
    files.no_links(result)
    return result


def read(location):
    return read_json(location)


def status(task_id=None):
    if task_id is None:
        pointer = ROOT / 'active.json'
        files.no_links(pointer)
        if not pointer.exists():
            return {'status': 'idle'}
        task_id = read(pointer)['id']
    value = read(path(task_id))
    if value['status'] in RUNNING and restore_process.identity(value['pid']) != value['birth']:
        value.update(status='interrupted', error='Capture process exited. Check history; completion was not confirmed.')
    return value


def start(action, request_id, payload):
    allowed = {'preview': {'options'}, 'create': {'label', 'options', 'preview_id', 'confirmed'}}
    if action not in allowed or not isinstance(payload, dict) or set(payload) - allowed[action]:
        raise ValueError('Invalid manual capture request')
    if action == 'create' and payload.get('confirmed') is not True:
        raise ValueError('Confirm capture scope before saving')
    location = path(request_id)
    fingerprint = hashlib.sha256(json.dumps({'action': action, 'payload': payload}, sort_keys=True).encode()).hexdigest()
    with LOCK:
        if location.exists():
            previous = status(request_id)
            if previous['fingerprint'] != fingerprint:
                raise ValueError('Capture request ID already belongs to another request')
            return previous  # A lost response must never publish a second snapshot.
        if status()['status'] in RUNNING or records.LOCK.locked():
            raise ValueError('A snapshot operation is already running')
        ROOT.mkdir(parents=True, exist_ok=True)
        task = {'id': request_id, 'action': action, 'status': 'queued', 'phase': 'queued',
                'label': str(payload.get('label', ''))[:120], 'fingerprint': fingerprint,
                'pid': os.getpid(), 'birth': restore_process.identity(os.getpid()), 'created': time.time()}
        atomic_json(location, task)
        atomic_json(ROOT / 'active.json', {'id': request_id})
        def run():
            def progress(fields):
                task.update(fields, status='running', updated=time.time())
                atomic_json(location, task)
            LOCAL.callback = progress
            try:
                emit('scan_core')
                if action == 'preview':
                    result = records.preview(payload.get('options'))
                else:
                    result = records.create(payload.get('label', ''), payload.get('options'), payload.get('preview_id'))
                task.update(status='completed', phase='completed', result=result, current='', done=None, total=None)
            except Exception as exc:
                task.update(status='failed', error=str(exc))
            finally:
                LOCAL.callback = None
                task['updated'] = time.time()
                atomic_json(location, task)
        worker = threading.Thread(target=run, name='qbb-record-' + request_id[:8], daemon=True)
        try:
            worker.start()
        except Exception as exc:
            task.update(status='failed', error=str(exc))
            atomic_json(location, task)
            raise
        return dict(task)
