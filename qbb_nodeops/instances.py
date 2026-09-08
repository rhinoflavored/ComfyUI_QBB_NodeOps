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


def _python_details(comfyui_root: Path, *, current: bool = False) -> tuple[str, str]:
    portable_root = comfyui_root.parent
    candidates: list[tuple[Path, str]] = [
        (portable_root / "python_embeded" / "python.exe", "Portable Python"),
        (portable_root / "python" / "python.exe", "Bundled Python"),
        (comfyui_root / ".venv" / "Scripts" / "python.exe", "Virtual environment"),
        (comfyui_root / "venv" / "Scripts" / "python.exe", "Virtual environment"),
        (comfyui_root.parent / ".venv" / "Scripts" / "python.exe", "Virtual environment"),
    ]
    if current:
        executable = Path(sys.executable).resolve()
        return str(executable), "Current Python"
    for executable, label in candidates:
        if executable.is_file():
            return str(executable), label
    return "", "External / standard Python"


def describe_instance(comfyui_root: Path, *, instance_id: str, name: str, current: bool = False) -> dict[str, Any]:
    root = _resolve_comfyui(comfyui_root)
    python_path, python_kind = _python_details(root, current=current)
    try:
        plugin_count = sum(1 for item in (root / "custom_nodes").iterdir() if item.is_dir() and not item.name.startswith("__"))
    except OSError:
        plugin_count = 0
    return {
        "id": instance_id,
        "name": name,
        "comfyui_root": str(root),
        "custom_nodes": str(root / "custom_nodes"),
        "python_path": python_path,
        "python_kind": python_kind,
        "portable": (root.parent / "python_embeded").is_dir(),
        "plugin_count": plugin_count,
        "current": current,
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
        changed = False
        valid: list[dict[str, str]] = []
        for entry in _load_saved():
            try:
                root = _resolve_comfyui(str(entry.get("comfyui_root") or ""))
                if root == CURRENT_COMFYUI_ROOT.resolve():
                    changed = True
                    continue
                instance_id = str(entry.get("id") or uuid.uuid4().hex)
                valid.append({"id": instance_id, "name": str(entry.get("name") or root.name), "comfyui_root": str(root)})
                result.append(describe_instance(root, instance_id=instance_id, name=valid[-1]["name"]))
            except Exception:
                changed = True
        if changed:
            _save(valid)
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
            return instance
    raise ValueError("instance not found")
