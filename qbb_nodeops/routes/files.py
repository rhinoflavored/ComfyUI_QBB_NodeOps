from __future__ import annotations

import os
import re
from datetime import datetime


def default_log_filename() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"comfyui-nodeops-{stamp}.log"


def safe_filename(name: str, fallback: str, default_ext: str = ".log") -> str:
    name = (name or "").strip() or fallback
    name = os.path.basename(name.replace("\\", "/"))
    name = re.sub(r'[\r\n\x00"\'`;$]', "", name).strip()
    if not name or name in (".", ".."):
        name = fallback
    if "." not in name:
        name += default_ext
    return name
