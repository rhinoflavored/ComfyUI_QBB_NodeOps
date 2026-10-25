"""Current-instance restore coordinator. Preparation is separate from handoff."""
import copy
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

from . import snapshot_state as state, state_diff as diff, file_snapshots as files
from . import version_records as records, restore_materials as material, restore_process, restore_receipts
from . import restore_installed
from .rebuild_executor import atomic_json, read_json

LOCK = threading.Lock()
INSTANCE = None
ACTIVE_WRITES = 0
ACTIVE = 'qbb_nodeops_restore_active.json'
BUSY = {'preparing', 'starting', 'waiting_for_exit', 'applying', 'rolling_back', 'restarting', 'awaiting_verification', 'awaiting_manual_restart', 'verifying', 'cleaning'}


def context():
    from .instances import CURRENT_COMFYUI_ROOT
    import folder_paths
    core = Path(CURRENT_COMFYUI_ROOT).resolve()
    if Path(sys.argv[0]).resolve() != core / 'main.py':
        raise ValueError('Automatic restart supports a direct main.py launch only; no wrapper/launcher guessing')
    from comfy.cli_args import args
    address = getattr(INSTANCE, 'address', None)
    if address not in ('127.0.0.1', 'localhost', '0.0.0.0', '::', '::1'):
        raise ValueError('Controlled restart requires a loopback-accessible service')
    host = '[::1]' if ':' in address else '127.0.0.1'
    scheme = 'https' if args.tls_keyfile and args.tls_certfile else 'http'
    python_flags = ['-I'] if sys.flags.isolated else [flag for flag, enabled in (
        ('-E', sys.flags.ignore_environment), ('-s', sys.flags.no_user_site),
        ('-P', getattr(sys.flags, 'safe_path', False))) if enabled]
    if sys.flags.no_site:
        python_flags.append('-S')
    return {'core': str(core), 'manager': str(Path(__file__).resolve().parent.parent),
            'store': str(records.directory()), 'plugin_roots': list(folder_paths.get_folder_paths('custom_nodes')),
            'python': sys.executable, 'cwd': os.getcwd(), 'argv': [sys.executable, *sys.orig_argv[1:]],
            'python_flags': python_flags,
            'parent_pid': os.getpid(), 'parent_birth': restore_process.identity(os.getpid()),
            'probe_url': f'{scheme}://{host}:{INSTANCE.port}/qbb_nodeops/current-restore'}


def idle(*, own_request=False):
    if INSTANCE is None or INSTANCE.prompt_queue.get_tasks_remaining() != 0:
        raise ValueError('ComfyUI must be idle; pause workflow submissions first')
    from . import migration, rebuild, offline_environment, update_check, market
    if ACTIVE_WRITES > int(own_request) or migration.get_status().status == 'running' or rebuild.LOCK.locked() or offline_environment.LOCK.locked() or update_check.is_pulling() or market._install_lock.locked():
        raise ValueError('Another environment operation is running')


def manager_path(manager=None):
    return Path(manager or Path(__file__).resolve().parent.parent).resolve()


def job_path(job_id, manager=None):
    if not isinstance(job_id, str) or not re.fullmatch('[a-f0-9]{32}', job_id):
        raise ValueError('Invalid restore job ID')
    return material.inside(manager_path(manager) / 'restore-tasks', manager_path(manager) / 'restore-tasks' / job_id)


def status(job_id=None, manager=None):
    root = manager_path(manager)
    if job_id is None:
        pointer = root / ACTIVE
        if not pointer.exists():
            return {'status': 'idle'}
        job_id = read_json(pointer)['id']
    return read_json(job_path(job_id, root) / 'state.json')


def update(job, **values):
    previous = read_json(job / 'state.json') if (job / 'state.json').exists() else {'id': job.name}
    if values.get('status') and values['status'] != previous.get('status'):
        previous['progress'] = {'phase': values['status'], 'current': '', 'done': None, 'total': None}
    previous.update(values, updated=time.time())
    atomic_json(job / 'state.json', previous)
    return previous


def progress(job, phase, current='', done=None, total=None):
    update(job, progress={'phase': phase, 'current': current, 'done': done, 'total': total})


def describe(job_id=None):
    result = status(job_id)
    if result['status'] == 'idle':
        return result
    job = job_path(result['id'])
    result['task_directory'] = str(job)
    for filename, key in [('plan.json', 'plan'), ('verification.json', 'verification')]:
        if (job / filename).is_file():
            result[key] = read_json(job / filename)
    return result


def scan(runtime):
    manifest, captures = state.scan_environment(core=Path(runtime['core']), plugin_roots=runtime['plugin_roots'],
        manager=Path(runtime['manager']), store=Path(runtime['store']), read_only=True)
    manifest['id'] = None
    for row in manifest['resources']:
        if row['id'] in captures:
            row['content'] = state.tree_evidence(captures[row['id']][1])
    return manifest


def binding(manifest):
    value = copy.deepcopy(manifest)
    for key in ('id', 'created', 'label', 'statistics', 'capabilities', 'artifacts'):
        value.pop(key, None)
    for row in value['resources']:
        if row.get('protected'):
            row['source'] = {'kind': 'protected_manager'}
    return state.digest(value)


def checked_plan(current, target, store, comparison_mode='strict'):
    result = diff.compare(current, target, comparison_mode)
    health = diff.object_health(target, store)
    plan = diff.recovery_plan(result, target, health)
    plan['fingerprint'] = state.digest({'diff': result, 'target': target, 'health': health})
    return plan


def prepare(record_id, fingerprint, confirmed=False, allow_network=False, *, runtime=None, comparison_mode='strict', comparison_token=None):
    if comparison_mode not in ('strict', 'names_only'):
        raise ValueError('Invalid comparison mode')
    if confirmed is not True or type(allow_network) is not bool:
        raise ValueError('Confirm the trusted snapshot, affected resources and optional downloads before preparation')
    if not LOCK.acquire(blocking=False):
        raise ValueError('Restore preparation/handoff is already running')
    job = None
    try:
        if runtime is None:
            idle(own_request=True)
            runtime = context()
        core, manager, store = (Path(runtime[k]).resolve() for k in ('core', 'manager', 'store'))
        material.inside(core, manager)
        previous = status(manager=manager)
        if previous['status'] not in {'idle', 'completed', 'cancelled'}:
            raise ValueError('An existing restore task needs completion, cancellation or manual review')
        with records.LOCK:
            current, target, plan = diff.reviewed_restore(record_id, fingerprint, comparison_mode, store, comparison_token)
            if Path(current['instance']['root_at_capture']).resolve() != core:
                raise ValueError('Reviewed plan belongs to another instance')
            if plan['blockers']:
                raise ValueError('Restore blocked: ' + ', '.join(sorted({b['reason'] for b in plan['blockers']})))
            if not plan['execution_ready']:
                raise ValueError('Restore preparation unavailable: ' + (plan['execution_notice'] or 'comparison_incomplete'))
            if not plan['actions']:
                return {'status': 'no_changes', 'execution_allowed': False}
            job_id = uuid.uuid4().hex
            job = job_path(job_id, manager)
            job.mkdir(parents=True)
            (job / 'payload' / 'empty-directory').mkdir(parents=True)
            update(job, status='preparing', record_id=record_id, target_label=target.get('label', ''),
                   target_created=target.get('created'), comparison_mode=comparison_mode, action_results=[])
            atomic_json(job / 'plan.json', plan)
            atomic_json(manager / ACTIVE, {'id': job_id})
            now, goal = diff.index(current), diff.index(target)
            file_actions = [a for a in plan['actions'] if a['kind'] == 'package' and a['material']['kind'] in ('local_zip', 'installed_file_removal')]
            package_layout = restore_installed.layout() if file_actions else None
            specs = restore_installed.package_specs(file_actions, current, target, package_layout, manager) if file_actions else {}
            if file_actions and any(a['kind'] == 'package' and a not in file_actions for a in plan['actions']):
                raise ValueError('Mixed installed-file and legacy pip changes require separate restore plans')
            prepared = []
            for number, action in enumerate(plan['actions']):
                identity = action['resource_id'].casefold()
                a, b = now.get(identity), goal.get(identity)
                row = b or a
                progress(job, 'prepare_resources', row['name'], number, len(plan['actions']))
                if action['status'] != 'proposed' or not row['managed'] or row.get('protected'):
                    raise ValueError('Cannot execute blocked, external or protected resource')
                item = {'resource_id': action['resource_id'], 'kind': row['kind'], 'current': a, 'target': b}
                folder = job / 'payload' / str(number)
                if row['kind'] == 'package':
                    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*', row['name']):
                        raise ValueError('Invalid package name')
                    if identity in specs:
                        progress(job, 'prepare_installed_rollback', row['name'], number, len(plan['actions']))
                        item['file_restore'] = restore_installed.prepare_package(b, target, store, folder, specs[identity], package_layout, manager)
                    else:
                        if a:
                            material.package_scope(a['name'], manager)
                        item['install'] = material.prepare_wheel(b, target, store, folder / 'target', allow_network) if b else None
                        item['rollback'] = material.prepare_wheel(a, current, store, folder / 'rollback', allow_network) if a else None
                else:
                    relative = row['relative_path']
                    if row['kind'] == 'configuration' and relative != row['name']:
                        raise ValueError('Configuration path is not allowlisted')
                    if row['kind'] == 'core' and relative != '.':
                        raise ValueError('Core path must be the current instance')
                    if row['kind'] == 'plugin' and not any(
                            (core / relative).parent == Path(root).resolve() for root in runtime['plugin_roots']):
                        raise ValueError('Plugin destination is outside current managed plugin roots')
                    path = core if row['kind'] == 'core' else material.inside(core, core / relative)
                    if path != core and (path == manager or path.is_relative_to(manager) or manager.is_relative_to(path)):
                        raise ValueError('Manager path is protected at execution boundary')
                    if not path.parent.is_dir():
                        raise ValueError('Plugin root must already exist')
                    if a and a.get('issues'):
                        raise ValueError('Current content has unknown gaps')
                    if a and row['kind'] == 'core' and diff.tree(a, current).get('') != diff.tree(b, target).get(''):
                        raise ValueError('Changing the instance root permissions is outside V1 execution scope')
                    if b and (b['kind'] != 'configuration' or b['present']):
                        recovery_material = action['material']
                        if recovery_material['kind'] == 'local_zip' or recovery_material.get('requires_file_executor'):
                            progress(job, 'prepare_zip_contents', row['name'], number, len(plan['actions']))
                            item['payload'] = restore_installed.prepare_code(b, target, store, folder, recovery_material)
                        else:
                            item['payload'] = material.prepare_code(b, target, store, folder, path if a else None, allow_network)
                    else:
                        item['payload'] = None
                    # Moving a whole plugin is allowed only after every contained path is checked.
                    if a and (b is None or item['payload'] and Path(item['payload']['content']).is_file()):
                        if any(e['reason'] in ('outside_scope_v1', 'configuration_not_allowlisted') for e in a.get('excluded', [])):
                            raise ValueError('Whole-resource replacement would affect unmanaged contents')
                        material.inventory(path)
                prepared.append(item)
            progress(job, 'verify_preparation', done=len(prepared), total=len(prepared))
            if package_layout:
                restore_installed.verify_before(prepared, package_layout, manager)
            payload_bytes = sum(p.stat().st_size for p in (job / 'payload').rglob('*') if p.is_file())
            unpacked_bytes = sum((item.get(key) or {}).get('unpacked_bytes', 0) for item in prepared for key in ('install', 'rollback'))
            if shutil.disk_usage(job).free < payload_bytes * 3 + unpacked_bytes * 2 + 64 * files.CHUNK:
                raise ValueError('Insufficient free space for offline replacement and rollback')
            if shutil.disk_usage(runtime['python']).free < unpacked_bytes * 2 + 64 * files.CHUNK:
                raise ValueError('Insufficient interpreter-volume space for package changes and rollback')
            if package_layout and shutil.disk_usage(package_layout['root']).free < sum(i.get('file_restore', {}).get('bytes', 0) for i in prepared) + 64 * files.CHUNK:
                raise ValueError('Insufficient interpreter-volume space for installed-file replacement')
            frozen = {'version': 1, 'comparison_mode': comparison_mode, 'runtime': runtime, 'target': target, 'current': current,
                      'current_binding': binding(current), 'actions': prepared,
                      'payload_binding': state.digest(material.inventory(job / 'payload')),
                      'package_layout': package_layout,
                      'created': time.time(), 'expires': time.time() + 1800}
            atomic_json(job / 'frozen.json', frozen)
            digest = files.hash_file(job / 'frozen.json')
            return update(job, status='prepared', plan_hash=digest, actions=len(prepared),
                          message='Prepared only. A separate explicit start stops and restarts this instance.')
    except Exception as exc:
        if job is not None:
            update(job, status='preparation_failed', error=str(exc))
        raise
    finally:
        LOCK.release()


def frozen(job, expected_hash):
    if files.hash_file(job / 'frozen.json') != expected_hash:
        raise ValueError('Frozen plan was modified')
    value = read_json(job / 'frozen.json')
    if value['version'] != 1 or Path(value['runtime']['manager']).resolve() != job.parent.parent.resolve():
        raise ValueError('Frozen plan manager binding mismatch')
    material.inside(Path(value['runtime']['core']), Path(value['runtime']['manager']))
    if state.digest(material.inventory(job / 'payload')) != value['payload_binding']:
        raise ValueError('Prepared payload changed or is incomplete')
    return value


def start(job_id, plan_hash, confirmed=False):
    if confirmed is not True:
        raise ValueError('Explicit confirmation to stop/restart this instance is required')
    with LOCK:
        idle(own_request=True)
        runtime = context()
        job = job_path(job_id, runtime['manager'])
        if status(job_id, runtime['manager'])['status'] != 'prepared':
            raise ValueError('Task is not prepared or has already been started')
        progress(job, 'verify_frozen_payload')
        value = frozen(job, plan_hash)
        if value['expires'] < time.time() or runtime != value['runtime']:
            raise ValueError('Prepared task expired or belongs to another process')
        with records.LOCK:
            # frozen() verified the complete prepared payload. Execution uses it,
            # not the original backup objects; do not rehash the store or rescan.
            if value.get('package_layout'):
                progress(job, 'verify_handoff_packages')
                restore_installed.verify_before(value['actions'], value['package_layout'], runtime['manager'])
        update(job, status='starting', manual_restart=True, message='Launching restore worker only. Close ComfyUI manually, then wait for the restore window to finish before restarting.')
        command = [runtime['python'], *runtime.get('python_flags', []), '-B', str(Path(runtime['manager']) / 'qbb_nodeops' / 'restore_worker.py'),
                   '--job', str(job), '--plan-hash', plan_hash, '--manual-lifecycle']
        kwargs = {'start_new_session': True} if os.name != 'nt' else {'creationflags': subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP}
        try:
            if os.name == 'nt':
                process = subprocess.Popen(command, cwd=runtime['manager'], **kwargs)
            else:
                with (job / 'worker.log').open('ab') as log:
                    process = subprocess.Popen(command, cwd=runtime['manager'], stdin=subprocess.DEVNULL, stdout=log, stderr=log, **kwargs)
            launcher_birth = restore_process.identity(process.pid)
            deadline = time.monotonic() + 20
            while not (job / 'worker-ready.json').exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise ValueError('Worker failed to become ready; ComfyUI was not stopped')
                time.sleep(0.05)
            ready = read_json(job / 'worker-ready.json')
            if not restore_process.spawned_process(ready['pid'], process.pid, launcher_birth) or ready['plan_hash'] != plan_hash:
                raise ValueError('Worker handshake mismatch')
            idle(own_request=True)
            atomic_json(job / 'handoff.json', {'plan_hash': plan_hash, 'parent_birth': runtime['parent_birth']})
            return {'id': job_id, 'status': 'waiting_for_exit', 'worker_pid': ready['pid'], 'manual_restart': True}
        except Exception as exc:
            update(job, status='handoff_failed', error=str(exc))
            raise


def cancel(job_id, confirmed=False):
    if confirmed is not True:
        raise ValueError('Confirmation required')
    with LOCK:
        job = job_path(job_id)
        if status(job_id)['status'] not in ('prepared', 'preparation_failed', 'handoff_failed'):
            raise ValueError('A handed-off task cannot be cancelled or discarded')
        with restore_process.task_lock(job / 'worker.lock'):
            material.remove_task_tree(job, job / 'payload')
            return update(job, status='cancelled')


def install_wheel(runtime, wheel):
    if files.hash_file(wheel['path']) != wheel['sha256']:
        raise ValueError('Wheel changed after preparation')
    material.run([runtime['python'], '-B', '-m', 'pip', '--isolated', '--disable-pip-version-check', 'install',
                  '--no-index', '--no-deps', '--no-compile', '--force-reinstall', wheel['path']])
    importlib.invalidate_caches()
    dist = importlib.metadata.distribution(wheel['name'])
    direct = json.loads(dist.read_text('direct_url.json') or '{}')
    sha = direct.get('archive_info', {}).get('hashes', {}).get('sha256')
    if dist.version != wheel['version'] or sha != wheel['sha256']:
        raise ValueError('Installed wheel metadata does not match frozen artifact')
    restore_receipts.save(runtime['manager'], state.key(wheel['name']), {
        'version': wheel['version'], 'observed': restore_receipts.digest(direct), 'sha256': sha,
        'source': wheel['source'], 'store': wheel['store']})


def uninstall(runtime, name):
    if state.key(name) == 'pip':
        raise ValueError('pip is protected')
    material.run([runtime['python'], '-B', '-m', 'pip', '--isolated', '--disable-pip-version-check', 'uninstall', '--yes', name])
    restore_receipts.save(runtime['manager'], state.key(name), None)


def execute(job, value):
    runtime = value['runtime']
    if restore_process.identity(runtime['parent_pid']) == runtime['parent_birth']:
        raise ValueError('Original ComfyUI process is still running; no offline mutation allowed')
    if Path(sys.executable).resolve() != Path(runtime['python']).resolve():
        raise ValueError('Worker interpreter mismatch')
    package_layout = value.get('package_layout')
    if package_layout:
        restore_installed.verify_before(value['actions'], package_layout, runtime['manager'])
    journal = restore_installed.Journal(job, runtime['core'], runtime['manager'], package_layout,
                                       [i['file_restore'] for i in value['actions'] if i.get('file_restore')])
    packages = []
    outcomes = []
    update(job, status='applying', worker_pid=os.getpid())
    try:
        for number, item in enumerate(value['actions']):
            progress(job, 'apply_' + item['kind'], (item['target'] or item['current'])['name'], number, len(value['actions']))
            if item['kind'] == 'package':
                if item.get('file_restore'):
                    restore_installed.apply_package(journal, item['file_restore'])
                else:
                    packages.append(item)
                    atomic_json(job / 'package-journal.json', packages)
                    if item['install']:
                        install_wheel(runtime, item['install'])
                    else:
                        uninstall(runtime, item['current']['name'])
            else:
                action = {'target_manifest': value['target'], 'current_manifest': value['current']}
                material.apply_code(journal, action, item['current'], item['target'], item.get('payload'))
            outcomes.append({'resource_id': item['resource_id'], 'kind': item['kind'], 'status': 'applied',
                             'saved_artifact': bool(item['target'] and (item['target'].get('artifact_ref') or item['target'].get('archive_ref'))),
                             'material': 'installed_files' if item.get('file_restore') else 'local_files' if (item.get('payload') or {}).get('preserve_repository') else 'legacy',
                             'operation': 'remove' if item['target'] is None or item['kind'] == 'configuration' and not item['target']['present'] else 'restore'})
            update(job, action_results=outcomes)
        progress(job, 'apply_finished', done=len(outcomes), total=len(value['actions']))
        return 'applied'
    except Exception as exc:
        outcomes.append({'resource_id': item['resource_id'], 'kind': item['kind'], 'status': 'failed', 'error': str(exc)})
        update(job, status='rolling_back', error=str(exc), action_results=outcomes)
        failures = []
        for number, item in enumerate(reversed(packages)):
            progress(job, 'rollback_packages', (item['current'] or item['target'])['name'], number, len(packages))
            try:
                if item['rollback']:
                    install_wheel(runtime, item['rollback'])
                else:
                    uninstall(runtime, item['target']['name'])
            except Exception as failure:
                failures.append(str(failure))
        try:
            progress(job, 'rollback_files')
            journal.rollback()
        except Exception as failure:
            failures.append(str(failure))
        if not failures:
            outcomes = [{**r, 'status': 'rolled_back' if r['status'] == 'applied' else r['status'], 'rollback': 'completed'} for r in outcomes]
        update(job, status='failed_rollback' if failures else 'rolled_back', rollback_errors=failures, action_results=outcomes)
        if failures:
            raise ValueError('Rollback incomplete; do not restart automatically') from exc
        return 'rolled_back'


def reconnect(*, runtime=None, job_id=None, service_ready=False):
    if service_ready is not True:
        raise ValueError('Final verification requires a successful restarted-service probe')
    runtime = runtime or context()
    manager = runtime['manager']
    active = status(job_id, manager)
    if active['status'] not in ('restarting', 'awaiting_verification', 'awaiting_manual_restart'):
        return active
    job = job_path(active['id'], manager)
    for _ in range(200):
        active = status(active['id'], manager)
        if active['status'] != 'restarting':
            break
        time.sleep(0.05)
    manual = active.get('manual_restart') is True or active['status'] == 'awaiting_manual_restart'
    if not manual and not restore_process.spawned_process(os.getpid(), active.get('restarted_pid'), active.get('restarted_birth')):
        return update(job, status='partial', error='Restarted process missing or identity mismatch; retained task requires manual review')
    with restore_process.task_lock(job / 'worker.lock'):
        value = read_json(job / 'frozen.json')
        if files.hash_file(job / 'frozen.json') != active['plan_hash']:
            return update(job, status='partial', error='Frozen target modified; staging retained')
        if manual:
            if any(Path(runtime[key]).resolve() != Path(value['runtime'][key]).resolve() for key in ('core', 'manager', 'python')):
                return update(job, status='partial', error='Manual restart belongs to another instance or interpreter; staging retained')
            if restore_process.identity(value['runtime']['parent_pid']) == value['runtime']['parent_birth']:
                return update(job, status='partial', error='Original ComfyUI still running; staging retained')
        update(job, status='verifying')
        try:
            # Report execution of the approved actions, not a newly scanned Diff.
            results = active.get('action_results', [])
            expected = {item['resource_id'] for item in value['actions']}
            applied = {item['resource_id'] for item in results if item.get('status') == 'applied'}
            success = active.get('outcome') == 'applied' and applied == expected and len(results) == len(expected)
            final_plan = {'status': 'actions_applied' if success else 'actions_incomplete',
                'read_only': True, 'environment_rescanned': False, 'blockers': [],
                'action_results': results, 'expected_actions': len(expected),
                'comparison_mode': value.get('comparison_mode', 'strict')}
            atomic_json(job / 'verification.json', final_plan)
            if not success:
                return update(job, status='failed_rolled_back' if active.get('outcome') == 'rolled_back' else 'partial',
                    message='Approved actions not fully applied; staging retained.')
            update(job, status='cleaning')
            for name in ('payload', 'backup'):
                material.remove_task_tree(job, job / name)
            return update(job, status='completed', differences=0, unverified=0,
                message='Approved restore actions applied; no environment rescan performed.')
        except Exception as exc:
            return update(job, status='partial', error=str(exc), message='Verification/cleanup failed; not reported as success')


def maintenance():
    try:
        return status()['status'] in BUSY
    except (OSError, ValueError, KeyError):
        return True  # A malformed active transaction must not open mutation endpoints.
