"""Current-instance control API and persisted task details for the recovery UI."""
import asyncio
import os
from aiohttp import web, ClientSession, ClientTimeout, ClientError

from .guards import require_local, write_forbidden
from .. import current_restore as restore


def register(routes, instance):
    restore.INSTANCE = instance

    @routes.get('/qbb_nodeops/current-restore')
    @require_local
    async def task_status(request):
        try:
            return web.json_response({**await asyncio.to_thread(restore.status), 'server_pid': os.getpid(),
                                      'server_birth': restore.restore_process.identity(os.getpid())})
        except (ValueError, OSError, KeyError) as exc:
            return web.json_response({'error': str(exc)}, status=409)

    @routes.post('/qbb_nodeops/current-restore/{action}')
    async def action(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        try:
            operation = request.match_info['action']
            fields = {'prepare': {'record_id', 'fingerprint', 'confirmed', 'allow_network', 'comparison_mode', 'comparison_token'},
                      'start': {'job_id', 'plan_hash', 'confirmed'}, 'cancel': {'job_id', 'confirmed'}}
            if operation not in fields:
                raise ValueError('Unknown restore action')
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) - fields[operation]:
                raise ValueError('Unsupported restore options; alternate instance paths are not accepted')
            result = await asyncio.to_thread(getattr(restore, operation), **payload)
            return web.json_response(result)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            return web.json_response({'error': str(exc)}, status=409)

    @routes.get('/qbb_nodeops/current-restore/{job_id}')
    @require_local
    async def task_details(request):
        try:
            return web.json_response(await asyncio.to_thread(restore.describe, request.match_info['job_id']))
        except (ValueError, OSError, KeyError) as exc:
            return web.json_response({'error': str(exc)}, status=404)


def install_maintenance_guard(instance):
    @web.middleware
    async def maintenance_guard(request, handler):
        writing = request.method not in ('GET', 'HEAD', 'OPTIONS')
        if writing and restore.maintenance():
            return web.json_response({'error': 'Current-instance restoration is in maintenance mode'}, status=503)
        if writing:
            restore.ACTIVE_WRITES += 1
        try:
            return await handler(request)
        finally:
            if writing:
                restore.ACTIVE_WRITES -= 1
    instance.app.middlewares.append(maintenance_guard)


async def reconnect(instance):
    # Scheduled at startup; do not verify before the server has actually bound its port.
    try:
        active = restore.status()
        if active['status'] in ('restarting', 'awaiting_verification', 'awaiting_manual_restart'):
            ready = False
            async with ClientSession(timeout=ClientTimeout(total=2), trust_env=False) as client:
                deadline = asyncio.get_running_loop().time() + 120
                while asyncio.get_running_loop().time() < deadline:
                    if hasattr(instance, 'address'):
                        runtime = restore.context()
                        try:
                            # TLS may use a self-signed local certificate; no remote host is accepted by context().
                            async with client.get(runtime['probe_url'], headers={'X-QBB-NodeOps': '1'}, ssl=False) as response:
                                value = await response.json()
                                ready = response.status == 200 and value.get('server_pid') == os.getpid() and value.get('server_birth') == restore.restore_process.identity(os.getpid())
                        except (OSError, ValueError, TimeoutError, ClientError):
                            pass
                        if ready:
                            break
                    await asyncio.sleep(0.1)
            if not ready:
                restore.update(restore.job_path(active['id']), status='partial', error='Restarted service did not become ready; staging retained')
                return
            result = await asyncio.to_thread(restore.reconnect, service_ready=True)
            print('[QBB NodeOps] Restore verification: ' + result['status'], flush=True)
        elif active['status'] in restore.BUSY:
            pid, birth = active.get('worker_pid'), active.get('worker_birth')
            if not pid or restore.restore_process.identity(pid) != birth:
                restore.update(restore.job_path(active['id']), status='interrupted',
                    error='Worker interrupted; no automatic replay. Inspect retained journal before manual recovery.')
    except Exception as exc:
        if 'active' in locals() and active['status'] in restore.BUSY:
            restore.update(restore.job_path(active['id']), status='partial', error=str(exc), message='Service reconnect failed; staging retained')
        print('[QBB NodeOps] Restore reconnect failed: ' + str(exc), flush=True)
