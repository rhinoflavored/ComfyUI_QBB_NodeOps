from __future__ import annotations

import builtins
import inspect
import logging
import os
import sys
import time
import traceback
from typing import Any, Callable

from . import terminal
from .store import REPORT, LoadRecord, extract_partial_issues

PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_PATCHED = False
_IMPORT_HOOKED = False
_ORIGINAL_IMPORT = builtins.__import__
_PATCH_MARK = "_qbb_nodeops_patched"
_PATCH_ORIG = "_qbb_nodeops_original"
_LEGACY_PATCH_MARK = "_qbb_loadhighlight_patched"


def _is_patched(fn: Any) -> bool:
    return bool(getattr(fn, _PATCH_MARK, False) or getattr(fn, _LEGACY_PATCH_MARK, False))


def _display_name(module_path: str) -> str:
    base = os.path.basename(module_path.rstrip("\\/"))
    if os.path.isfile(module_path):
        base = os.path.splitext(base)[0]
    return base


def _is_self(module_path: str) -> bool:
    try:
        base = os.path.basename(module_path.rstrip("\\/"))
        if "QBB_NodeOps" in base or base.lower() == "nodeops":
            return True
        real_target = os.path.realpath(module_path)
        real_root = os.path.realpath(PLUGIN_ROOT)
        return real_target == real_root or real_target.startswith(real_root + os.sep)
    except Exception:
        return False


def _is_custom_nodes_target(module_path: str, module_parent: str) -> bool:
    if module_parent == "custom_nodes":
        return True
    norm = module_path.replace("\\", "/").lower()
    return "/custom_nodes/" in norm or norm.endswith("/custom_nodes")


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        # INFO too: many custom nodes log partial failures at info/warning
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            if record.exc_info:
                msg = f"{msg}\n{logging.Formatter().formatException(record.exc_info)}"
            self.lines.append(msg)
        except Exception:
            pass


class _TeeStream:
    def __init__(self, original: Any) -> None:
        self.original = original
        self.chunks: list[str] = []

    def write(self, data: str) -> int:
        if data:
            self.chunks.append(data)
        return self.original.write(data)

    def flush(self) -> None:
        return self.original.flush()

    def __getattr__(self, item: str) -> Any:
        return getattr(self.original, item)

    def getvalue(self) -> str:
        return "".join(self.chunks)


def _extract_reason(captured: list[str], fallback: str = "") -> tuple[str, str]:
    text = "\n".join(captured).strip()
    if not text and fallback:
        return fallback, fallback

    reason = fallback
    for line in reversed(captured):
        if "Cannot import" in line and "custom nodes:" in line:
            reason = line.split("custom nodes:", 1)[-1].strip()
            break
        if "IMPORT FAILED:" in line:
            reason = line.split(":", 1)[-1].strip()
            break

    tb = text
    if "Traceback (most recent call last):" in text:
        idx = text.rfind("Traceback (most recent call last):")
        tb = text[idx:]
    return reason or "IMPORT FAILED", tb or reason or "IMPORT FAILED"


def _record_result(
    *,
    name: str,
    module_path: str,
    module_parent: str,
    success: bool,
    elapsed: float,
    captured_text: str,
    err_reason: str = "",
    err_tb: str = "",
) -> None:
    captured_log = (captured_text or "").strip()

    if not success:
        terminal.print_failure(name, elapsed, err_reason, err_tb)
        REPORT.add(
            LoadRecord(
                name=name,
                path=module_path,
                success=False,
                elapsed_sec=elapsed,
                reason=err_reason,
                traceback=err_tb,
                module_parent=module_parent,
                status="failed",
                captured_log=captured_log,
            )
        )
        return

    issues = extract_partial_issues(captured_text)
    if issues:
        reason = f"{len(issues)} internal node/module issue(s) while package still loaded"
        terminal.print_partial(name, elapsed, issues)
        REPORT.add(
            LoadRecord(
                name=name,
                path=module_path,
                success=True,
                elapsed_sec=elapsed,
                reason=reason,
                traceback="\n\n".join(issues),
                module_parent=module_parent,
                status="partial",
                partial_issues=issues,
                captured_log=captured_log,
            )
        )
        return

    terminal.print_success(name, elapsed)
    REPORT.add(
        LoadRecord(
            name=name,
            path=module_path,
            success=True,
            elapsed_sec=elapsed,
            module_parent=module_parent,
            status="ok",
            captured_log=captured_log,
        )
    )


def _run_tracked(original: Callable[..., Any], is_coro: bool, module_path: str, ignore, module_parent: str, args, kwargs):
    """Shared load tracking path for sync/async wrappers."""

    async def _async_body():
        track = _is_custom_nodes_target(module_path, module_parent) and not _is_self(module_path)
        name = _display_name(module_path)

        if track:
            terminal.print_plugin_header(name, module_path)

        handler = _CaptureHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        root_logger = logging.getLogger()
        tee_out = _TeeStream(sys.stdout)
        tee_err = _TeeStream(sys.stderr)
        t0 = time.perf_counter()
        success = False
        err_reason = ""
        err_tb = ""

        old_out, old_err = sys.stdout, sys.stderr
        if track:
            root_logger.addHandler(handler)
            sys.stdout, sys.stderr = tee_out, tee_err
        try:
            if is_coro:
                success = await original(module_path, ignore, module_parent, *args, **kwargs)
            else:
                success = original(module_path, ignore, module_parent, *args, **kwargs)
            if track and not success:
                err_reason, err_tb = _extract_reason(handler.lines, "IMPORT FAILED")
        except Exception as e:
            success = False
            err_reason = f"{type(e).__name__}: {e}"
            err_tb = traceback.format_exc()
            if track:
                captured = "\n".join(handler.lines) + "\n" + tee_out.getvalue() + "\n" + tee_err.getvalue()
                _record_result(
                    name=name,
                    module_path=module_path,
                    module_parent=module_parent,
                    success=False,
                    elapsed=time.perf_counter() - t0,
                    captured_text=captured,
                    err_reason=err_reason,
                    err_tb=err_tb,
                )
            raise
        finally:
            if track:
                sys.stdout, sys.stderr = old_out, old_err
                root_logger.removeHandler(handler)

        if track:
            captured = "\n".join(handler.lines) + "\n" + tee_out.getvalue() + "\n" + tee_err.getvalue()
            _record_result(
                name=name,
                module_path=module_path,
                module_parent=module_parent,
                success=bool(success),
                elapsed=time.perf_counter() - t0,
                captured_text=captured,
                err_reason=err_reason,
                err_tb=err_tb,
            )
        return success

    return _async_body


def _wrap_load_custom_node(original: Callable[..., Any]) -> Callable[..., Any]:
    is_coro = inspect.iscoroutinefunction(original)

    async def async_wrapper(module_path: str, ignore=None, module_parent: str = "custom_nodes", *args, **kwargs):
        if ignore is None:
            ignore = set()
        body = _run_tracked(original, True, module_path, ignore, module_parent, args, kwargs)
        return await body()

    def sync_wrapper(module_path: str, ignore=None, module_parent: str = "custom_nodes", *args, **kwargs):
        if ignore is None:
            ignore = set()

        # Run the async body via a tiny sync bridge without requiring an event loop API for sync originals
        track = _is_custom_nodes_target(module_path, module_parent) and not _is_self(module_path)
        name = _display_name(module_path)

        if track:
            terminal.print_plugin_header(name, module_path)

        handler = _CaptureHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        root_logger = logging.getLogger()
        tee_out = _TeeStream(sys.stdout)
        tee_err = _TeeStream(sys.stderr)
        t0 = time.perf_counter()
        success = False
        err_reason = ""
        err_tb = ""

        old_out, old_err = sys.stdout, sys.stderr
        if track:
            root_logger.addHandler(handler)
            sys.stdout, sys.stderr = tee_out, tee_err
        try:
            success = original(module_path, ignore, module_parent, *args, **kwargs)
            if track and not success:
                err_reason, err_tb = _extract_reason(handler.lines, "IMPORT FAILED")
        except Exception as e:
            success = False
            err_reason = f"{type(e).__name__}: {e}"
            err_tb = traceback.format_exc()
            if track:
                captured = "\n".join(handler.lines) + "\n" + tee_out.getvalue() + "\n" + tee_err.getvalue()
                _record_result(
                    name=name,
                    module_path=module_path,
                    module_parent=module_parent,
                    success=False,
                    elapsed=time.perf_counter() - t0,
                    captured_text=captured,
                    err_reason=err_reason,
                    err_tb=err_tb,
                )
            raise
        finally:
            if track:
                sys.stdout, sys.stderr = old_out, old_err
                root_logger.removeHandler(handler)

        if track:
            captured = "\n".join(handler.lines) + "\n" + tee_out.getvalue() + "\n" + tee_err.getvalue()
            _record_result(
                name=name,
                module_path=module_path,
                module_parent=module_parent,
                success=bool(success),
                elapsed=time.perf_counter() - t0,
                captured_text=captured,
                err_reason=err_reason,
                err_tb=err_tb,
            )
        return success

    return async_wrapper if is_coro else sync_wrapper


def _wrap_init_external(original: Callable[..., Any]) -> Callable[..., Any]:
    # Do not print summary here — that lands in the middle of startup logs.
    # Summary is deferred to PromptServer app on_startup (terminal end).
    return original


def _wrap_init_extra_nodes(original: Callable[..., Any]) -> Callable[..., Any]:
    """Fallback: print summary after all node init if server-end hook was not scheduled."""
    is_coro = inspect.iscoroutinefunction(original)

    async def async_wrapper(*args, **kwargs):
        result = await original(*args, **kwargs)
        if not REPORT.summary_scheduled:
            print_final_summary_if_needed()
        return result

    def sync_wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        if not REPORT.summary_scheduled:
            print_final_summary_if_needed()
        return result

    return async_wrapper if is_coro else sync_wrapper


def patch_nodes_module(nodes_module: Any) -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    if not hasattr(nodes_module, "load_custom_node"):
        return False

    original = nodes_module.load_custom_node
    if _is_patched(original):
        _PATCHED = True
        REPORT.hook_installed = True
        return True

    wrapped = _wrap_load_custom_node(original)
    setattr(wrapped, _PATCH_MARK, True)
    setattr(wrapped, _PATCH_ORIG, original)
    nodes_module.load_custom_node = wrapped

    init_ext = getattr(nodes_module, "init_external_custom_nodes", None)
    if callable(init_ext) and not _is_patched(init_ext):
        # Keep identity wrap marker only; summary is deferred.
        setattr(init_ext, _PATCH_MARK, True)

    init_extra = getattr(nodes_module, "init_extra_nodes", None)
    if callable(init_extra) and not _is_patched(init_extra):
        wrapped_extra = _wrap_init_extra_nodes(init_extra)
        setattr(wrapped_extra, _PATCH_MARK, True)
        nodes_module.init_extra_nodes = wrapped_extra

    _PATCHED = True
    REPORT.hook_installed = True
    print(
        f"[QBB NodeOps] Hooked nodes.load_custom_node "
        f"({'async' if inspect.iscoroutinefunction(original) else 'sync'})",
        flush=True,
    )
    return True


def _try_patch_existing_nodes() -> bool:
    nodes_module = sys.modules.get("nodes")
    if nodes_module is None:
        return False
    return patch_nodes_module(nodes_module)


def _import_hook(name, globals=None, locals=None, fromlist=(), level=0):
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "nodes" or getattr(module, "__name__", None) == "nodes":
            patch_nodes_module(module)
        elif fromlist and name == "nodes":
            patch_nodes_module(module)
    except Exception as e:
        print(f"[QBB NodeOps] Failed to patch nodes: {e}", flush=True)
    return module


def install() -> None:
    """Install early hook. Safe to call multiple times."""
    global _IMPORT_HOOKED
    terminal.enable_windows_ansi()

    if _try_patch_existing_nodes():
        return

    if not _IMPORT_HOOKED:
        builtins.__import__ = _import_hook
        _IMPORT_HOOKED = True
        print(
            "[QBB NodeOps] Import hook installed "
            "(will patch nodes.load_custom_node when nodes is imported)",
            flush=True,
        )


def print_final_summary_if_needed() -> None:
    if REPORT.summary_printed or not REPORT.records:
        return
    total = sum(r.elapsed_sec for r in REPORT.records)
    terminal.print_summary(
        len(REPORT.successes),
        len(REPORT.failures),
        len(REPORT.partials),
        total,
    )
    REPORT.summary_printed = True
