"""HTTP API for QBB NodeOps. Handlers live in feature modules under this package."""

from __future__ import annotations

from ..store import REPORT
from ..update_check import start_update_check_background

_REGISTERED = False


def _schedule_summary_at_terminal_end(instance) -> bool:
    """Print load summary when the web server starts — near the end of startup logs."""
    from ..hook import print_final_summary_if_needed

    app = getattr(instance, "app", None)
    if app is None:
        return False
    if getattr(app, "_qbb_nodeops_summary_hook", False) or getattr(app, "_qbb_loadhighlight_summary_hook", False):
        REPORT.summary_scheduled = True
        return True

    async def _on_startup(_app):
        import asyncio
        from .current_restore import reconnect
        asyncio.create_task(reconnect(instance))
        from .. import health
        import time
        health.READY = time.time()
        from ..checkpoint_policy import start
        start(lambda: instance.prompt_queue.get_tasks_remaining()==0)
        print_final_summary_if_needed()
        start_update_check_background(force=False)
        from ..runtime_catalog import warm_catalog
        from threading import Thread
        Thread(target=warm_catalog, name="qbb-runtime-preload", daemon=True).start()
        def check_instances():
            from ..instances import list_instances
            try:
                list_instances()
            except Exception as exc:
                print(f"[QBB NodeOps] Instance check failed: {exc}", flush=True)
        Thread(target=check_instances, name="qbb-instance-check", daemon=True).start()

    try:
        app.on_startup.append(_on_startup)
        async def stop_checkpoints(_app):
            from ..checkpoint_policy import STOP
            STOP.set()
        app.on_cleanup.append(stop_checkpoints)
        app._qbb_nodeops_summary_hook = True
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

    from .guards import install_request_guard

    if not install_request_guard(instance):
        print("[QBB NodeOps] App-wide request guard unavailable; route guards still apply", flush=True)

    routes = instance.routes
    from . import report, updates, market, migrate, records, ops, current_restore

    current_restore.install_maintenance_guard(instance)
    current_restore.register(routes, instance)

    records.register(routes)
    migrate.register(routes)
    ops.register(routes, instance)
    market.register(routes)
    report.register(routes)
    updates.register(routes)

    _schedule_summary_at_terminal_end(instance)

    _REGISTERED = True
    print("[QBB NodeOps] API routes registered", flush=True)
    return True
