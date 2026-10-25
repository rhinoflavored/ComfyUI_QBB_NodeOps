from __future__ import annotations

import asyncio

from aiohttp import web

from ..ai_service import analyse_issue, public_settings as get_ai_settings
from ..ai_service import save_ai_settings
from .guards import require_local, write_forbidden


def register(routes, instance) -> None:
    @routes.post('/qbb_nodeops/communication-test/{kind}')
    async def communication_test(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        try:
            from ..communication_test import test_ai, test_network
            tests = {'ai':test_ai, 'network':test_network}
            kind = request.match_info['kind']
            payload = await request.json()
            if kind not in tests or not isinstance(payload, dict):
                raise ValueError('Invalid test request')
            return web.json_response(await asyncio.to_thread(tests[kind], payload))
        except Exception as exc:
            return web.json_response({'error':str(exc)}, status=400)

    @routes.get("/qbb_nodeops/preferences")
    @require_local
    async def preferences_get(request):
        from ..preferences import load
        return web.json_response(load())

    @routes.post("/qbb_nodeops/preferences")
    async def preferences_save(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        from ..preferences import save
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("Invalid preferences")
            return web.json_response(save(payload))
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    from .. import health
    instance.add_on_prompt_handler(health.observe)

    @routes.get("/qbb_nodeops/health")
    @require_local
    async def health_report(request):
        return web.json_response(await asyncio.to_thread(health.report))

    @routes.post("/qbb_nodeops/health/cleanup")
    async def health_cleanup(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        try:
            payload = await request.json()
            if payload.get("confirm") is not True:
                raise ValueError("Confirmation required")
            return web.json_response(await asyncio.to_thread(health.cleanup, payload.get("ids")))
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.get("/qbb_nodeops/ai/settings")
    @require_local
    async def qbb_nodeops_ai_settings_get(request):
        return web.json_response(get_ai_settings())

    @routes.post("/qbb_nodeops/ai/settings")
    async def qbb_nodeops_ai_settings_post(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("invalid request body")
            return web.json_response({"ok": True, **save_ai_settings(payload)})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    @routes.post("/qbb_nodeops/ai/analyse")
    async def qbb_nodeops_ai_analyse(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("issue"), dict):
                raise ValueError("invalid issue payload")
            language = str(payload.get("language") or "zh")
            result = await asyncio.to_thread(analyse_issue, payload["issue"], language)
            return web.json_response({"ok": True, **result})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
