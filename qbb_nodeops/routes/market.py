from __future__ import annotations

import asyncio

from aiohttp import web

from ..market import search as market_search, install as market_install
from ..market import build_index
from .. import market_index
from .guards import require_local, write_forbidden


def register(routes) -> None:
    @routes.get('/qbb_nodeops/market/cache-status')
    @require_local
    async def market_cache_status(request):
        return web.json_response({'revision': await asyncio.to_thread(market_index.cache_revision)})

    @routes.post("/qbb_nodeops/market/metadata")
    async def market_metadata(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        from ..market import repository_url
        try:
            payload = await request.json()
            urls = [repository_url(u) for u in payload.get("urls", [])[:30]]
            return web.json_response(await asyncio.to_thread(market_index.refresh_metadata, urls))
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.get("/qbb_nodeops/market/index")
    @require_local
    async def market_index_status(request):
        return web.json_response(await asyncio.to_thread(market_index.status))

    @routes.post("/qbb_nodeops/market/index")
    async def market_index_start(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        try:
            payload = await request.json()
            result = await asyncio.to_thread(market_index.stop if payload.get("stop") else build_index)
            return web.json_response(result)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.get("/qbb_nodeops/market")
    @require_local
    async def market_catalogue(request):
        try:
            result = await asyncio.to_thread(
                market_search,
                request.query.get("q", ""),
                request.query.get("refresh") == "1",
            )
            return web.json_response(result)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    @routes.post("/qbb_nodeops/market/install")
    async def market_download(request):
        denied = write_forbidden(request)
        if denied:
            return denied
        try:
            payload = await request.json()
            result = await asyncio.to_thread(market_install, payload.get("url", ""))
            return web.json_response(result)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
