from __future__ import annotations

import os
import platform
import subprocess
import sys
from datetime import datetime
from typing import Any


def _run_pip_freeze() -> tuple[str, str | None]:
    """Return (stdout, error_message)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "freeze"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode != 0 and not out:
            return "", err or f"pip freeze failed with code {proc.returncode}"
        return out, None if proc.returncode == 0 else err or None
    except Exception as e:
        return "", str(e)


def _package_meta() -> dict[str, Any]:
    info: dict[str, Any] = {
        "python_version": sys.version.replace("\n", " "),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "processor": platform.processor() or "",
    }

    try:
        import torch  # type: ignore

        info["torch_version"] = getattr(torch, "__version__", "unknown")
        info["cuda_available"] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            info["cuda_version"] = getattr(getattr(torch, "version", None), "cuda", None)
            try:
                info["gpu_name"] = torch.cuda.get_device_name(0)
            except Exception:
                info["gpu_name"] = ""
        else:
            info["cuda_version"] = getattr(getattr(torch, "version", None), "cuda", None)
            info["gpu_name"] = ""
    except Exception as e:
        info["torch_version"] = f"unavailable ({e})"
        info["cuda_available"] = False
        info["cuda_version"] = None
        info["gpu_name"] = ""

    try:
        import comfyui_version  # type: ignore

        info["comfyui_version"] = getattr(comfyui_version, "__version__", "unknown")
    except Exception:
        info["comfyui_version"] = "unknown"

    return info


def build_environment_report() -> str:
    meta = _package_meta()
    freeze_text, freeze_err = _run_pip_freeze()
    lines: list[str] = []
    lines.append("ComfyUI_QBB_LoadHighlight - Environment Dependencies Export")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append("=" * 72)
    lines.append("RUNTIME")
    lines.append("-" * 72)
    lines.append(f"Python        : {meta['python_version']}")
    lines.append(f"Executable    : {meta['python_executable']}")
    lines.append(f"Platform      : {meta['platform']}")
    lines.append(f"Architecture  : {meta['architecture']}")
    if meta.get("processor"):
        lines.append(f"Processor     : {meta['processor']}")
    lines.append(f"ComfyUI       : {meta.get('comfyui_version', 'unknown')}")
    lines.append(f"Torch         : {meta.get('torch_version', 'unknown')}")
    lines.append(f"CUDA available: {meta.get('cuda_available', False)}")
    lines.append(f"CUDA version  : {meta.get('cuda_version', None)}")
    if meta.get("gpu_name"):
        lines.append(f"GPU           : {meta['gpu_name']}")
    lines.append("")
    lines.append("PIP FREEZE")
    lines.append("-" * 72)
    if freeze_err and not freeze_text:
        lines.append(f"[ERROR] {freeze_err}")
    else:
        if freeze_err:
            lines.append(f"[WARN] pip reported: {freeze_err}")
        lines.append(freeze_text if freeze_text else "(no packages listed)")
        pkg_count = len([ln for ln in freeze_text.splitlines() if ln.strip()]) if freeze_text else 0
        lines.append("")
        lines.append(f"Package count: {pkg_count}")
    lines.append("")
    lines.append("NOTES")
    lines.append("-" * 72)
    lines.append("- This file was generated from the Python environment currently running ComfyUI.")
    lines.append("- You can recreate a similar env with: pip install -r <this-file-as-requirements>.txt")
    lines.append("- Prefer copying only the PIP FREEZE section into a requirements.txt for installs.")
    lines.append("")
    lines.append("End of environment report.")
    lines.append("")
    return "\n".join(lines)


def default_deps_filename() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"comfyui-environment-{stamp}.txt"
