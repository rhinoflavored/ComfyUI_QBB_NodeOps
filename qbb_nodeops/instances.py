"""Registry and inspection helpers for multiple ComfyUI installations."""

from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CURRENT_COMFYUI_ROOT = PLUGIN_ROOT.parent.parent
REGISTRY_PATH = PLUGIN_ROOT / "qbb_nodeops_instances.json"
_LOCK = threading.RLock()


def _resolve_comfyui(raw: str | Path) -> Path:
    root = Path(raw).expanduser().resolve()
    for candidate in (root / "ComfyUI", root):
        if (candidate / "custom_nodes").is_dir():
            return candidate
    raise ValueError("path must be an existing ComfyUI folder containing custom_nodes")


def _contains(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def python_executable(home: Path) -> Path | None:
    for relative in ("python.exe", "Scripts/python.exe", "bin/python3", "bin/python", "python3", "python"):
        candidate = home / relative
        if candidate.is_file():
            return candidate
    return None


def _venv_home(executable: Path) -> Path | None:
    for directory in (executable.parent, executable.parent.parent):
        if (directory / "pyvenv.cfg").is_file():
            return directory
    return None


def inspect_runtime(comfyui_root: Path, *, current: bool = False) -> dict[str, Any]:
    """Describe how a ComfyUI tree is launched, without assuming a portable layout."""
    root = _resolve_comfyui(comfyui_root)
    parent = root.parent
    bundled = (
        (parent / "python_embeded", "Portable Python"),
        (parent / "python", "Bundled Python"),
    )
    virtual = (
        (parent / "python_env" / ".venv", "Independent Python environment"),
        (root / ".venv", "Virtual environment"),
        (root / "venv", "Virtual environment"),
        (parent / ".venv", "Virtual environment"),
        (parent / "venv", "Virtual environment"),
    )

    executable = Path(sys.executable).resolve() if current else None
    python_home: Path | None = None
    python_kind = "External / standard Python"
    if executable is None:
        for home, label in (*bundled, *virtual):
            found = python_executable(home)
            if found:
                executable = found.resolve()
                python_home = home.resolve()
                python_kind = label
                break
    else:
        python_kind = "Current Python"
        for home, label in (*bundled, *virtual):
            if home.is_dir() and _contains(executable, home):
                python_home = home.resolve()
                python_kind = label
                break
        if python_home is None:
            python_home = _venv_home(executable)
            if python_home is not None:
                python_kind = "Virtual environment"

    relocatable = bool(
        python_home
        and any(python_home == home.resolve() for home, _label in bundled if home.is_dir())
        and not (python_home / "pyvenv.cfg").is_file()
    )
    portable = relocatable
    separated = python_home == (parent / "python_env" / ".venv").resolve()
    install_root = parent if portable or separated else root
    return {
        "comfyui_root": str(root),
        "install_root": str(install_root),
        "python_path": str(executable) if executable else "",
        "python_kind": python_kind,
        "python_home": str(python_home) if python_home else "",
        "relocatable_python": relocatable,
        "portable": portable,
    }


def _python_details(comfyui_root: Path, *, current: bool = False) -> tuple[str, str]:
    runtime = inspect_runtime(comfyui_root, current=current)
    return runtime["python_path"], runtime["python_kind"]


def describe_instance(comfyui_root: Path, *, instance_id: str, name: str, current: bool = False) -> dict[str, Any]:
    root = _resolve_comfyui(comfyui_root)
    runtime = inspect_runtime(root, current=current)
    try:
        plugin_count = sum(1 for item in (root / "custom_nodes").iterdir() if (item.is_dir() or (item.is_file() and item.suffix == ".py")) and not item.name.startswith(("__", ".")))
    except OSError:
        plugin_count = 0
    return {
        "id": instance_id,
        "name": name,
        "comfyui_root": str(root),
        "custom_nodes": str(root / "custom_nodes"),
        "python_path": runtime["python_path"],
        "python_kind": runtime["python_kind"],
        "portable": runtime["portable"],
        "relocatable_python": runtime["relocatable_python"],
        "install_root": runtime["install_root"],
        "plugin_count": plugin_count,
        "current": current,
        "available": True,
    }


def _load_saved() -> list[dict[str, str]]:
    try:
        if REGISTRY_PATH.is_file():
            raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                return [item for item in raw if isinstance(item, dict)]
    except Exception as e:
        print(f"[QBB NodeOps] Failed to read instance registry: {e}", flush=True)
    return []


def _save(entries: list[dict[str, str]]) -> None:
    REGISTRY_PATH.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def list_instances() -> list[dict[str, Any]]:
    with _LOCK:
        current = describe_instance(CURRENT_COMFYUI_ROOT, instance_id="current", name="Current ComfyUI", current=True)
        result = [current]
        for entry in _load_saved():
            raw = str(entry.get("comfyui_root") or "")
            if not raw:
                continue
            instance_id = str(entry.get("id") or uuid.uuid5(uuid.NAMESPACE_URL, raw).hex)
            name = str(entry.get("name") or Path(raw).name)
            try:
                root = _resolve_comfyui(raw)
                if root == CURRENT_COMFYUI_ROOT.resolve():
                    continue
                result.append(describe_instance(root, instance_id=instance_id, name=name))
            except (OSError, ValueError):
                result.append({"id": instance_id, "name": name, "comfyui_root": raw,
                               "current": False, "available": False,
                               "unavailable_reason": "实例目录暂时无法访问，请检查磁盘连接、路径和访问权限。",
                               "python_path": "", "python_kind": "", "plugin_count": None})
        return result


def add_instance(raw_path: str, name: str = "") -> dict[str, Any]:
    root = _resolve_comfyui(raw_path)
    if root == CURRENT_COMFYUI_ROOT.resolve():
        return next(item for item in list_instances() if item["id"] == "current")
    with _LOCK:
        entries = _load_saved()
        for entry in entries:
            if Path(str(entry.get("comfyui_root") or "")).resolve() == root:
                return describe_instance(root, instance_id=str(entry["id"]), name=str(entry.get("name") or root.name))
        entry = {"id": uuid.uuid4().hex, "name": name.strip() or root.parent.name or root.name, "comfyui_root": str(root)}
        entries.append(entry)
        _save(entries)
        return describe_instance(root, instance_id=entry["id"], name=entry["name"])


def remove_instance(instance_id: str) -> None:
    if instance_id == "current":
        raise ValueError("the current ComfyUI instance cannot be removed")
    with _LOCK:
        entries = _load_saved()
        updated = [entry for entry in entries if str(entry.get("id")) != instance_id]
        if len(updated) == len(entries):
            raise ValueError("instance not found")
        _save(updated)


def get_instance(instance_id: str) -> dict[str, Any]:
    for instance in list_instances():
        if instance["id"] == instance_id:
            if not instance.get("available", True):
                raise ValueError(instance["unavailable_reason"])
            return instance
    raise ValueError("instance not found")
