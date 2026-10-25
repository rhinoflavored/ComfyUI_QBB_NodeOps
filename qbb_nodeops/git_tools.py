"""System Git prerequisite; never download or install Git implicitly."""
import shutil


def require_git():
    executable = shutil.which('git')
    if not executable:
        raise ValueError('未找到系统 Git。请安装 Git 并将其加入 PATH，然后重启 ComfyUI。可在启动 ComfyUI 的终端运行 git --version 检查；GitPython 不能替代系统 Git。')
    return executable
