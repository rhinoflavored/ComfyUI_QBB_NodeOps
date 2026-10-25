from __future__ import annotations

import asyncio

from aiohttp import web

from .guards import require_local, write_forbidden


def register(routes) -> None:
    @routes.get('/qbb_nodeops/record-tasks')
    @routes.get('/qbb_nodeops/record-tasks/{task_id}')
    @require_local
    async def capture_status(request):
        from .. import record_tasks
        try:
            return web.json_response(await asyncio.to_thread(record_tasks.status, request.match_info.get('task_id')))
        except (ValueError, OSError, KeyError) as exc:
            return web.json_response({'error': str(exc)}, status=404)

    @routes.post('/qbb_nodeops/record-tasks/{action}')
    async def capture_start(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        from .. import record_tasks
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) != {'request_id', 'payload'}:
                raise ValueError('Expected request_id and payload')
            result = await asyncio.to_thread(record_tasks.start, request.match_info['action'], payload['request_id'], payload['payload'])
            return web.json_response(result, status=202)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            return web.json_response({'error': str(exc)}, status=409)

    @routes.get('/qbb_nodeops/version-records/comparison-progress/{progress_id}')
    @require_local
    async def comparison_status(request):
        from ..state_diff import comparison_progress
        return web.json_response(comparison_progress(request.match_info['progress_id']))

    @routes.post('/qbb_nodeops/version-records/{record_id}/recovery-plan')
    async def readonly_recovery_plan(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        from ..state_diff import inspect
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) - {'current_record_id', 'content_only', 'progress_id', 'comparison_mode', 'comparison_token'}:
                raise ValueError('Read-only plan accepts no execution options or destination paths')
            if type(payload.get('content_only', False)) is not bool:
                raise ValueError('content_only must be boolean')
            return web.json_response(await asyncio.to_thread(inspect, request.match_info['record_id'], payload.get('current_record_id'), content_only=payload.get('content_only', False), progress_id=payload.get('progress_id'), comparison_mode=payload.get('comparison_mode', 'strict'), comparison_token=payload.get('comparison_token')))
        except (ValueError, OSError, KeyError, TypeError) as exc:
            return web.json_response({'error': str(exc)}, status=400)

    @routes.post('/qbb_nodeops/version-records/archive/{action}')
    async def archive_action(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        from .. import record_archives as archive
        from .. import offline_environment as offline
        from .. import checkpoint_policy as checkpoints
        from ..folder_picker import choose
        try:
            p = await request.json()
            action = request.match_info['action']
            operations = {
                'choose-path': lambda: choose(p.get('kind','folder')),
                'export-preview': lambda: archive.preview_export(p['record_id'],p['path']),
                'export': lambda: archive.export_bundle(p['record_id'], p['path'], p.get('confirmed'),token=p.get('token')),
                'check': lambda: archive.check_record(p['record_id']),
                'inspect': lambda: archive.preview_import(p['path']),
                'import': lambda: archive.import_bundle(p['token'], p.get('confirmed')),
                'gc-preview': archive.preview_gc,
                'gc-apply': lambda: archive.apply_gc(p['token'], p.get('confirmed')),
                'remove-preview': lambda: archive.preview_remove(p['record_id']),
                'remove': lambda: archive.remove_record(p['record_id'], p.get('confirmed'),token=p.get('token')),
                'offline-start': lambda: offline.start(p['record_id'],p.get('wheel_directory',''),p.get('confirmed')),
                'offline-status': offline.status,
                'checkpoint-status': checkpoints.storage_status,
                'checkpoint-configure': lambda: checkpoints.configure(p.get('policy'),p.get('confirmed',False)),
            }
            if action not in operations:
                return web.json_response({'error': 'Unknown action'}, status=404)
            return web.json_response(await asyncio.to_thread(operations[action]))
        except Exception as exc:
            return web.json_response({'error': str(exc)}, status=400)

    @routes.get("/qbb_nodeops/rebuild")
    @require_local
    async def rebuild_status(request):
        from ..rebuild import status, history
        return web.json_response({"current": status(), "history": await asyncio.to_thread(history)})

    @routes.post("/qbb_nodeops/rebuild/{action}")
    async def rebuild_action(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        from ..rebuild import plan, start
        try:
            payload = await request.json()
            if request.match_info["action"] == "compare":
                from ..record_compare import compare
                result = await asyncio.to_thread(compare, payload['record_id'], payload.get('other_record_id'))
            elif request.match_info["action"] == "plan":
                result = await asyncio.to_thread(plan, payload["record_id"], payload.get('choices'))
            elif request.match_info["action"] == "start":
                result = await asyncio.to_thread(start, payload["record_id"], payload["target"], payload.get("confirmed", False), payload.get('choices'), payload.get('plan_id'))
            elif request.match_info["action"] == "resume":
                from ..rebuild import resume
                result = await asyncio.to_thread(resume, payload['job_id'], payload.get('confirmed', False))
            elif request.match_info["action"] == "verify":
                from ..rebuild import verify_recovery
                result = await asyncio.to_thread(verify_recovery, payload['job_id'], payload.get('confirmed', False), payload.get('startup',False), payload.get('plugins',False), payload.get('service',False), payload.get('expected_nodes'), payload.get('workflow'), payload.get('workflow_confirmed',False))
            else:
                return web.json_response({"error": "Unknown action"}, status=404)
            return web.json_response(result)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.get("/qbb_nodeops/snapshots")
    @require_local
    async def snapshot_list(request):
        from ..snapshots import listing
        return web.json_response(await asyncio.to_thread(listing))

    @routes.post("/qbb_nodeops/snapshots")
    async def snapshot_create(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        return web.json_response({"error": "Full snapshots are no longer created. Use version records."}, status=409)

    @routes.get("/qbb_nodeops/version-records/storage")
    @require_local
    async def record_storage_get(request):
        from ..version_records import directory
        return web.json_response({"directory": str(directory())})

    @routes.post("/qbb_nodeops/version-records/storage")
    async def record_storage_set(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        from ..version_records import set_directory
        try:
            payload = await request.json()
            return web.json_response(await asyncio.to_thread(set_directory, payload.get("directory")))
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.get("/qbb_nodeops/version-records")
    @require_local
    async def version_records_list(request):
        from ..version_records import listing
        return web.json_response(await asyncio.to_thread(listing))

    @routes.get("/qbb_nodeops/version-records/{record_id}")
    @require_local
    async def version_records_read(request):
        from ..version_records import read
        try:
            return web.json_response(await asyncio.to_thread(read, request.match_info["record_id"]))
        except (ValueError, OSError):
            return web.json_response({"error": "Record not found"}, status=404)

    @routes.post("/qbb_nodeops/version-records/preview")
    async def version_records_preview(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        from ..version_records import preview
        try:
            payload = await request.json()
            return web.json_response(await asyncio.to_thread(preview, payload.get('options')))
        except Exception as exc:
            return web.json_response({'error': str(exc)}, status=400)

    @routes.post("/qbb_nodeops/version-records")
    async def version_records_create(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        from ..version_records import create
        try:
            payload = await request.json()
            return web.json_response(await asyncio.to_thread(create, payload.get("label", ""), payload.get('options'), payload.get('preview_id'), checkpoint=payload.get('checkpoint')))
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
