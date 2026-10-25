from __future__ import annotations

from aiohttp import web

from ..env_export import build_environment_report, default_deps_filename
from ..store import REPORT
from .files import default_log_filename, safe_filename
from .guards import require_local


def register(routes) -> None:
    @routes.get("/qbb_nodeops/report")
    @require_local
    async def report(request):
        return web.json_response(REPORT.to_dict())

    @routes.get("/qbb_nodeops/failures")
    @require_local
    async def failures(request):
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
    @require_local
    async def export_defaults(request):
        kind = str(request.rel_url.query.get("kind") or "log").strip().lower()
        if kind in ("deps", "environment", "env", "requirements"):
            filename = default_deps_filename()
        else:
            filename = default_log_filename()
        return web.json_response(
            {
                "filename": filename,
                "kind": kind,
            }
        )

    @routes.get("/qbb_nodeops/export")
    @require_local
    async def export_log(request):
        """Browser download fallback for load log."""
        body = REPORT.to_log_text()
        filename = safe_filename(str(request.rel_url.query.get("filename") or ""), default_log_filename(), ".log")
        return web.Response(
            body=body.encode("utf-8"),
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    @routes.get("/qbb_nodeops/environment")
    @require_local
    async def environment(request):
        """Return environment dependency report as plain text."""
        body = build_environment_report()
        filename = safe_filename(str(request.rel_url.query.get("filename") or ""), default_deps_filename(), ".txt")
        return web.Response(
            body=body.encode("utf-8"),
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )
