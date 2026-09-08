"""Safe custom-node migration to a new portable ComfyUI environment.

The migration intentionally *copies* instead of moving.  The original ComfyUI
installation and its models remain untouched, while the target receives a
fresh portable Python copy plus only the custom nodes the user selected.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .instances import get_instance

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CUSTOM_NODES_ROOT = PLUGIN_ROOT.parent
COMFYUI_ROOT = CUSTOM_NODES_ROOT.parent
PORTABLE_ROOT = COMFYUI_ROOT.parent
PYTHON_ROOT = Path(sys.executable).resolve().parent

_LOCK = threading.Lock()
_WORKER: threading.Thread | None = None


@dataclass
class MigrationPlugin:
    name: str
    path: str
    git: bool
    remote_url: str = ""
    commit: str = ""
    selected_default: bool = True


@dataclass
class MigrationStatus:
    status: str = "idle"  # idle | running | success | error
    phase: str = ""
    destination_root: str = ""
    mode: str = "new"
    delete_source: bool = False
    source_root: str = ""
    total: int = 0
    completed: int = 0
    current: str = ""
    logs: list[str] = field(default_factory=list)
    error: str = ""
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


STATUS = MigrationStatus()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(message: str) -> None:
    with _LOCK:
        STATUS.logs.append(message)
        STATUS.logs = STATUS.logs[-200:]
    print(f"[QBB NodeOps Migration] {message}", flush=True)


def _run_git(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=120,
            check=False,
        )
        output = (proc.stdout or proc.stderr or "").strip()
        return proc.returncode, output
    except Exception as e:
        return 1, str(e)


def _git_value(path: Path, args: list[str]) -> str:
    code, out = _run_git(args, path)
    return out if code == 0 else ""


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def scan_plugins(source_comfyui: Path | None = None) -> dict[str, Any]:
    """List all direct custom_nodes directories without changing anything."""
    source_comfyui = (source_comfyui or COMFYUI_ROOT).resolve()
    source_nodes = source_comfyui / "custom_nodes"
    plugins: list[MigrationPlugin] = []
    if source_nodes.is_dir():
        for path in sorted(source_nodes.iterdir(), key=lambda p: p.name.lower()):
            if not path.is_dir() or path.name.startswith("__"):
                continue
            # The currently running QBB plugin is selectable; it is useful in
            # a migration target and remains safe because the source is copied.
            git = (path / ".git").exists()
            remote = _git_value(path, ["remote", "get-url", "origin"]) if git else ""
            commit = _git_value(path, ["rev-parse", "HEAD"]) if git else ""
            plugins.append(
                MigrationPlugin(
                    name=path.name,
                    path=str(path),
                    git=git,
                    remote_url=remote,
                    commit=commit,
                )
            )

    portable_root = source_comfyui.parent
    portable = portable_root if (portable_root / "python_embeded").is_dir() else None
    return {
        "source_comfyui": str(source_comfyui),
        "source_portable": str(portable or source_comfyui),
        "source_python": str(PYTHON_ROOT),
        "portable_layout": portable is not None,
        "plugins": [asdict(p) for p in plugins],
        "model_root": str(source_comfyui / "models"),
        "status": get_status().to_dict(),
    }


def get_status() -> MigrationStatus:
    with _LOCK:
        return MigrationStatus(**asdict(STATUS))


def _safe_destination(raw: str) -> Path:
    destination = Path(raw).expanduser().resolve()
    if not raw.strip():
        raise ValueError("destination_root is required")
    if destination == PORTABLE_ROOT.resolve() or _is_inside(destination, PORTABLE_ROOT):
        raise ValueError("destination must not be inside the current ComfyUI installation")
    if _is_inside(PORTABLE_ROOT, destination):
        raise ValueError("destination must not contain the current ComfyUI installation")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("destination folder must be empty or not exist")
    return destination


def _existing_comfyui_target(raw: str) -> Path:
    """Accept either a portable root or its ComfyUI subdirectory."""
    raw = str(raw or "").strip()
    if not raw:
        raise ValueError("destination_root is required")
    root = Path(raw).expanduser().resolve()
    candidates = [root / "ComfyUI", root]
    target = next((path for path in candidates if (path / "custom_nodes").is_dir()), None)
    if target is None:
        raise ValueError("destination must be an existing ComfyUI folder with custom_nodes")
    if target == COMFYUI_ROOT.resolve() or _is_inside(target, COMFYUI_ROOT):
        raise ValueError("destination must not be the current ComfyUI installation")
    return target


def _resolve_selected(selected: list[str], source_comfyui: Path) -> list[MigrationPlugin]:
    discovered = {p["path"]: p for p in scan_plugins(source_comfyui)["plugins"]}
    output: list[MigrationPlugin] = []
    for raw in selected:
        try:
            path = Path(str(raw)).resolve()
        except Exception:
            continue
        info = discovered.get(str(path))
        if not info:
            continue
        output.append(MigrationPlugin(**info))
    if not output:
        raise ValueError("select at least one custom node")
    return output


def _copytree(source: Path, destination: Path, *, ignore: Any = None) -> None:
    shutil.copytree(source, destination, symlinks=True, ignore=ignore, dirs_exist_ok=False)


def _write_model_config(target_comfyui: Path) -> Path:
    """Append a safe model-root reference to the target's extra model config."""
    config = target_comfyui / "extra_model_paths.yaml"
    source = str(COMFYUI_ROOT).replace("\\", "/").replace("'", "''")
    suffix = (
        "\n# Added by QBB NodeOps migration. Models stay in the original installation.\n"
        "qbb_shared_models:\n"
        f"  base_path: '{source}'\n"
        "  checkpoints: models/checkpoints\n"
        "  text_encoders: models/text_encoders\n"
        "  vae: models/vae\n"
        "  loras: models/loras\n"
        "  controlnet: models/controlnet\n"
        "  embeddings: models/embeddings\n"
        "  upscale_models: models/upscale_models\n"
        "  diffusion_models: models/diffusion_models\n"
        "  clip_vision: models/clip_vision\n"
    )
    with open(config, "a", encoding="utf-8", newline="\n") as f:
        f.write(suffix)
    return config


def _copy_launchers(destination: Path) -> list[str]:
    """Keep the standard portable launch scripts with the new environment."""
    copied: list[str] = []
    for launcher in PORTABLE_ROOT.glob("*.bat"):
        shutil.copy2(launcher, destination / launcher.name)
        copied.append(launcher.name)
    return copied


def _copy_plugin(plugin: MigrationPlugin, target_nodes: Path) -> None:
    source = Path(plugin.path)
    target = target_nodes / plugin.name
    if plugin.git and plugin.remote_url:
        _log(f"Cloning {plugin.name}")
        code, out = _run_git(["clone", "--no-checkout", plugin.remote_url, str(target)], target_nodes)
        if code == 0:
            if plugin.commit:
                code, out = _run_git(["checkout", "--detach", plugin.commit], target)
            else:
                code, out = _run_git(["checkout"], target)
        if code == 0:
            return
        # Network / private remotes may fail. Preserve a usable local fallback.
        _log(f"Git clone failed for {plugin.name}; copying local folder instead: {out}")
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
    else:
        _log(f"Copying local plugin {plugin.name}")
    shutil.copytree(source, target, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", ".git"))


def _validate_target_slots(plugins: list[MigrationPlugin], target_nodes: Path) -> None:
    occupied = [plugin.name for plugin in plugins if (target_nodes / plugin.name).exists()]
    if occupied:
        raise ValueError("destination already contains: " + ", ".join(occupied))


def _copy_selected_plugins(
    target_nodes: Path,
    plugins: list[MigrationPlugin],
    *,
    delete_source: bool,
    source_nodes: Path | None = None,
) -> None:
    _validate_target_slots(plugins, target_nodes)
    target_nodes.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        STATUS.phase = "copy_plugins"
    for index, plugin in enumerate(plugins, 1):
        with _LOCK:
            STATUS.current = plugin.name
        _copy_plugin(plugin, target_nodes)
        with _LOCK:
            STATUS.completed = index

    # Source folders are removed only after every selected plugin copied
    # successfully.  The running QBB NodeOps plugin is protected explicitly.
    if delete_source:
        with _LOCK:
            STATUS.phase = "delete_sources"
        for plugin in plugins:
            source = Path(plugin.path).resolve()
            if source == PLUGIN_ROOT.resolve():
                raise ValueError("QBB NodeOps cannot delete itself while it is running")
            if source_nodes is None or not _is_inside(source, source_nodes):
                raise ValueError(f"refusing to delete a path outside custom_nodes: {source}")
            _log(f"Removing original plugin {plugin.name}")
            shutil.rmtree(source)


def _finish() -> None:
    with _LOCK:
        STATUS.status = "success"
        STATUS.phase = "complete"
        STATUS.current = ""
        STATUS.finished_at = _now()


def _execute_new(destination: Path, plugins: list[MigrationPlugin]) -> None:
    global STATUS
    target_comfyui = destination / "ComfyUI"
    target_python = destination / PYTHON_ROOT.name
    target_nodes = target_comfyui / "custom_nodes"
    try:
        with _LOCK:
            STATUS.phase = "copy_core"
            STATUS.current = "ComfyUI core"
        _log(
            "Copying ComfyUI core (models, user runtime data, output, temp, and custom_nodes are excluded)"
        )
        _copytree(
            COMFYUI_ROOT,
            target_comfyui,
            # `user` contains the live SQLite database and its .lock file.
            # Copying it while ComfyUI is running fails on Windows and also
            # transports transient UI/session state to a supposedly clean
            # installation.  The target creates a fresh user directory on
            # first start instead.
            ignore=shutil.ignore_patterns(
                "models",
                "custom_nodes",
                "output",
                "temp",
                "user",
                "__pycache__",
                ".git",
            ),
        )

        with _LOCK:
            STATUS.phase = "copy_python"
            STATUS.current = PYTHON_ROOT.name
        _log("Copying the current Python environment")
        _copytree(PYTHON_ROOT, target_python, ignore=shutil.ignore_patterns("__pycache__"))
        launchers = _copy_launchers(destination)
        if launchers:
            _log("Copied portable launchers: " + ", ".join(launchers))

        _copy_selected_plugins(target_nodes, plugins, delete_source=False)

        with _LOCK:
            STATUS.phase = "write_model_paths"
            STATUS.current = "extra_model_paths.yaml"
        config = _write_model_config(target_comfyui)
        _log(f"Wrote shared model path configuration: {config}")

        _finish()
        _log("Migration finished. Start the new portable ComfyUI from the target folder.")
    except Exception as e:
        with _LOCK:
            STATUS.status = "error"
            STATUS.phase = "error"
            STATUS.error = str(e)
            STATUS.finished_at = _now()
        _log(f"Migration failed: {e}")


def _execute_existing(
    target_comfyui: Path,
    plugins: list[MigrationPlugin],
    delete_source: bool,
    source_nodes: Path,
) -> None:
    try:
        _log(f"Migrating selected plugins to existing ComfyUI: {target_comfyui}")
        _copy_selected_plugins(
            target_comfyui / "custom_nodes",
            plugins,
            delete_source=delete_source,
            source_nodes=source_nodes,
        )
        _finish()
        action = " and removed originals" if delete_source else ""
        _log("Plugin migration finished" + action + ".")
    except Exception as e:
        with _LOCK:
            STATUS.status = "error"
            STATUS.phase = "error"
            STATUS.error = str(e)
            STATUS.finished_at = _now()
        _log(f"Migration failed: {e}")


def start_migration(payload: dict[str, Any]) -> MigrationStatus:
    global STATUS, _WORKER
    with _LOCK:
        if STATUS.status == "running":
            raise RuntimeError("a migration is already running")

    mode = str(payload.get("mode") or "new").lower()
    if mode not in {"new", "existing", "instance"}:
        raise ValueError("invalid migration mode")
    delete_source = bool(payload.get("delete_source"))
    if mode == "new" and not (PORTABLE_ROOT / "python_embeded").is_dir():
        raise ValueError("creating a new environment currently supports portable installations only")
    source_instance = get_instance(str(payload.get("source_instance_id") or "current"))
    source_comfyui = Path(source_instance["comfyui_root"])
    if mode == "new":
        destination = _safe_destination(str(payload.get("destination_root") or ""))
    elif mode == "instance":
        target_instance = get_instance(str(payload.get("target_instance_id") or ""))
        destination = Path(target_instance["comfyui_root"])
        if destination.resolve() == source_comfyui.resolve():
            raise ValueError("source and target instances must be different")
    else:
        destination = _existing_comfyui_target(str(payload.get("destination_root") or ""))
    selected_raw = payload.get("plugins")
    if not isinstance(selected_raw, list):
        raise ValueError("plugins must be a list")
    plugins = _resolve_selected([str(p) for p in selected_raw], source_comfyui)
    if delete_source and any(Path(plugin.path).resolve() == PLUGIN_ROOT.resolve() for plugin in plugins):
        raise ValueError("cannot remove the currently running QBB NodeOps plugin")
    _validate_target_slots(
        plugins,
        (destination / "ComfyUI" / "custom_nodes") if mode == "new" else destination / "custom_nodes",
    )

    with _LOCK:
        STATUS = MigrationStatus(
            status="running",
            phase="prepare",
            destination_root=str(destination),
            source_root=str(source_comfyui),
            mode=mode,
            delete_source=delete_source,
            total=len(plugins),
            logs=["Migration started. Source plugins will remain unchanged." if not delete_source else "Migration started. Sources will be removed only after all selected plugins copy successfully."],
            started_at=_now(),
        )
        _WORKER = threading.Thread(
            target=_execute_new if mode == "new" else _execute_existing,
            args=(destination, plugins) if mode == "new" else (destination, plugins, delete_source, source_comfyui / "custom_nodes"),
            name="qbb-nodeops-migration",
            daemon=True,
        )
        _WORKER.start()
        return MigrationStatus(**asdict(STATUS))
