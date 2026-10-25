"""Download a private Python runtime and install a selected PyTorch build."""

import os
import platform
from pathlib import Path
import subprocess
import sys


from .runtime_catalog import resolve_config
from .local_gpu import detect, supports, filter_catalog
from .install_network import pip_network


class CudaValidationError(RuntimeError):
    def __init__(self, python, message):
        super().__init__(message)
        self.python = python


def validate_config(value):
    gpu = detect()
    if isinstance(value, dict) and not supports(value.get('build', ''), gpu):
        raise ValueError('所选 CUDA 不适合本机显卡或驱动，请刷新可用版本重新选择。')
    row = resolve_config(value)
    if not filter_catalog({'builds': [row['build']], 'combinations': [row]}, gpu)['combinations']:
        raise ValueError('所选 PyTorch 不支持本机显卡架构，请刷新可用版本。')
    return row


def install_runtime(target, config, requirements, log, progress=None):
    config = validate_config(config)
    target = Path(target)
    env, network_args, network_label = pip_network()
    log("安装网络：" + network_label)
    # uv environment/config files must not override this task's selected network.
    for key in list(env):
        if key.upper().startswith("UV_"):
            env.pop(key, None)
    env["UV_NO_CONFIG"] = "1"
    env["UV_DEFAULT_INDEX"] = "https://pypi.org/simple"
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX", "PIP_TARGET", "PIP_PREFIX", "PIP_USER",
                "UV_MANAGED_PYTHON", "UV_NO_MANAGED_PYTHON"):
        env.pop(key, None)
    env.update(UV_PYTHON_INSTALL_DIR=str(target / ".python"),
               UV_PYTHON_PREFERENCE="only-managed", UV_PYTHON_INSTALL_BIN="0",
               UV_CACHE_DIR=str(target / ".runtime-cache"), PYTHONNOUSERSITE="1")

    def run(args, timeout=3600):
        log("Running: " + " ".join(str(arg) for arg in args))
        result = subprocess.run([str(arg) for arg in args], cwd=target, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if result.returncode:
            raise RuntimeError(result.stdout[-6000:] or "Runtime installation failed")
        return result.stdout.strip()

    tools = target / ".runtime-tools"
    if progress:
        progress("prepare_tools", "准备下载工具")
    run([sys.executable, "-m", "pip", "install", "--isolated", "--no-user",
         "--target", tools, "uv", *network_args], 600)
    # pip --target installs uv's executable beside its package, under bin.
    uv = tools / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    if not uv.is_file():
        raise RuntimeError(f"uv 安装后未找到可执行文件：{uv}")
    log("Downloading independent Python " + config["python"])
    if progress:
        progress("download_python", "下载 Python " + config["python"])
    run([uv, "python", "install", config["python"]])
    # UV_PYTHON_PREFERENCE already requires a managed interpreter. Passing
    # --managed-python as well is rejected by uv as conflicting options.
    downloaded = Path(run([uv, "python", "find", config["python"]]))
    if not downloaded.resolve().is_relative_to((target / ".python").resolve()):
        raise RuntimeError("Downloaded Python is outside the new instance")
    if progress:
        progress("create_environment", "创建独立环境")
    run([uv, "venv", "--python", downloaded, "--seed", target / ".venv"])
    python = target / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    pins = [f"{name}=={version}" for name, version in config["pins"].items()]
    constraints = target / "nodeops-runtime-constraints.txt"
    constraints.write_text("\n".join(pins) + "\n", encoding="utf-8")
    if progress:
        progress("install_torch", "下载并安装 PyTorch / CUDA")
    torch_network_args = list(network_args)
    torch_network_args[torch_network_args.index("--index-url") + 1] = "https://download.pytorch.org/whl/" + config["build"]
    run([python, "-m", "pip", "--isolated", "install", *pins, *torch_network_args])
    args = [python, "-m", "pip", "--isolated", "install", "-c", constraints, *network_args]
    for path in requirements:
        if path.is_file():
            args.extend(["-r", path])
    if "-r" in args:
        if progress:
            progress("install_dependencies", "安装 ComfyUI 和插件依赖")
        run(args)
    if progress:
        progress("verify_runtime", "检查 Python / PyTorch")
    run([python, "-m", "pip", "check"])
    probe = "import torch; print(torch.__version__, torch.version.cuda); "
    run([python, "-c", probe], 120)
    if config["build"] != "cpu":
        try:
            run([python, '-c', "import torch; assert torch.cuda.is_available(), 'CUDA unavailable: check NVIDIA driver and GPU'; torch.ones(1, device='cuda'); torch.cuda.synchronize()"], 120)
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
            raise CudaValidationError(python, 'CUDA 运算验证失败，已安装的实例将保留：' + str(exc)) from exc
    log("Independent Python and PyTorch verified")
    return python
