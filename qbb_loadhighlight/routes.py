from __future__ import annotations

import os
from datetime import datetime

from aiohttp import web

from .env_export import build_environment_report, default_deps_filename
from .store import REPORT

_REGISTERED = False


def _default_export_dir() -> str:
    home = os.path.expanduser("~")
    for name in ("Desktop", "桌面"):
        path = os.path.join(home, name)
        if os.path.isdir(path):
            return path
    return home


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


def _write_text_file(directory: str, filename: str, content: str) -> tuple[bool, str]:
    directory = os.path.abspath(os.path.expanduser(directory))
    try:
        os.makedirs(directory, exist_ok=True)
    except Exception as e:
        return False, f"无法创建目录: {e}"

    filepath = os.path.join(directory, filename)
    try:
        with open(filepath, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    except Exception as e:
        return False, f"写入失败: {e}"
    return True, filepath


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

    try:
        app.on_startup.append(_on_startup)
        app._qbb_loadhighlight_summary_hook = True
        REPORT.summary_scheduled = True
        print(
            "[ComfyUI_QBB_LoadHighlight] Summary will print at terminal end (server startup)",
            flush=True,
        )
        return True
    except Exception as e:
        print(f"[ComfyUI_QBB_LoadHighlight] Failed to schedule end summary: {e}", flush=True)
        return False


def register_routes() -> bool:
    global _REGISTERED
    if _REGISTERED:
        return True

    try:
        from server import PromptServer
    except Exception as e:
        print(f"[ComfyUI_QBB_LoadHighlight] PromptServer unavailable: {e}", flush=True)
        return False

    instance = getattr(PromptServer, "instance", None)
    if instance is None:
        print("[ComfyUI_QBB_LoadHighlight] PromptServer.instance is None", flush=True)
        return False

    routes = instance.routes

    @routes.get("/qbb_loadhighlight/report")
    async def qbb_loadhighlight_report(request):
        return web.json_response(REPORT.to_dict())

    @routes.get("/qbb_loadhighlight/failures")
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

    @routes.get("/qbb_loadhighlight/export_defaults")
    async def qbb_loadhighlight_export_defaults(request):
        kind = str(request.rel_url.query.get("kind") or "log").strip().lower()
        if kind in ("deps", "environment", "env", "requirements"):
            filename = default_deps_filename()
        else:
            filename = _default_log_filename()
        return web.json_response(
            {
                "directory": _default_export_dir(),
                "filename": filename,
                "kind": kind,
            }
        )

    @routes.get("/qbb_loadhighlight/export")
    async def qbb_loadhighlight_export(request):
        """Browser download fallback for load log."""
        body = REPORT.to_log_text()
        filename = _default_log_filename()
        return web.Response(
            body=body.encode("utf-8"),
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    @routes.post("/qbb_loadhighlight/export_to_path")
    async def qbb_loadhighlight_export_to_path(request):
        """Write load log to a user-specified directory + filename."""
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求格式无效"}, status=400)

        directory = str(data.get("directory") or "").strip()
        filename = _safe_filename(str(data.get("filename") or ""), _default_log_filename(), ".log")
        if not directory:
            return web.json_response({"ok": False, "error": "请填写导出目录"}, status=400)

        ok, result = _write_text_file(directory, filename, REPORT.to_log_text())
        if not ok:
            return web.json_response({"ok": False, "error": result}, status=500)
        return web.json_response({"ok": True, "path": result})

    @routes.get("/qbb_loadhighlight/environment")
    async def qbb_loadhighlight_environment(request):
        """Return environment dependency report as plain text."""
        body = build_environment_report()
        return web.Response(
            body=body.encode("utf-8"),
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Content-Disposition": f'attachment; filename="{default_deps_filename()}"',
                "Cache-Control": "no-store",
            },
        )

    @routes.post("/qbb_loadhighlight/export_environment_to_path")
    async def qbb_loadhighlight_export_environment_to_path(request):
        """Write full environment/pip dependency report to disk."""
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "请求格式无效"}, status=400)

        directory = str(data.get("directory") or "").strip()
        filename = _safe_filename(str(data.get("filename") or ""), default_deps_filename(), ".txt")
        if not directory:
            return web.json_response({"ok": False, "error": "请填写导出目录"}, status=400)

        try:
            content = build_environment_report()
        except Exception as e:
            return web.json_response({"ok": False, "error": f"收集依赖失败: {e}"}, status=500)

        ok, result = _write_text_file(directory, filename, content)
        if not ok:
            return web.json_response({"ok": False, "error": result}, status=500)
        return web.json_response({"ok": True, "path": result})

    _schedule_summary_at_terminal_end(instance)

    _REGISTERED = True
    print("[ComfyUI_QBB_LoadHighlight] API routes registered", flush=True)
    return True
