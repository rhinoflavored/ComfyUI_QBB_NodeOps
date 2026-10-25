from __future__ import annotations

import asyncio
from pathlib import Path

from aiohttp import web

from ..folder_picker import choose
from ..instances import add_instance, get_instance, list_instances, remove_instance
from ..migration import get_status as get_migration_status
from ..migration import scan_plugins as scan_migration_plugins
from ..migration import start_migration
from ..runtime_catalog import catalog
from ..local_gpu import detect, filter_catalog
from .guards import require_local, write_forbidden


def register(routes) -> None:
    @routes.get("/qbb_nodeops/migration/runtime-catalog")
    @require_local
    async def runtime_catalog(request):
        gpu = None
        try:
            gpu = await asyncio.to_thread(detect)
            data = await asyncio.to_thread(catalog, request.query.get("build", "cpu"), request.query.get("refresh") == "1")
            return web.json_response(filter_catalog(data, gpu))
        except Exception as exc:
            return web.json_response({"error": "Unable to load runtime versions: " + str(exc), "hardware": gpu}, status=502)

    @routes.post("/qbb_nodeops/instances/choose-folder")
    async def choose_instance_folder(request):
        denied = write_forbidden(
            request,
            error="Folder selection is available only from the local ComfyUI page. Enter the server directory manually.",
        )
        if denied:
            return denied
        try:
            return web.json_response(await asyncio.to_thread(choose))
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.post("/qbb_nodeops/conflicts/check")
    async def conflict_check(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        from ..conflicts import preflight
        try:
            payload = await request.json()
            return web.json_response(await asyncio.to_thread(preflight, payload))
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.get("/qbb_nodeops/migration/scan")
    @require_local
    async def qbb_nodeops_migration_scan(request):
        """List migration candidates and source-environment details."""
        try:
            source_id = str(request.rel_url.query.get("source_instance_id") or "current")
            source = await asyncio.to_thread(get_instance, source_id)
            data = await asyncio.to_thread(scan_migration_plugins, Path(source["comfyui_root"]))
            return web.json_response(data)
        except ValueError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    @routes.get("/qbb_nodeops/instances")
    @require_local
    async def qbb_nodeops_instances_get(request):
        return web.json_response({"instances": await asyncio.to_thread(list_instances)})

    @routes.post("/qbb_nodeops/instances")
    async def qbb_nodeops_instances_post(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("invalid request body")
            instance = await asyncio.to_thread(add_instance, str(payload.get("path") or ""), str(payload.get("name") or ""))
            return web.json_response({"ok": True, "instance": instance, "instances": await asyncio.to_thread(list_instances)})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    @routes.delete("/qbb_nodeops/instances/{instance_id}")
    async def qbb_nodeops_instances_delete(request):
        denied = write_forbidden(request, json_body=False)
        if denied:
            return denied
        try:
            await asyncio.to_thread(remove_instance, str(request.match_info.get("instance_id") or ""))
            return web.json_response({"ok": True, "instances": await asyncio.to_thread(list_instances)})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    @routes.get("/qbb_nodeops/migration/status")
    @require_local
    async def qbb_nodeops_migration_status(request):
        return web.json_response(get_migration_status().to_dict())

    @routes.post("/qbb_nodeops/migration/start")
    async def qbb_nodeops_migration_start(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid request body"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"ok": False, "error": "invalid request body"}, status=400)
        try:
            # Preparation scans files, runs Git and parses plugin sources.
            # Keep it off aiohttp's event loop, not just the later copy worker.
            status = await asyncio.to_thread(start_migration, payload)
        except (ValueError, RuntimeError) as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        return web.json_response({"ok": True, **status.to_dict()})

    @routes.post('/qbb_nodeops/migration/confirm-recycle')
    async def confirm_source_recycle(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        try:
            from ..migration import confirm_recycle
            result = await asyncio.to_thread(confirm_recycle, await request.json())
            return web.json_response(result.to_dict())
        except (ValueError, RuntimeError) as exc:
            return web.json_response({'error': str(exc)}, status=400)
