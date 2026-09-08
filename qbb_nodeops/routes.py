from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path

from aiohttp import web

from .env_export import build_environment_report, default_deps_filename
from .store import REPORT
from .update_check import get_report as get_update_report
from .update_check import is_pulling, pull_many, start_update_check_background
from .settings import apply_comfyui_process_proxy, load_settings, network_mode, save_settings
from .migration import get_status as get_migration_status
from .migration import scan_plugins as scan_migration_plugins
from .migration import start_migration
from .instances import add_instance, get_instance, list_instances, remove_instance
from .ai_service import analyse_issue, public_settings as get_ai_settings
from .ai_service import hot_topics, recommend_plugins, save_ai_settings

_REGISTERED = False


def _default_log_filename() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"comfyui-loadhighlight-{stamp}.log"


def _safe_filename(name: str, fallback: str, default_ext: str = ".log") -> str:
    name = (name or "").strip() or fallback
    name = os.path.basename(name.replace("\\", "/"))
    if not name:
        name = fallback
    if "." not in name:
        name += default_ext
    return name


def _schedule_summary_at_terminal_end(instance) -> bool:
    """Print load summary when the web server starts — near the end of startup logs."""
    from .hook import print_final_summary_if_needed

    app = getattr(instance, "app", None)
    if app is None:
        return False
    if getattr(app, "_qbb_loadhighlight_summary_hook", False):
        REPORT.summary_scheduled = True
        return True

    async def _on_startup(_app):
        print_final_summary_if_needed()
        start_update_check_background(force=False)

    try:
        app.on_startup.append(_on_startup)
        app._qbb_loadhighlight_summary_hook = True
        REPORT.summary_scheduled = True
        print(
            "[QBB NodeOps] Summary will print at terminal end (server startup)",
            flush=True,
        )
        return True
    except Exception as e:
        print(f"[QBB NodeOps] Failed to schedule end summary: {e}", flush=True)
        return False


def register_routes() -> bool:
    global _REGISTERED
    if _REGISTERED:
        return True

    try:
        from server import PromptServer
    except Exception as e:
        print(f"[QBB NodeOps] PromptServer unavailable: {e}", flush=True)
        return False

    instance = getattr(PromptServer, "instance", None)
    if instance is None:
        print("[QBB NodeOps] PromptServer.instance is None", flush=True)
        return False

    routes = instance.routes

    @routes.get("/qbb_nodeops/report")
    async def qbb_loadhighlight_report(request):
        return web.json_response(REPORT.to_dict())

    @routes.get("/qbb_nodeops/failures")
    async def qbb_loadhighlight_failures(request):
        data = REPORT.to_dict()
        return web.json_response(
            {
                "failure_count": data["failure_count"],
                "partial_count": data["partial_count"],
                "attention_count": data["attention_count"],
                "failures": data["failures"],
                "partials": data["partials"],
                "attention": data["attention"],
                "hook_installed": data["hook_installed"],
            }
        )

    @routes.get("/qbb_nodeops/export_defaults")
    async def qbb_loadhighlight_export_defaults(request):
        kind = str(request.rel_url.query.get("kind") or "log").strip().lower()
        if kind in ("deps", "environment", "env", "requirements"):
            filename = default_deps_filename()
        else:
            filename = _default_log_filename()
        return web.json_response(
            {
                "filename": filename,
                "kind": kind,
            }
        )

    @routes.get("/qbb_nodeops/export")
    async def qbb_loadhighlight_export(request):
        """Browser download fallback for load log."""
        body = REPORT.to_log_text()
        filename = _safe_filename(str(request.rel_url.query.get("filename") or ""), _default_log_filename(), ".log")
        return web.Response(
            body=body.encode("utf-8"),
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    @routes.get("/qbb_nodeops/environment")
    async def qbb_loadhighlight_environment(request):
        """Return environment dependency report as plain text."""
        body = build_environment_report()
        filename = _safe_filename(str(request.rel_url.query.get("filename") or ""), default_deps_filename(), ".txt")
        return web.Response(
            body=body.encode("utf-8"),
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    @routes.get("/qbb_nodeops/updates")
    async def qbb_loadhighlight_updates(request):
        """Return latest custom-node update check report (may still be running)."""
        data = get_update_report().to_dict()
        if not data.get("network_mode"):
            data["network_mode"] = network_mode()
        return web.json_response(data)

    @routes.get("/qbb_nodeops/network_settings")
    async def qbb_loadhighlight_network_settings_get(request):
        s = load_settings()
        return web.json_response({**s, "mode": network_mode(s)})

    @routes.post("/qbb_nodeops/network_settings")
    async def qbb_loadhighlight_network_settings_post(request):
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求格式无效"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"ok": False, "error": "请求格式无效"}, status=400)
        try:
            saved = save_settings(data)
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
    async def qbb_loadhighlight_updates_refresh(request):
        """Re-run update check in background."""
        current = get_update_report()
        if current.status == "running":
            return web.json_response({"ok": True, "started": False, **current.to_dict()})
        start_update_check_background(force=True)
        return web.json_response({"ok": True, "started": True, **get_update_report().to_dict()})

    @routes.post("/qbb_nodeops/updates/pull")
    async def qbb_loadhighlight_updates_pull(request):
        """git pull one or more outdated custom nodes (ff-only)."""
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

    @routes.get("/qbb_nodeops/migration/scan")
    async def qbb_nodeops_migration_scan(request):
        """List migration candidates and source-environment details."""
        try:
            source_id = str(request.rel_url.query.get("source_instance_id") or "current")
            source = get_instance(source_id)
            return web.json_response(scan_migration_plugins(Path(source["comfyui_root"])))
        except ValueError as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    @routes.get("/qbb_nodeops/instances")
    async def qbb_nodeops_instances_get(request):
        return web.json_response({"instances": list_instances()})

    @routes.post("/qbb_nodeops/instances")
    async def qbb_nodeops_instances_post(request):
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("invalid request body")
            instance = add_instance(str(payload.get("path") or ""), str(payload.get("name") or ""))
            return web.json_response({"ok": True, "instance": instance, "instances": list_instances()})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    @routes.delete("/qbb_nodeops/instances/{instance_id}")
    async def qbb_nodeops_instances_delete(request):
        try:
            remove_instance(str(request.match_info.get("instance_id") or ""))
            return web.json_response({"ok": True, "instances": list_instances()})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    @routes.get("/qbb_nodeops/migration/status")
    async def qbb_nodeops_migration_status(request):
        return web.json_response(get_migration_status().to_dict())

    @routes.post("/qbb_nodeops/migration/start")
    async def qbb_nodeops_migration_start(request):
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid request body"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"ok": False, "error": "invalid request body"}, status=400)
        try:
            status = start_migration(payload)
        except (ValueError, RuntimeError) as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        return web.json_response({"ok": True, **status.to_dict()})

    @routes.get("/qbb_nodeops/ai/settings")
    async def qbb_nodeops_ai_settings_get(request):
        return web.json_response(get_ai_settings())

    @routes.post("/qbb_nodeops/ai/settings")
    async def qbb_nodeops_ai_settings_post(request):
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("invalid request body")
            return web.json_response({"ok": True, **save_ai_settings(payload)})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    @routes.post("/qbb_nodeops/ai/analyse")
    async def qbb_nodeops_ai_analyse(request):
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("issue"), dict):
                raise ValueError("invalid issue payload")
            language = str(payload.get("language") or "zh")
            result = await asyncio.to_thread(analyse_issue, payload["issue"], language)
            return web.json_response({"ok": True, **result})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    @routes.post("/qbb_nodeops/ai/recommend")
    async def qbb_nodeops_ai_recommend(request):
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("invalid request body")
            language = str(payload.get("language") or "zh")
            result = await asyncio.to_thread(recommend_plugins, payload.get("query"), language)
            return web.json_response({"ok": True, **result})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    @routes.post("/qbb_nodeops/ai/hot_topics")
    async def qbb_nodeops_ai_hot_topics(request):
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("invalid request body")
            language = str(payload.get("language") or "zh")
            days = int(payload.get("days") or 7)
            query = str(payload.get("query") or "")
            result = await asyncio.to_thread(hot_topics, days, language, query)
            return web.json_response({"ok": True, **result})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    _schedule_summary_at_terminal_end(instance)

    _REGISTERED = True
    print("[QBB NodeOps] API routes registered", flush=True)
    return True
