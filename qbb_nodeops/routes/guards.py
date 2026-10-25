from __future__ import annotations

from urllib.parse import urlsplit

from aiohttp import web

from ..folder_picker import is_loopback

WRITE_DENIED = "Local same-origin request required"
APP_HEADER = "X-QBB-NodeOps"


def _hostname(value: str) -> str:
    text = (value or "").strip()
    if not text or text.lower() == "null":
        return ""
    if "://" not in text:
        text = "http://" + text
    return (urlsplit(text).hostname or "").strip().lower()


def _netloc(value: str) -> str:
    text = (value or "").strip()
    if not text or text.lower() == "null":
        return ""
    if "://" in text:
        return (urlsplit(text).netloc or "").strip()
    return text


def _site(request) -> str:
    origin = (request.headers.get("Origin") or "").strip()
    if origin and origin.lower() != "null":
        return _netloc(origin)
    referer = (request.headers.get("Referer") or "").strip()
    if referer:
        return _netloc(referer)
    return ""


def local_forbidden(request, *, json_body: bool = False, error: str = WRITE_DENIED):
    """Reject requests that are not from the local ComfyUI page.

    Returns a 403 response, or None when the request may proceed.
    """
    if not is_loopback(request.remote):
        return web.json_response({"ok": False, "error": error}, status=403)
    host = (request.host or "").strip()
    if not host or not is_loopback(_hostname(host)):
        return web.json_response({"ok": False, "error": error}, status=403)
    site = _site(request)
    if site:
        if not is_loopback(_hostname(site)) or site.lower() != host.lower():
            return web.json_response({"ok": False, "error": error}, status=403)
    elif (request.headers.get(APP_HEADER) or "").strip() != "1":
        return web.json_response({"ok": False, "error": error}, status=403)
    if json_body:
        ctype = (request.content_type or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            return web.json_response({"ok": False, "error": error}, status=403)
    return None


def write_forbidden(request, *, json_body: bool = True, error: str = WRITE_DENIED):
    return local_forbidden(request, json_body=json_body, error=error)


def require_local(handler=None, *, json_body: bool = False):
    def deco(fn):
        async def wrapped(request):
            denied = local_forbidden(request, json_body=json_body)
            if denied:
                return denied
            return await fn(request)

        wrapped.__name__ = getattr(fn, "__name__", "handler")
        wrapped.__qualname__ = getattr(fn, "__qualname__", wrapped.__name__)
        return wrapped

    if handler is not None:
        return deco(handler)
    return deco


def install_request_guard(instance) -> bool:
    app = getattr(instance, "app", None)
    if app is None:
        return False
    if getattr(app, "_qbb_nodeops_guard", False):
        return True

    @web.middleware
    async def nodeops_local_guard(request, handler):
        path = request.path or ""
        if not path.startswith("/qbb_nodeops"):
            return await handler(request)
        method = (request.method or "").upper()
        json_body = method in ("POST", "PUT", "PATCH")
        denied = local_forbidden(request, json_body=json_body)
        if denied:
            return denied
        return await handler(request)

    try:
        app.middlewares.append(nodeops_local_guard)
    except Exception:
        return False
    app._qbb_nodeops_guard = True
    return True
