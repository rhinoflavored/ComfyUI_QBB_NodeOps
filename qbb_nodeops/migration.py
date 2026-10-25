"""Copy selected custom nodes into another ComfyUI tree.

New instances download private Python runtimes. Models stay in the source installation.
"""

from __future__ import annotations

import os
import hashlib
import shutil
import subprocess
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .instances import get_instance, inspect_runtime, python_executable
from .runtime_install import CudaValidationError, install_runtime, validate_config
from .git_tools import require_git

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CUSTOM_NODES_ROOT = PLUGIN_ROOT.parent
COMFYUI_ROOT = CUSTOM_NODES_ROOT.parent

_LOCK = threading.Lock()
_START_LOCK = threading.Lock()
_WORKER: threading.Thread | None = None
_PENDING_RECYCLE = None


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
    environment: dict[str, Any] = field(default_factory=dict)
    plugin_names: list[str] = field(default_factory=list)
    delete_source: bool = False
    source_root: str = ""
    total: int = 0
    completed: int = 0
    current: str = ""
    logs: list[str] = field(default_factory=list)
    error: str = ""
    started_at: str = ""
    finished_at: str = ""
    validation: dict[str, Any] = field(default_factory=dict)

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
    executable = require_git()
    try:
        proc = subprocess.run(
            [executable, "-c", "core.hooksPath=", *args],
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
    """List direct custom_nodes directories and standalone Python nodes."""
    source_comfyui = (source_comfyui or COMFYUI_ROOT).resolve()
    source_nodes = source_comfyui / "custom_nodes"
    plugins: list[MigrationPlugin] = []
    if source_nodes.is_dir():
        for path in sorted(source_nodes.iterdir(), key=lambda p: p.name.lower()):
            if (not path.is_dir() and not (path.is_file() and path.suffix == ".py")) or path.name.startswith(("__", ".")):
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

    runtime = inspect_runtime(source_comfyui, current=source_comfyui == COMFYUI_ROOT.resolve())
    python_path = runtime["python_path"]
    return {
        "source_comfyui": str(source_comfyui),
        "source_portable": runtime["install_root"],
        "source_python": str(Path(python_path).parent) if python_path else "",
        "python_path": python_path,
        "python_kind": runtime["python_kind"],
        "portable_layout": runtime["portable"],
        "relocatable_python": runtime["relocatable_python"],
        "plugins": [asdict(p) for p in plugins],
        "model_root": str(source_comfyui / "models"),
        "status": get_status().to_dict(),
    }


def get_status() -> MigrationStatus:
    with _LOCK:
        return MigrationStatus(**asdict(STATUS))


def _safe_destination(raw: str, *roots: Path) -> Path:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("destination_root is required")
    if any(ch in text for ch in '"\r\n'):
        raise ValueError("destination path is invalid")
    destination = Path(text).expanduser().resolve()
    if destination == Path(destination.anchor):
        raise ValueError("destination must not be a drive root")
    for root in roots:
        if root is None:
            continue
        base = Path(root).resolve()
        if destination == base or _is_inside(destination, base) or _is_inside(base, destination):
            raise ValueError("destination must not overlap an existing ComfyUI installation")
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


def _copy_contents(source: Path, destination: Path, *, ignore: Any = None, created: list | None = None) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("destination folder must be empty or not exist")
    names = [item.name for item in source.iterdir()]
    excluded = set(ignore(str(source), names)) if ignore else set()
    for item in source.iterdir():
        if item.name in excluded:
            continue
        target = destination / item.name
        if created is not None:
            created.append(target)
        if item.is_dir():
            shutil.copytree(item, target, symlinks=True, ignore=ignore)
        else:
            shutil.copy2(item, target)


def _write_launcher(target_comfyui: Path, python_exe: Path, cpu: bool = False, launcher_root: Path | None = None) -> Path:
    launcher_root = launcher_root or target_comfyui
    python = os.path.relpath(python_exe, target_comfyui)
    core = os.path.relpath(target_comfyui, launcher_root)
    flags = " --cpu" if cpu else ""
    if '"' in python or '"' in str(target_comfyui):
        raise ValueError("Python path must not contain quotes")
    if os.name == "nt":
        path = launcher_root / "run_comfyui.bat"
        path.write_text(
            "@echo off\r\n"
            'setlocal DisableDelayedExpansion\r\n'
            f'cd /d "%~dp0{core}" || exit /b 1\r\n'
            f'"{python}" main.py{flags} %*\r\n'
            'if errorlevel 1 pause\r\n',
            encoding="utf-8",
            newline="",
        )
    else:
        path = launcher_root / "run_comfyui.sh"
        path.write_text(
            "#!/bin/sh\n"
            f'cd "$(dirname "$0")/{core}" || exit 1\n'
            f'exec "{python}" main.py{flags} "$@"\n',
            encoding="utf-8",
            newline="\n",
        )
        path.chmod(path.stat().st_mode | 0o111)
    return path


def _write_model_config(target_comfyui: Path, source_comfyui: Path) -> Path:
    """Append a safe model-root reference to the target's extra model config."""
    config = target_comfyui / "extra_model_paths.yaml"
    source = str(source_comfyui).replace("\\", "/").replace("'", "''")
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


def _copy_launchers(destination: Path, source_install: Path) -> list[str]:
    """Keep portable launch scripts that live beside a bundled Python."""
    copied: list[str] = []
    for launcher in [*source_install.glob("*.bat"), *source_install.glob("*.ps1"), *source_install.glob("*.sh")]:
        shutil.copy2(launcher, destination / launcher.name)
        copied.append(launcher.name)
    return copied


def _copy_plugin(plugin: MigrationPlugin, target_nodes: Path, created: list | None = None) -> None:
    source = Path(plugin.path)
    target = target_nodes / plugin.name
    # A fresh checkout discards dirty/untracked/ignored local files. Migration
    # copies the actual working tree instead; it must never reset or fetch it.
    _log(f"Copying local working tree (including local changes): {plugin.name}")
    if source.is_file():
        from .file_snapshots import no_links
        no_links(source)
        if source.suffix != '.py':
            raise ValueError('Unsupported single-file plugin')
        # Exclusive creation preserves the no-overwrite migration contract.
        with target.open('xb') as output:
            if created is not None:
                created.append(target)
            with source.open('rb') as stream:
                shutil.copyfileobj(stream, output)
        shutil.copystat(source, target)
        return
    def ignore(directory: str, names: list[str]) -> list[str]:
        excluded = []
        if '.git' in names:
            metadata = Path(directory) / '.git'
            # Worktrees/submodules and shared object stores refer back to the
            # source. Keep their working files, but do not transport that link.
            if (not metadata.is_dir() or metadata.is_symlink()
                    or getattr(metadata.stat(), 'st_file_attributes', 0) & 0x400
                    or (metadata / 'objects' / 'info' / 'alternates').exists()
                    or (metadata / 'commondir').exists()):
                excluded.append('.git')
                _log(f"External Git metadata omitted; local files retained: {directory}")
        return excluded
    target.mkdir()
    if created is not None:
        created.append(target)
    shutil.copytree(source, target, symlinks=True, ignore=ignore, dirs_exist_ok=True)


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
    created: list | None = None,
) -> None:
    _validate_target_slots(plugins, target_nodes)
    if not target_nodes.exists():
        target_nodes.mkdir(parents=True)
        if created is not None:
            created.append(target_nodes)
    with _LOCK:
        STATUS.phase = "copy_plugins"
    for index, plugin in enumerate(plugins, 1):
        with _LOCK:
            STATUS.current = plugin.name
        _copy_plugin(plugin, target_nodes, created)
        with _LOCK:
            STATUS.completed = index

    if delete_source:
        raise ValueError("Source recycling must run after target verification")


def _recycle_verified_sources(target: Path, plugins: list[MigrationPlugin], source_nodes: Path) -> None:
    from send2trash import send2trash
    if STATUS.validation.get("user_confirmed_runtime") is not True:
        with _LOCK:
            STATUS.status = "needs_attention"
            STATUS.phase = "needs_attention"
            STATUS.finished_at = _now()
        _log("目标 ComfyUI 启动及插件加载尚未验证，原插件全部保留，未执行删除。")
        return
    if STATUS.validation.get("issues") or STATUS.validation.get("unknown"):
        _log("目标依赖检查未通过或信息不足，原插件全部保留。")
        return
    def digest(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").digest()
    sources = []
    for plugin in plugins:
        source = Path(plugin.path)
        if not (source.is_dir() or (source.is_file() and source.suffix == '.py')) or source.is_symlink() or source.resolve() == (target / 'custom_nodes' / plugin.name).resolve():
            raise ValueError('Invalid source/target; originals retained')
        copied_root = target / 'custom_nodes' / plugin.name
        for entry in [target, target / 'custom_nodes', copied_root, *copied_root.rglob('*')]:
            if entry.is_symlink() or getattr(entry.stat(), 'st_file_attributes', 0) & 0x400:
                raise ValueError('Target contains linked files; originals retained')
        if source.resolve() == PLUGIN_ROOT.resolve() or source.resolve().parent != source_nodes.resolve():
            raise ValueError("Refusing to recycle a protected or out-of-scope plugin")
        for entry in [source, *source.rglob("*")]:
            if entry.is_symlink() or getattr(entry.stat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("Source contains linked files; originals retained")
            if entry.is_file():
                copied = copied_root if source.is_file() else copied_root / entry.relative_to(source)
                if not copied.is_file() or copied.is_symlink() or digest(entry) != digest(copied):
                    raise ValueError(f"Copy verification failed; originals retained: {entry}")
        sources.append(source)
    for source in sources:
        send2trash(str(source))
        _log(f"原插件已移入回收站，可恢复：{source}")
    _log("搬家文件已校验并回收原插件；请重启来源实例，并验证目标实例启动。")
    with _LOCK:
        STATUS.status = "success"
        STATUS.phase = "complete"
        STATUS.finished_at = _now()


def confirm_recycle(payload):
    global _PENDING_RECYCLE
    if not isinstance(payload, dict) or payload.get('confirmed') is not True:
        raise ValueError('请先确认目标实例已启动，所选插件均可正常使用。')
    with _START_LOCK:
        pending = _PENDING_RECYCLE
        if not pending or payload.get('token') != pending[0] or (_WORKER and _WORKER.is_alive()):
            raise ValueError('确认已失效或任务仍在运行，请重新检查搬家任务。')
        token, target, plugins, source_nodes, python = pending
        from .migration_validation import validate
        validation = validate(python, target, plugins)
        if validation['issues'] or validation['unknown']:
            raise ValueError('目标依赖检查未通过，原插件保留。')
        validation['user_confirmed_runtime'] = True
        with _LOCK:
            STATUS.validation = validation
        try:
            _recycle_verified_sources(target, plugins, source_nodes)
        except Exception as exc:
            _log('回收未完成：' + str(exc))
            raise ValueError('回收未完成，请查看日志；已回收的文件可从回收站恢复。') from exc
        finally:
            _PENDING_RECYCLE = None
        return get_status()


def _finish() -> None:
    with _LOCK:
        STATUS.status = "success"
        STATUS.phase = "complete"
        STATUS.current = ""
        STATUS.finished_at = _now()


def _verify_copied(target: Path, plugins: list[MigrationPlugin], python: Path | None) -> None:
    from .migration_validation import validate
    with _LOCK:
        STATUS.phase = 'verify_dependencies'
    result = validate(python, target, plugins)
    with _LOCK:
        STATUS.validation = result
        STATUS.status = 'needs_attention' if result['issues'] or result['unknown'] else 'copied_unverified'
        if STATUS.delete_source and result.get("runtime_verified") is True and STATUS.status == 'copied_unverified':
            STATUS.status = "running"
        STATUS.phase = STATUS.status
        STATUS.current = ''
        STATUS.finished_at = _now()
    _log('文件复制完成；尚未验证目标 ComfyUI 启动和插件加载，不代表搬家成功。')
    for issue in result['issues'] + result['unknown']:
        _log(issue)
    _log('请处理上述依赖问题，再启动目标 ComfyUI 验证插件加载。')


CORE_IGNORE = shutil.ignore_patterns(
    "models",
    "custom_nodes",
    "output",
    "temp",
    "user",
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    ".python",
    ".runtime-tools",
    ".runtime-cache",
)


def _cleanup_failed_copy(paths: list[Path], boundary: Path) -> None:
    from send2trash import send2trash
    with _LOCK:
        STATUS.phase = "cleanup_failed"
    for path in reversed(paths):
        try:
            if not path.exists() and not path.is_symlink():
                continue
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("linked target requires manual review")
            if path.resolve() == boundary.resolve() or not _is_inside(path, boundary):
                raise ValueError("cleanup target is outside the migration directory")
            send2trash(str(path))
            _log(f"失败残留已移入回收站：{path}")
        except Exception as exc:
            _log(f"无法清理，请手动检查：{path} — {exc}")


def _download_comfyui(target: Path) -> None:
    """Fetch a clean official tree; never copy or fall back to the source instance."""
    from .settings import load_settings
    from .update_check import _run_git as network_git, _apply_mirror
    settings = load_settings()
    official = "https://github.com/Comfy-Org/ComfyUI.git"
    url = _apply_mirror(official, settings)
    _log("下载官方 ComfyUI: " + official)
    code, stdout, stderr = network_git(
        ["clone", "--depth", "1", "--", url, str(target)],
        cwd=str(target.parent), settings=settings, timeout=900)
    if code:
        raise RuntimeError("下载官方 ComfyUI 失败：" + (stderr or stdout)[-4000:])
    for name in ("main.py", "requirements.txt", "comfy/ldm/models/autoencoder.py"):
        if not (target / name).is_file():
            raise RuntimeError("官方 ComfyUI 下载不完整，缺少：" + name)
    _log("官方 ComfyUI 源码下载完成。")


def _execute_new(destination: Path, plugins: list[MigrationPlugin], source_comfyui: Path, runtime: dict[str, Any], delete_source: bool = False) -> None:
    global STATUS
    target_comfyui = destination / "ComfyUI"
    environment_root = destination / "python_env"
    created = []
    may_cleanup = True
    try:
        if plugins or delete_source:
            raise ValueError("新建实例不复制插件或删除来源文件，请使用插件搬家。")
        destination.mkdir(parents=True, exist_ok=True)
        created.extend([target_comfyui, environment_root])
        environment_root.mkdir()
        with _LOCK:
            STATUS.phase = "download_comfyui"
            STATUS.current = "下载官方 ComfyUI"
        _download_comfyui(target_comfyui)
        for name in ("run_comfyui.bat", "run_comfyui.sh"):
            path = destination / name
            if not path.exists():
                created.append(path)
        with _LOCK:
            STATUS.phase = "install_runtime"
            STATUS.current = "Downloading Python and installing dependencies"
        requirements = [target_comfyui / "requirements.txt"]
        def progress(phase, current):
            with _LOCK:
                STATUS.phase = phase
                STATUS.current = current
        cuda_warning = ''
        try:
            verify_python = install_runtime(environment_root, runtime["new_environment"], requirements, _log, progress=progress)
        except CudaValidationError as exc:
            verify_python = exc.python
            cuda_warning = str(exc)
            _log(cuda_warning)
        # Installation completed. Later validation/registration failures must
        # not discard the downloaded environment.
        may_cleanup = False
        shutil.copy2(environment_root / "nodeops-runtime-constraints.txt", target_comfyui / "nodeops-runtime-constraints.txt")
        launcher = _write_launcher(target_comfyui, verify_python, cpu=runtime["new_environment"]["build"] == "cpu", launcher_root=destination)
        _log(f"Wrote launcher using target Python: {launcher}")

        from .instances import add_instance
        add_instance(str(target_comfyui), destination.name)
        _log("新实例已自动加入插件搬家列表。")
        with _LOCK:
            STATUS.status = "needs_attention" if cuda_warning else "success"
            STATUS.phase = "complete"
            STATUS.current = "安装完成，CUDA 验证失败，文件已保留" if cuda_warning else "创建完成"
            STATUS.error = cuda_warning
            STATUS.finished_at = _now()
        _log("全新 ComfyUI 和所选 Python / PyTorch 已安装；未复制旧实例插件、模型或配置。")
        _log(f"{STATUS.current}。启动器：{launcher}")
        may_cleanup = False
    except Exception as e:
        if may_cleanup:
            _cleanup_failed_copy(created, destination)
        with _LOCK:
            STATUS.status = "error"
            STATUS.phase = "error"
            STATUS.error = str(e)
            STATUS.finished_at = _now()
        _log(f"Migration failed: {e}")


def _install_plugin_dependencies(target: Path, plugins: list[MigrationPlugin], python: Path | None) -> None:
    if python is None or not python.is_file():
        raise ValueError("无法识别目标 Python，未安装依赖，原插件保留。请先配置目标实例的 Python。")
    from .install_network import pip_network, install_error
    environment, network_args, network_label = pip_network()
    _log('依赖安装网络：' + network_label)
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX",
                "PIP_TARGET", "PIP_PREFIX", "PIP_USER"):
        environment.pop(key, None)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with _LOCK:
        STATUS.phase = "install_dependencies"
    constraints = target / "nodeops-runtime-constraints.txt"
    for plugin in plugins:
        directory = target / "custom_nodes" / plugin.name
        requirements = directory / "requirements.txt"
        if not requirements.is_file():
            _log(f"{plugin.name}: 没有 requirements.txt，无法自动确认依赖；原插件将保留。")
            continue
        with _LOCK:
            STATUS.current = plugin.name
        _log(f"安装 {plugin.name} 的依赖，目标 Python: {python}")
        command = [str(python), "-I", "-m", "pip", "--isolated", "install", *network_args, "-r", str(requirements)]
        if constraints.is_file():
            command.extend(["-c", str(constraints)])
        result = subprocess.run(command, cwd=str(directory), env=environment,
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=3600, creationflags=flags)
        if result.returncode:
            output = result.stdout + result.stderr
            raise RuntimeError(f"{plugin.name}：{install_error(output)} 原插件保留，目标环境可能已安装部分依赖。\n" + output[-6000:])
        _log(f"{plugin.name}: 依赖安装完成")


def _execute_existing(
    target_comfyui: Path,
    plugins: list[MigrationPlugin],
    delete_source: bool,
    source_nodes: Path,
    target_python: Path | None = None,
) -> None:
    global _PENDING_RECYCLE
    created = []
    may_cleanup = True
    try:
        _log(f"Migrating selected plugins to existing ComfyUI: {target_comfyui}")
        _copy_selected_plugins(
            target_comfyui / "custom_nodes",
            plugins,
            delete_source=False,
            source_nodes=source_nodes,
            created=created,
        )
        _install_plugin_dependencies(target_comfyui, plugins, target_python)
        _verify_copied(target_comfyui, plugins, target_python)
        may_cleanup = False
        if delete_source:
            token = uuid.uuid4().hex
            _PENDING_RECYCLE = (token, target_comfyui, plugins, source_nodes, target_python)
            with _LOCK:
                STATUS.validation['recycle_token'] = token
            _log('请保持当前 ComfyUI 后台运行，暂停来源工作流，用不同端口启动目标实例确认插件可用，再点击“确认可用，回收原插件”。回收后再重启来源实例；确认前重启后台或开始新任务会使凭据失效，原插件不会自动删除。')
    except Exception as e:
        if may_cleanup:
            _cleanup_failed_copy(created, target_comfyui / "custom_nodes")
            _log("本次复制残留已尝试清理；已安装到目标 Python 的依赖不自动卸载，避免影响原有插件。")
        with _LOCK:
            STATUS.status = "error"
            STATUS.phase = "error"
            STATUS.error = str(e)
            STATUS.finished_at = _now()
        _log(f"Migration failed: {e}")


def start_migration(payload: dict[str, Any]) -> MigrationStatus:
    # Reserve submission through preflight and worker launch. The status lock
    # stays short-lived, so polling can respond while preparation is running.
    if not _START_LOCK.acquire(blocking=False):
        raise RuntimeError("a migration is already being prepared")
    try:
        return _prepare_and_start_migration(payload)
    finally:
        _START_LOCK.release()


def _prepare_and_start_migration(payload: dict[str, Any]) -> MigrationStatus:
    global STATUS, _WORKER, _PENDING_RECYCLE
    with _LOCK:
        # Workers publish their result before emitting the final logs. Keep
        # ownership until the thread exits, or those logs can reach the next
        # task's global STATUS. _START_LOCK also covers preflight/launch.
        if STATUS.status == "running" or (_WORKER is not None and _WORKER.is_alive()):
            raise RuntimeError("a migration is already running")

    creating = payload.get("purpose") == "create"
    mode = str(payload.get("mode") or ("new" if creating else "existing")).lower()
    if mode not in {"new", "existing", "instance"}:
        raise ValueError("invalid migration mode")
    selected_raw = payload.get("plugins")
    if not isinstance(selected_raw, list):
        raise ValueError("plugins must be a list")
    if creating:
        if mode != "new" or selected_raw or payload.get("delete_source"):
            raise ValueError("Creating an instance requires new mode, no plugins and no source deletion")
    elif mode == "new":
        raise ValueError("Create the instance in Create instance first, then migrate plugins into it")
    delete_source = bool(payload.get("delete_source"))
    if delete_source:
        from send2trash import send2trash
    source_instance = get_instance(str(payload.get("source_instance_id") or "current"))
    source_comfyui = Path(source_instance["comfyui_root"]).resolve()
    runtime = inspect_runtime(source_comfyui, current=source_comfyui == COMFYUI_ROOT.resolve())
    current_runtime = inspect_runtime(COMFYUI_ROOT, current=True)
    if mode == "new":
        require_git()
        runtime["new_environment"] = validate_config(payload.get("environment"))
        destination = _safe_destination(
            str(payload.get("destination_root") or ""),
            Path(runtime["install_root"]),
            Path(current_runtime["install_root"]),
            source_comfyui,
            COMFYUI_ROOT,
        )
        target_nodes = destination / "ComfyUI" / "custom_nodes"
    elif mode == "instance":
        target_instance = get_instance(str(payload.get("target_instance_id") or ""))
        destination = Path(target_instance["comfyui_root"])
        if destination.resolve() == source_comfyui.resolve():
            raise ValueError("source and target instances must be different")
        target_nodes = destination / "custom_nodes"
    else:
        destination = _existing_comfyui_target(str(payload.get("destination_root") or ""))
        target_nodes = destination / "custom_nodes"
    if mode != "new":
        executable = target_instance.get("python_path") if mode == "instance" else inspect_runtime(destination, current=destination.resolve() == COMFYUI_ROOT.resolve())["python_path"]
        target_python = Path(executable) if executable else None
    plugins = [] if creating else _resolve_selected([str(p) for p in selected_raw], source_comfyui)
    if delete_source and any(Path(plugin.path).resolve() == PLUGIN_ROOT.resolve() for plugin in plugins):
        raise ValueError("cannot remove the currently running QBB NodeOps plugin")
    _validate_target_slots(plugins, target_nodes)
    if not creating:
        from .conflicts import preflight
        compatibility = preflight(payload)
        if compatibility['confirmed']:
            raise ValueError('Confirmed conflicts found. Choose another existing target instance.')

    with _LOCK:
        _PENDING_RECYCLE = None
        STATUS = MigrationStatus(
            status="running",
            phase="prepare",
            destination_root=str(destination),
            source_root=str(source_comfyui),
            mode=mode,
            environment=runtime.get("new_environment", {}) if creating else {},
            plugin_names=[plugin.name for plugin in plugins],
            delete_source=delete_source,
            total=len(plugins),
            logs=["Migration started. Source plugins will remain unchanged." if not delete_source else "搬家开始。原插件保留，待您确认目标插件可用并通过文件校验后移入回收站。"],
            started_at=_now(),
        )
        try:
            _WORKER = threading.Thread(
                target=_execute_new if mode == "new" else _execute_existing,
                args=(destination, plugins, source_comfyui, runtime, delete_source) if mode == "new" else (destination, plugins, delete_source, source_comfyui / "custom_nodes", target_python),
                name="qbb-nodeops-migration",
                daemon=True,
            )
            _WORKER.start()
        except Exception as e:
            # No worker can finish the task if construction/start failed.
            # Publish the failure atomically and allow the next submission.
            _WORKER = None
            STATUS.status = "error"
            STATUS.phase = "error"
            STATUS.error = f"Unable to start migration worker: {e}"
            STATUS.finished_at = _now()
            STATUS.logs.append(STATUS.error)
            raise RuntimeError(STATUS.error) from e
        return MigrationStatus(**asdict(STATUS))
