"""Persistent network settings for update checks (local VPN proxy / China GitHub mirror)."""

from __future__ import annotations

import json
import os
import threading
from typing import Any

PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SETTINGS_PATH = os.path.join(PLUGIN_ROOT, "qbb_nodeops_network_settings.json")

# Default China-friendly GitHub proxy prefix (used when no local VPN proxy).
DEFAULT_MIRROR_PREFIX = "https://ghfast.top/"

_DEFAULTS: dict[str, Any] = {
    "proxy_enabled": False,
    "proxy_host": "127.0.0.1",
    "proxy_port": 7890,
    # Do not force China mirror on first launch; remember whatever the user last saved.
    "mirror_enabled": False,
    "mirror_prefix": DEFAULT_MIRROR_PREFIX,
}

_LOCK = threading.Lock()
_CACHE: dict[str, Any] | None = None


def _clamp_port(value: Any) -> int:
    try:
        port = int(value)
    except Exception:
        port = 7890
    if port < 1 or port > 65535:
        port = 7890
    return port


def _normalize(data: dict[str, Any] | None) -> dict[str, Any]:
    src = dict(_DEFAULTS)
    if isinstance(data, dict):
        src.update({k: data[k] for k in _DEFAULTS if k in data})
    src["proxy_enabled"] = bool(src.get("proxy_enabled"))
    src["mirror_enabled"] = bool(src.get("mirror_enabled"))
    # Proxy and mirror are mutually exclusive network routes. Prefer the
    # proxy setting when reading legacy settings that have both set.
    if src["proxy_enabled"]:
        src["mirror_enabled"] = False
    src["proxy_host"] = str(src.get("proxy_host") or "127.0.0.1").strip() or "127.0.0.1"
    src["proxy_port"] = _clamp_port(src.get("proxy_port"))
    prefix = str(src.get("mirror_prefix") or DEFAULT_MIRROR_PREFIX).strip() or DEFAULT_MIRROR_PREFIX
    if not prefix.endswith("/"):
        prefix += "/"
    src["mirror_prefix"] = prefix
    return src


def load_settings() -> dict[str, Any]:
    global _CACHE
    with _LOCK:
        if _CACHE is not None:
            return dict(_CACHE)
        data = None
        try:
            if os.path.isfile(SETTINGS_PATH):
                with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
        except Exception as e:
            print(f"[ComfyUI_QBB_NodeOps] Failed to read network settings: {e}", flush=True)
        _CACHE = _normalize(data if isinstance(data, dict) else None)
        return dict(_CACHE)


def save_settings(patch: dict[str, Any] | None) -> dict[str, Any]:
    global _CACHE
    with _LOCK:
        current = dict(_CACHE) if _CACHE is not None else _normalize(None)
        if isinstance(patch, dict):
            for key in _DEFAULTS:
                if key in patch:
                    current[key] = patch[key]
        current = _normalize(current)
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8", newline="\n") as f:
                json.dump(current, f, ensure_ascii=False, indent=2)
                f.write("\n")
        except Exception as e:
            print(f"[ComfyUI_QBB_NodeOps] Failed to save network settings: {e}", flush=True)
            raise
        _CACHE = current
        return dict(current)


def proxy_url(settings: dict[str, Any] | None = None) -> str | None:
    s = settings or load_settings()
    if not s.get("proxy_enabled"):
        return None
    host = str(s.get("proxy_host") or "").strip()
    port = _clamp_port(s.get("proxy_port"))
    if not host:
        return None
    return f"http://{host}:{port}"


def apply_comfyui_process_proxy(settings: dict[str, Any] | None = None) -> bool:
    """Apply the saved local proxy to this ComfyUI process and its children.

    This deliberately only handles the proxy route. A GitHub mirror is a
    Git-specific URL rewrite and must not be imposed on model downloads or
    arbitrary ComfyUI HTTP requests.
    """
    url = proxy_url(settings)
    if not url:
        # Only remove variables previously set by this plugin; never disturb a
        # user or launcher supplied process proxy.
        if os.environ.pop("QBB_NODEOPS_PROCESS_PROXY", None) == "1":
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                os.environ.pop(key, None)
        return False

    # Different Python libraries and subprocesses honor different spellings.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ[key] = url
    os.environ["QBB_NODEOPS_PROCESS_PROXY"] = "1"
    os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")
    os.environ.setdefault("no_proxy", os.environ["NO_PROXY"])
    return True


def network_mode(settings: dict[str, Any] | None = None) -> str:
    """Return 'proxy' | 'mirror' | 'direct'."""
    s = settings or load_settings()
    if proxy_url(s):
        return "proxy"
    if s.get("mirror_enabled"):
        return "mirror"
    return "direct"
