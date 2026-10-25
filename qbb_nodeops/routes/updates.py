from __future__ import annotations

import asyncio

from aiohttp import web

from ..settings import apply_comfyui_process_proxy, load_settings, network_mode, save_settings
from ..update_check import get_report as get_update_report
from ..update_check import is_pulling, pull_many, start_update_check_background
from .guards import require_local, write_forbidden


def register(routes) -> None:
    @routes.get("/qbb_nodeops/updates")
    @require_local
    async def updates(request):
        """Return latest custom-node update check report (may still be running)."""
        data = get_update_report().to_dict()
        if not data.get("network_mode"):
            data["network_mode"] = network_mode()
        return web.json_response(data)

    @routes.get("/qbb_nodeops/network_settings")
    @require_local
    async def network_settings_get(request):
        s = load_settings()
        return web.json_response({**s, "mode": network_mode(s)})

    @routes.post("/qbb_nodeops/network_settings")
    async def network_settings_post(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求格式无效"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "请求格式无效"}, status=400)
        try:
            saved = save_settings(data)
        except ValueError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=500)
        # This takes effect for new requests/subprocesses right away. Restarting
        # ComfyUI is still required for startup imports and all libraries to
        # inherit the saved process-wide proxy from prestartup_script.py.
        applied_to_process = apply_comfyui_process_proxy(saved)
        return web.json_response(
            {
                "ok": True,
                **saved,
                "mode": network_mode(saved),
                "applied_to_process": applied_to_process,
                "restart_required": True,
            }
        )

    @routes.post("/qbb_nodeops/updates/refresh")
    async def updates_refresh(request):
        """Re-run update check in background."""
        denied = write_forbidden(request)
        if denied:
            return denied
        current = get_update_report()
        if current.status == "running":
            return web.json_response({"ok": True, "started": False, **current.to_dict()})
        start_update_check_background(force=True)
        return web.json_response({"ok": True, "started": True, **get_update_report().to_dict()})

    @routes.post("/qbb_nodeops/updates/pull")
    async def updates_pull(request):
        """git pull one or more outdated custom nodes (ff-only)."""
        denied = write_forbidden(request)
        if denied:
            return denied
        if is_pulling():
            return web.json_response(
                {"ok": False, "error": "another update is already running"},
                status=409,
            )

        try:
            data = await request.json()
        except Exception:
            data = {}

        paths = data.get("paths") if isinstance(data, dict) else None
        path = data.get("path") if isinstance(data, dict) else None
        if path and not paths:
            paths = [path]
        if paths is not None and not isinstance(paths, list):
            return web.json_response({"ok": False, "error": "paths must be a list"}, status=400)

        # None => pull all outdated; non-empty list => those paths only
        pull_all = bool(data.get("all")) if isinstance(data, dict) else False
        if pull_all:
            target_paths = None
        elif paths:
            target_paths = [str(p) for p in paths]
        else:
            return web.json_response(
                {"ok": False, "error": "provide path/paths or set all=true"},
                status=400,
            )

        result = await asyncio.to_thread(pull_many, target_paths)
        status = 200 if result.get("ok") else 500
        if result.get("error") == "another update is already running":
            status = 409
        return web.json_response(result, status=status)
